"""Regenerate frontend/src/test/fixtures from real API responses.

Drives the FastAPI app in-process with a scripted network (no real probes, no network traffic) and
saves the JSON it returns, so the frontend tests run against the actual contract.
tests/test_frontend_contract.py checks the saved files still match the API models.

Usage (repo root):  .\\.venv\\Scripts\\python.exe tools\\make_frontend_fixtures.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from web_support import ScriptedNetwork, make_client, scan_body, wait_for, wait_until  # noqa: E402

from netscope.jobs import JobLimits  # noqa: E402

OUT = ROOT / "frontend" / "src" / "test" / "fixtures"


def save(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {name}.json")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    network = ScriptedNetwork(default="refused")
    network.outcomes[80] = "open"
    network.outcomes[("127.0.0.2", 22)] = "hang"
    network.outcomes[("127.0.0.2", 80)] = "hang"
    network.outcomes[("127.0.0.2", 443)] = "hang"
    network.outcomes[("127.0.0.3", 22)] = "error"
    network.outcomes[("127.0.0.3", 80)] = "hang"
    network.outcomes[("127.0.0.3", 443)] = "hang"
    limits = JobLimits(max_running=1, max_queued=2, flush_interval_s=0.02)
    with tempfile.TemporaryDirectory() as tmp, make_client(Path(tmp) / "fixtures.db", network, limits=limits) as c:
        save("limits", c.get("/api/limits").json())
        save("stats_empty", c.get("/api/stats").json())
        save("scans_empty", c.get("/api/scans").json())

        completed = c.post("/api/scans", json=scan_body(ports="22,80,443,8080")).json()
        save("scan_completed", wait_for(c, completed["id"]))
        save("results_scan", c.get(f"/api/scans/{completed['id']}/hosts").json())

        discover = c.post("/api/scans", json=scan_body("127.0.0.0/29", kind="discover", timeout=0.2)).json()
        save("discover_completed", wait_for(c, discover["id"]))
        save("results_discover", c.get(f"/api/scans/{discover['id']}/hosts").json())

        network.default = "hang"
        for port in (1, 2, 3):
            network.outcomes[port] = "open" if port == 2 else "refused"
        running =c.post("/api/scans", json=scan_body(ports="1-40", timeout=10, concurrency=4)).json()
        queued = c.post("/api/scans", json=scan_body(ports="1-5", timeout=10)).json()
        wait_until(lambda: c.get(f"/api/scans/{running['id']}").json()["progress"]["completed"] >= 1)
        time.sleep(0.1)
        save("scan_running", c.get(f"/api/scans/{running['id']}").json())
        save("scan_queued", c.get(f"/api/scans/{queued['id']}").json())
        save("scans", c.get("/api/scans").json())
        save("stats", c.get("/api/stats").json())
        c.post(f"/api/scans/{queued['id']}/cancel")
        c.post(f"/api/scans/{running['id']}/cancel")
        save("scan_cancelled", wait_for(c, running["id"]))
        wait_for(c, queued["id"])

        save("error_public", c.post("/api/scans", json=scan_body("8.8.8.8")).json())
        save("error_validation", c.post("/api/scans", json=scan_body(ports="100-1")).json())


if __name__ == "__main__":
    main()
