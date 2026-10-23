"""Ctrl-C: partial reports, exit code 130, and no leaked tasks or sockets."""

import asyncio
import csv
import errno
import io
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import pytest
from typer.testing import CliRunner

from netscope import probe
from netscope.cli import app
from netscope.config import DiscoveryConfig, ScanConfig
from netscope.discovery import build_discovery_report, prepare_discovery, run_discovery
from netscope.scanner import build_scan_report, prepare_scan, run_scan
from netscope.scheduler import RunTimer

runner = CliRunner()


class SocketTrackingNetwork:
    """Opens a real socket per probe; some ports complete, the rest stay pending until cancelled."""

    def __init__(self, completes=lambda port: port % 2 == 0):
        self.completes = completes
        self.open_sockets = 0
        self.opened = 0
        self.cancelled = 0

    async def __call__(self, ip, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.opened += 1
        self.open_sockets += 1
        try:
            if self.completes(port):
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            sock.close()
            self.open_sockets -= 1


def _netscope_tasks():
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("netscope-worker") and not t.done()]


async def test_cancelling_a_scan_keeps_completed_results_and_cleans_up():
    plan = await prepare_scan(ScanConfig(target="127.0.0.1", ports="1-200", concurrency=20, rate=10_000, authorized=True))
    net = SocketTrackingNetwork()
    collected = []
    timer = RunTimer()
    task = asyncio.create_task(run_scan(plan, on_result=collected.append, connect=net))
    while len(collected) < 5:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert net.open_sockets == 0
    assert net.cancelled > 0
    assert _netscope_tasks() == []
    # Pending probes never completed, so they are not reported: nothing is invented.
    assert all(r.port % 2 == 0 for r in collected)
    report = build_scan_report(plan, collected, timer=timer, interrupted=True)
    assert report.metadata.interrupted
    assert report.metadata.completed_probes == len(collected) < report.metadata.requested_probes == 200
    json.loads(report.model_dump_json())


async def test_cancelling_discovery_reports_only_hosts_with_a_verdict():
    plan = prepare_discovery(DiscoveryConfig(network="10.0.0.0/28", ports="2,4", concurrency=4, rate=10_000, authorized=True))
    hosts_done = {"10.0.0.1", "10.0.0.2"}
    net = SocketTrackingNetwork()

    async def connect(ip, port):
        if ip in hosts_done:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        await net(ip, 1)  # pending until cancelled

    collected = []
    task = asyncio.create_task(run_discovery(plan, on_result=collected.append, connect=connect))
    while len(collected) < 2:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert {str(h.ip) for h in collected} == hosts_done
    assert net.open_sockets == 0
    assert _netscope_tasks() == []
    report = build_discovery_report(plan, collected, timer=RunTimer(), interrupted=True)
    assert (report.metadata.completed_hosts, report.metadata.requested_hosts) == (2, 14)


@pytest.mark.parametrize("n", [1, 5, 17])
async def test_result_completed_just_before_cancellation_is_kept(n):
    """Regression: a probe that finished right before Ctrl-C used to be lost in a queue handoff.

    The run task is cancelled synchronously from inside probe n+1, before the event loop can
    schedule anything else, which is the tightest possible race.
    """
    plan = await prepare_scan(ScanConfig(target="127.0.0.1", ports="1-100", concurrency=1, rate=10_000, authorized=True))
    collected = []
    calls = 0

    async def connect(ip, port):
        nonlocal calls
        calls += 1
        if calls == n + 1:
            run_task.cancel()
            await asyncio.sleep(30)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")

    run_task = asyncio.create_task(run_scan(plan, on_result=collected.append, connect=connect))
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert [r.port for r in collected] == list(range(1, n + 1))
    assert _netscope_tasks() == []


# ---- CLI: a real SIGINT delivered through Python's signal machinery --------------------------


@pytest.fixture
def interrupting_network(monkeypatch):
    """Probes complete until the Nth, which raises SIGINT in-process (what Ctrl-C does)."""
    state = {"calls": 0, "interrupt_at": 6, "net": SocketTrackingNetwork(completes=lambda port: True)}

    def factory(fast_refusal=True):
        async def connect(ip, port):
            state["calls"] += 1
            if state["calls"] == state["interrupt_at"]:
                signal.raise_signal(signal.SIGINT)
                await asyncio.sleep(30)
            await state["net"](ip, port)

        return connect

    monkeypatch.setattr(probe, "make_tcp_connect", factory)
    return state


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_cli_sigint_emits_valid_partial_report_and_exits_130(interrupting_network, fmt):
    result = runner.invoke(
        app, ["scan", "127.0.0.1", "-p", "1-100", "--yes-authorized", "--format", fmt, "-c", "1", "-r", "10000"]
    )
    assert result.exit_code == 130, result.output
    if fmt == "json":
        data = json.loads(result.stdout)
        assert data["metadata"]["interrupted"] is True
        assert data["metadata"]["completed_probes"] == len(data["results"]) == 5
        assert data["metadata"]["requested_probes"] == 100
    else:
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        assert len(rows) == 5 and {r["state"] for r in rows} == {"closed"}
    assert "interrupted" in result.stderr
    assert interrupting_network["net"].open_sockets == 0


def test_cli_sigint_writes_partial_file_atomically(interrupting_network, tmp_path):
    out = tmp_path / "partial.json"
    result = runner.invoke(app, ["scan", "127.0.0.1", "-p", "1-100", "--yes-authorized", "-o", str(out), "-c", "1"])
    assert result.exit_code == 130
    assert json.loads(out.read_text(encoding="utf-8"))["metadata"]["interrupted"] is True
    assert [p.name for p in tmp_path.iterdir()] == ["partial.json"]


def test_cli_sigint_during_discovery(interrupting_network):
    result = runner.invoke(app, ["discover", "127.0.0.0/28", "--yes-authorized", "--json", "-c", "1", "-r", "10000"])
    assert result.exit_code == 130
    data = json.loads(result.stdout)
    assert data["metadata"]["interrupted"] is True
    # 3 ports per host; SIGINT arrives on the 6th probe (host 2's last port). Host 2 already had two
    # refusals, but its probes never all completed, so it gets no verdict.
    assert data["metadata"]["completed_hosts"] == len(data["results"]) == 1
    assert data["results"][0]["ip"] == "127.0.0.1"


def test_cli_interrupt_during_preflight(monkeypatch):
    async def interrupted_resolver(host):
        raise KeyboardInterrupt

    monkeypatch.setattr("netscope.resolver.system_resolver", interrupted_resolver)
    result = runner.invoke(app, ["scan", "some.lan", "--yes-authorized", "--json"])
    assert result.exit_code == 130
    assert result.stdout == ""
    assert "nothing was scanned" in " ".join(result.stderr.split())


# ---- real process ---------------------------------------------------------------------------

CHILD = textwrap.dedent(
    """
    import signal, sys, threading
    from netscope.cli import main
    threading.Timer(1.5, signal.raise_signal, (signal.SIGINT,)).start()
    sys.argv = ["netscope"] + sys.argv[1:]
    main()
    """
)


@pytest.mark.integration
@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_subprocess_sigint_partial_output(fmt):
    """A real process scanning loopback slowly (5 probes/s) is interrupted after ~1.5 s."""
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, "scan", "127.0.0.1", "-p", "1-100", "--rate", "5", "--yes-authorized", "--format", fmt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )
    assert proc.returncode == 130, proc.stderr
    if fmt == "json":
        data = json.loads(proc.stdout)
        assert data["metadata"]["interrupted"] is True
        assert 1 <= data["metadata"]["completed_probes"] < 100
        assert data["metadata"]["completed_probes"] == len(data["results"])
    else:
        rows = list(csv.DictReader(io.StringIO(proc.stdout)))
        assert 1 <= len(rows) < 100
    assert "Traceback" not in proc.stderr
