"""Global probe-start rate limiter, separate from the concurrency limit."""

import asyncio
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    """Spaces successive acquisitions at least 1/rate seconds apart, with no burst allowance.

    One instance is shared by every worker in a run, so the limit is scan-wide rather than per host.
    """

    def __init__(
        self,
        rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
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
