"""Bounded, in-process scan job manager for the web application.

Takes an approved plan (every guardrail already passed), runs it through the same engine entry
points as the CLI, streams results into the store, and always drives the job to a terminal status.

Jobs live in this process only: a crash or restart loses running jobs. Results flushed before that
point stay in the database and the scan is marked failed on the next start (see
ScanStore.recover_interrupted). There is no durable queue and no resume.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from netscope.discovery import DiscoveryPlan, build_discovery_report, run_discovery
from netscope.estimate import estimate_duration
from netscope.models import HostResult, PortResult
from netscope.probe import ConnectFn, make_tcp_connect
from netscope.rate_limit import ChainedLimiter, RateLimiter
from netscope.scanner import ScanPlan, build_scan_report, run_scan
from netscope.scheduler import RunTimer
from netscope.store import ScanStore

log = logging.getLogger(__name__)

ConnectFactory = Callable[[bool], ConnectFn]


class CapacityError(Exception):
    """Too many scans are queued or running."""


class ShuttingDownError(Exception):
    """The server is stopping and accepts no new scans."""


@dataclass
class _Job:
    id: str
    kind: str
    plan: ScanPlan | DiscoveryPlan
    total: int
    status: str = "queued"
    completed: int = 0
    buffer: list[Any] = field(default_factory=list)
    task: asyncio.Task | None = None
    cancel_requested: bool = False

    def request_cancel(self) -> bool:
        # Cancel the task at most once: a second cancel could interrupt the final status write.
        if self.cancel_requested or self.task is None or self.task.done():
            return False
        self.cancel_requested = True
        self.task.cancel()
        return True

    def add(self, result: Any) -> None:
        # Called synchronously by the engine as each result completes: nothing can be lost to cancellation.
        self.buffer.append(result)
        self.completed += 1


@dataclass(frozen=True)
class JobLimits:
    max_running: int = 2
    max_queued: int = 8
    global_rate: float = 500.0
    flush_interval_s: float = 0.25
    flush_batch: int = 250


def plan_settings(plan: ScanPlan | DiscoveryPlan) -> dict[str, Any]:
    """The effective, validated settings of a plan, as stored with the scan and shown to the user."""
    config = plan.config
    settings = {
        "ports": list(config.ports),
        "timeout_s": config.timeout,
        "concurrency": config.concurrency,
        "rate_per_s": config.rate,
        "max_hosts": config.max_hosts,
        "allow_public": config.allow_public,
        "allow_shared": config.allow_shared,
        "allowlist": None if config.allowlist is None else [str(n) for n in config.allowlist],
        "fast_refusal_requested": config.fast_refusal,
    }
    if isinstance(plan, DiscoveryPlan):
        settings["network"] = str(config.network)
        settings["requested_hosts"] = plan.requested_hosts
    settings["requested_probes"] = plan.requested_probes
    return settings


class JobManager:
    def __init__(
        self,
        store: ScanStore,
        *,
        limits: JobLimits = JobLimits(),
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self.store = store
        self.limits = limits
        self._connect_factory = connect_factory
        self._jobs: dict[str, _Job] = {}
        self._running = asyncio.Semaphore(limits.max_running)
        self._global_limiter = RateLimiter(limits.global_rate)
        self._closing = False

    # ---- queries -------------------------------------------------------------------------------

    def live(self, scan_id: str) -> _Job | None:
        return self._jobs.get(scan_id)

    @property
    def active_count(self) -> int:
        return len(self._jobs)

    # ---- lifecycle -----------------------------------------------------------------------------

    async def submit(self, plan: ScanPlan | DiscoveryPlan) -> str:
        """Record an approved plan and schedule it. Raises CapacityError or ShuttingDownError."""
        if self._closing:
            raise ShuttingDownError("the server is shutting down")
        if len(self._jobs) >= self.limits.max_running + self.limits.max_queued:
            raise CapacityError(
                f"{len(self._jobs)} scans are already queued or running (limit "
                f"{self.limits.max_running} running + {self.limits.max_queued} queued); try again when one finishes"
            )
        kind = "scan" if isinstance(plan, ScanPlan) else "discover"
        config = plan.config
        if isinstance(plan, ScanPlan):
            target, total = config.target, plan.requested_probes
            resolved, target_errors = [str(ip) for ip in plan.ips], list(plan.target_errors)
        else:
            target, total = str(config.network), plan.requested_hosts
            resolved, target_errors = [], []
        estimate = estimate_duration(
            plan.requested_probes,
            rate=min(config.rate, self.limits.global_rate),
            concurrency=config.concurrency,
            timeout=config.timeout,
        )
        job = _Job(id=uuid.uuid4().hex, kind=kind, plan=plan, total=total)
        # Reserve the slot before awaiting, so concurrent submissions cannot overshoot the limit.
        self._jobs[job.id] = job
        try:
            await asyncio.to_thread(
                self.store.create_scan,
                scan_id=job.id,
                kind=kind,
                target=target,
                settings=plan_settings(plan),
                resolved_ips=resolved,
                target_errors=target_errors,
                estimate=estimate.model_dump(mode="json"),
                total=total,
            )
        except BaseException:
            del self._jobs[job.id]
            raise
        job.task = asyncio.create_task(self._run(job), name=f"netscope-job-{job.id}")
        return job.id

    async def cancel(self, scan_id: str) -> bool:
        """Request cancellation. Returns False if the scan is not active. The terminal status is
        written by the job itself once the engine has stopped, never by this call."""
        job = self._jobs.get(scan_id)
        if job is None:
            return False
        job.request_cancel()
        return True

    async def shutdown(self, timeout: float = 15.0) -> None:
        """Stop accepting work, cancel every job, and wait for each to record its terminal status."""
        self._closing = True
        tasks = [job.task for job in self._jobs.values() if job.task is not None]
        for job in list(self._jobs.values()):
            job.request_cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                log.warning("%d scan job(s) did not stop within %ss; they will be marked failed on next start", len(pending), timeout)

    # ---- execution -----------------------------------------------------------------------------

    def _connect(self, fast_refusal: bool) -> ConnectFn:
        factory = self._connect_factory or make_tcp_connect
        return factory(fast_refusal)

    async def _flush(self, job: _Job) -> None:
        if not job.buffer:
            return
        batch, job.buffer = job.buffer, []
        completed = job.completed
        if job.kind == "scan":
            await asyncio.to_thread(self.store.add_port_results, job.id, batch, completed)
        else:
            await asyncio.to_thread(self.store.add_host_results, job.id, batch, completed)

    async def _flush_loop(self, job: _Job, stop: asyncio.Event) -> None:
        # Stopped with an event, never cancelled: cancelling mid-write would abandon an await whose
        # database write still lands later, after the scan had been marked finished.
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), self.limits.flush_interval_s)
            except TimeoutError:
                pass
            await self._flush(job)

    async def _engine(self, job: _Job) -> None:
        plan = job.plan
        limiter = ChainedLimiter(RateLimiter(plan.config.rate), self._global_limiter)
        connect = self._connect(plan.config.fast_refusal)
        if isinstance(plan, ScanPlan):
            await run_scan(plan, on_result=job.add, connect=connect, limiter=limiter)
        else:
            await run_discovery(plan, on_result=job.add, connect=connect, limiter=limiter)

    def _metadata(self, job: _Job, timer: RunTimer, interrupted: bool) -> dict[str, Any]:
        plan = job.plan
        if isinstance(plan, ScanPlan):
            metadata = build_scan_report(plan, [], timer=timer, interrupted=interrupted).metadata
            metadata = metadata.model_copy(update={"completed_probes": job.completed})
        else:
            metadata = build_discovery_report(plan, [], timer=timer, interrupted=interrupted).metadata
            metadata = metadata.model_copy(update={"completed_hosts": job.completed})
        return metadata.model_dump(mode="json")

    async def _run(self, job: _Job) -> None:
        status, error_code, error_message = "completed", None, None
        timer = RunTimer()
        stop_flushing = asyncio.Event()
        flusher: asyncio.Task | None = None
        try:
            async with self._running:
                await asyncio.to_thread(self.store.mark_running, job.id)
                job.status = "running"
                timer = RunTimer()
                flusher = asyncio.create_task(self._flush_loop(job, stop_flushing), name=f"netscope-flush-{job.id}")
                await self._engine(job)
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:  # recorded on the scan, never swallowed silently
            log.exception("scan %s failed", job.id)
            status, error_code, error_message = "failed", "engine_error", f"{type(exc).__name__}: {exc}"[:300]
        # From here on the job only records its outcome; refuse further cancellation so a late cancel
        # (or shutdown) cannot interrupt the final writes. No await separates this from the engine.
        job.cancel_requested = True
        try:
            if flusher is not None:
                stop_flushing.set()
                await flusher  # lets an in-flight write finish before the final status is written
            await self._flush(job)
            await asyncio.to_thread(
                self.store.finish,
                job.id,
                status=status,
                completed=job.completed,
                interrupted=status != "completed",
                metadata=self._metadata(job, timer, interrupted=status != "completed"),
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            # The row stays non-terminal; recover_interrupted() marks it failed on the next start.
            log.exception("could not record the final status of scan %s", job.id)
        finally:
            job.status = status
            self._jobs.pop(job.id, None)
