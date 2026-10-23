"""Keep benchmarks/bench.py working: a tiny ground-truth run must classify perfectly."""

import importlib.util
from pathlib import Path

import pytest

BENCH = Path(__file__).parent.parent / "benchmarks" / "bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("netscope_bench", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
async def test_ground_truth_run_is_accurate(bench):
    servers, truth = await bench.build_fixture(3, 3, 2, 1, seed=1)
    try:
        run = await bench.one_run(truth, concurrency=10, rate=1000, timeout=0.2, fast_refusal=True)
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
    assert run["completed"] == run["probes"] == 9
    assert run["accuracy"] == 1.0
    assert run["false_filtered"] == 0
    assert run["confusion"]["timeout"] == {"inconclusive": 2}
