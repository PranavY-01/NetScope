"""Port-scan orchestration: preflight, bounded probing, and report assembly."""

import logging
import sys
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address

from netscope import __version__
from netscope.config import CommonConfig, ScanConfig, parse_scan_target
from netscope.guardrails import check_authorized, preflight_addresses
from netscope.models import PortResult, RunSettings, ScanMetadata, ScanReport, TargetError
from netscope.probe import ConnectFn, fast_refusal_active, make_tcp_connect, probe_port
from netscope.rate_limit import RateLimiter
from netscope.resolver import ResolutionError, Resolver, resolve_ipv4, system_resolver
from netscope.scheduler import RunTimer, run_bounded, stream_results

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanPlan:
    """A scan that has passed every guardrail. Only a plan can be executed."""

    config: ScanConfig
    ips: tuple[IPv4Address, ...]
    target_errors: tuple[TargetError, ...]

    @property
    def requested_probes(self) -> int:
        return len(self.ips) * len(self.config.ports)


async def prepare_scan(config: ScanConfig, *, resolver: Resolver = system_resolver) -> ScanPlan:
    """Check the acknowledgement, resolve, then run the scope guardrails. Raises ScopeError.

    The acknowledgement gate comes first, so an unacknowledged request causes no DNS query and no probe.
    Scope checks run on the resolved addresses, and the plan pins them: the engine probes exactly these
    addresses, never a fresh resolution, so a name that later resolves elsewhere cannot widen the scope.
    """
    target = parse_scan_target(config.target)  # pure parsing, no network
    check_authorized(config.authorized)
    target_errors: list[TargetError] = []
    if isinstance(target, IPv4Address):
        ips = [target]
    else:
        try:
            ips = await resolve_ipv4(target, resolver)
            log.debug("%s resolved to %s", target, ", ".join(map(str, ips)))
        except ResolutionError as exc:
            ips = []
            target_errors.append(TargetError(target_input=config.target, error_code=exc.code, error_message=exc.message))
    preflight_addresses(
        ips,
        max_hosts=config.max_hosts,
        allow_public=config.allow_public,
        allow_shared=config.allow_shared,
        allowlist=config.allowlist,
        authorized=config.authorized,
        what=f"target {config.target!r}",
    )
    return ScanPlan(config=config, ips=tuple(ips), target_errors=tuple(target_errors))


async def run_scan(
    plan: ScanPlan,
    *,
    on_result: Callable[[PortResult], None],
    connect: ConnectFn | None = None,
    limiter: RateLimiter | None = None,
) -> None:
    """Probe the plan, calling on_result as each probe completes.

    Every completed result reaches on_result even if the run is cancelled immediately afterwards, so
    a caller that collects in on_result keeps all completed work on Ctrl-C.
    """
    config = plan.config
    connect = connect or make_tcp_connect(config.fast_refusal)
    limiter = limiter or RateLimiter(config.rate)
    # Port-major order spreads consecutive probes across a hostname's addresses.
    jobs = ((ip, port) for port in config.ports for ip in plan.ips)

    async def run(job: tuple[IPv4Address, int]) -> PortResult:
        ip, port = job
        return await probe_port(config.target, ip, port, timeout=config.timeout, connect=connect)

    await run_bounded(jobs, run, concurrency=config.concurrency, limiter=limiter, on_result=on_result)


def iter_scan(
    plan: ScanPlan,
    *,
    connect: ConnectFn | None = None,
    limiter: RateLimiter | None = None,
) -> AsyncIterator[PortResult]:
    """Stream port results as probes complete. Lossy on cancellation; see run_scan."""
    return stream_results(lambda on_result: run_scan(plan, on_result=on_result, connect=connect, limiter=limiter))


def run_settings(config: CommonConfig) -> RunSettings:
    return RunSettings(
        timeout_s=config.timeout,
        concurrency=config.concurrency,
        rate_per_s=config.rate,
        max_hosts=config.max_hosts,
        allow_public=config.allow_public,
        allow_shared=config.allow_shared,
        allowlist_entries=None if config.allowlist is None else len(config.allowlist),
        fast_refusal_requested=config.fast_refusal,
        fast_refusal_active=config.fast_refusal and fast_refusal_active(),
    )


def build_scan_report(
    plan: ScanPlan,
    results: Iterable[PortResult],
    *,
    timer: RunTimer,
    interrupted: bool,
) -> ScanReport:
    finished_at, duration_ms = timer.finish()
    ordered = sorted(results, key=lambda r: (int(r.ip), r.port))
    return ScanReport(
        metadata=ScanMetadata(
            version=__version__,
            target_input=plan.config.target,
            settings=run_settings(plan.config),
            platform=sys.platform,
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            interrupted=interrupted,
            ports=list(plan.config.ports),
            requested_probes=plan.requested_probes,
            completed_probes=len(ordered),
        ),
        resolved_ips=list(plan.ips),
        target_errors=list(plan.target_errors),
        results=ordered,
    )


async def scan_ports(
    config: ScanConfig,
    *,
    resolver: Resolver = system_resolver,
    connect: ConnectFn | None = None,
) -> ScanReport:
    """Preflight, scan, and report. For partial results on cancellation, use run_scan directly."""
    plan = await prepare_scan(config, resolver=resolver)
    timer = RunTimer()
    results: list[PortResult] = []
    await run_scan(plan, on_result=results.append, connect=connect)
    return build_scan_report(plan, results, timer=timer, interrupted=False)
