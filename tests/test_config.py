from ipaddress import IPv4Address, IPv4Network

import pytest
from pydantic import ValidationError

from netscope.config import (
    DEFAULT_PORTS,
    ConfigError,
    DiscoveryConfig,
    ScanConfig,
    parse_network,
    parse_ports,
    parse_scan_target,
)

pytestmark = pytest.mark.usefixtures("no_sockets")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("80", (80,)),
        ("22,80,443", (22, 80, 443)),
        ("8000-8003", (8000, 8001, 8002, 8003)),
        ("22,80,8000-8002", (22, 80, 8000, 8001, 8002)),
        (" 443 , 22 ", (22, 443)),
        ("80,80,79-81", (79, 80, 81)),
        ("1", (1,)),
        ("65535", (65535,)),
        ("5-5", (5,)),
        ("8000 - 8001", (8000, 8001)),
        ("080", (80,)),
    ],
)
def test_parse_ports_valid(spec, expected):
    assert parse_ports(spec) == expected


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("80,", "empty element"),
        (",80", "empty element"),
        ("22,,80", "empty element"),
        ("0", "out of range"),
        ("65536", "out of range"),
        ("99999", "out of range"),
        ("-1", "invalid port"),
        ("abc", "invalid port"),
        ("80a", "invalid port"),
        ("+80", "invalid port"),
        ("0x50", "invalid port"),
        ("8.0", "invalid port"),
        ("٨٠", "invalid port"),  # Arabic-Indic digits are not ASCII ports
        ("100-1", "inverted"),
        ("443-80", "inverted"),
        ("1-2-3", "malformed"),
        ("80-", "invalid port"),
        ("-80", "invalid port"),
        ("1-65536", "out of range"),
        ("123456", "invalid port"),
    ],
)
def test_parse_ports_invalid_never_widens(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_ports(spec)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", IPv4Address("127.0.0.1")),
        (" 192.168.1.10 ", IPv4Address("192.168.1.10")),
        ("localhost", "localhost"),
        ("router.lan", "router.lan"),
        ("example.com.", "example.com."),
        ("my-host_01", "my-host_01"),
    ],
)
def test_parse_scan_target_valid(raw, expected):
    assert parse_scan_target(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "empty"),
        ("  ", "empty"),
        ("10.0.0.0/24", "use 'discover'"),
        ("::1", "IPv6"),
        ("fe80::1", "IPv6"),
        ("host:80", "--ports"),
        ("10.1", "dotted-quad"),
        ("256.1.1.1", "dotted-quad"),
        ("1.2.3.4.5", "dotted-quad"),
        ("010.0.0.1", "dotted-quad"),
        ("bad host", "not a valid"),
        ("-leading.example", "not a valid"),
        ("a..b", "not a valid"),
        ("x" * 64 + ".com", "not a valid"),
        ("ex$mple.com", "not a valid"),
    ],
)
def test_parse_scan_target_invalid(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_scan_target(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.0/24", "192.168.1.0/24"),
        ("10.0.0.5", "10.0.0.5/32"),
        ("10.0.0.0/255.255.255.0", "10.0.0.0/24"),
        ("127.0.0.0/31", "127.0.0.0/31"),
    ],
)
def test_parse_network_valid(raw, expected):
    assert parse_network(raw) == IPv4Network(expected)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "empty"),
        ("192.168.1.5/24", "did you mean 192.168.1.0/24"),
        ("10.0.0.0/33", "not a valid"),
        ("10.0.0/24", "not a valid"),
        ("example.com", "not a valid"),
        ("::/64", "IPv6"),
    ],
)
def test_parse_network_invalid(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_network(raw)


def test_scan_config_defaults():
    config = ScanConfig(target="127.0.0.1")
    assert config.ports == DEFAULT_PORTS == (22, 80, 443)
    assert config.timeout == 1.0
    assert config.concurrency == 100
    assert config.rate == 50.0
    assert config.max_hosts == 256
    assert config.authorized is False
    assert config.allow_public is False
    assert config.fast_refusal is True


def test_scan_config_accepts_port_spec_string():
    assert ScanConfig(target="h.lan", ports="443,22-23").ports == (22, 23, 443)


def test_discovery_config_parses_network():
    config = DiscoveryConfig(network="10.0.0.0/30")
    assert config.network == IPv4Network("10.0.0.0/30")
    assert config.ports == (22, 80, 443)


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": 61},
        {"concurrency": 0},
        {"concurrency": 1001},
        {"rate": 0},
        {"rate": -5},
        {"rate": 10_001},
        {"max_hosts": 0},
        {"max_hosts": 65_537},
        {"ports": ()},
        {"ports": (0,)},
        {"ports": (70000,)},
        {"ports": "100-1"},
        {"target": ""},
        {"target": "10.0.0.0/8"},
        {"unexpected": 1},
    ],
)
def test_scan_config_rejects(overrides):
    with pytest.raises(ValidationError):
        ScanConfig(**({"target": "127.0.0.1"} | overrides))


def test_config_is_frozen():
    config = ScanConfig(target="127.0.0.1")
    with pytest.raises(ValidationError):
        config.authorized = True
