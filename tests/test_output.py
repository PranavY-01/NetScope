import csv
import io
import json

import pytest
from factories import discovery_report, dns_error, evidence, host_result, port_result, scan_report

from netscope.models import HostState, PortState
from netscope.output import (
    DISCOVERY_CSV_COLUMNS,
    SCAN_CSV_COLUMNS,
    render,
    to_csv,
    to_json,
    write_atomic,
)

pytestmark = pytest.mark.usefixtures("no_sockets")


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_scan_json_shape():
    report = scan_report([port_result(80, PortState.OPEN), port_result(81, PortState.CLOSED)])
    data = json.loads(to_json(report))
    assert data["metadata"]["tool"] == "netscope"
    assert data["metadata"]["schema_version"] == "1"
    assert data["metadata"]["command"] == "scan"
    assert data["metadata"]["started_at"].startswith("2026-10-23T12:00:00")
    assert data["results"][0] == {
        "target_input": "127.0.0.1",
        "ip": "127.0.0.1",
        "port": 80,
        "state": "open",
        "elapsed_ms": 1.5,
        "started_at": data["results"][0]["started_at"],
        "error_code": None,
        "error_message": None,
    }
    assert data["summary"] == {"open": 1, "closed": 1, "filtered": 0, "inconclusive": 0, "error": 0}


def test_scan_csv_columns_and_dns_error_row():
    report = scan_report([port_result(443, PortState.INCONCLUSIVE, error_code="timeout")], [dns_error()])
    rows = _rows(to_csv(report))
    assert tuple(rows[0].keys()) == SCAN_CSV_COLUMNS
    assert rows[0]["target_input"] == "nx.example"
    assert rows[0]["state"] == "error"
    assert rows[0]["ip"] == "" and rows[0]["port"] == ""
    assert rows[1]["state"] == "inconclusive"
    assert rows[1]["error_code"] == "timeout"


def test_csv_escaping():
    message = 'boom, "quoted"\nsecond line'
    report = scan_report([port_result(80, PortState.ERROR, error_code="errno:EX", error_message=message)])
    rows = _rows(to_csv(report))
    assert rows[0]["error_message"] == message


def test_discovery_json_keeps_evidence():
    host = host_result(probes=[evidence(22, PortState.INCONCLUSIVE), evidence(80, PortState.CLOSED)])
    data = json.loads(to_json(discovery_report([host])))
    assert data["metadata"]["command"] == "discover"
    assert data["results"][0]["state"] == "responsive"
    assert [e["state"] for e in data["results"][0]["evidence"]] == ["inconclusive", "closed"]
    assert data["summary"] == {"responsive": 1, "no_response": 0, "error": 0}


def test_discovery_csv_flattens_one_row_per_probe():
    hosts = [
        host_result("10.0.0.1", probes=[evidence(22, PortState.INCONCLUSIVE), evidence(80, PortState.OPEN)]),
        host_result("10.0.0.2", HostState.NO_RESPONSE, probes=[evidence(22, PortState.INCONCLUSIVE)]),
    ]
    rows = _rows(to_csv(discovery_report(hosts)))
    assert tuple(rows[0].keys()) == DISCOVERY_CSV_COLUMNS
    assert [(r["ip"], r["host_state"], r["port"], r["probe_state"]) for r in rows] == [
        ("10.0.0.1", "responsive", "22", "inconclusive"),
        ("10.0.0.1", "responsive", "80", "open"),
        ("10.0.0.2", "no_response", "22", "inconclusive"),
    ]


def test_has_errors():
    assert not scan_report([port_result(state=PortState.CLOSED)]).has_errors
    assert not scan_report([port_result(state=PortState.INCONCLUSIVE)]).has_errors
    assert scan_report([port_result(state=PortState.ERROR)]).has_errors
    assert scan_report([], [dns_error()]).has_errors
    assert not discovery_report([host_result(state=HostState.NO_RESPONSE)]).has_errors
    assert discovery_report([host_result(state=HostState.ERROR)]).has_errors


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_machine_output_has_no_ansi(fmt):
    text = render(scan_report([port_result()], [dns_error()]), fmt)
    assert "\x1b" not in text


def test_partial_report_is_still_valid_json():
    data = json.loads(to_json(scan_report([port_result()], interrupted=True)))
    assert data["metadata"]["interrupted"] is True


def test_render_rejects_unknown_format():
    with pytest.raises(ValueError):
        render(scan_report(), "xml")


def test_write_atomic(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")
    write_atomic(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_write_atomic_leaves_original_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.csv"
    target.write_text("original", encoding="utf-8")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("netscope.output.os.replace", fail)
    with pytest.raises(OSError):
        write_atomic(target, "partial")
    assert target.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["out.csv"]
