"""Scope guardrails: address-class policy, host cap, allowlist, and authorization.

Everything here is pure and runs before any socket is opened.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

from netscope.config import MAX_HOSTS_CEILING, ConfigError

_MAX_LISTED = 5


class AddressClass(StrEnum):
    LOOPBACK = "loopback"
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    UNSPECIFIED = "unspecified"
    BROADCAST = "broadcast"
    RESERVED = "reserved"
    SPECIAL = "special"


@dataclass(frozen=True)
class RangeRule:
    network: IPv4Network
    address_class: AddressClass
    label: str


# Ordered: the first matching rule wins, so the broadcast address precedes 240.0.0.0/4.
ADDRESS_TABLE: tuple[RangeRule, ...] = tuple(
    RangeRule(IPv4Network(net), cls, label)
    for net, cls, label in [
        ("0.0.0.0/8", AddressClass.UNSPECIFIED, "'this network' (RFC 791)"),
        ("127.0.0.0/8", AddressClass.LOOPBACK, "loopback (RFC 1122)"),
        ("10.0.0.0/8", AddressClass.PRIVATE, "private (RFC 1918)"),
        ("172.16.0.0/12", AddressClass.PRIVATE, "private (RFC 1918)"),
        ("192.168.0.0/16", AddressClass.PRIVATE, "private (RFC 1918)"),
        # Carrier-grade NAT space: other customers of the same ISP can sit here, so it is not
        # treated as "your" private network and needs its own explicit override.
        ("100.64.0.0/10", AddressClass.SHARED, "shared address space / CGNAT (RFC 6598)"),
        ("169.254.0.0/16", AddressClass.LINK_LOCAL, "link-local (RFC 3927)"),
        ("224.0.0.0/4", AddressClass.MULTICAST, "multicast (RFC 5771)"),
        ("255.255.255.255/32", AddressClass.BROADCAST, "limited broadcast (RFC 919)"),
        ("240.0.0.0/4", AddressClass.RESERVED, "reserved (RFC 1112)"),
        ("192.0.0.0/24", AddressClass.SPECIAL, "IETF protocol assignments (RFC 6890)"),
        ("192.0.2.0/24", AddressClass.SPECIAL, "documentation TEST-NET-1 (RFC 5737)"),
        ("198.51.100.0/24", AddressClass.SPECIAL, "documentation TEST-NET-2 (RFC 5737)"),
        ("203.0.113.0/24", AddressClass.SPECIAL, "documentation TEST-NET-3 (RFC 5737)"),
        ("198.18.0.0/15", AddressClass.SPECIAL, "benchmarking (RFC 2544)"),
        ("192.88.99.0/24", AddressClass.SPECIAL, "6to4 relay anycast (RFC 7526)"),
    ]
)

_PUBLIC_LABEL = "globally routable"
ALLOWED_CLASSES = frozenset({AddressClass.LOOPBACK, AddressClass.PRIVATE})
# Each overridable class has its own flag; one override never implies another.
OVERRIDABLE_CLASSES = frozenset({AddressClass.PUBLIC, AddressClass.SHARED})


class ScopeError(Exception):
    """The requested scope failed a guardrail. Nothing has been probed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AllowlistError(ConfigError):
    """The allowlist file is unreadable or malformed."""


def classify_address(ip: IPv4Address) -> tuple[AddressClass, str]:
    for rule in ADDRESS_TABLE:
        if ip in rule.network:
            return rule.address_class, rule.label
    return AddressClass.PUBLIC, _PUBLIC_LABEL


def count_hosts(network: IPv4Network) -> int:
    """Probe-target hosts in a CIDR, matching IPv4Network.hosts() without enumerating it."""
    if network.prefixlen >= 31:
        return network.num_addresses
    return network.num_addresses - 2


def expand_hosts(network: IPv4Network, max_hosts: int) -> list[IPv4Address]:
    """Enforce the cap from the CIDR size, then expand. Oversized ranges are never enumerated."""
    enforce_host_cap(count_hosts(network), max_hosts, f"network {network}")
    return list(network.hosts())


def enforce_host_cap(count: int, max_hosts: int, what: str) -> None:
    if max_hosts > MAX_HOSTS_CEILING:
        raise ScopeError("host_cap", f"--max-hosts may not exceed {MAX_HOSTS_CEILING}")
    if count > max_hosts:
        raise ScopeError(
            "host_cap",
            f"{what} has {count} probe-target hosts, over the cap of {max_hosts}; "
            "narrow the range or raise --max-hosts deliberately",
        )


def _listed(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_MAX_LISTED])
    if len(items) > _MAX_LISTED:
        shown += f", and {len(items) - _MAX_LISTED} more"
    return shown


def check_address_policy(ips: Iterable[IPv4Address], allow_public: bool, allow_shared: bool = False) -> None:
    """Reject special-purpose addresses outright; public and shared space each need their own override."""
    forbidden: list[str] = []
    public: list[str] = []
    shared: list[str] = []
    for ip in ips:
        address_class, label = classify_address(ip)
        if address_class in ALLOWED_CLASSES:
            continue
        if address_class is AddressClass.PUBLIC:
            if not allow_public:
                public.append(str(ip))
        elif address_class is AddressClass.SHARED:
            if not allow_shared:
                shared.append(str(ip))
        else:
            forbidden.append(f"{ip} ({label})")
    if forbidden:
        raise ScopeError(
            "special_address",
            f"special-purpose addresses cannot be scanned: {_listed(forbidden)}",
        )
    if shared:
        raise ScopeError(
            "shared_address",
            "shared address space (100.64.0.0/10, carrier-grade NAT) can belong to other ISP "
            f"customers and requires --allow-shared: {_listed(shared)}",
        )
    if public:
        raise ScopeError(
            "public_target",
            f"public (globally routable) targets require --allow-public: {_listed(public)}",
        )


def parse_allowlist(text: str, source: str = "<allowlist>") -> tuple[IPv4Network, ...]:
    """One IPv4 address or CIDR per line; blank lines and '#' comments are ignored."""
    entries: list[IPv4Network] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        if ":" in entry:
            raise AllowlistError(f"{source}:{lineno}: {entry!r} is not IPv4")
        try:
            entries.append(IPv4Network(entry, strict=True))
        except ValueError as exc:
            raise AllowlistError(f"{source}:{lineno}: invalid entry {entry!r} ({exc})") from None
    if not entries:
        raise AllowlistError(f"{source}: allowlist contains no entries")
    return tuple(entries)


def load_allowlist(path: Path) -> tuple[IPv4Network, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AllowlistError(f"cannot read allowlist {path}: {exc}") from None
    return parse_allowlist(text, str(path))


def check_allowlist(ips: Iterable[IPv4Address], allowlist: Sequence[IPv4Network] | None) -> None:
    if allowlist is None:
        return
    outside = [str(ip) for ip in ips if not any(ip in net for net in allowlist)]
    if outside:
        raise ScopeError(
            "allowlist",
            f"targets outside the allowlist (whole run rejected): {_listed(outside)}",
        )


def check_authorized(authorized: bool) -> None:
    if not authorized:
        raise ScopeError(
            "not_authorized",
            "active scanning requires an explicit authorization acknowledgement (--yes-authorized on the "
            "command line, the authorization checkbox in the web UI) confirming you own or are explicitly "
            "authorized to test every target",
        )


def preflight_addresses(
    ips: Sequence[IPv4Address],
    *,
    max_hosts: int,
    allow_public: bool,
    allowlist: Sequence[IPv4Network] | None,
    authorized: bool,
    what: str,
    allow_shared: bool = False,
) -> None:
    """Scope checks on a concrete address list, in order: cap, address policy, allowlist, acknowledgement.

    Callers that resolve names must call check_authorized() before resolving (see prepare_scan); the
    acknowledgement is re-checked here so this function is safe on its own.
    """
    enforce_host_cap(len(ips), max_hosts, what)
    check_address_policy(ips, allow_public, allow_shared)
    check_allowlist(ips, allowlist)
    check_authorized(authorized)
