"""HTTP API: validation, centrally enforced scope policy, job lifecycle, persistence and exports."""

import csv
import io
import json
import socket

import pytest
from web_support import (
    CLIENT_HEADERS,
    RecordingResolver,
    ScriptedNetwork,
    make_client,
    scan_body,
    wait_for,
    wait_until,
)

from netscope.jobs import JobLimits


@pytest.fixture
def network():
    return ScriptedNetwork()


@pytest.fixture
def resolver():
    return RecordingResolver()


@pytest.fixture
def client(tmp_path, network, resolver):
    with make_client(tmp_path / "netscope.db", network, resolver) as c:
        yield c


def create(client, **body):
    return client.post("/api/scans", json=scan_body(**body))


# ---- basics -----------------------------------------------------------------------------------


def test_health_limits_and_empty_stats(client):
    assert client.get("/api/health").json()["status"] == "ok"
    limits = client.get("/api/limits").json()
    assert limits["max_probes"] == 20_000 and limits["defaults"]["ports"] == "22,80,443"
    assert client.get("/api/stats").json() == {
        "total": 0,
        "by_status": {"queued": 0, "running": 0, "completed": 0, "cancelled": 0, "failed": 0},
    }
    assert client.get("/api/scans").json()["items"] == []


def test_security_headers(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers


# ---- a full scan --------------------------------------------------------------------------------


def test_scan_lifecycle_results_and_exports(client, network):
    network.outcomes[80] = "open"
    response = create(client, ports="22,80,443")
    assert response.status_code == 201, response.text
    scan = response.json()
    assert len(scan["id"]) == 32 and scan["status"] in ("queued", "running", "completed")
    assert scan["settings"]["ports"] == [22, 80, 443] and scan["settings"]["allow_shared"] is False
    assert scan["estimate"]["min_seconds"] >= 0 and scan["estimate"]["assumptions"]
    assert scan["progress"]["total"] == 3 and scan["progress"]["unit"] == "probes"

    done = wait_for(client, scan["id"])
    assert done["status"] == "completed"
    assert done["progress"] == {"completed": 3, "total": 3, "unit": "probes", "determinate": True}
    assert done["summary"] == {"open": 1, "closed": 2}
    assert done["partial"] is False and done["exportable"] is True
    assert done["started_at"] and done["finished_at"]

    hosts = client.get(f"/api/scans/{scan['id']}/hosts").json()
    assert hosts["kind"] == "scan" and hosts["total"] == 1
    assert [(p["port"], p["state"]) for p in hosts["items"][0]["ports"]] == [(22, "closed"), (80, "open"), (443, "closed")]
    only_open = client.get(f"/api/scans/{scan['id']}/hosts", params={"state": "open"}).json()
    assert [p["port"] for p in only_open["items"][0]["ports"]] == [80]

    exported = client.get(f"/api/scans/{scan['id']}/export", params={"format": "json"})
    assert exported.status_code == 200
    assert 'filename="netscope-scan-' in exported.headers["content-disposition"]
    report = exported.json()
    assert report["metadata"]["command"] == "scan" and report["metadata"]["interrupted"] is False
    assert report["metadata"]["completed_probes"] == 3
    assert [r["state"] for r in report["results"]] == ["closed", "open", "closed"]

    csv_text = client.get(f"/api/scans/{scan['id']}/export", params={"format": "csv"}).text
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert list(rows[0]) == ["target_input", "ip", "port", "state", "elapsed_ms", "error_code", "error_message", "started_at"]
    assert [r["state"] for r in rows] == ["closed", "open", "closed"]
    assert client.get("/api/stats").json()["by_status"]["completed"] == 1


def test_discovery_lifecycle_with_evidence(client, network):
    network.outcomes[("127.0.0.1", 80)] = "open"
    network.outcomes[("127.0.0.2", 22)] = "hang"
    network.outcomes[("127.0.0.2", 80)] = "hang"
    network.outcomes[("127.0.0.2", 443)] = "hang"
    scan = client.post("/api/scans", json=scan_body("127.0.0.0/30", kind="discover", timeout=0.1)).json()
    assert scan["progress"]["unit"] == "hosts" and scan["progress"]["total"] == 2
    done = wait_for(client, scan["id"])
    assert done["summary"] == {"responsive": 1, "no_response": 1}
    page = client.get(f"/api/scans/{scan['id']}/hosts").json()
    assert page["kind"] == "discover"
    assert [(h["ip"], h["state"]) for h in page["items"]] == [("127.0.0.1", "responsive"), ("127.0.0.2", "no_response")]
    assert [e["state"] for e in page["items"][1]["evidence"]] == ["inconclusive"] * 3
    filtered = client.get(f"/api/scans/{scan['id']}/hosts", params={"state": "responsive"}).json()
    assert filtered["total"] == 1
    report = client.get(f"/api/scans/{scan['id']}/export").json()
    assert report["metadata"]["command"] == "discover" and len(report["results"]) == 2


def test_hostname_with_several_addresses(client, network, resolver):
    resolver.answers["multi.lan"] = ["10.0.0.2", "10.0.0.1", "10.0.0.2"]
    scan = create(client, target="multi.lan", ports="80").json()
    assert scan["resolved_ips"] == ["10.0.0.2", "10.0.0.1"]
    wait_for(client, scan["id"])
    hosts = client.get(f"/api/scans/{scan['id']}/hosts").json()
    assert [h["ip"] for h in hosts["items"]] == ["10.0.0.1", "10.0.0.2"]
    assert hosts["items"][0]["ports"][0]["target_input"] == "multi.lan"


def test_dns_failure_is_a_structured_target_error(client, resolver):
    scan = create(client, target="nx.lan").json()
    assert scan["target_errors"][0]["error_code"] == "dns:EAI_NONAME"
    done = wait_for(client, scan["id"])
    assert done["status"] == "completed" and done["progress"]["total"] == 0
    assert done["progress"]["determinate"] is False
    report = client.get(f"/api/scans/{scan['id']}/export").json()
    assert report["results"] == [] and report["target_errors"][0]["target_input"] == "nx.lan"


# ---- scope policy is the engine's, enforced on the server ---------------------------------------


def test_missing_authorization_is_rejected_before_dns(client, resolver, network):
    response = create(client, target="some.lan", authorized=False)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_authorized"
    assert resolver.calls == [] and network.calls == []
    assert client.get("/api/stats").json()["total"] == 0


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (dict(target="8.8.8.8"), "public_target"),
        (dict(target="100.64.0.1"), "shared_address"),
        (dict(target="100.64.0.1", allow_public=True), "shared_address"),
        (dict(target="8.8.8.8", allow_shared=True), "public_target"),
        (dict(target="169.254.169.254", allow_public=True), "special_address"),
        (dict(target="224.0.0.1", allow_public=True, allow_shared=True), "special_address"),
        (dict(target="10.0.0.0/16", kind="discover"), "host_cap"),
        (dict(target="10.0.0.0/29", kind="discover", allowlist=["10.0.0.0/30"]), "allowlist"),
        (dict(target="10.0.0.0/22", kind="discover", max_hosts=1024, ports="1-30"), "probe_limit"),
    ],
)
def test_scope_rejections(client, network, body, code):
    response = create(client, **body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == code
    assert network.calls == []


def test_overrides_are_narrow_and_recorded(client, network):
    shared = create(client, target="100.64.0.1", allow_shared=True)
    assert shared.status_code == 201
    assert shared.json()["settings"]["allow_shared"] is True
    public = create(client, target="8.8.8.8", allow_public=True)
    assert public.status_code == 201
    assert public.json()["settings"]["allow_public"] is True and public.json()["settings"]["allow_shared"] is False


def test_hostname_is_checked_on_its_resolved_addresses(client, resolver, network):
    resolver.answers["rebind.lan"] = ["10.0.0.1", "8.8.8.8"]
    response = create(client, target="rebind.lan")
    assert response.status_code == 400 and response.json()["error"]["code"] == "public_target"
    assert network.calls == []


def test_allowlist_entries_are_accepted_inline(client):
    response = create(client, target="10.0.0.0/30", kind="discover", allowlist=["10.0.0.0/30", "# lab", "192.168.0.1"])
    assert response.status_code == 201
    assert response.json()["settings"]["allowlist"] == ["10.0.0.0/30", "192.168.0.1/32"]


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        (dict(ports="100-1"), "invalid_config", "ports"),
        (dict(ports="1-5000"), "too_many_ports", None),
        (dict(target="10.0.0.0/24"), "invalid_config", "target"),
        (dict(target="10.0.0.5/24", kind="discover"), "invalid_config", "target"),
        (dict(target="::1"), "invalid_config", "target"),
        (dict(allowlist=["not-an-ip"]), "invalid_config", None),
        (dict(timeout=0), "invalid_request", "timeout"),
        (dict(timeout=11), "invalid_request", "timeout"),
        (dict(concurrency=1000), "invalid_request", "concurrency"),
        (dict(rate=10_000), "invalid_request", "rate"),
        (dict(max_hosts=5000), "invalid_request", "max_hosts"),
        (dict(kind="sweep"), "invalid_request", "kind"),
        (dict(script="rm -rf /"), "invalid_request", "script"),
        (dict(target=""), "invalid_request", "target"),
    ],
)
def test_invalid_requests(client, network, body, code, field):
    response = create(client, **body)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == code
    if field:
        assert field in {f["field"] for f in error["fields"]}
    assert network.calls == []


# ---- request hardening --------------------------------------------------------------------------


def test_mutations_require_the_client_header(tmp_path, network):
    with make_client(tmp_path / "x.db", network, headers={}) as bare:
        response = bare.post("/api/scans", json=scan_body())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "missing_client_header"
        assert bare.get("/api/health").status_code == 200
        assert network.calls == []


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400


def test_oversized_body_is_rejected(client):
    response = client.post(
        "/api/scans",
        content=b"{" + b" " * 70_000 + b"}",
        headers={**CLIENT_HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_unknown_and_malformed_ids(client):
    assert client.get("/api/scans/" + "0" * 32).status_code == 404
    assert client.get("/api/scans/../../etc").status_code == 404
    assert client.get("/api/scans/not-an-id").status_code == 422
    assert client.post("/api/scans/" + "0" * 32 + "/cancel").status_code == 404
    assert client.get("/api/scans/" + "0" * 32 + "/export").status_code == 404
    assert client.get("/api/scans", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/scans", params={"limit": 1000}).status_code == 422


# ---- cancellation, failure, capacity ---------------------------------------------------------


def test_cancel_keeps_completed_results(client, network):
    for port in range(1, 10):
        network.outcomes[port] = "open"
    network.default = "hang"
    scan = create(client, ports="1-50", concurrency=5, timeout=10).json()
    wait_until(lambda: client.get(f"/api/scans/{scan['id']}").json()["progress"]["completed"] >= 9)
    response = client.post(f"/api/scans/{scan['id']}/cancel")
    assert response.status_code == 202 and response.json()["cancel_requested"] is True
    done = wait_for(client, scan["id"])
    assert done["status"] == "cancelled" and done["partial"] is True
    assert done["progress"]["completed"] == 9 and done["summary"] == {"open": 9}
    report = client.get(f"/api/scans/{scan['id']}/export").json()
    assert report["metadata"]["interrupted"] is True
    assert report["metadata"]["completed_probes"] == 9 and len(report["results"]) == 9
    assert client.post(f"/api/scans/{scan['id']}/cancel").status_code == 409


def test_engine_failure_is_recorded_not_stuck(tmp_path):
    def broken_factory(fast_refusal=True):
        raise RuntimeError("connect backend unavailable")

    from netscope.api import create_app
    from fastapi.testclient import TestClient

    app = create_app(
        db_path=tmp_path / "f.db",
        connect_factory=broken_factory,
        allowed_hosts=("testserver",),
        limits=JobLimits(flush_interval_s=0.02),
    )
    with TestClient(app, headers=CLIENT_HEADERS) as c:
        scan = c.post("/api/scans", json=scan_body()).json()
        done = wait_for(c, scan["id"])
        assert done["status"] == "failed"
        assert done["error"]["code"] == "engine_error"
        assert "connect backend unavailable" in done["error"]["message"]
        assert "Traceback" not in json.dumps(done)


def test_capacity_is_bounded_and_queued_scans_can_be_cancelled(tmp_path):
    network = ScriptedNetwork(default="hang")
    limits = JobLimits(max_running=1, max_queued=1, flush_interval_s=0.02)
    with make_client(tmp_path / "c.db", network, limits=limits) as c:
        first = c.post("/api/scans", json=scan_body(timeout=10)).json()
        wait_for(c, first["id"], statuses=("running",))
        second = c.post("/api/scans", json=scan_body(timeout=10)).json()
        assert c.get(f"/api/scans/{second['id']}").json()["status"] == "queued"
        third = c.post("/api/scans", json=scan_body(timeout=10))
        assert third.status_code == 429 and third.json()["error"]["code"] == "capacity"

        assert c.post(f"/api/scans/{second['id']}/cancel").status_code == 202
        queued_done = wait_for(c, second["id"])
        assert queued_done["status"] == "cancelled" and queued_done["started_at"] is None
        assert queued_done["progress"]["completed"] == 0
        assert c.post(f"/api/scans/{first['id']}/cancel").status_code == 202
        assert wait_for(c, first["id"])["status"] == "cancelled"
        assert c.post("/api/scans", json=scan_body(timeout=10)).status_code == 201


def test_running_scan_cannot_be_exported_or_deleted(client, network):
    network.default = "hang"
    scan = create(client, timeout=10).json()
    wait_for(client, scan["id"], statuses=("running",))
    assert client.get(f"/api/scans/{scan['id']}/export").status_code == 409
    assert client.delete(f"/api/scans/{scan['id']}").status_code == 409
    client.post(f"/api/scans/{scan['id']}/cancel")
    wait_for(client, scan["id"])


# ---- history ------------------------------------------------------------------------------------


def test_history_listing_filters_search_and_delete(client, network, resolver):
    resolver.answers["alpha.lan"] = ["10.0.0.1"]
    ids = [create(client, target=t).json()["id"] for t in ("127.0.0.1", "alpha.lan", "10.9.9.9")]
    for scan_id in ids:
        wait_for(client, scan_id)
    page = client.get("/api/scans", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["id"] == ids[2]  # newest first
    assert [s["target"] for s in client.get("/api/scans", params={"q": "alpha"}).json()["items"]] == ["alpha.lan"]
    assert client.get("/api/scans", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/scans", params={"status": "completed"}).json()["total"] == 3
    assert client.get("/api/scans", params={"kind": "discover"}).json()["total"] == 0

    assert client.delete(f"/api/scans/{ids[1]}").status_code == 204
    assert client.get(f"/api/scans/{ids[1]}").status_code == 404
    remaining = {s["id"] for s in client.get("/api/scans").json()["items"]}
    assert remaining == {ids[0], ids[2]}
    assert client.get(f"/api/scans/{ids[0]}/hosts").json()["total"] == 1


def test_history_survives_a_restart(tmp_path, network):
    db = tmp_path / "persist.db"
    with make_client(db, network) as first:
        scan_id = first.post("/api/scans", json=scan_body()).json()["id"]
        wait_for(first, scan_id)
    with make_client(db, network) as second:
        data = second.get(f"/api/scans/{scan_id}").json()
        assert data["status"] == "completed" and data["summary"] == {"closed": 3}
        assert second.get(f"/api/scans/{scan_id}/export").status_code == 200


def test_shutdown_records_running_scans_as_cancelled(tmp_path):
    network = ScriptedNetwork(default="hang")
    network.outcomes[22] = "open"
    db = tmp_path / "shutdown.db"
    with make_client(db, network) as c:
        scan_id = c.post("/api/scans", json=scan_body(timeout=10)).json()["id"]
        wait_until(lambda: c.get(f"/api/scans/{scan_id}").json()["progress"]["completed"] == 1)
    with make_client(db, network) as c:
        data = c.get(f"/api/scans/{scan_id}").json()
        assert data["status"] == "cancelled" and data["partial"] is True
        assert data["summary"] == {"open": 1}


# ---- static UI ----------------------------------------------------------------------------------


def test_built_ui_is_served_alongside_the_api(tmp_path, network):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>NetScope</title>", encoding="utf-8")
    with make_client(tmp_path / "ui.db", network, static_dir=dist) as c:
        page = c.get("/")
        assert page.status_code == 200 and "NetScope" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert c.get("/api/health").json()["status"] == "ok"


# ---- real engine, real sockets -----------------------------------------------------------------


@pytest.mark.integration
def test_real_engine_end_to_end_on_loopback(tmp_path):
    """API -> job manager -> real engine -> SQLite -> API, against a loopback listener."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)  # the kernel completes handshakes without accept()
    open_port = listener.getsockname()[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    try:
        with make_client(tmp_path / "real.db") as c:
            scan = c.post("/api/scans", json=scan_body(ports=f"{open_port},{closed_port}")).json()
            done = wait_for(c, scan["id"])
            states = {p["port"]: p["state"] for p in c.get(f"/api/scans/{scan['id']}/hosts").json()["items"][0]["ports"]}
            assert done["status"] == "completed"
            assert states == {open_port: "open", closed_port: "closed"}
    finally:
        listener.close()
