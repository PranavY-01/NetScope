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

ResultCallback = Callable[[R], None]

_DONE = object()


async def run_bounded(
    jobs: Iterable[J],
    fn: Callable[[J], Awaitable[R]],
    *,
    concurrency: int,
    limiter: RateLimiter,
    on_result: ResultCallback,
) -> None:
    """Run fn over jobs and hand each result to on_result the moment it completes.

    At most `concurrency` worker tasks ever exist, so in-flight work is bounded by construction, and
    jobs are pulled lazily. on_result is called synchronously in the worker with no await between
    fn returning and the call, so a cancellation can never drop a result that already exists.
    Cancelling this coroutine cancels and awaits every worker before it returns.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    job_iter = iter(jobs)

    async def worker() -> None:
        for job in job_iter:
            await limiter.acquire()
            on_result(await fn(job))

    workers = [asyncio.create_task(worker(), name=f"netscope-worker-{i}") for i in range(concurrency)]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


async def stream_results(start: Callable[[ResultCallback], Awaitable[None]]) -> AsyncIterator:
    """Adapt a callback-style run into an async iterator of results in completion order.

    Results the consumer has not yet received when it is cancelled are dropped. Callers that must keep
    every completed result across cancellation should use the callback form directly.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def runner() -> None:
        try:
            await start(queue.put_nowait)
        finally:
            queue.put_nowait(_DONE)

    task = asyncio.create_task(runner(), name="netscope-stream")
    try:
        while (item := await queue.get()) is not _DONE:
            yield item
        await task  # re-raise a failure from the run
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass
class RunTimer:
    """Wall-clock timestamps for the report, monotonic duration for the measurement."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _start: float = field(default_factory=time.perf_counter)

    def finish(self) -> tuple[datetime, float]:
        return datetime.now(timezone.utc), round((time.perf_counter() - self._start) * 1000, 3)
