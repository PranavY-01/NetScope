"""Input parsing and validated scan/discovery configuration. Pure logic: no sockets, no DNS."""

import re
from ipaddress import AddressValueError, IPv4Address, IPv4Network, ip_address, ip_network

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PORTS: tuple[int, ...] = (22, 80, 443)
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_CONCURRENCY = 100
DEFAULT_RATE_PER_S = 50.0
DEFAULT_MAX_HOSTS = 256
MAX_HOSTS_CEILING = 65_536

_PORT_RE = re.compile(r"[0-9]{1,5}")
_DIGITS_AND_DOTS_RE = re.compile(r"[0-9.]+")
_LABEL_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?")


class ConfigError(ValueError):
    """User-supplied input or configuration is invalid. Nothing has been probed."""


def _parse_port(text: str, element: str) -> int:
    if not _PORT_RE.fullmatch(text):
        raise ConfigError(f"invalid port {text!r} in {element!r}: ports are integers 1-65535")
    port = int(text)
    if not 1 <= port <= 65535:
        raise ConfigError(f"port {port} in {element!r} is out of range 1-65535")
    return port


def parse_ports(spec: str) -> tuple[int, ...]:
    """Parse '22,80,8000-8010' into sorted unique ports. Invalid input never widens the scan."""
    if not spec or not spec.strip():
        raise ConfigError("port specification is empty")
    ports: set[int] = set()
    for element in spec.split(","):
        element = element.strip()
        if not element:
            raise ConfigError(f"empty element in port specification {spec!r}")
        if "-" in element:
            low_text, _, high_text = element.partition("-")
            if "-" in high_text:
                raise ConfigError(f"malformed port range {element!r}")
            low = _parse_port(low_text.strip(), element)
            high = _parse_port(high_text.strip(), element)
            if low > high:
                raise ConfigError(f"inverted port range {element!r}: start is greater than end")
            ports.update(range(low, high + 1))
        else:
            ports.add(_parse_port(element, element))
    return tuple(sorted(ports))


def _validate_port_values(ports: tuple[int, ...]) -> tuple[int, ...]:
    if not ports:
        raise ConfigError("at least one port is required")
    for port in ports:
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConfigError(f"port {port!r} is out of range 1-65535")
    return tuple(sorted(set(ports)))


def parse_scan_target(raw: str) -> IPv4Address | str:
    """Return an IPv4Address for a literal, or the hostname string to resolve."""
    text = raw.strip()
    if not text:
        raise ConfigError("target is empty")
    if "/" in text:
        raise ConfigError(f"{text!r} looks like a CIDR range; 'scan' takes one host, use 'discover' for ranges")
    if ":" in text:
        try:
            ip_address(text)
        except ValueError:
            raise ConfigError(f"invalid target {text!r}; give ports with --ports, not host:port") from None
        raise ConfigError(f"{text!r} is an IPv6 address; IPv6 is not supported in V1")
    try:
        return IPv4Address(text)
    except AddressValueError:
        pass
    if _DIGITS_AND_DOTS_RE.fullmatch(text):
        # The OS resolver would accept shorthand like '10.1' as 10.0.0.1; fail closed instead.
        raise ConfigError(f"{text!r} is not a valid dotted-quad IPv4 address")
    hostname = text[:-1] if text.endswith(".") else text
    if len(hostname) > 253 or not all(_LABEL_RE.fullmatch(label) for label in hostname.split(".")):
        raise ConfigError(f"{text!r} is not a valid IPv4 address or hostname")
    return text


def parse_network(raw: str) -> IPv4Network:
    """Parse a discovery target: an IPv4 CIDR, or a single IPv4 address treated as /32."""
    text = raw.strip()
    if not text:
        raise ConfigError("network is empty")
    if ":" in text:
        raise ConfigError(f"{text!r} is not IPv4; IPv6 is not supported in V1")
    try:
        network = ip_network(text, strict=True)
    except ValueError as exc:
        try:
            suggestion = ip_network(text, strict=False)
        except ValueError:
            raise ConfigError(f"{text!r} is not a valid IPv4 address or CIDR ({exc})") from None
        raise ConfigError(f"{text!r} has host bits set; did you mean {suggestion}?") from None
    if not isinstance(network, IPv4Network):
        raise ConfigError(f"{text!r} is not IPv4; IPv6 is not supported in V1")
    return network


class CommonConfig(BaseModel):
    """Settings shared by scan and discovery."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ports: tuple[int, ...] = DEFAULT_PORTS
    timeout: float = Field(DEFAULT_TIMEOUT_S, gt=0, le=60)
    concurrency: int = Field(DEFAULT_CONCURRENCY, ge=1, le=1000)
    rate: float = Field(DEFAULT_RATE_PER_S, gt=0, le=10_000)
    max_hosts: int = Field(DEFAULT_MAX_HOSTS, ge=1, le=MAX_HOSTS_CEILING)
    allow_public: bool = False
    allowlist: tuple[IPv4Network, ...] | None = None
    authorized: bool = False
    # Windows only: disable SYN retransmission after RST so refusals are not ~2 s (capsule §20.3).
    fast_refusal: bool = True

    @field_validator("ports", mode="before")
    @classmethod
    def _parse_ports(cls, value: object) -> object:
        return parse_ports(value) if isinstance(value, str) else value

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return _validate_port_values(value)


class ScanConfig(CommonConfig):
    """Port scan of one IPv4 literal or hostname (every resolved IPv4 address)."""

    target: str

    @field_validator("target")
    @classmethod
    def _check_target(cls, value: str) -> str:
        parse_scan_target(value)
        return value.strip()


class DiscoveryConfig(CommonConfig):
    """TCP-connect host discovery across an IPv4 CIDR."""

    network: IPv4Network

    @field_validator("network", mode="before")
    @classmethod
    def _parse_network(cls, value: object) -> object:
        return parse_network(value) if isinstance(value, str) else value
