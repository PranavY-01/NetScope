# NetScope

An evidence-based IPv4 TCP port scanner and host-discovery tool for authorized networks. It has a command-line interface and a local web application, both built on one reusable async Python engine.

> **Status: V2 in development (`2.0.0.dev0`).**
> - **What V2 adds:** the local web app (FastAPI backend, React + TypeScript UI, SQLite history), a separate opt-in for shared address space, and an authorization check that now runs before any DNS lookup.
> - **Where it has been tested:** only locally, on Windows 11 with Python 3.12.0 and Node 24. Linux and macOS are expected to work but have not been verified for V2. The GitHub Actions matrix (Windows + Ubuntu, Python 3.11–3.13, plus a frontend job) will report them once this branch is pushed.

---

## Contents

- [What it does](#what-it-does)
- [Responsible use](#responsible-use)
- [Install](#install)
- [Command line](#command-line)
- [Web application](#web-application)
- [Reading the results](#reading-the-results)
- [Safety guardrails](#safety-guardrails)
- [Speed: rate, concurrency and timeout](#speed-rate-concurrency-and-timeout)
- [Output and exit codes](#output-and-exit-codes)
- [Architecture](#architecture)
- [Testing](#testing)
- [Platform notes](#platform-notes)
- [Not included](#not-included)
- [Documentation](#documentation)
- [License](#license)

## What it does

- **Port scan:** TCP connect probes against one IPv4 address or hostname. Every IPv4 address the name resolves to is scanned and reported separately.
- **Host discovery:** TCP connect probes across an IPv4 CIDR. Each address gets an evidence-based verdict with its per-probe evidence.
- **Interfaces:** the command line (Rich tables, JSON or CSV) and a local web UI (dashboard, scan form, live progress, results, history, exports).
- **Shared rules:** both use the same engine and the same scope policy. The web API cannot apply a weaker policy than the CLI.

NetScope uses ordinary `connect()` calls, so it needs no administrator rights and sends no crafted packets.

## Responsible use

Only scan systems and networks that you own or have explicit permission to test. Scanning without authorization may be illegal and may breach acceptable-use policies.

The guardrails reduce the chance of accidental misuse: the authorization acknowledgement, host cap, address-range policy and allowlist. They cannot prove that you are authorized.

## Install

Requires Python 3.11+. The web UI additionally needs Node.js (developed with Node 24) to build.

```powershell
git clone https://github.com/PranavY-01/NetScope.git
cd NetScope
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"               # CLI + web backend + test tools
```

The `web` extra (FastAPI, uvicorn) is optional. `pip install -e .` alone gives you the CLI.

## Command line

```text
netscope scan TARGET [OPTIONS]        # one IPv4 address or hostname
netscope discover NETWORK [OPTIONS]   # an IPv4 CIDR, e.g. 192.168.1.0/24
netscope serve [OPTIONS]              # the local web application
```

```powershell
netscope scan 127.0.0.1 --yes-authorized
netscope scan router.lan -p 22,80,8000-8010 --yes-authorized --json
netscope discover 192.168.1.0/24 --yes-authorized -o hosts.csv
netscope discover 100.64.10.0/28 --yes-authorized --allow-shared
```

| Option | Default | Meaning |
|---|---|---|
| `-p`, `--ports` / `--discovery-ports` | `22,80,443` | Ports, lists and inclusive ranges. Malformed or inverted specs are rejected, never widened. |
| `-t`, `--timeout` | `1.0` | TCP connect timeout per probe, in seconds. |
| `-c`, `--concurrency` | `100` | Maximum probes in flight. |
| `-r`, `--rate` | `50` | Maximum probe starts per second across the whole run. |
| `--max-hosts` | `256` | Host cap. Raising it is logged as a warning (ceiling 65,536). |
| `--allow-public` | off | Permit globally routable targets. |
| `--allow-shared` | off | Permit shared address space `100.64.0.0/10` (carrier-grade NAT). Not implied by `--allow-public`. |
| `--allowlist FILE` | none | Permitted IPv4 addresses/CIDRs; any target outside it rejects the whole run. |
| `--yes-authorized` | **required** | Confirms you are authorized to test every target. Checked before any DNS lookup. |
| `-f`, `--format json\|csv`, `--json`, `--csv` | human table | Machine-readable output. Conflicting choices are rejected. |
| `-o`, `--output FILE` | stdout | Write the report atomically; the format can be inferred from `.json`/`.csv`. |
| `-v`, `--verbose` / `-q`, `--quiet` | info | Logging detail. Logs always go to stderr. |

Each run logs an **estimated duration**: a lower bound from the rate limit, and a figure for the case where every probe times out. See [Speed](#speed-rate-concurrency-and-timeout).

The [User Guide](docs/USER_GUIDE.md) walks through every option step by step.

## Web application

```powershell
cd frontend
npm ci
npm run build           # produces frontend/dist
cd ..
netscope serve          # http://127.0.0.1:8765
```

- **What `serve` runs:** the API and the built UI on `127.0.0.1:8765`, with scan history stored in `./netscope.db` (change it with `--db`).
- **Loopback only:** `serve` refuses to bind anywhere but loopback, because the API has no user accounts.
- **API reference:** interactive docs are at `/api/docs`; the contract is in [docs/API.md](docs/API.md).
- **UI development:** run `netscope serve` and `npm run dev` in `frontend/`, then open `http://127.0.0.1:5173`. The dev server proxies `/api` to the backend. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

The UI has five parts:
- **Dashboard:** stored, completed, running/queued and cancelled/failed counts, plus recent scans, all from the database.
- **New scan:** the target, ports, timing and limits, the scope overrides, an optional inline allowlist, a required authorization acknowledgement, and an effective-settings preview. The server re-validates everything.
- **Scan detail:**
  - live progress (indeterminate when the total is unknown, never an invented percentage) and cancellation;
  - effective settings and the duration estimate with its assumptions;
  - result tables with state filtering and sorting, partial-result and error banners;
  - JSON/CSV export and delete.
- **History:** search, status and type filters, and pagination.

Scans run in a bounded in-process job manager: 2 running and 8 queued by default, with a server-wide cap of 500 probe starts per second. Jobs are **not durable**. If the server stops, running scans are marked cancelled (graceful stop) or failed (crash). Results already recorded are kept.

## Reading the results

Results are **observations from the scanner's network position at probe time**. They are not facts about a device.

| Port state | Meaning |
|---|---|
| `open` | The TCP connection attempt succeeded from where NetScope runs. |
| `closed` | The attempt was actively refused (consistent with nothing listening, or a device refusing on its behalf). |
| `inconclusive` | Not enough evidence: no answer before the timeout, or a host/network-unreachable signal. **A timeout does not prove a host is offline or a port is filtered.** |
| `error` | A local or operational failure prevented classification (a code and message are kept). |
| `filtered` | Reserved. **Never emitted**: a TCP connect alone gives no reliable evidence of filtering. |

| Discovery verdict | Rule |
|---|---|
| `responsive` | At least one probe got a TCP-level answer: a connection or a refusal. This is network evidence, **not proof that a specific device exists**. |
| `error` | No TCP answer, and at least one probe failed locally, so absence cannot be asserted. |
| `no_response` | Every probe was inconclusive. Absence of evidence, **not proof the host is down**. |

**Middleboxes matter.**
- Firewalls, NAT gateways, proxies and load balancers can answer on a host's behalf. For example, a firewall that sends resets makes ports look `closed`, and a load balancer can make an address look `open`.
- Middleboxes can also silently drop probes, which turns into `inconclusive` or `no_response`.

**Service names are never shown.** A port number alone does not prove which service is listening.

## Safety guardrails

Applied identically to CLI and API requests, in this order, before any probe socket is opened:

1. **Input validation:** port spec, target or CIDR syntax, numeric bounds, output flags, allowlist syntax. This is pure parsing, with no network activity.
2. **Authorization acknowledgement** (`--yes-authorized`, or the checkbox / `"authorized": true` in the web app). It is checked **before any DNS lookup**: an unacknowledged request causes no network activity at all.
3. **Name resolution** (port scans only), IPv4 answers only.
4. **Host cap:** 256 probe-target hosts by default, counted from the CIDR size before anything is enumerated.
5. **Address policy, checked on every resolved address:**

   | Class | Ranges | Policy |
   |---|---|---|
   | loopback | 127.0.0.0/8 | allowed |
   | private | 10/8, 172.16/12, 192.168/16 | allowed |
   | shared | 100.64.0.0/10 (carrier-grade NAT) | needs `--allow-shared` |
   | public | everything not listed | needs `--allow-public` |
   | link-local, multicast, `0/8`, broadcast, `240/4`, documentation/benchmarking/IETF ranges | — | always refused |

6. **Allowlist:** all-or-nothing.

**How the overrides combine:**
- Each override covers only its own class: `--allow-public` does not authorize shared space, and `--allow-shared` does not authorize public space.
- No override bypasses the cap, the allowlist, the acknowledgement, or the rate and timeout limits.
- The scan plan pins the resolved addresses, and the engine probes exactly those, so a name that later resolves elsewhere cannot widen the scope.

The web server adds its own hardening. Details are in [docs/SECURITY.md](docs/SECURITY.md).
- **Binding:** loopback only.
- **Request protection:**
  - a Host-header allowlist (against DNS rebinding);
  - a required `X-NetScope-Client` header on mutations (against CSRF);
  - no CORS;
  - a 64 KB body cap.
- **Response headers:** a strict Content-Security-Policy, plus tighter per-scan bounds than the CLI.

## Speed: rate, concurrency and timeout

- **Rate** caps how many probes *start* per second across the whole run (default 50/s).
- **Concurrency** caps how many are *in flight* at once (default 100).
- **Timeout** is how long one probe may wait (default 1 s).

**The rate limit usually sets the pace.** At 50 starts/s, 762 probes (a /24 × 3 ports) need at least (762 − 1) / 50 ≈ 15.2 s, however high concurrency is set.

**Concurrency only becomes the bottleneck when probes time out.** Each silent probe holds a slot for the full timeout. With all of them timing out, throughput is at most concurrency / timeout. The defaults keep 100 slots for 50 starts/s × 1 s, so concurrency is not the limit.

The run's **estimate** reports both figures, the rate-bound minimum and the "every probe times out" duration, and labels them as estimates. Real runs fall in between.

The V1 benchmark measured these effects on a loopback ground-truth fixture: accuracy was 100% at every setting, concurrency above rate × timeout made no difference, and rates above about 200/s inflated measured latency. The tables and limitations are in the [capsule](docs/NetScope_V1_Memory_Capsule.md#phase-8--benchmark-and-tune-done-not-committed).

## Output and exit codes

- **JSON:** a metadata envelope, including the effective settings with `allow_public`/`allow_shared`, UTC timestamps and the `interrupted` flag, plus results, DNS target errors and a per-state summary.
- **CSV:** one row per probe. The column lists are in the [User Guide](docs/USER_GUIDE.md).
- **Web exports:** the same JSON and CSV, rebuilt from stored results.
- **Ctrl-C:** the CLI stops, emits every completed result, marks the report partial, and exits `130`.

| Code | Meaning |
|---|---|
| `0` | Completed normally. |
| `1` | Completed, but at least one result is `error` (including a DNS failure). |
| `2` | Invalid arguments, configuration, or scope rejection. Nothing was probed. |
| `3` | Unexpected internal failure. |
| `130` | Interrupted by Ctrl-C; partial report emitted. |

## Architecture

```text
Browser UI (React)  ──HTTP──▶  FastAPI (api/)  ──▶  job manager (jobs.py)  ──▶  engine  ──▶  TCP probes
                                     │                       │
CLI (cli.py) ───────────────────────────────────────────────▶ engine (scanner.py, discovery.py)
                                     │                       │
                         scope policy (config.py, guardrails.py)    SQLite history (store.py)
```

- **One engine, two callers:** the engine (config, guardrails, resolver, probe, rate limiter, scheduler, scanner, discovery, output) knows nothing about HTTP, Rich or the database. The CLI and the API call the same `prepare_*` / `run_*` / `build_*_report` functions.
- **Where results go:** each completed result is handed over through a synchronous callback the moment it exists, so cancellation never loses finished work.
- **More detail:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Testing

```powershell
python -m pytest                          # engine, CLI, API, job manager, store, contract tests
python -m pytest -m "not integration"     # skip tests that open real loopback sockets
cd frontend; npm test                      # UI component tests (Vitest + Testing Library)
cd frontend; npm run build                 # type-check + production build
```

- **No automated test scans a public target.** Tests use injected resolvers, scripted connect functions and loopback listeners.
- **Frontend fixtures are real API responses** (generated by `tools/make_frontend_fixtures.py`), and a Python contract test fails if they drift from the backend models.

## Platform notes

- **Windows refusal delay:** by default, Windows retries the SYN after a RST, so refused ports take about 2 s. NetScope disables that per probe socket (`SIO_TCP_INITIAL_RTO`), and refusals then report in milliseconds. A single lost SYN is then not retried and shows up as `inconclusive`.
- **Windows timer resolution:** on Python 3.11/3.12, asyncio's clock ticks every 15.6 ms, so timeouts and rate slots can fire up to about 31 ms early. Over a whole run the rate is still exact.
- **Linux and macOS** report refusals immediately and need no mitigation.

## Not included

These are out of scope:
- IPv6
- banner grabbing / service detection
- raw-packet (SYN) scanning, ICMP or ARP discovery
- retries or baselines that could justify `filtered`
- user accounts, multi-user or remote deployment
- distributed workers
- durable job queues

## Documentation

- [User Guide](docs/USER_GUIDE.md): CLI and web UI, step by step.
- [Architecture](docs/ARCHITECTURE.md): layers, data flow, job lifecycle, persistence.
- [Security and scope](docs/SECURITY.md): policy, ordering, server hardening, limitations.
- [API](docs/API.md): the HTTP contract.
- [Development](docs/DEVELOPMENT.md): running each component, tests, fixtures, CI.
- [Design log](docs/NetScope_V1_Memory_Capsule.md): the original V1 specification, measurements and decision history.

## License

**No license has been chosen yet.** Until the repository owner adds one, default copyright applies and others have no license to reuse the code. This is an open owner decision.

NetScope was conceptually inspired by the beginner `simple-port-scanner` project in `CarterPerez-dev/Cybersecurity-Projects` (AGPL-3.0). No code was copied from it. That project's license does not determine NetScope's.
