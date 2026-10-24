"""Helpers for API and job-manager tests: a scripted network, a recording resolver, and polling."""

import asyncio
import errno
import time

from fastapi.testclient import TestClient

from netscope.api import create_app
from netscope.jobs import JobLimits

TERMINAL = ("completed", "cancelled", "failed")
CLIENT_HEADERS = {"X-NetScope-Client": "tests"}


class ScriptedNetwork:
    """Outcome per (ip, port), then per port, then a default: 'open', 'refused', 'error', or 'hang'."""

    def __init__(self, default: str = "refused") -> None:
        self.default = default
        self.outcomes: dict = {}
        self.calls: list[tuple[str, int]] = []

    def factory(self, fast_refusal: bool = True):
        async def connect(ip: str, port: int) -> None:
            self.calls.append((ip, port))
            outcome = self.outcomes.get((ip, port), self.outcomes.get(port, self.default))
            await asyncio.sleep(0)
            if outcome == "open":
                return
            if outcome == "refused":
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            if outcome == "error":
                raise OSError(errno.EMFILE, "too many open files")
            await asyncio.sleep(3600)

        return connect


class RecordingResolver:
    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {}
        self.calls: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        from netscope.resolver import ResolutionError

        self.calls.append(host)
        if host not in self.answers:
            raise ResolutionError("dns:EAI_NONAME", f"cannot resolve {host}")
        return self.answers[host]


def make_client(db_path, network=None, resolver=None, limits=None, static_dir=None, headers=CLIENT_HEADERS) -> TestClient:
    app = create_app(
        db_path=db_path,
        static_dir=static_dir,
        limits=limits or JobLimits(max_running=2, max_queued=2, flush_interval_s=0.02),
        connect_factory=network.factory if network is not None else None,
        resolver=resolver,
        allowed_hosts=("testserver", "127.0.0.1", "localhost"),
    )
    return TestClient(app, headers=headers)


def wait_for(client: TestClient, scan_id: str, statuses=TERMINAL, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        data = client.get(f"/api/scans/{scan_id}").json()
        if data["status"] in statuses:
            return data
        if time.monotonic() > deadline:
            raise AssertionError(f"scan did not reach {statuses}: {data}")
        time.sleep(0.02)


def wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.02)


def scan_body(target: str = "127.0.0.1", **overrides) -> dict:
    return {"kind": "scan", "target": target, "authorized": True, "rate": 500, **overrides}
