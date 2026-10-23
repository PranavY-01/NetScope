import asyncio
import errno
import socket
from ipaddress import IPv4Address, IPv4Network

import pytest
from factories import evidence

from netscope.config import DiscoveryConfig
from netscope.discovery import discover_hosts, host_verdict, iter_discovery, prepare_discovery
from netscope.guardrails import ScopeError
from netscope.models import HostState, PortState

P = PortState
H = HostState


def config(network="10.0.0.0/29", **overrides):
    return DiscoveryConfig(**({"network": network, "authorized": True, "rate": 10_000} | overrides))


# ---- verdict rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([P.OPEN], H.RESPONSIVE),
        ([P.CLOSED], H.RESPONSIVE),  # a refusal is evidence that something answered
        ([P.INCONCLUSIVE, P.CLOSED], H.RESPONSIVE),
        ([P.ERROR, P.OPEN], H.RESPONSIVE),  # positive evidence wins over an error
        ([P.ERROR, P.INCONCLUSIVE, P.CLOSED], H.RESPONSIVE),
        ([P.INCONCLUSIVE], H.NO_RESPONSE),
        ([P.INCONCLUSIVE, P.INCONCLUSIVE, P.INCONCLUSIVE], H.NO_RESPONSE),
        ([P.INCONCLUSIVE, P.ERROR], H.ERROR),  # mixed timeout + error: cannot assert no_response
        ([P.ERROR, P.INCONCLUSIVE, P.INCONCLUSIVE], H.ERROR),
        ([P.ERROR], H.ERROR),
        ([P.FILTERED], H.NO_RESPONSE),  # never emitted in V1; would not be positive evidence
    ],
)
def test_host_verdict(states, expected):
    probes = [
        evidence(22 + i, state, **({"error_code": "errno:EMFILE", "error_message": "x"} if state is P.ERROR else {}))
        for i, state in enumerate(states)
    ]
    state, code, message = host_verdict(probes)
    assert state is expected
    if expected is H.ERROR:
        assert code == "errno:EMFILE" and "cannot be asserted" in message
    else:
        assert code is None and message is None


def test_verdict_requires_evidence():
    with pytest.raises(ValueError):
        host_verdict([])


# ---- preflight ------------------------------------------------------------------------------


def test_prepare_expands_slash_24_to_254_hosts():
    plan = prepare_discovery(config("192.168.50.0/24"))
    assert plan.requested_hosts == 254
    assert plan.requested_probes == 254 * 3
    assert IPv4Address("192.168.50.0") not in plan.hosts


@pytest.mark.parametrize(
    ("cfg", "code"),
    [
        (config("10.0.0.0/23"), "host_cap"),
        (config("10.0.0.0/8"), "host_cap"),
        (config("10.0.0.0/24", max_hosts=100), "host_cap"),
        (config("8.8.8.0/29"), "public_target"),
        (config("169.254.0.0/29"), "special_address"),
        (config("224.0.0.0/29", allow_public=True), "special_address"),
        (config("10.0.0.0/29", allowlist=(IPv4Network("10.0.0.0/30"),)), "allowlist"),
        (config("10.0.0.0/29", authorized=False), "not_authorized"),
    ],
)
def test_prepare_rejects_scope(cfg, code):
    with pytest.raises(ScopeError) as info:
        prepare_discovery(cfg)
    assert info.value.code == code


def test_raised_cap_allows_larger_network():
    assert prepare_discovery(config("10.0.0.0/23", max_hosts=510)).requested_hosts == 510


# ---- orchestration with a scripted network --------------------------------------------------


class ScriptedNetwork:
    """Per-(ip, port) outcomes: 'open', 'refused', 'hang', or 'error'. Records concurrency."""

    def __init__(self, script, default="hang"):
        self.script = script
        self.default = default
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.max_tasks = 0

    async def __call__(self, ip, port):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.max_tasks = max(self.max_tasks, len(asyncio.all_tasks()))
        try:
            outcome = self.script.get((ip, port), self.script.get(ip, self.default))
            await asyncio.sleep(0)
            if outcome == "open":
                return
            if outcome == "refused":
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            if outcome == "error":
                raise OSError(errno.EMFILE, "too many open files")
            await asyncio.sleep(30)
        finally:
            self.in_flight -= 1


async def test_discovery_verdicts_with_evidence():
    net = ScriptedNetwork(
        {
            ("10.0.0.1", 80): "open",
            "10.0.0.2": "refused",
            ("10.0.0.4", 22): "error",
        }
    )
    report = await discover_hosts(config("10.0.0.0/29", ports="22,80", timeout=0.05), connect=net)
    verdicts = {str(h.ip): h.state for h in report.results}
    assert verdicts == {
        "10.0.0.1": H.RESPONSIVE,
        "10.0.0.2": H.RESPONSIVE,
        "10.0.0.3": H.NO_RESPONSE,
        "10.0.0.4": H.ERROR,
        "10.0.0.5": H.NO_RESPONSE,
        "10.0.0.6": H.NO_RESPONSE,
    }
    host1 = report.results[0]
    assert host1.probed_ports == [22, 80]
    assert [(e.port, e.state) for e in host1.evidence] == [(22, P.INCONCLUSIVE), (80, P.OPEN)]
    assert host1.target_input == "10.0.0.0/29"
    host4 = report.results[3]
    assert host4.error_code == "errno:EMFILE"
    assert [e.state for e in host4.evidence] == [P.ERROR, P.INCONCLUSIVE]
    assert report.summary == {H.RESPONSIVE: 2, H.NO_RESPONSE: 3, H.ERROR: 1}
    assert report.has_errors
    assert report.metadata.requested_hosts == report.metadata.completed_hosts == 6
    assert all(h.elapsed_ms >= 0 for h in report.results)


async def test_no_timeout_ever_becomes_filtered():
    report = await discover_hosts(config("10.0.0.0/30", timeout=0.02), connect=ScriptedNetwork({}))
    assert {h.state for h in report.results} == {H.NO_RESPONSE}
    assert all(e.state is P.INCONCLUSIVE for h in report.results for e in h.evidence)


async def test_verdicts_are_yielded_progressively():
    net = ScriptedNetwork({}, default="refused")
    plan = prepare_discovery(config("10.0.0.0/28", concurrency=1))
    async for host in iter_discovery(plan, connect=net):
        assert host.ip == IPv4Address("10.0.0.1")
        assert net.calls < plan.requested_probes
        break


async def test_slash_24_discovery_is_bounded():
    net = ScriptedNetwork({}, default="refused")
    report = await discover_hosts(config("10.20.30.0/24", concurrency=20), connect=net)
    assert report.metadata.completed_hosts == 254
    assert net.calls == 254 * 3
    assert net.max_in_flight <= 20
    assert net.max_tasks <= 20 + 2


@pytest.mark.integration
async def test_real_loopback_discovery_counts_refusals_as_responsive():
    async def handle(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    open_port = server.sockets[0].getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    try:
        report = await discover_hosts(config("127.0.0.0/29", ports=f"{open_port},{unused}", timeout=2.0))
    finally:
        server.close()
        await server.wait_closed()
    assert {h.state for h in report.results} == {H.RESPONSIVE}
    first = report.results[0]
    assert str(first.ip) == "127.0.0.1"
    assert {e.port: e.state for e in first.evidence} == {open_port: P.OPEN, unused: P.CLOSED}
    assert all(e.state is P.CLOSED for h in report.results[1:] for e in h.evidence)
