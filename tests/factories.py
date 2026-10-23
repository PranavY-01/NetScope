"""Builders for result and report models used across tests."""

from datetime import datetime, timezone
from ipaddress import IPv4Address

from netscope import __version__
from netscope.models import (
    DiscoveryMetadata,
    DiscoveryReport,
    HostResult,
    HostState,
    PortResult,
    PortState,
    ProbeEvidence,
    RunSettings,
    ScanMetadata,
    ScanReport,
    TargetError,
)

T0 = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

SETTINGS = RunSettings(
    timeout_s=1.0,
    concurrency=100,
    rate_per_s=50.0,
    max_hosts=256,
    allow_public=False,
    allowlist_entries=None,
    fast_refusal_requested=True,
    fast_refusal_active=False,
)


def port_result(port=80, state=PortState.OPEN, ip="127.0.0.1", target="127.0.0.1", **extra):
    return PortResult(
        target_input=target, ip=IPv4Address(ip), port=port, state=state, elapsed_ms=1.5, started_at=T0, **extra
    )


def evidence(port=80, state=PortState.CLOSED, **extra):
    return ProbeEvidence(port=port, state=state, elapsed_ms=2.0, started_at=T0, **extra)


def scan_report(results=(), target_errors=(), interrupted=False, target="127.0.0.1"):
    return ScanReport(
        metadata=ScanMetadata(
            version=__version__,
            target_input=target,
            settings=SETTINGS,
            platform="test",
            started_at=T0,
            finished_at=T0,
            duration_ms=0.0,
            interrupted=interrupted,
            ports=sorted({r.port for r in results}) or [80],
            requested_probes=len(results),
            completed_probes=len(results),
        ),
        resolved_ips=sorted({r.ip for r in results}),
        target_errors=list(target_errors),
        results=list(results),
    )


def host_result(ip="10.0.0.1", state=HostState.RESPONSIVE, probes=None, **extra):
    probes = probes if probes is not None else [evidence()]
    return HostResult(
        target_input="10.0.0.0/30",
        ip=IPv4Address(ip),
        state=state,
        probed_ports=[p.port for p in probes],
        evidence=probes,
        elapsed_ms=3.0,
        **extra,
    )


def discovery_report(results=(), interrupted=False):
    return DiscoveryReport(
        metadata=DiscoveryMetadata(
            version=__version__,
            target_input="10.0.0.0/30",
            settings=SETTINGS,
            platform="test",
            started_at=T0,
            finished_at=T0,
            duration_ms=0.0,
            interrupted=interrupted,
            network="10.0.0.0/30",
            discovery_ports=[80],
            requested_hosts=2,
            completed_hosts=len(results),
        ),
        results=list(results),
    )


def dns_error(target="nx.example"):
    return TargetError(target_input=target, error_code="dns:EAI_NONAME", error_message="name not found")
