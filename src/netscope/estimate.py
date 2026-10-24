"""Duration estimates for a planned run. Pure arithmetic on the plan; no network activity."""

import math

from pydantic import BaseModel, ConfigDict

ASSUMPTIONS = (
    "Probe starts are spaced 1/rate apart across the whole run (the rate limiter), so the minimum is "
    "(probes - 1) / rate. It assumes every probe answers instantly.",
    "Probes that get no answer hold a concurrency slot until the timeout. The 'all time out' figure "
    "assumes every probe does, so the slower of the rate limit and the concurrency limit decides the time.",
    "Real runs fall between the two. Event-loop scheduling, DNS, and timer granularity "
    "(about 15.6 ms ticks on Windows with Python 3.11/3.12) add small deviations.",
    "Raising concurrency does not speed up a run whose pace is set by the rate limit.",
)


class DurationEstimate(BaseModel):
    """Bounds on run time from the plan's size and settings. An estimate, never a guarantee."""

    model_config = ConfigDict(frozen=True)

    probes: int
    min_seconds: float
    all_timeout_seconds: float
    bottleneck_if_all_time_out: str
    assumptions: tuple[str, ...] = ASSUMPTIONS


def estimate_duration(probes: int, *, rate: float, concurrency: int, timeout: float) -> DurationEstimate:
    if probes <= 0:
        return DurationEstimate(probes=0, min_seconds=0.0, all_timeout_seconds=0.0, bottleneck_if_all_time_out="none")
    rate_bound = (probes - 1) / rate
    # With every probe timing out, each holds a slot for `timeout`: waves of `concurrency` probes.
    slot_bound = (math.ceil(probes / concurrency) - 1) * timeout
    bottleneck = "rate limit" if rate_bound >= slot_bound else "concurrency limit"
    return DurationEstimate(
        probes=probes,
        min_seconds=round(rate_bound, 3),
        all_timeout_seconds=round(max(rate_bound, slot_bound) + timeout, 3),
        bottleneck_if_all_time_out=bottleneck,
    )


def describe(estimate: DurationEstimate) -> str:
    if estimate.probes == 0:
        return "nothing to probe"
    return (
        f"estimated duration: at least {estimate.min_seconds:.1f}s; about {estimate.all_timeout_seconds:.1f}s "
        f"if every probe times out ({estimate.bottleneck_if_all_time_out} bound)"
    )
