"""JSON and CSV serialization of reports. No terminal styling, no logging."""

import csv
import io
import os
import tempfile
from pathlib import Path

from netscope.models import DiscoveryReport, ScanReport

SCAN_CSV_COLUMNS = (
    "target_input",
    "ip",
    "port",
    "state",
    "elapsed_ms",
    "error_code",
    "error_message",
    "started_at",
)

DISCOVERY_CSV_COLUMNS = (
    "target_input",
    "ip",
    "host_state",
    "host_elapsed_ms",
    "host_error_code",
    "host_error_message",
    "port",
    "probe_state",
    "probe_elapsed_ms",
    "probe_error_code",
    "probe_error_message",
    "probe_started_at",
)


def to_json(report: ScanReport | DiscoveryReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def _writer(buffer: io.StringIO, columns: tuple[str, ...]) -> csv.DictWriter:
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    return writer


def scan_to_csv(report: ScanReport) -> str:
    """One row per port probe. Each target error (e.g. DNS failure) adds a row with empty ip/port."""
    buffer = io.StringIO()
    writer = _writer(buffer, SCAN_CSV_COLUMNS)
    for error in report.target_errors:
        writer.writerow(
            {
                "target_input": error.target_input,
                "state": "error",
                "error_code": error.error_code,
                "error_message": error.error_message,
            }
        )
    for result in report.results:
        writer.writerow(result.model_dump(mode="json"))
    return buffer.getvalue()


def discovery_to_csv(report: DiscoveryReport) -> str:
    """One row per probe; host columns repeat on each of that host's rows."""
    buffer = io.StringIO()
    writer = _writer(buffer, DISCOVERY_CSV_COLUMNS)
    for host in report.results:
        host_row = {
            "target_input": host.target_input,
            "ip": str(host.ip),
            "host_state": host.state.value,
            "host_elapsed_ms": host.elapsed_ms,
            "host_error_code": host.error_code,
            "host_error_message": host.error_message,
        }
        if not host.evidence:
            writer.writerow(host_row)
        for probe in host.evidence:
            data = probe.model_dump(mode="json")
            writer.writerow(
                host_row
                | {
                    "port": data["port"],
                    "probe_state": data["state"],
                    "probe_elapsed_ms": data["elapsed_ms"],
                    "probe_error_code": data["error_code"],
                    "probe_error_message": data["error_message"],
                    "probe_started_at": data["started_at"],
                }
            )
    return buffer.getvalue()


def to_csv(report: ScanReport | DiscoveryReport) -> str:
    if isinstance(report, ScanReport):
        return scan_to_csv(report)
    return discovery_to_csv(report)


def render(report: ScanReport | DiscoveryReport, fmt: str) -> str:
    if fmt == "json":
        return to_json(report)
    if fmt == "csv":
        return to_csv(report)
    raise ValueError(f"unknown output format {fmt!r}")


def write_atomic(path: Path, text: str) -> None:
    """Write to a temporary file in the same directory, then replace, so readers never see a torn file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
