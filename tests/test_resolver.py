import asyncio
import socket
from ipaddress import IPv4Address

import pytest

from netscope import resolver as resolver_module
from netscope.resolver import ResolutionError, resolve_ipv4, system_resolver


def fixed(*answers):
    async def resolve(host):
        return list(answers)

    return resolve


async def test_multiple_answers_are_all_kept_and_deduplicated_in_order():
    ips = await resolve_ipv4("multi.lan", fixed("10.0.0.2", "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.1"))
    assert ips == [IPv4Address("10.0.0.2"), IPv4Address("10.0.0.1"), IPv4Address("10.0.0.3")]


async def test_non_ipv4_answers_are_dropped():
    ips = await resolve_ipv4("mixed.lan", fixed("::1", "10.0.0.1", "garbage"))
    assert ips == [IPv4Address("10.0.0.1")]


@pytest.mark.parametrize("answers", [(), ("::1", "fe80::1"), ("not-an-ip",)])
async def test_no_usable_ipv4_is_structured_error(answers):
    with pytest.raises(ResolutionError) as info:
        await resolve_ipv4("v6.lan", fixed(*answers))
    assert info.value.code == "dns:no_ipv4"


async def test_resolver_failure_propagates():
    async def failing(host):
        raise ResolutionError("dns:EAI_NONAME", "nope")

    with pytest.raises(ResolutionError) as info:
        await resolve_ipv4("nx.lan", failing)
    assert info.value.code == "dns:EAI_NONAME"


async def test_slow_resolver_times_out(monkeypatch):
    monkeypatch.setattr(resolver_module, "DNS_TIMEOUT_S", 0.05)

    async def slow(host):
        await asyncio.sleep(10)
        return ["10.0.0.1"]

    with pytest.raises(ResolutionError) as info:
        await resolve_ipv4("slow.lan", slow)
    assert info.value.code == "dns:timeout"


# ---- system_resolver with getaddrinfo stubbed (no real DNS) -------------------------------


def _info(family, addr):
    return (family, socket.SOCK_STREAM, 6, "", (addr, 0) if family == socket.AF_INET else (addr, 0, 0, 0))


async def test_system_resolver_asks_for_ipv4_only(monkeypatch):
    calls = []

    async def getaddrinfo(host, port, *, family=0, type=0, **kw):
        calls.append(family)
        return [_info(socket.AF_INET, "10.1.1.1"), _info(socket.AF_INET, "10.1.1.2")]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    assert await system_resolver("h.lan") == ["10.1.1.1", "10.1.1.2"]
    assert calls == [socket.AF_INET]


async def test_system_resolver_reports_ipv6_only(monkeypatch):
    async def getaddrinfo(host, port, *, family=0, type=0, **kw):
        if family == socket.AF_INET:
            raise socket.gaierror(socket.EAI_NONAME, "no address")
        return [_info(socket.AF_INET6, "2001:db8::1")]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    with pytest.raises(ResolutionError) as info:
        await system_resolver("v6only.lan")
    assert info.value.code == "dns:ipv6_only"


async def test_system_resolver_maps_gaierror(monkeypatch):
    async def getaddrinfo(host, port, *, family=0, type=0, **kw):
        raise socket.gaierror(socket.EAI_NONAME, "name not known")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    with pytest.raises(ResolutionError) as info:
        await system_resolver("nx.lan")
    assert info.value.code.startswith("dns:EAI_")
    assert "nx.lan" in info.value.message


@pytest.mark.integration
async def test_system_resolver_localhost_is_ipv4():
    ips = await resolve_ipv4("localhost")
    assert IPv4Address("127.0.0.1") in ips
    assert all(isinstance(ip, IPv4Address) for ip in ips)
