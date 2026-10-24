"""Global probe-start rate limiter, separate from the concurrency limit."""

import asyncio
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    """Assigns acquisitions slots 1/rate seconds apart, with no burst allowance.

    One instance is shared by every worker in a run, so the limit is scan-wide rather than per host.
    Slots are absolute, so the long-run rate is exact. An individual start can precede its slot by up
    to two ticks of the event loop's clock: the loop computes the deadline from its own clock and may
    fire a timer up to one tick early. On Windows with Python 3.11/3.12 that clock is GetTickCount64
    (15.6 ms ticks, so up to ~31 ms); elsewhere, and from Python 3.13 on Windows, it is sub-millisecond.
    Waiting out the remainder exactly would mean busy-spinning.
    """

    def __init__(
        self,
        rate: float,
        *,
        # perf_counter is monotonic; time.monotonic ticks every 15.6 ms on Windows, too coarse for 20 ms slots.
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not rate > 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next_slot: float | None = None

    async def acquire(self) -> None:
        # Reserve a slot synchronously (no await between read and write), then wait for it.
        now = self._clock()
        slot = now if self._next_slot is None or self._next_slot < now else self._next_slot
        self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            await self._sleep(delay)


class ChainedLimiter:
    """Acquire each limiter in turn, so the effective start rate is the slowest of them.

    The web server chains a job's own limiter with one server-wide limiter, so concurrent jobs can
    never add up to more than the global cap.
    """

    def __init__(self, *limiters: RateLimiter) -> None:
        self.limiters = limiters

    async def acquire(self) -> None:
        for limiter in self.limiters:
            await limiter.acquire()
