"""Typed result and report models shared by the engine, CLI, tests, and a future API."""

from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

SCHEMA_VERSION = "1"


class PortState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    # Reserved in the schema; never emitted in V1 (capsule §20.1).
    FILTERED = "filtered"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class HostState(StrEnum):
    RESPONSIVE = "responsive"
    NO_RESPONSE = "no_response"
    ERROR = "error"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PortResult(_Model):
    """Outcome of one TCP connect probe."""

    target_input: str
    ip: IPv4Address
    port: int = Field(ge=1, le=65535)
    state: PortState
    elapsed_ms: float = Field(ge=0)
    started_at: datetime
    error_code: str | None = None
    error_message: str | None = None


class ProbeEvidence(_Model):
    """One discovery probe, recorded so a host verdict can be explained."""

    port: int = Field(ge=1, le=65535)
    state: PortState
    elapsed_ms: float = Field(ge=0)
    started_at: datetime
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_port_result(cls, result: PortResult) -> "ProbeEvidence":
        return cls(
            port=result.port,
            state=result.state,
            elapsed_ms=result.elapsed_ms,
            started_at=result.started_at,
            error_code=result.error_code,
            error_message=result.error_message,
        )


class HostResult(_Model):
    """Discovery verdict for one IPv4 address, with the evidence behind it."""

    target_input: str
    ip: IPv4Address
    state: HostState
    probed_ports: list[int]
    evidence: list[ProbeEvidence]
    elapsed_ms: float = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = None


class TargetError(_Model):
    """A target that could not be probed at all, such as a DNS failure. Not a port state."""

    target_input: str
    error_code: str
    error_message: str


class RunSettings(_Model):
    timeout_s: float
    concurrency: int
    rate_per_s: float
    max_hosts: int
    allow_public: bool
    allow_shared: bool = False
    allowlist_entries: int | None
    fast_refusal_requested: bool
    fast_refusal_active: bool


class _Metadata(_Model):
    tool: Literal["netscope"] = "netscope"
    version: str
    schema_version: str = SCHEMA_VERSION
    target_input: str
    settings: RunSettings
    platform: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    interrupted: bool


class ScanMetadata(_Metadata):
    command: Literal["scan"] = "scan"
    ports: list[int]
    requested_probes: int
    completed_probes: int


class DiscoveryMetadata(_Metadata):
    command: Literal["discover"] = "discover"
    network: str
    discovery_ports: list[int]
    requested_hosts: int
    completed_hosts: int


class ScanReport(_Model):
    metadata: ScanMetadata
    resolved_ips: list[IPv4Address]
    target_errors: list[TargetError]
    results: list[PortResult]

    @computed_field
    @property
    def summary(self) -> dict[PortState, int]:
        counts = {state: 0 for state in PortState}
        for result in self.results:
            counts[result.state] += 1
        return counts

    @property
    def has_errors(self) -> bool:
        return bool(self.target_errors) or any(r.state is PortState.ERROR for r in self.results)


class DiscoveryReport(_Model):
    metadata: DiscoveryMetadata
    results: list[HostResult]

    @computed_field
    @property
    def summary(self) -> dict[HostState, int]:
        counts = {state: 0 for state in HostState}
        for result in self.results:
            counts[result.state] += 1
        return counts

    @property
    def has_errors(self) -> bool:
        return any(r.state is HostState.ERROR for r in self.results)
