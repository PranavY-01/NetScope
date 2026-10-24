"""The frontend tests run against saved API responses (frontend/src/test/fixtures). This keeps those
fixtures honest: each must still validate against the API's response models, so a backend contract
change that the UI would not survive fails here. Regenerate with tools/make_frontend_fixtures.py."""

import json
from pathlib import Path

import pytest

from netscope.api.schemas import (
    DiscoveryResultsPage,
    ErrorResponse,
    Limits,
    ScanOut,
    ScanPage,
    ScanResultsPage,
    Stats,
)

FIXTURES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "test" / "fixtures"

MODELS = {
    "limits": Limits,
    "stats_empty": Stats,
    "stats": Stats,
    "scans_empty": ScanPage,
    "scans": ScanPage,
    "scan_completed": ScanOut,
    "scan_running": ScanOut,
    "scan_queued": ScanOut,
    "scan_cancelled": ScanOut,
    "discover_completed": ScanOut,
    "results_scan": ScanResultsPage,
    "results_discover": DiscoveryResultsPage,
    "error_public": ErrorResponse,
    "error_validation": ErrorResponse,
}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_fixture_matches_api_model(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    model = MODELS[name].model_validate(data)
    # Round-trip: nothing in the fixture is silently dropped (models ignore unknown keys by default).
    assert model.model_dump(mode="json", exclude_unset=True) == data


def test_every_fixture_is_covered():
    assert {p.stem for p in FIXTURES.glob("*.json")} == set(MODELS)


def test_fixtures_cover_each_lifecycle_state():
    statuses = {json.loads((FIXTURES / f"{n}.json").read_text(encoding="utf-8"))["status"] for n in MODELS if n.startswith(("scan_", "discover_"))}
    assert statuses == {"completed", "running", "queued", "cancelled"}
