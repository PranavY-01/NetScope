"""SQLite persistence for scan history. Synchronous and thread-safe; callers on an event loop use
asyncio.to_thread. Knows nothing about HTTP or the job manager.

Each fact is stored once: the original target and effective settings live on the scan row, and the
per-result rows hold only what differs between results.
"""

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from netscope.models import (
    DiscoveryMetadata,
    DiscoveryReport,
    HostResult,
    PortResult,
    ProbeEvidence,
    ScanMetadata,
    ScanReport,
    TargetError,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE scans (
    id                 TEXT PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('scan', 'discover')),
    target             TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'cancelled', 'failed')),
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    settings_json      TEXT NOT NULL,
    resolved_ips_json  TEXT NOT NULL,
    target_errors_json TEXT NOT NULL,
    estimate_json      TEXT,
    total              INTEGER NOT NULL,
    completed          INTEGER NOT NULL DEFAULT 0,
    interrupted        INTEGER NOT NULL DEFAULT 0,
    error_code         TEXT,
    error_message      TEXT,
    metadata_json      TEXT
);
CREATE INDEX scans_created ON scans (created_at DESC);
CREATE TABLE port_results (
    scan_id       TEXT NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
    ip            TEXT NOT NULL,
    ip_int        INTEGER NOT NULL,
    port          INTEGER NOT NULL,
    state         TEXT NOT NULL,
    elapsed_ms    REAL NOT NULL,
    started_at    TEXT NOT NULL,
    error_code    TEXT,
    error_message TEXT
);
CREATE INDEX port_results_scan ON port_results (scan_id, ip_int, port);
CREATE TABLE host_results (
    scan_id       TEXT NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
    ip            TEXT NOT NULL,
    ip_int        INTEGER NOT NULL,
    state         TEXT NOT NULL,
    elapsed_ms    REAL NOT NULL,
    error_code    TEXT,
    error_message TEXT,
    evidence_json TEXT NOT NULL
);
CREATE INDEX host_results_scan ON host_results (scan_id, ip_int);
"""

STATUSES = ("queued", "running", "completed", "cancelled", "failed")
ACTIVE_STATUSES = ("queued", "running")


class StoreError(RuntimeError):
    """The database cannot be used safely. Nothing was modified."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScanRow:
    id: str
    kind: str
    target: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    settings: dict[str, Any]
    resolved_ips: list[str]
    target_errors: list[TargetError]
    estimate: dict[str, Any] | None
    total: int
    completed: int
    interrupted: bool
    error_code: str | None
    error_message: str | None
    metadata: dict[str, Any] | None
    summary: dict[str, int]


class ScanStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- schema --------------------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the schema in an empty database, or verify an existing one. Never drops data."""
        with self._lock:
            tables = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not tables:
                self._conn.executescript(f"BEGIN; {_SCHEMA} INSERT INTO meta VALUES ('schema_version', '{SCHEMA_VERSION}'); COMMIT;")
                return
            if "meta" not in tables or "scans" not in tables:
                raise StoreError(
                    f"{self.path} is not a NetScope database (unexpected tables: {sorted(tables)}). "
                    "Point --db at a new file; the existing file was not modified."
                )
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            found = row[0] if row else None
            if found != str(SCHEMA_VERSION):
                raise StoreError(
                    f"{self.path} has schema version {found}, this NetScope expects {SCHEMA_VERSION}. "
                    "No automatic migration exists for it; keep the file and use a new --db path, "
                    "or open it with the NetScope version that created it."
                )

    def recover_interrupted(self) -> int:
        """Scans left queued/running by a crashed or killed process cannot resume: mark them failed.

        Results already flushed stay attached to the scan. Returns the number of scans updated.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE scans SET status = 'failed', interrupted = 1, finished_at = ?, "
                "error_code = 'server_restart', "
                "error_message = 'the server stopped before this scan finished; results saved up to that point are kept' "
                "WHERE status IN ('queued', 'running')",
                (utc_now(),),
            )
            return cur.rowcount

    # ---- writes --------------------------------------------------------------------------------

    def create_scan(
        self,
        *,
        scan_id: str,
        kind: str,
        target: str,
        settings: dict[str, Any],
        resolved_ips: Sequence[str],
        target_errors: Sequence[TargetError],
        estimate: dict[str, Any] | None,
        total: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO scans (id, kind, target, status, created_at, settings_json, resolved_ips_json, "
                "target_errors_json, estimate_json, total) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    kind,
                    target,
                    utc_now(),
                    json.dumps(settings),
                    json.dumps(list(resolved_ips)),
                    json.dumps([e.model_dump(mode="json") for e in target_errors]),
                    json.dumps(estimate) if estimate is not None else None,
                    total,
                ),
            )

    def mark_running(self, scan_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scans SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                (utc_now(), scan_id),
            )

    def add_port_results(self, scan_id: str, results: Iterable[PortResult], completed: int) -> None:
        rows = [
            (
                scan_id,
                str(r.ip),
                int(r.ip),
                r.port,
                r.state.value,
                r.elapsed_ms,
                r.started_at.isoformat(),
                r.error_code,
                r.error_message,
            )
            for r in results
        ]
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany("INSERT INTO port_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                self._conn.execute("UPDATE scans SET completed = ? WHERE id = ?", (completed, scan_id))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def add_host_results(self, scan_id: str, hosts: Iterable[HostResult], completed: int) -> None:
        rows = [
            (
                scan_id,
                str(h.ip),
                int(h.ip),
                h.state.value,
                h.elapsed_ms,
                h.error_code,
                h.error_message,
                json.dumps([e.model_dump(mode="json") for e in h.evidence]),
            )
            for h in hosts
        ]
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany("INSERT INTO host_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                self._conn.execute("UPDATE scans SET completed = ? WHERE id = ?", (completed, scan_id))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def finish(
        self,
        scan_id: str,
        *,
        status: str,
        completed: int,
        interrupted: bool,
        metadata: dict[str, Any] | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in ("completed", "cancelled", "failed"):
            raise ValueError(f"not a terminal status: {status}")
        with self._lock:
            self._conn.execute(
                "UPDATE scans SET status = ?, finished_at = ?, completed = ?, interrupted = ?, metadata_json = ?, "
                "error_code = ?, error_message = ? WHERE id = ?",
                (
                    status,
                    utc_now(),
                    completed,
                    int(interrupted),
                    json.dumps(metadata) if metadata is not None else None,
                    error_code,
                    error_message,
                    scan_id,
                ),
            )

    def delete_scan(self, scan_id: str) -> bool:
        """Delete one finished scan and its results (cascade). Active scans are never deleted."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM scans WHERE id = ? AND status NOT IN ('queued', 'running')", (scan_id,)
            )
            return cur.rowcount == 1

    # ---- reads ---------------------------------------------------------------------------------

    def _summary(self, scan_id: str, kind: str) -> dict[str, int]:
        table = "port_results" if kind == "scan" else "host_results"
        rows = self._conn.execute(f"SELECT state, COUNT(*) FROM {table} WHERE scan_id = ? GROUP BY state", (scan_id,))
        return {state: count for state, count in rows}

    def _row(self, row: sqlite3.Row) -> ScanRow:
        return ScanRow(
            id=row["id"],
            kind=row["kind"],
            target=row["target"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            settings=json.loads(row["settings_json"]),
            resolved_ips=json.loads(row["resolved_ips_json"]),
            target_errors=[TargetError(**e) for e in json.loads(row["target_errors_json"])],
            estimate=json.loads(row["estimate_json"]) if row["estimate_json"] else None,
            total=row["total"],
            completed=row["completed"],
            interrupted=bool(row["interrupted"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
            summary=self._summary(row["id"], row["kind"]),
        )

    def get_scan(self, scan_id: str) -> ScanRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
            return self._row(row) if row else None

    def list_scans(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        kind: str | None = None,
        search: str | None = None,
    ) -> tuple[list[ScanRow], int]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if search:
            clauses.append("(target LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params += [pattern, pattern]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM scans {where}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT * FROM scans {where} ORDER BY created_at DESC, id LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
            return [self._row(r) for r in rows], total

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            counts = {status: 0 for status in STATUSES}
            for status, count in self._conn.execute("SELECT status, COUNT(*) FROM scans GROUP BY status"):
                counts[status] = count
            return counts

    def _port_result(self, row: sqlite3.Row, target: str) -> PortResult:
        return PortResult(
            target_input=target,
            ip=IPv4Address(row["ip"]),
            port=row["port"],
            state=row["state"],
            elapsed_ms=row["elapsed_ms"],
            started_at=row["started_at"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def _host_result(self, row: sqlite3.Row, target: str) -> HostResult:
        evidence = [ProbeEvidence(**e) for e in json.loads(row["evidence_json"])]
        return HostResult(
            target_input=target,
            ip=IPv4Address(row["ip"]),
            state=row["state"],
            probed_ports=[e.port for e in evidence],
            evidence=evidence,
            elapsed_ms=row["elapsed_ms"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def port_results(self, scan_id: str, target: str, *, ips: Sequence[str] | None = None, state: str | None = None) -> list[PortResult]:
        clauses, params = ["scan_id = ?"], [scan_id]
        if ips is not None:
            if not ips:
                return []
            clauses.append(f"ip IN ({','.join('?' * len(ips))})")
            params += list(ips)
        if state:
            clauses.append("state = ?")
            params.append(state)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM port_results WHERE {' AND '.join(clauses)} ORDER BY ip_int, port", params
            ).fetchall()
            return [self._port_result(r, target) for r in rows]

    def port_result_ips(self, scan_id: str, *, state: str | None, limit: int, offset: int) -> tuple[list[str], int]:
        """Distinct addresses with port results (optionally having a port in `state`), for paging by host."""
        where, params = "scan_id = ?", [scan_id]
        if state:
            where += " AND state = ?"
            params.append(state)
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(DISTINCT ip_int) FROM port_results WHERE {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT ip FROM port_results WHERE {where} GROUP BY ip_int ORDER BY ip_int LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return [r[0] for r in rows], total

    def host_results(
        self, scan_id: str, target: str, *, state: str | None = None, limit: int | None = None, offset: int = 0
    ) -> tuple[list[HostResult], int]:
        where, params = "scan_id = ?", [scan_id]
        if state:
            where += " AND state = ?"
            params.append(state)
        page = "" if limit is None else " LIMIT ? OFFSET ?"
        page_params = [] if limit is None else [limit, offset]
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM host_results WHERE {where}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT * FROM host_results WHERE {where} ORDER BY ip_int{page}", [*params, *page_params]
            ).fetchall()
            return [self._host_result(r, target) for r in rows], total

    def report(self, scan_id: str) -> ScanReport | DiscoveryReport | None:
        """Rebuild the CLI-compatible report for a finished scan, or None if it has no metadata."""
        scan = self.get_scan(scan_id)
        if scan is None or scan.metadata is None:
            return None
        if scan.kind == "scan":
            return ScanReport(
                metadata=ScanMetadata(**scan.metadata),
                resolved_ips=[IPv4Address(ip) for ip in scan.resolved_ips],
                target_errors=scan.target_errors,
                results=self.port_results(scan_id, scan.target),
            )
        hosts, _ = self.host_results(scan_id, scan.target)
        return DiscoveryReport(metadata=DiscoveryMetadata(**scan.metadata), results=hosts)
