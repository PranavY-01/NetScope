# NetScope architecture

This describes NetScope V2 as implemented. The [design log](NetScope_V1_Memory_Capsule.md) keeps the original V1 specification, measurements and decision history.

## Layers

```text
┌──────────────────────┐        ┌──────────────────────┐
│ Browser UI           │  HTTP  │ FastAPI app          │  netscope/api/
│ frontend/ (React+TS) │ ─────▶ │ validation, errors,  │
└──────────────────────┘        │ request hardening    │
                                └──────────┬───────────┘
┌──────────────────────┐                   │ approved plan
│ CLI (Typer + Rich)   │                   ▼
│ netscope/cli.py      │        ┌──────────────────────┐      ┌──────────────────┐
└──────────┬───────────┘        │ Job manager          │ ───▶ │ SQLite store     │
           │                    │ netscope/jobs.py     │      │ netscope/store.py│
           │                    └──────────┬───────────┘      └──────────────────┘
           ▼                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ Engine (no HTTP, no Rich, no database)                                        │
│ config.py  guardrails.py  resolver.py  probe.py  rate_limit.py  scheduler.py │
│ scanner.py  discovery.py  estimate.py  models.py  output.py                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

The engine is the only place that probes the network or decides scope. The CLI and the API are two thin callers of the same functions. The frontend never touches the network: it only calls the API.

## Engine

| Module | Responsibility |
|---|---|
| `config.py` | Pure parsing of ports, scan targets and CIDRs; the `ScanConfig` / `DiscoveryConfig` Pydantic models and their bounds. |
| `guardrails.py` | Address-class table, host cap, allowlist parsing, the acknowledgement check, `preflight_addresses`. |
| `resolver.py` | IPv4-only DNS behind an injectable `Resolver`; deduplication; structured errors (`dns:EAI_*`, `dns:ipv6_only`, `dns:no_ipv4`, `dns:timeout`). |
| `probe.py` | One TCP connect with an injectable `ConnectFn`; deadline and monotonic timing; classification of the outcome to a port state; Windows `SIO_TCP_INITIAL_RTO` mitigation. |
| `rate_limit.py` | `RateLimiter` (absolute slots 1/rate apart) and `ChainedLimiter` (the slowest of several). |
| `scheduler.py` | `run_bounded`: a fixed pool of `concurrency` workers pulling jobs lazily; each result goes synchronously to `on_result`. `stream_results` adapts that to an async iterator. |
| `scanner.py` / `discovery.py` | `prepare_*` (preflight → plan), `run_*` (execute a plan), `build_*_report`, and convenience wrappers. |
| `estimate.py` | Duration bounds from the plan size and settings. |
| `models.py` / `output.py` | Result and report models; JSON/CSV serialisation; atomic file writes. |

### Preflight and plans

`prepare_scan(config, resolver)` and `prepare_discovery(config)` are the only ways to get a plan. They run these steps in order:

1. Pure parsing of the target (no network).
2. **The authorization acknowledgement.** An unacknowledged request stops here, before any DNS query.
3. Resolution (scans of hostnames only), IPv4 only, all answers kept.
4. The host cap, counted before any CIDR is enumerated.
5. The address policy on every concrete address.
6. The allowlist.

The resulting `ScanPlan` / `DiscoveryPlan` **pins the addresses**. `run_scan` probes `plan.ips`, never a fresh resolution, so DNS changes after the check cannot widen the scope.

### Execution

`run_scan(plan, on_result, connect, limiter)` and `run_discovery(...)`:
- start exactly `concurrency` worker tasks;
- acquire a rate slot before each probe;
- call `on_result` synchronously as each probe (or, for discovery, each host verdict) completes.

Because there is no `await` between a result existing and `on_result` receiving it, cancellation can never drop a finished result. Callers that want partial results on Ctrl-C or cancel collect them in `on_result`. That is what the CLI and the job manager both do.

## CLI

`cli.py` wires Typer options into the config models, runs `prepare_*` then `run_*` in `asyncio.run`, renders with Rich (`console.py`), and maps outcomes to exit codes. Ctrl-C makes `asyncio.run` cancel the run. The CLI then builds a report from what `on_result` collected, marks it interrupted, and exits 130.

`netscope serve` starts the web application (lazy imports, so the CLI works without the `web` extra).

## Web backend

### API (`netscope/api/`)

`app.py` holds the routes, middleware and error mapping; `schemas.py` holds the request/response models and the server's size bounds. A scan request becomes a `ScanConfig`/`DiscoveryConfig` and goes through the engine's `prepare_*`. The API adds bounds but never re-implements policy. The full contract is in [API.md](API.md).

### Job manager (`netscope/jobs.py`)

**Limits.** At most `max_running` jobs run (an `asyncio.Semaphore`), plus `max_queued` waiting. Beyond that, `submit` raises `CapacityError` (HTTP 429). The slot is reserved before any `await`, so concurrent submissions cannot overshoot.

**Rate.** Every job's limiter is chained with one server-wide `RateLimiter(global_rate)`, so several scans together never exceed the global start rate.

**Result handling.** Results go from `on_result` into a buffer. A flusher task writes the buffer to SQLite in batches every `flush_interval_s`. It is stopped with an event, never cancelled mid-write, so no write can land after the final status.

**Lifecycle.**

```text
queued ──▶ running ──▶ completed
   │          ├──────▶ cancelled   (user cancel or server shutdown; partial results kept)
   │          └──────▶ failed      (engine error, recorded with a code; or server restart)
   └─────────────────▶ cancelled   (cancelled while waiting for a slot)
```

- **Every job ends in a terminal state:** errors are caught and recorded, and finalisation cannot be interrupted by a second cancel.
- **Shutdown:** shutdown cancels every job and waits for each to record its outcome.
- **Not durable:** jobs live in the process only. On startup, `ScanStore.recover_interrupted()` marks scans left `queued`/`running` by a crash as `failed` (`server_restart`), keeping the results already flushed.

### Persistence (`netscope/store.py`)

SQLite via the standard library, one connection guarded by a lock, called through `asyncio.to_thread` from the event loop. WAL journal mode and foreign keys are enabled.

| Table | Contents |
|---|---|
| `meta` | `schema_version` (currently 1). |
| `scans` | One row per scan: kind, original target, status, UTC timestamps, effective settings, resolved addresses, target errors, estimate, progress counters, error code/message, and the final report metadata. |
| `port_results` | One row per port probe (scan kind), linked by `scan_id`, `ON DELETE CASCADE`. |
| `host_results` | One row per host verdict (discovery kind) with its evidence as JSON, `ON DELETE CASCADE`. |

- **Stored once:** the target and settings live only on the scan row, and result rows hold only what varies.
- **Exports:** they rebuild the CLI's `ScanReport`/`DiscoveryReport` from these rows, so they match CLI output.
- **Database safety:**
  - `initialize()` creates the schema only in an empty database;
  - a foreign database or a different schema version is refused with a clear error and left untouched;
  - nothing ever drops or recreates tables.
- **Deletion:** `delete_scan` removes one finished scan and its rows only.

## Frontend (`frontend/`)

React 19 + TypeScript, built by Vite, with no UI framework and no router dependency (hash routes: `#/`, `#/new`, `#/scans`, `#/scans/<id>`).

| Module | Role |
|---|---|
| `src/api.ts` | Typed fetch client. Adds the `X-NetScope-Client` header to mutations and turns error bodies into `ApiError`. |
| `src/types.ts` | Mirrors the API models. |
| `src/hooks.ts` | `useResource`: fetching with polling while a condition holds. |
| `src/pages/*` | The pages: Dashboard, New scan, Scan detail, History. |
| `src/components/*` | Badges, progress bar, alerts, results tables. |

- **Progress:** progress bars are determinate only when the API reports a known total.
- **Cancellation:** the UI shows "Cancelling…" until the server reports a terminal status.
- **Serving:** in production, `netscope serve` serves `frontend/dist`. In development, Vite proxies `/api` to the backend, so the browser is always same-origin.

## Key decisions

| Decision | Reason |
|---|---|
| One engine for CLI and API | No second scanner and no second, weaker policy. |
| Acknowledgement before DNS | An unacknowledged request should cause no network activity at all (review finding 6.2). |
| Shared space (100.64.0.0/10) as its own class with its own override | It can belong to other ISP customers; neither "private" nor "public" describes it (review finding 6.1). |
| Plans pin resolved addresses | Scope checks apply to exactly what is probed (DNS-rebinding guard). |
| Synchronous `on_result` | A completed result can never be lost to cancellation (found as a race in V1 testing). |
| `filtered` never emitted | A TCP connect gives no reliable evidence of filtering; timeouts stay `inconclusive`. |
| In-process jobs, SQLite, polling | Enough for a local single-user tool; no Redis/Celery/WebSockets to operate. Not durable, and documented as such. |
| Loopback-only server, no accounts | The API can trigger scans; it must not be reachable by others. Remote use would need authentication and isolation designed first. |
