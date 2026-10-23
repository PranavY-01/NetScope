from ipaddress import IPv4Address, IPv4Network

import pytest

from netscope.guardrails import (
    ADDRESS_TABLE,
    AddressClass,
    AllowlistError,
    ScopeError,
    check_address_policy,
    check_allowlist,
    check_authorized,
    classify_address,
    count_hosts,
    enforce_host_cap,
    expand_hosts,
    load_allowlist,
    parse_allowlist,
    preflight_addresses,
)

pytestmark = pytest.mark.usefixtures("no_sockets")

A = AddressClass

# (address, expected class, allowed without override, allowed with --allow-public)
POLICY_TABLE = [
    ("127.0.0.1", A.LOOPBACK, True, True),
    ("127.255.255.254", A.LOOPBACK, True, True),
    ("10.0.0.1", A.PRIVATE, True, True),
    ("10.255.255.255", A.PRIVATE, True, True),
    ("172.16.0.1", A.PRIVATE, True, True),
    ("172.31.255.254", A.PRIVATE, True, True),
    ("192.168.1.1", A.PRIVATE, True, True),
    ("100.64.0.1", A.PRIVATE, True, True),
    ("100.127.255.254", A.PRIVATE, True, True),
    ("8.8.8.8", A.PUBLIC, False, True),
    ("1.1.1.1", A.PUBLIC, False, True),
    ("172.15.255.255", A.PUBLIC, False, True),
    ("172.32.0.0", A.PUBLIC, False, True),
    ("100.63.255.255", A.PUBLIC, False, True),
    ("100.128.0.0", A.PUBLIC, False, True),
    ("11.0.0.0", A.PUBLIC, False, True),
    ("223.255.255.255", A.PUBLIC, False, True),
    ("169.254.0.1", A.LINK_LOCAL, False, False),
    ("169.254.169.254", A.LINK_LOCAL, False, False),
    ("224.0.0.1", A.MULTICAST, False, False),
    ("239.255.255.250", A.MULTICAST, False, False),
    ("0.0.0.0", A.UNSPECIFIED, False, False),
    ("0.1.2.3", A.UNSPECIFIED, False, False),
    ("255.255.255.255", A.BROADCAST, False, False),
    ("240.0.0.1", A.RESERVED, False, False),
    ("255.255.255.254", A.RESERVED, False, False),
    ("192.0.0.8", A.SPECIAL, False, False),
    ("192.0.2.10", A.SPECIAL, False, False),
    ("198.51.100.7", A.SPECIAL, False, False),
    ("203.0.113.200", A.SPECIAL, False, False),
    ("198.18.0.1", A.SPECIAL, False, False),
    ("198.19.255.254", A.SPECIAL, False, False),
    ("192.88.99.1", A.SPECIAL, False, False),
]


@pytest.mark.parametrize(("addr", "expected", "_a", "_b"), POLICY_TABLE)
def test_classify_address(addr, expected, _a, _b):
    assert classify_address(IPv4Address(addr))[0] is expected


@pytest.mark.parametrize(("addr", "_cls", "allowed_default", "allowed_with_override"), POLICY_TABLE)
def test_address_policy(addr, _cls, allowed_default, allowed_with_override):
    ip = IPv4Address(addr)
    for allow_public, allowed in ((False, allowed_default), (True, allowed_with_override)):
        if allowed:
            check_address_policy([ip], allow_public=allow_public)
        else:
            with pytest.raises(ScopeError):
                check_address_policy([ip], allow_public=allow_public)


# Special addresses for which ipaddress.is_global has returned True on some Python versions, while our
# policy always rejects them. Relying on is_global alone would have let them through with
# --allow-public (capsule §7.2 warning). The stdlib answer is version-dependent: CVE-2024-4032 fixes
# (e.g. Python 3.12.4) changed 192.0.0.0/24, so no assertion is made about the stdlib for these.
STDLIB_IS_GLOBAL_VARIES_OR_DIFFERS = {"224.0.0.1", "239.255.255.250", "192.0.0.8", "192.88.99.1"}


@pytest.mark.parametrize(("addr", "cls", "_a", "_b"), POLICY_TABLE)
def test_table_vs_stdlib_is_global(addr, cls, _a, _b):
    ip = IPv4Address(addr)
    if cls is A.PUBLIC:
        # Never classify a stdlib-non-global address as merely 'public'.
        assert ip.is_global
    elif addr not in STDLIB_IS_GLOBAL_VARIES_OR_DIFFERS:
        assert not ip.is_global


def test_table_covers_ranges_explicitly():
    """Every non-public stdlib range we care about has an explicit row (no 'not RFC1918' shortcut)."""
    classes = {rule.address_class for rule in ADDRESS_TABLE}
    assert classes == set(AddressClass) - {A.PUBLIC}


def test_special_rejection_wins_over_public_and_names_the_range():
    with pytest.raises(ScopeError) as info:
        check_address_policy([IPv4Address("8.8.8.8"), IPv4Address("169.254.1.1")], allow_public=False)
    assert info.value.code == "special_address"
    assert "link-local" in info.value.message


def test_public_rejection_code():
    with pytest.raises(ScopeError) as info:
        check_address_policy([IPv4Address("8.8.8.8")], allow_public=False)
    assert info.value.code == "public_target"
    assert "--allow-public" in info.value.message


@pytest.mark.parametrize(
    ("cidr", "count"),
    [
        ("10.0.0.0/24", 254),
        ("10.0.0.0/23", 510),
        ("10.0.0.0/30", 2),
        ("10.0.0.0/31", 2),
        ("10.0.0.7/32", 1),
        ("10.0.0.0/8", 16_777_214),
        ("0.0.0.0/0", 2**32 - 2),
    ],
)
def test_count_hosts_matches_hosts_semantics(cidr, count):
    network = IPv4Network(cidr)
    assert count_hosts(network) == count
    if count <= 1024:
        assert len(list(network.hosts())) == count


def test_expand_hosts_excludes_network_and_broadcast():
    hosts = expand_hosts(IPv4Network("192.168.1.0/24"), max_hosts=256)
    assert len(hosts) == 254
    assert IPv4Address("192.168.1.0") not in hosts
    assert IPv4Address("192.168.1.255") not in hosts


def test_expand_single_host():
    assert expand_hosts(IPv4Network("10.0.0.7/32"), max_hosts=1) == [IPv4Address("10.0.0.7")]


@pytest.mark.parametrize(("cidr", "cap"), [("10.0.0.0/23", 256), ("10.0.0.0/8", 256), ("10.0.0.0/24", 253)])
def test_expand_rejects_over_cap_before_enumerating(cidr, cap, monkeypatch):
    def boom(self):
        raise AssertionError("oversized network was enumerated")

    monkeypatch.setattr(IPv4Network, "hosts", boom)
    with pytest.raises(ScopeError) as info:
        expand_hosts(IPv4Network(cidr), max_hosts=cap)
    assert info.value.code == "host_cap"


def test_cap_boundary():
    enforce_host_cap(256, 256, "x")
    with pytest.raises(ScopeError):
        enforce_host_cap(257, 256, "x")


ALLOWLIST_TEXT = """\
# NetScope permitted lab ranges
127.0.0.1

192.168.10.0/24   # lab VLAN
10.20.0.15
"""


def test_parse_allowlist():
    assert parse_allowlist(ALLOWLIST_TEXT) == (
        IPv4Network("127.0.0.1/32"),
        IPv4Network("192.168.10.0/24"),
        IPv4Network("10.20.0.15/32"),
    )


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("127.0.0.1\nnot-an-ip\n", ":2:"),
        ("# c\n\n10.0.0.1/24\n", ":3:"),  # host bits set
        ("10.0.0.0/33\n", ":1:"),
        ("::1\n", ":1:"),
        ("example.com\n", ":1:"),
    ],
)
def test_parse_allowlist_reports_line_numbers(text, location):
    with pytest.raises(AllowlistError, match=location):
        parse_allowlist(text, "lab.txt")


def test_empty_allowlist_rejected():
    with pytest.raises(AllowlistError, match="no entries"):
        parse_allowlist("# only comments\n\n")


def test_load_allowlist(tmp_path):
    path = tmp_path / "allow.txt"
    path.write_text(ALLOWLIST_TEXT, encoding="utf-8")
    assert len(load_allowlist(path)) == 3
    with pytest.raises(AllowlistError, match="cannot read"):
        load_allowlist(tmp_path / "missing.txt")


def test_allowlist_is_all_or_nothing():
    allow = parse_allowlist(ALLOWLIST_TEXT)
    check_allowlist([IPv4Address("192.168.10.5"), IPv4Address("127.0.0.1")], allow)
    with pytest.raises(ScopeError) as info:
        check_allowlist([IPv4Address("192.168.10.5"), IPv4Address("192.168.11.5")], allow)
    assert info.value.code == "allowlist"
    assert "192.168.11.5" in info.value.message
    check_allowlist([IPv4Address("8.8.8.8")], None)


def test_authorization_required():
    check_authorized(True)
    with pytest.raises(ScopeError) as info:
        check_authorized(False)
    assert info.value.code == "not_authorized"


def _preflight(ips, **overrides):
    kwargs = dict(max_hosts=256, allow_public=False, allowlist=None, authorized=True, what="t")
    preflight_addresses([IPv4Address(ip) for ip in ips], **(kwargs | overrides))


@pytest.mark.parametrize(
    ("ips", "overrides", "code"),
    [
        # Everything wrong at once: the cap is reported first.
        (["8.8.8.8", "8.8.4.4"], dict(max_hosts=1, allowlist=(IPv4Network("10.0.0.0/8"),), authorized=False), "host_cap"),
        # Address policy precedes the allowlist and acknowledgement.
        (["8.8.8.8"], dict(allowlist=(IPv4Network("10.0.0.0/8"),), authorized=False), "public_target"),
        # Allowlist precedes the acknowledgement.
        (["10.0.0.1"], dict(allowlist=(IPv4Network("192.168.0.0/16"),), authorized=False), "allowlist"),
        (["10.0.0.1"], dict(authorized=False), "not_authorized"),
    ],
)
def test_preflight_order(ips, overrides, code):
    with pytest.raises(ScopeError) as info:
        _preflight(ips, **overrides)
    assert info.value.code == code


def test_allow_public_never_bypasses_other_checks():
    for overrides, code in [
        (dict(max_hosts=1), "host_cap"),
        (dict(allowlist=(IPv4Network("10.0.0.0/8"),)), "allowlist"),
        (dict(authorized=False), "not_authorized"),
    ]:
        with pytest.raises(ScopeError) as info:
            _preflight(["8.8.8.8", "1.1.1.1"], allow_public=True, **overrides)
        assert info.value.code == code


def test_preflight_passes():
    _preflight(["127.0.0.1", "10.1.2.3"])
    _preflight(["8.8.8.8"], allow_public=True, allowlist=(IPv4Network("8.8.8.0/24"),))
