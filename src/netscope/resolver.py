"""IPv4 name resolution behind an injectable seam."""

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import AddressValueError, IPv4Address

log = logging.getLogger(__name__)

Resolver = Callable[[str], Awaitable[Sequence[str]]]
"""Return the IPv4 address strings for a hostname (duplicates allowed); raise ResolutionError on failure."""

DNS_TIMEOUT_S = 10.0

_EAI_NAMES = {
    getattr(socket, name): name
    for name in dir(socket)
    if name.startswith("EAI_") and isinstance(getattr(socket, name), int)
}


class ResolutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _has_ipv6_only(loop: asyncio.AbstractEventLoop, host: str) -> bool:
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET6, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    return bool(infos)


async def system_resolver(host: str) -> list[str]:
    """getaddrinfo restricted to AF_INET, so IPv6 answers can never turn into an IPv6 scan."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if await _has_ipv6_only(loop, host):
            raise ResolutionError(
                "dns:ipv6_only", f"{host} resolves only to IPv6 addresses; IPv6 is not supported in V1"
            ) from None
        name = _EAI_NAMES.get(exc.errno, str(exc.errno))
        raise ResolutionError(f"dns:{name}", f"cannot resolve {host}: {exc.strerror or exc}") from None
    except (OSError, UnicodeError) as exc:
        raise ResolutionError("dns:error", f"cannot resolve {host}: {exc}") from None
    return [info[4][0] for info in infos]


async def resolve_ipv4(host: str, resolver: Resolver = system_resolver) -> list[IPv4Address]:
    """Resolve to unique IPv4 addresses in first-seen order. Non-IPv4 answers are dropped and logged."""
    try:
        async with asyncio.timeout(DNS_TIMEOUT_S):
            answers = await resolver(host)
    except TimeoutError:
        raise ResolutionError("dns:timeout", f"resolving {host} took longer than {DNS_TIMEOUT_S:g}s") from None
    unique: dict[IPv4Address, None] = {}
    for answer in answers:
        try:
            unique.setdefault(IPv4Address(answer), None)
        except AddressValueError:
            log.debug("ignoring non-IPv4 answer %r for %s", answer, host)
    if not unique:
        raise ResolutionError("dns:no_ipv4", f"{host} has no IPv4 addresses")
    if len(unique) < len(answers):
        log.debug("%s: %d answers, %d unique IPv4 addresses", host, len(answers), len(unique))
    return list(unique)
