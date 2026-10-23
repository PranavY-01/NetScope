"""Bounded execution: a fixed pool of workers shares one job iterator and one rate limiter."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypeVar

from netscope.rate_limit import RateLimiter

J = TypeVar("J")
R = TypeVar("R")

_WORKER_DONE = object()


@dataclass
class _WorkerFailure:
    exc: BaseException


async def run_bounded(
    jobs: Iterable[J],
    fn: Callable[[J], Awaitable[R]],
    *,
    concurrency: int,
    limiter: RateLimiter,
) -> AsyncIterator[R]:
    """Yield fn(job) results as they complete.

    At most `concurrency` worker tasks ever exist, whatever the number of jobs, so in-flight work is
    bounded by construction. Jobs are pulled lazily. Closing the generator (or cancelling its
    consumer) cancels and awaits every worker before returning.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    job_iter = iter(jobs)
    queue: asyncio.Queue = asyncio.Queue()

    async def worker() -> None:
        try:
            for job in job_iter:
                await limiter.acquire()
                queue.put_nowait(await fn(job))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # surfaced to the consumer, never swallowed
            queue.put_nowait(_WorkerFailure(exc))
        finally:
            queue.put_nowait(_WORKER_DONE)

    workers = [asyncio.create_task(worker(), name=f"netscope-worker-{i}") for i in range(concurrency)]
    try:
        remaining = len(workers)
        while remaining:
            item = await queue.get()
            if item is _WORKER_DONE:
                remaining -= 1
            elif isinstance(item, _WorkerFailure):
                raise item.exc
            else:
                yield item
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


@dataclass
class RunTimer:
    """Wall-clock timestamps for the report, monotonic duration for the measurement."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _start: float = field(default_factory=time.perf_counter)

    def finish(self) -> tuple[datetime, float]:
        return datetime.now(timezone.utc), round((time.perf_counter() - self._start) * 1000, 3)
