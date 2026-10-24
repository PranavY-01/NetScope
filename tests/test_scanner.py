import asyncio
import errno
import time
from contextlib import aclosing
from ipaddress import IPv4Address

import pytest
from timing import EARLY_WAKE_MS, EARLY_WAKE_S

from netscope.config import ScanConfig
from netscope.guardrails import ScopeError
from netscope.models import PortState
from netscope.probe import make_tcp_connect
from netscope.rate_limit import RateLimiter
from netscope.resolver import ResolutionError
from netscope.scanner import build_scan_report, iter_scan, prepare_scan, scan_ports
from netscope.scheduler import RunTimer, run_bounded, stream_results


def config(target="127.0.0.1", **overrides):
    return ScanConfig(**({"target": target, "authorized": True, "rate": 10_000} | overrides))


def resolver_of(*answers):
    async def resolve(host):
        return list(answers)

    return resolve


async def failing_resolver(host):
    raise ResolutionError("dns:EAI_NONAME", f"cannot resolve {host}")


class FakeNetwork:
    """Connect function with configurable outcomes that records concurrency and timing."""

    def __init__(self, open_ports=(), delay=0.0):
        self.open = set(open_ports)
        self.delay = delay
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.max_tasks = 0
        self.cancelled = 0

    async def __call__(self, ip, port):
        self.calls.append((ip, port, time.perf_counter()))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.max_tasks = max(self.max_tasks, len(asyncio.all_tasks()))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if (ip, port) not in self.open:
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1


# ---- preflight -------------------------------------------------------------------------------


async def test_prepare_ip_literal():
    plan = await prepare_scan(config("127.0.0.1"), resolver=failing_resolver)
    assert plan.ips == (IPv4Address("127.0.0.1"),)
    assert plan.requested_probes == 3


async def test_prepare_hostname_keeps_every_ipv4_answer():
    plan = await prepare_scan(config("multi.lan"), resolver=resolver_of("10.0.0.1", "10.0.0.2", "10.0.0.1"))
    assert plan.ips == (IPv4Address("10.0.0.1"), IPv4Address("10.0.0.2"))
    assert plan.requested_probes == 6


async def test_prepare_dns_failure_is_structured_not_probed():
    plan = await prepare_scan(config("nx.lan"), resolver=failing_resolver)
    assert plan.ips == ()
    assert plan.target_errors[0].error_code == "dns:EAI_NONAME"
    assert plan.requested_probes == 0


@pytest.mark.parametrize(
    ("cfg", "answers", "code"),
    [
        (config("pub.example"), ("93.184.216.34",), "public_target"),
        (config("ll.lan"), ("169.254.1.1",), "special_address"),
        (config("many.lan", max_hosts=2), ("10.0.0.1", "10.0.0.2", "10.0.0.3"), "host_cap"),
        (config("h.lan", authorized=False), ("10.0.0.1",), "not_authorized"),
        (config("nx.lan", authorized=False), None, "not_authorized"),
    ],
)
async def test_prepare_rejects_scope(cfg, answers, code):
    resolver = failing_resolver if answers is None else resolver_of(*answers)
    with pytest.raises(ScopeError) as info:
        await prepare_scan(cfg, resolver=resolver)
    assert info.value.code == code


async def test_public_ok_with_override():
    plan = await prepare_scan(config("pub.example", allow_public=True), resolver=resolver_of("93.184.216.34"))
    assert plan.ips == (IPv4Address("93.184.216.34"),)


async def test_hostname_resolving_to_shared_space_needs_allow_shared():
    with pytest.raises(ScopeError) as info:
        await prepare_scan(config("cgnat.lan", allow_public=True), resolver=resolver_of("100.64.1.2"))
    assert info.value.code == "shared_address"
    plan = await prepare_scan(config("cgnat.lan", allow_shared=True), resolver=resolver_of("100.64.1.2"))
    assert plan.ips == (IPv4Address("100.64.1.2"),)


async def test_scope_applies_to_every_resolved_address():
    """One out-of-scope A record rejects the whole hostname, not just that address."""
    with pytest.raises(ScopeError) as info:
        await prepare_scan(config("mixed.lan"), resolver=resolver_of("10.0.0.1", "8.8.8.8"))
    assert info.value.code == "public_target"


async def test_unauthorized_request_never_reaches_the_resolver():
    """Review finding 6.2: the acknowledgement gate precedes any DNS query."""

    async def must_not_resolve(host):
        raise AssertionError(f"resolver called for {host!r} before authorization")

    for target in ("some.lan", "8.8.8.8.example", "pub.example"):
        with pytest.raises(ScopeError) as info:
            await prepare_scan(config(target, authorized=False), resolver=must_not_resolve)
        assert info.value.code == "not_authorized"


async def test_authorized_request_does_resolve():
    calls = []

    async def recording(host):
        calls.append(host)
        return ["10.0.0.9"]

    await prepare_scan(config("ok.lan"), resolver=recording)
    assert calls == ["ok.lan"]


async def test_input_errors_are_reported_without_resolving():
    """Pure parsing may precede the gate; invalid syntax fails before any resolver call either way."""
    from netscope.config import ConfigError

    async def must_not_resolve(host):
        raise AssertionError("resolver called")

    with pytest.raises((ConfigError, ValueError)):
        await prepare_scan(ScanConfig.model_construct(target="10.1", authorized=True, ports=(80,)), resolver=must_not_resolve)


async def test_plan_pins_resolved_addresses():
    """The engine probes the plan's addresses; it never re-resolves the name (DNS rebinding guard)."""
    answers = iter([["10.0.0.5"], ["8.8.8.8"]])

    async def changing(host):
        return next(answers)

    plan = await prepare_scan(config("flip.lan", ports="80"), resolver=changing)
    net = FakeNetwork()
    [r async for r in iter_scan(plan, connect=net)]
    assert [(ip, port) for ip, port, _ in net.calls] == [("10.0.0.5", 80)]


# ---- orchestration ---------------------------------------------------------------------------


async def test_every_resolved_ip_is_scanned_and_reported_separately():
    plan = await prepare_scan(config("multi.lan", ports="22,80"), resolver=resolver_of("10.0.0.2", "10.0.0.1"))
    net = FakeNetwork(open_ports={("10.0.0.1", 80)})
    results = [r async for r in iter_scan(plan, connect=net)]
    assert len(results) == 4
    assert {(str(r.ip), r.port, r.state) for r in results} == {
        ("10.0.0.1", 22, PortState.CLOSED),
        ("10.0.0.1", 80, PortState.OPEN),
        ("10.0.0.2", 22, PortState.CLOSED),
        ("10.0.0.2", 80, PortState.CLOSED),
    }
    assert {r.target_input for r in results} == {"multi.lan"}


async def test_in_flight_probes_never_exceed_concurrency():
    plan = await prepare_scan(config(ports="1-100", concurrency=7))
    net = FakeNetwork(delay=0.01)
    results = [r async for r in iter_scan(plan, connect=net)]
    assert len(results) == 100
    assert net.max_in_flight == 7


async def test_task_creation_is_bounded_across_a_slash_24():
    ips = [f"10.9.8.{i}" for i in range(1, 255)]
    plan = await prepare_scan(config("big.lan", ports="80", concurrency=16), resolver=resolver_of(*ips))
    net = FakeNetwork(delay=0.001)
    results = [r async for r in iter_scan(plan, connect=net)]
    assert len(results) == 254
    # 16 workers + the test's own task; never one task per job.
    assert net.max_tasks <= 16 + 2


async def test_start_rate_is_limited_independently_of_concurrency():
    plan = await prepare_scan(config(ports="1-41", concurrency=50, rate=200))
    net = FakeNetwork()
    start = time.perf_counter()
    [r async for r in iter_scan(plan, connect=net)]
    starts = sorted(t for _, _, t in net.calls)
    # Slots are absolute, so only the last start's early wake-up can shorten the span.
    assert starts[-1] - starts[0] >= 40 / 200 - EARLY_WAKE_S
    assert time.perf_counter() - start < 5


async def test_results_are_yielded_as_they_complete():
    plan = await prepare_scan(config(ports="1,2"))

    async def connect(ip, port):
        await asyncio.sleep(0.2 if port == 1 else 0.0)

    order = [r.port async for r in iter_scan(plan, connect=connect)]
    assert order == [2, 1]


async def test_closing_the_stream_cancels_outstanding_probes():
    plan = await prepare_scan(config(ports="1-50", concurrency=10))
    net = FakeNetwork(delay=0.05)
    got = []
    async with aclosing(iter_scan(plan, connect=net)) as stream:
        async for result in stream:
            got.append(result)
            if len(got) == 3:
                break
    assert net.in_flight == 0
    assert net.cancelled > 0
    assert len(net.calls) < 50
    assert all(t.done() for t in asyncio.all_tasks() if t.get_name().startswith("netscope-worker"))


async def test_worker_failure_surfaces():
    async def boom(job):
        raise RuntimeError("bug in fn")

    with pytest.raises(RuntimeError, match="bug in fn"):
        await run_bounded([1, 2, 3], boom, concurrency=2, limiter=RateLimiter(1000), on_result=print)
    assert _live_workers() == []

    with pytest.raises(RuntimeError, match="bug in fn"):
        [r async for r in stream_results(lambda cb: run_bounded([1], boom, concurrency=1, limiter=RateLimiter(1000), on_result=cb))]


def _live_workers():
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("netscope-") and not t.done()]


async def test_run_bounded_delivers_every_result_via_callback():
    async def double(job):
        await asyncio.sleep(0)
        return job * 2

    got = []
    await run_bounded(range(50), double, concurrency=8, limiter=RateLimiter(10_000), on_result=got.append)
    assert sorted(got) == [i * 2 for i in range(50)]
    assert _live_workers() == []


async def test_scan_ports_report():
    report = await scan_ports(config("h.lan", ports="443,22"), resolver=resolver_of("10.0.0.5"), connect=FakeNetwork())
    md = report.metadata
    assert md.requested_probes == md.completed_probes == 2
    assert md.interrupted is False
    assert md.ports == [22, 443]
    assert [r.port for r in report.results] == [22, 443]
    assert report.summary[PortState.CLOSED] == 2
    assert not report.has_errors


async def test_dns_failure_report_has_error_and_no_fake_timeouts():
    report = await scan_ports(config("nx.lan"), resolver=failing_resolver, connect=FakeNetwork())
    assert report.results == []
    assert report.target_errors[0].target_input == "nx.lan"
    assert report.has_errors


async def test_partial_report_from_collected_results():
    plan = await prepare_scan(config(ports="1-10"))
    timer = RunTimer()
    collected = []
    async with aclosing(iter_scan(plan, connect=FakeNetwork())) as stream:
        async for result in stream:
            collected.append(result)
            if len(collected) == 4:
                break
    report = build_scan_report(plan, collected, timer=timer, interrupted=True)
    assert report.metadata.interrupted is True
    assert (report.metadata.requested_probes, report.metadata.completed_probes) == (10, 4)


# ---- real loopback /24 fixture -------------------------------------------------------------


@pytest.mark.integration
async def test_slash_24_scan_against_local_fixture_within_limits():
    async def handle(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ips = [f"127.0.0.{i}" for i in range(1, 255)]
    real = make_tcp_connect()
    net = FakeNetwork()

    async def counted(ip, p):
        net.in_flight += 1
        net.max_in_flight = max(net.max_in_flight, net.in_flight)
        net.max_tasks = max(net.max_tasks, len(asyncio.all_tasks()))
        try:
            await real(ip, p)
        finally:
            net.in_flight -= 1

    try:
        cfg = config("loop.lan", ports=str(port), concurrency=32, rate=2000, timeout=2.0)
        report = await scan_ports(cfg, resolver=resolver_of(*ips), connect=counted)
    finally:
        server.close()
        await server.wait_closed()

    states = {str(r.ip): r.state for r in report.results}
    assert len(states) == 254
    assert states["127.0.0.1"] is PortState.OPEN
    assert net.max_in_flight <= 32
    assert net.max_tasks <= 32 + 4
    assert report.summary[PortState.ERROR] == 0
    # Other loopback addresses have no listener on this port: refused (or, on some stacks, open
    # if the listener were bound to all of 127/8 - it is bound to 127.0.0.1 only).
    assert all(state is PortState.CLOSED for ip, state in states.items() if ip != "127.0.0.1"), report.summary
    # At 2000 starts/s, 254 probes need >= ~126 ms.
    assert report.metadata.duration_ms >= 253 / 2000 * 1000 - EARLY_WAKE_MS
