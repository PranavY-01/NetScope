"""Job manager and store, without HTTP: bounds, lifecycle, persistence safety."""

import asyncio
import sqlite3

import pytest
from web_support import ScriptedNetwork

from netscope.config import DiscoveryConfig, ScanConfig
from netscope.discovery import prepare_discovery
from netscope.jobs import CapacityError, JobLimits, JobManager, ShuttingDownError
from netscope.scanner import prepare_scan
from netscope.store import SCHEMA_VERSION, ScanStore, StoreError


@pytest.fixture
def store(tmp_path):
    s = ScanStore(tmp_path / "jobs.db")
    s.initialize()
    yield s
    s.close()


async def plan(target="127.0.0.1", **overrides):
    return await prepare_scan(ScanConfig(target=target, authorized=True, rate=10_000, **overrides))


async def wait_terminal(manager, store, scan_id, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while manager.live(scan_id) is not None:
        assert loop.time() < deadline, "job did not finish"
        await asyncio.sleep(0.01)
    return store.get_scan(scan_id)


async def test_concurrent_submissions_cannot_exceed_capacity(store):
    network = ScriptedNetwork(default="hang")
    manager = JobManager(store, limits=JobLimits(max_running=1, max_queued=2), connect_factory=network.factory)
    plans = [await plan(timeout=10) for _ in range(6)]
    outcomes = await asyncio.gather(*(manager.submit(p) for p in plans), return_exceptions=True)
    accepted = [o for o in outcomes if isinstance(o, str)]
    assert len(accepted) == 3
    assert sum(isinstance(o, CapacityError) for o in outcomes) == 3
    await manager.shutdown()
    assert {store.get_scan(i).status for i in accepted} == {"cancelled"}


async def test_global_rate_limit_spans_jobs(store):
    """Two jobs at 10,000/s each are still held to the server-wide cap together."""
    network = ScriptedNetwork(default="refused")
    manager = JobManager(store, limits=JobLimits(max_running=2, global_rate=100), connect_factory=network.factory)
    loop = asyncio.get_running_loop()
    start = loop.time()
    ids = [await manager.submit(await plan(ports="1-10")), await manager.submit(await plan(ports="11-20"))]
    for scan_id in ids:
        await wait_terminal(manager, store, scan_id)
    # 20 starts at 100/s need >= 190 ms (minus the Windows timer slack of up to ~31 ms).
    assert loop.time() - start >= 0.19 - 0.035
    assert len(network.calls) == 20


async def test_shutdown_rejects_new_work_and_finishes_jobs(store):
    network = ScriptedNetwork(default="hang")
    network.outcomes[22] = "open"
    manager = JobManager(store, limits=JobLimits(flush_interval_s=0.01), connect_factory=network.factory)
    scan_id = await manager.submit(await plan(timeout=10))
    while store.get_scan(scan_id).completed < 1:
        await asyncio.sleep(0.01)
    await manager.shutdown()
    row = store.get_scan(scan_id)
    assert row.status == "cancelled" and row.interrupted and row.completed == 1
    assert row.metadata["interrupted"] is True and row.metadata["completed_probes"] == 1
    with pytest.raises(ShuttingDownError):
        await manager.submit(await plan())


async def test_repeated_cancel_does_not_break_finalization(store):
    network = ScriptedNetwork(default="hang")
    manager = JobManager(store, connect_factory=network.factory)
    scan_id = await manager.submit(await plan(timeout=10))
    while manager.live(scan_id).status != "running":
        await asyncio.sleep(0.01)
    for _ in range(5):
        await manager.cancel(scan_id)
        await asyncio.sleep(0)
    row = await wait_terminal(manager, store, scan_id)
    assert row.status == "cancelled"
    assert await manager.cancel(scan_id) is False


async def test_discovery_job_persists_host_verdicts(store):
    network = ScriptedNetwork(default="refused")
    manager = JobManager(store, connect_factory=network.factory)
    p = prepare_discovery(DiscoveryConfig(network="127.0.0.0/29", authorized=True, rate=10_000))
    scan_id = await manager.submit(p)
    row = await wait_terminal(manager, store, scan_id)
    assert row.status == "completed" and row.completed == 6 and row.summary == {"responsive": 6}
    report = store.report(scan_id)
    assert report.metadata.completed_hosts == 6 and len(report.results) == 6
    assert all(len(h.evidence) == 3 for h in report.results)


# ---- store safety -------------------------------------------------------------------------------


def test_initialize_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "keep.db"
    first = ScanStore(path)
    first.initialize()
    first.create_scan(scan_id="a" * 32, kind="scan", target="127.0.0.1", settings={}, resolved_ips=[], target_errors=[], estimate=None, total=0)
    first.close()
    again = ScanStore(path)
    again.initialize()
    assert again.get_scan("a" * 32).target == "127.0.0.1"
    again.close()


def test_foreign_database_is_refused_untouched(tmp_path):
    path = tmp_path / "other.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE invoices (id INTEGER)")
    conn.execute("INSERT INTO invoices VALUES (42)")
    conn.commit()
    conn.close()
    store = ScanStore(path)
    with pytest.raises(StoreError, match="not a NetScope database"):
        store.initialize()
    store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT id FROM invoices").fetchall() == [(42,)]
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"invoices"}
    conn.close()


def test_schema_version_mismatch_is_refused(tmp_path):
    path = tmp_path / "future.db"
    store = ScanStore(path)
    store.initialize()
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    store = ScanStore(path)
    with pytest.raises(StoreError, match="schema version"):
        store.initialize()
    store.close()


def test_recover_marks_only_unfinished_scans(store):
    for scan_id, status in (("1" * 32, "running"), ("2" * 32, "queued"), ("3" * 32, "completed")):
        store.create_scan(scan_id=scan_id, kind="scan", target="t", settings={}, resolved_ips=[], target_errors=[], estimate=None, total=1)
        if status != "queued":
            store.mark_running(scan_id)
        if status == "completed":
            store.finish(scan_id, status="completed", completed=1, interrupted=False, metadata=None)
    assert store.recover_interrupted() == 2
    assert store.get_scan("1" * 32).error_code == "server_restart"
    assert store.get_scan("2" * 32).status == "failed"
    assert store.get_scan("3" * 32).status == "completed"


def test_delete_is_scoped_to_one_finished_scan(store):
    from factories import port_result

    for scan_id in ("a" * 32, "b" * 32):
        store.create_scan(scan_id=scan_id, kind="scan", target="127.0.0.1", settings={}, resolved_ips=["127.0.0.1"], target_errors=[], estimate=None, total=2)
        store.add_port_results(scan_id, [port_result(80), port_result(81)], completed=2)
        store.finish(scan_id, status="completed", completed=2, interrupted=False, metadata=None)
    store.create_scan(scan_id="c" * 32, kind="scan", target="t", settings={}, resolved_ips=[], target_errors=[], estimate=None, total=1)
    assert store.delete_scan("a" * 32) is True
    assert store.get_scan("a" * 32) is None
    assert len(store.port_results("b" * 32, "127.0.0.1")) == 2
    assert store.port_results("a" * 32, "127.0.0.1") == []
    assert store.delete_scan("c" * 32) is False  # still queued
    assert store.get_scan("c" * 32) is not None
