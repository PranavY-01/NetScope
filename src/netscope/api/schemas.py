"""HTTP request and response models. The scan policy itself lives in the engine (config, guardrails);
these models only add the server's tighter size bounds and describe the JSON contract."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from netscope.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_HOSTS,
    DEFAULT_PORTS,
    DEFAULT_RATE_PER_S,
    DEFAULT_TIMEOUT_S,
)
from netscope.estimate import DurationEstimate
from netscope.models import HostResult, PortResult, TargetError

# Server-side bounds, tighter than the CLI's, because the API is a long-running local service.
MAX_PORTS = 4096
MAX_PROBES = 20_000
MAX_HOSTS = 1024
MAX_TIMEOUT_S = 10.0
MAX_CONCURRENCY = 256
MAX_RATE_PER_S = 500.0
MAX_ALLOWLIST_ENTRIES = 1024
MAX_BODY_BYTES = 64 * 1024
MAX_PAGE = 200

ScanKind = Literal["scan", "discover"]
ScanStatus = Literal["queued", "running", "completed", "cancelled", "failed"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ScanCreate(BaseModel):
    """A scan request. `authorized` must be true; the engine checks it before any DNS lookup."""

    model_config = ConfigDict(extra="forbid")

    kind: ScanKind
    target: str = Field(min_length=1, max_length=253, description="IPv4 address or hostname (scan), or IPv4 CIDR (discover)")
    ports: str = Field(default=",".join(map(str, DEFAULT_PORTS)), min_length=1, max_length=512)
    timeout: float = Field(default=DEFAULT_TIMEOUT_S, gt=0, le=MAX_TIMEOUT_S)
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=MAX_CONCURRENCY)
    rate: float = Field(default=DEFAULT_RATE_PER_S, gt=0, le=MAX_RATE_PER_S)
    max_hosts: int = Field(default=DEFAULT_MAX_HOSTS, ge=1, le=MAX_HOSTS)
    allow_public: bool = False
    allow_shared: bool = False
    allowlist: list[str] | None = Field(default=None, max_length=MAX_ALLOWLIST_ENTRIES)
    authorized: bool = False


class ErrorDetail(_Out):
    code: str
    message: str
    fields: list[dict[str, str]] | None = None


class ErrorResponse(_Out):
    error: ErrorDetail


class Progress(_Out):
    completed: int
    total: int
    unit: Literal["probes", "hosts"]
    determinate: bool


class ScanFailure(_Out):
    code: str
    message: str


class ScanOut(_Out):
    id: str
    kind: ScanKind
    target: str
    status: ScanStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    progress: Progress
    settings: dict[str, Any]
    resolved_ips: list[str]
    target_errors: list[TargetError]
    summary: dict[str, int]
    estimate: DurationEstimate | None
    error: ScanFailure | None
    partial: bool
    exportable: bool


class ScanPage(_Out):
    items: list[ScanOut]
    total: int
    limit: int
    offset: int


class AddressResults(_Out):
    """Port observations for one probed IPv4 address of a scan."""

    ip: str
    ports: list[PortResult]


class ScanResultsPage(_Out):
    kind: Literal["scan"]
    items: list[AddressResults]
    total: int
    limit: int
    offset: int


class DiscoveryResultsPage(_Out):
    kind: Literal["discover"]
    items: list[HostResult]
    total: int
    limit: int
    offset: int


class CancelOut(_Out):
    id: str
    status: ScanStatus
    cancel_requested: bool


class Stats(_Out):
    total: int
    by_status: dict[str, int]


class Health(_Out):
    status: Literal["ok"]
    version: str


class Limits(_Out):
    max_ports: int = MAX_PORTS
    max_probes: int = MAX_PROBES
    max_hosts: int = MAX_HOSTS
    max_timeout_s: float = MAX_TIMEOUT_S
    max_concurrency: int = MAX_CONCURRENCY
    max_rate_per_s: float = MAX_RATE_PER_S
    max_allowlist_entries: int = MAX_ALLOWLIST_ENTRIES
    max_running_jobs: int
    max_queued_jobs: int
    global_rate_per_s: float
    defaults: dict[str, Any] = Field(
        default_factory=lambda: {
            "ports": ",".join(map(str, DEFAULT_PORTS)),
            "timeout": DEFAULT_TIMEOUT_S,
            "concurrency": DEFAULT_CONCURRENCY,
            "rate": DEFAULT_RATE_PER_S,
            "max_hosts": DEFAULT_MAX_HOSTS,
        }
    )
