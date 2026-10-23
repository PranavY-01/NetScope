"""CIDR host discovery: expand under the cap, probe each host, and derive evidence-based verdicts."""

import sys
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from ipaddress import IPv4Address

from netscope import __version__
from netscope.config import DiscoveryConfig
from netscope.guardrails import expand_hosts, preflight_addresses
from netscope.models import (
    DiscoveryMetadata,
    DiscoveryReport,
    HostResult,
    HostState,
    PortResult,
    PortState,
    ProbeEvidence,
)
from netscope.probe import ConnectFn, make_tcp_connect, probe_port
from netscope.rate_limit import RateLimiter
from netscope.scanner import run_settings
from netscope.scheduler import RunTimer, run_bounded

_TCP_RESPONSE = frozenset({PortState.OPEN, PortState.CLOSED})


@dataclass(frozen=True)
class DiscoveryPlan:
    """A discovery run that has passed every guardrail. Only a plan can be executed."""

    config: DiscoveryConfig
    hosts: tuple[IPv4Address, ...]

    @property
    def requested_hosts(self) -> int:
        return len(self.hosts)

    @property
    def requested_probes(self) -> int:
        return len(self.hosts) * len(self.config.ports)


def prepare_discovery(config: DiscoveryConfig) -> DiscoveryPlan:
    """Enforce the cap from the CIDR size, expand, then run the remaining guardrails. Raises ScopeError."""
    hosts = expand_hosts(config.network, config.max_hosts)
    preflight_addresses(
        hosts,
        max_hosts=config.max_hosts,
        allow_public=config.allow_public,
        allowlist=config.allowlist,
        authorized=config.authorized,
        what=f"network {config.network}",
    )
    return DiscoveryPlan(config=config, hosts=tuple(hosts))


def host_verdict(evidence: Sequence[ProbeEvidence]) -> tuple[HostState, str | None, str | None]:
    """Any TCP response (open or refused) wins; else any probe error; else no response observed."""
    if not evidence:
        raise ValueError("a verdict needs at least one probe")
    if any(probe.state in _TCP_RESPONSE for probe in evidence):
        return HostState.RESPONSIVE, None, None
    for probe in evidence:
        if probe.state is PortState.ERROR:
            return (
                HostState.ERROR,
                probe.error_code,
                f"probe to port {probe.port} failed, so no_response cannot be asserted: {probe.error_message}",
            )
    return HostState.NO_RESPONSE, None, None


def _host_result(
    target_input: str, ip: IPv4Address, ports: Sequence[int], results: list[PortResult], elapsed_ms: float
) -> HostResult:
    by_port = {r.port: r for r in results}
    evidence = [ProbeEvidence.from_port_result(by_port[port]) for port in ports]
    state, code, message = host_verdict(evidence)
    return HostResult(
        target_input=target_input,
        ip=ip,
        state=state,
        probed_ports=list(ports),
        evidence=evidence,
        elapsed_ms=elapsed_ms,
        error_code=code,
        error_message=message,
    )


async def iter_discovery(
    plan: DiscoveryPlan,
    *,
    connect: ConnectFn | None = None,
    limiter: RateLimiter | None = None,
) -> AsyncIterator[HostResult]:
    """Yield each host's verdict once all of its probes have completed.

    A host whose probes were cut short by cancellation is not reported: its verdict was never reached.
    """
    config = plan.config
    target_input = str(config.network)
    ports = config.ports
    connect = connect or make_tcp_connect(config.fast_refusal)
    limiter = limiter or RateLimiter(config.rate)
    first_probe_start: dict[IPv4Address, float] = {}
    pending: dict[IPv4Address, list[PortResult]] = {}
    # Host-major order, so verdicts become available progressively.
    jobs = ((ip, port) for ip in plan.hosts for port in ports)

    async def run(job: tuple[IPv4Address, int]) -> PortResult:
        ip, port = job
        # Runs after the rate-limit slot, so host timing excludes time spent queued.
        first_probe_start.setdefault(ip, time.perf_counter())
        return await probe_port(target_input, ip, port, timeout=config.timeout, connect=connect)

    async with aclosing(run_bounded(jobs, run, concurrency=config.concurrency, limiter=limiter)) as stream:
        async for result in stream:
            collected = pending.setdefault(result.ip, [])
            collected.append(result)
            if len(collected) == len(ports):
                del pending[result.ip]
                elapsed_ms = round((time.perf_counter() - first_probe_start.pop(result.ip)) * 1000, 3)
                yield _host_result(target_input, result.ip, ports, collected, elapsed_ms)


def build_discovery_report(
    plan: DiscoveryPlan,
    hosts: Iterable[HostResult],
    *,
    timer: RunTimer,
    interrupted: bool,
) -> DiscoveryReport:
    finished_at, duration_ms = timer.finish()
    ordered = sorted(hosts, key=lambda h: int(h.ip))
    config = plan.config
    return DiscoveryReport(
        metadata=DiscoveryMetadata(
            version=__version__,
            target_input=str(config.network),
            settings=run_settings(config),
            platform=sys.platform,
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            interrupted=interrupted,
            network=str(config.network),
            discovery_ports=list(config.ports),
            requested_hosts=plan.requested_hosts,
            completed_hosts=len(ordered),
        ),
        results=ordered,
    )


async def discover_hosts(config: DiscoveryConfig, *, connect: ConnectFn | None = None) -> DiscoveryReport:
    """Preflight, discover, and report. For partial results on cancellation, use iter_discovery directly."""
    plan = prepare_discovery(config)
    timer = RunTimer()
    hosts = [host async for host in iter_discovery(plan, connect=connect)]
    return build_discovery_report(plan, hosts, timer=timer, interrupted=False)
