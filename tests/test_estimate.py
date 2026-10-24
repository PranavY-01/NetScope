import pytest

from netscope.estimate import describe, estimate_duration

pytestmark = pytest.mark.usefixtures("no_sockets")


def test_defaults_are_rate_bound():
    # 254 hosts x 3 ports at the defaults (rate 50/s, concurrency 100, timeout 1 s).
    est = estimate_duration(762, rate=50, concurrency=100, timeout=1.0)
    assert est.min_seconds == pytest.approx(761 / 50)
    assert est.bottleneck_if_all_time_out == "rate limit"
    assert est.all_timeout_seconds == pytest.approx(761 / 50 + 1.0)


def test_more_concurrency_does_not_help_a_rate_bound_run():
    slow = estimate_duration(762, rate=50, concurrency=100, timeout=1.0)
    wide = estimate_duration(762, rate=50, concurrency=1000, timeout=1.0)
    assert slow.min_seconds == wide.min_seconds
    assert slow.all_timeout_seconds == wide.all_timeout_seconds


def test_low_concurrency_becomes_the_bottleneck_when_probes_time_out():
    # Benchmark shape: 200 probes, concurrency 10, timeout 1 s, fast rate.
    est = estimate_duration(200, rate=5000, concurrency=10, timeout=1.0)
    assert est.bottleneck_if_all_time_out == "concurrency limit"
    assert est.all_timeout_seconds == pytest.approx(19 * 1.0 + 1.0)
    assert est.min_seconds == pytest.approx(199 / 5000, abs=1e-3)  # rounded to milliseconds


def test_nothing_to_probe():
    est = estimate_duration(0, rate=50, concurrency=100, timeout=1.0)
    assert est.min_seconds == est.all_timeout_seconds == 0
    assert describe(est) == "nothing to probe"


def test_single_probe():
    est = estimate_duration(1, rate=50, concurrency=100, timeout=2.0)
    assert est.min_seconds == 0
    assert est.all_timeout_seconds == pytest.approx(2.0)


def test_description_says_it_is_an_estimate():
    text = describe(estimate_duration(100, rate=50, concurrency=100, timeout=1.0))
    assert text.startswith("estimated duration: at least")
    assert "if every probe times out" in text


def test_estimate_lists_its_assumptions():
    assert len(estimate_duration(10, rate=50, concurrency=100, timeout=1.0).assumptions) >= 3
