import asyncio
import csv
import errno
import io
import json
import os
import re
import socket
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from netscope import probe
from netscope.cli import app
from netscope.resolver import ResolutionError

runner = CliRunner()

# Typer forces coloured --help when it detects GitHub Actions (GITHUB_ACTIONS=true), and the colour
# codes split option names like "--yes-authorized"; Rich also wraps at the console width, which
# varies between machines. Assertions on human-readable text compare it with both removed.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text):
    return " ".join(_ANSI.sub("", text).split())


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def probe_sockets(monkeypatch):
    """Record every socket the probe module creates, leaving all other sockets alone."""
    created = []
    real = socket

    class SocketModuleSpy:
        def __getattr__(self, name):
            return getattr(real, name)

        def socket(self, *args, **kwargs):
            sock = real.socket(*args, **kwargs)
            created.append(sock)
            return sock

    monkeypatch.setattr(probe, "socket", SocketModuleSpy())
    probe.fast_refusal_active.cache_clear()
    yield created
    probe.fast_refusal_active.cache_clear()


@pytest.fixture
def network(monkeypatch):
    """Scripted outcomes per (ip, port): 'open', 'refused', 'error'; anything else hangs."""
    script = {}

    def factory(fast_refusal=True):
        async def connect(ip, port):
            outcome = script.get((ip, port), script.get(ip, "refused"))
            if outcome == "open":
                return
            if outcome == "refused":
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            if outcome == "error":
                raise OSError(errno.EMFILE, "too many open files")
            await asyncio.sleep(30)

        return connect

    monkeypatch.setattr(probe, "make_tcp_connect", factory)
    return script


@pytest.fixture
def dns(monkeypatch):
    answers = {}

    async def resolve(host):
        if host not in answers:
            raise ResolutionError("dns:EAI_NONAME", f"cannot resolve {host}")
        return answers[host]

    monkeypatch.setattr("netscope.resolver.system_resolver", resolve)
    return answers


# ---- preflight: nothing is probed until every check passes -----------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["scan", "127.0.0.1"],  # no acknowledgement
        ["scan", "8.8.8.8", "--yes-authorized"],
        ["scan", "169.254.1.1", "--yes-authorized", "--allow-public"],
        ["scan", "224.0.0.1", "--yes-authorized", "--allow-public"],
        ["scan", "127.0.0.1", "-p", "100-1", "--yes-authorized"],
        ["scan", "127.0.0.1", "-p", "", "--yes-authorized"],
        ["scan", "127.0.0.1", "--timeout", "0", "--yes-authorized"],
        ["scan", "127.0.0.1", "--concurrency", "0", "--yes-authorized"],
        ["scan", "10.0.0.0/24", "--yes-authorized"],
        ["scan", "10.1", "--yes-authorized"],
        ["scan", "127.0.0.1", "--json", "--csv", "--yes-authorized"],
        ["scan", "127.0.0.1", "--format", "csv", "--json", "--yes-authorized"],
        ["scan", "127.0.0.1", "-o", "report.txt", "--yes-authorized"],
        ["scan", "127.0.0.1", "-v", "-q", "--yes-authorized"],
        ["discover", "10.0.0.0/16", "--yes-authorized"],
        ["discover", "10.0.0.0/23", "--yes-authorized", "--allow-public"],
        ["discover", "10.0.0.5/24", "--yes-authorized"],
        ["discover", "127.0.0.0/29"],
        ["discover", "8.8.8.0/30", "--yes-authorized"],
    ],
)
def test_rejected_before_any_probe_socket(args, probe_sockets):
    result = invoke(*args)
    assert result.exit_code == 2, result.output
    assert probe_sockets == []


def test_allowlist_rejection_opens_no_probe_socket(tmp_path, probe_sockets):
    allow = tmp_path / "allow.txt"
    allow.write_text("127.0.0.0/30\n", encoding="utf-8")
    result = invoke("discover", "127.0.0.0/29", "--yes-authorized", "--allowlist", allow)
    assert result.exit_code == 2
    assert "allowlist" in plain(result.stderr)
    assert probe_sockets == []


def test_malformed_allowlist_is_usage_error(tmp_path, probe_sockets):
    allow = tmp_path / "allow.txt"
    allow.write_text("127.0.0.1\nbogus\n", encoding="utf-8")
    result = invoke("scan", "127.0.0.1", "--yes-authorized", "--allowlist", allow)
    assert result.exit_code == 2
    assert ":2:" in plain(result.stderr)
    assert probe_sockets == []


@pytest.mark.integration
def test_positive_control_probe_socket_is_opened_after_preflight(probe_sockets):
    result = invoke("scan", "127.0.0.1", "-p", unused_port(), "--yes-authorized", "-q")
    assert result.exit_code == 0, result.output
    assert len(probe_sockets) >= 1


def test_error_messages(network):
    assert "--yes-authorized" in plain(invoke("scan", "127.0.0.1").stderr)
    assert "--allow-public" in plain(invoke("scan", "8.8.8.8", "--yes-authorized").stderr)
    assert "did you mean 10.0.0.0/24" in plain(invoke("discover", "10.0.0.5/24", "--yes-authorized").stderr)
    assert "--ports" in plain(invoke("scan", "127.0.0.1", "-p", "9-1", "--yes-authorized").stderr)
    assert "inverted" in plain(invoke("scan", "127.0.0.1", "-p", "9-1", "--yes-authorized").stderr)


def test_help_needs_no_acknowledgement():
    for args in (["--help"], ["scan", "--help"], ["discover", "--help"]):
        result = invoke(*args)
        assert result.exit_code == 0
    # A wide terminal so the help table never folds the option name, whatever the machine's width.
    wide = runner.invoke(app, ["scan", "--help"], env={"COLUMNS": "200"})
    assert "--yes-authorized" in plain(wide.output)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["scan", "100.64.0.1", "--yes-authorized"], 2),
        (["scan", "100.64.0.1", "--yes-authorized", "--allow-public"], 2),
        (["discover", "100.64.0.0/30", "--yes-authorized", "--allow-public"], 2),
        (["scan", "100.64.0.1", "--yes-authorized", "--allow-shared", "-q"], 0),
        (["discover", "100.64.0.0/30", "--yes-authorized", "--allow-shared", "-q"], 0),
        (["scan", "8.8.8.8", "--yes-authorized", "--allow-shared"], 2),
    ],
)
def test_allow_shared_flag(network, args, code):
    result = invoke(*args)
    assert result.exit_code == code, result.output
    if code == 2:
        assert "--allow-shared" in plain(result.stderr) or "--allow-public" in plain(result.stderr)


def test_allow_shared_is_recorded_in_report_settings(network):
    data = json.loads(invoke("scan", "100.64.0.1", "--yes-authorized", "--allow-shared", "--json").stdout)
    assert data["metadata"]["settings"]["allow_shared"] is True
    data = json.loads(invoke("scan", "127.0.0.1", "--yes-authorized", "--json").stdout)
    assert data["metadata"]["settings"]["allow_shared"] is False


def test_no_dns_lookup_without_acknowledgement(monkeypatch):
    async def must_not_resolve(host):
        raise AssertionError("DNS lookup before authorization")

    monkeypatch.setattr("netscope.resolver.system_resolver", must_not_resolve)
    result = invoke("scan", "some.lan", "--json")
    assert result.exit_code == 2
    assert "not_authorized" in plain(result.stderr)


def test_estimate_is_logged(network):
    result = invoke("scan", "127.0.0.1", "-p", "1-100", "--yes-authorized", "--json", "--rate", "10000")
    assert "estimated duration: at least" in plain(result.stderr)


# ---- machine-readable output ----------------------------------------------------------------


def test_json_stdout_is_clean_even_when_verbose(network):
    network[("127.0.0.1", 80)] = "open"
    result = invoke("scan", "127.0.0.1", "--yes-authorized", "--json", "-v")
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "\x1b" not in result.stdout
    assert data["summary"]["open"] == 1 and data["summary"]["closed"] == 2
    assert "scanning 127.0.0.1" in plain(result.stderr)


def test_csv_stdout(network):
    result = invoke("scan", "127.0.0.1", "-p", "22,80", "--yes-authorized", "--format", "csv")
    assert result.exit_code == 0
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert [(r["ip"], r["port"], r["state"]) for r in rows] == [("127.0.0.1", "22", "closed"), ("127.0.0.1", "80", "closed")]


def test_same_format_twice_is_not_a_conflict(network):
    assert invoke("scan", "127.0.0.1", "--format", "json", "--json", "--yes-authorized", "-q").exit_code == 0


@pytest.mark.parametrize(("name", "flags"), [("r.json", []), ("r.csv", []), ("r.out", ["--format", "json"])])
def test_output_file(tmp_path, network, name, flags):
    path = tmp_path / name
    result = invoke("scan", "127.0.0.1", "--yes-authorized", "-o", path, *flags)
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    text = path.read_text(encoding="utf-8")
    if name.endswith(".csv"):
        assert text.startswith("target_input,ip,port,state")
    else:
        assert json.loads(text)["metadata"]["command"] == "scan"
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_discover_json(network):
    network["127.0.0.1"] = "open"
    network["127.0.0.2"] = "hang"
    result = invoke("discover", "127.0.0.0/30", "--yes-authorized", "--json", "--timeout", "0.05", "-q")
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [(h["ip"], h["state"]) for h in data["results"]] == [("127.0.0.1", "responsive"), ("127.0.0.2", "no_response")]
    assert len(data["results"][0]["evidence"]) == 3


def test_human_output_goes_to_stdout_without_json(network):
    network[("127.0.0.1", 443)] = "open"
    result = invoke("scan", "127.0.0.1", "--yes-authorized", "-q")
    assert result.exit_code == 0
    assert "open" in result.stdout and "443" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_human_output_collapses_many_closed_ports(network):
    result = invoke("scan", "127.0.0.1", "-p", "1-40", "--yes-authorized", "-q", "--rate", "10000")
    assert "40 closed ports not listed" in result.stdout


def test_max_hosts_raise_is_warned(network):
    result = invoke("discover", "127.0.0.0/23", "--yes-authorized", "--max-hosts", "600", "--json", "--rate", "10000")
    assert result.exit_code == 0
    assert "host cap raised" in plain(result.stderr)
    assert len(json.loads(result.stdout)["results"]) == 510


# ---- exit codes -----------------------------------------------------------------------------


def test_exit_0_for_closed_and_inconclusive(network):
    network[("127.0.0.1", 22)] = "hang"
    assert invoke("scan", "127.0.0.1", "--yes-authorized", "--timeout", "0.05", "-q").exit_code == 0


def test_exit_1_for_probe_error(network):
    network[("127.0.0.1", 22)] = "error"
    assert invoke("scan", "127.0.0.1", "--yes-authorized", "-q").exit_code == 1


def test_exit_1_for_dns_failure(network, dns):
    result = invoke("scan", "nx.lan", "--yes-authorized", "--json", "-q")
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["results"] == []
    assert data["target_errors"][0]["error_code"] == "dns:EAI_NONAME"


def test_hostname_with_several_addresses(network, dns):
    dns["multi.lan"] = ["10.0.0.2", "10.0.0.1", "10.0.0.2"]
    result = invoke("scan", "multi.lan", "-p", "80", "--yes-authorized", "--json", "-q")
    data = json.loads(result.stdout)
    assert data["resolved_ips"] == ["10.0.0.2", "10.0.0.1"]
    assert [(r["ip"], r["target_input"]) for r in data["results"]] == [("10.0.0.1", "multi.lan"), ("10.0.0.2", "multi.lan")]


def test_exit_1_for_discovery_error_host(network):
    network["127.0.0.1"] = "error"
    assert invoke("discover", "127.0.0.1", "--yes-authorized", "-q").exit_code == 1


def test_exit_3_for_fatal_failure(monkeypatch):
    def broken(fast_refusal=True):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(probe, "make_tcp_connect", broken)
    result = invoke("scan", "127.0.0.1", "--yes-authorized", "--json")
    assert result.exit_code == 3
    assert "internal error" in plain(result.stderr)
    assert result.stdout == ""


# ---- real process: stream separation with colour forced on ----------------------------------


@pytest.mark.integration
def test_subprocess_json_has_no_ansi_or_logs():
    env = os.environ | {"FORCE_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "netscope", "scan", "127.0.0.1", "-p", str(unused_port()), "--yes-authorized", "--json", "-v"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "\x1b" not in proc.stdout
    data = json.loads(proc.stdout)
    assert data["results"][0]["state"] == "closed"
    assert "scanning" in proc.stderr
