import asyncio
import time

import pytest

from netscope.rate_limit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.sleeps.append(round(delay, 9))
        self.now += delay


async def test_sequential_acquisitions_are_spaced():
    clock = FakeClock()
    limiter = RateLimiter(10, clock=clock, sleep=clock.sleep)
    starts = []
    for _ in range(5):
        await limiter.acquire()
        starts.append(round(clock.now, 9))
    assert starts == [0.0, 0.1, 0.2, 0.3, 0.4]


async def test_concurrent_acquisitions_get_distinct_slots():
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock, sleep=clock.sleep)

    async def no_advance(delay):
        clock.sleeps.append(round(delay, 9))

    limiter._sleep = no_advance
    await asyncio.gather(*(limiter.acquire() for _ in range(5)))
    assert sorted(clock.sleeps) == [0.25, 0.5, 0.75, 1.0]  # first acquisition does not sleep


async def test_idle_time_does_not_bank_a_burst():
    clock = FakeClock()
    limiter = RateLimiter(10, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    clock.now = 100.0
    await limiter.acquire()
    await limiter.acquire()
    assert clock.sleeps == [pytest.approx(0.1)]


@pytest.mark.parametrize("rate", [0, -1, float("nan")])
def test_rate_must_be_positive(rate):
    with pytest.raises(ValueError):
        RateLimiter(rate)


async def test_real_time_rate_is_respected():
    limiter = RateLimiter(200)
    start = time.perf_counter()
    await asyncio.gather(*(limiter.acquire() for _ in range(21)))
    assert time.perf_counter() - start >= 20 / 200 * 0.9
