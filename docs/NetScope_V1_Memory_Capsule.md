# NetScope V1 — Project Memory Capsule

> **Purpose of this file:** the all-in-one reference for NetScope's objective, design choices, pre-implementation decisions, safety rules, architecture, evaluation plan, and remaining questions. Use it as the source of truth when continuing this project in a new chat or returning to it later. Preserve the distinctions between **agreed decisions**, **recommended defaults**, and **open details**.

**Project:** NetScope  
**Phase:** V1 implementation — see **§20 (frozen decisions)** and **§21 (build log)** for current state  
**Version target:** V1  
**Primary implementation language:** Python  
**Primary interface:** installable command-line application (CLI)  
**Future integration:** FastAPI wrapper around the same reusable scan engine  
**Network scope in V1:** IPv4 only  

---

## 1. Project objective

Build **NetScope**, a modular, installable cybersecurity utility for TCP port scanning and evidence-based IPv4 host discovery. It began from the idea of a straightforward TCP scanner, inspired by the repository's beginner **simple-port-scanner** project, and is being planned as a more robust, testable, safer, extensible tool rather than a direct copy of the original implementation.

The reference project discussed was `CarterPerez-dev/Cybersecurity-Projects/PROJECTS/beginner/simple-port-scanner`. The inspected reference was a C++20/Boost.Asio asynchronous TCP-connect scanner. Its ideas were useful for understanding asynchronous scanning, but its behavior and code are not the specification for NetScope. The repository was noted as having an AGPL-3.0 license: **do not copy/reuse its code without reviewing and complying with the applicable license terms**. Conceptual inspiration is not the same as permission to copy code.

### V1 should deliver

- An installable Python package with a `netscope` console command.
- IPv4 TCP port scanning for a single IPv4 address or hostname.
- Support for hostnames that resolve to multiple IPv4 addresses: scan **all** resolved IPv4 addresses and report each result separately.
- IPv4 CIDR host discovery using configurable TCP-connect probes and evidence-based verdicts.
- Explicit scope guardrails for CIDR and active scanning.
- Structured port/host result models and JSON/CSV exports.
- Controlled concurrency, timeout handling, configurable rate limiting, and graceful Ctrl-C behavior that preserves and outputs partial results.
- Tests and repeatable benchmarks based on known ground truth.
- A scan engine that can be called by a future FastAPI service without depending on the CLI, console rendering, or command-line state.

### V1 is intentionally not

- A raw SYN scanner.
- A packet-crafting or stealth-scanning implementation.
- A banner-grabbing tool (deferred to V2).
- An IPv6 scanner (deferred to a later release).
- A tool that calls hosts “dead” solely because a TCP probe times out.
- A completed FastAPI service; V1 only needs an engine boundary that makes later wrapping straightforward.

NetScope is intended for systems and networks the operator owns or is explicitly authorized to test. The safety controls help reduce accidental misuse; they cannot prove authorization.

---

## 2. Current agreement snapshot

The following is the high-level checkpoint we aligned on:

- **Core stack:** IPv4-only Typer CLI using Rich for readable terminal output and Pydantic for validated/configurable data models, packaged for installation and structured so FastAPI can wrap the engine later.
- **Scanning:** five port-state labels: `open`, `closed`, `filtered`, `inconclusive`, and `error`.
- **Timeouts:** timeout remains `inconclusive` in V1. Do not upgrade a timeout to `filtered` based only on a timeout, and do not add retries or known-closed-port baseline comparison in V1.
- **Hostnames:** scan every resolved IPv4 address, not only the first result; report results by concrete IP.
- **Default ports:** `22,80,443` for ordinary scanning when ports are omitted.
- **Discovery:** TCP-connect, multi-probe host discovery over user-configurable probe ports, with a documented default of `22,80,443`.
- **Scope safety:** include all four guardrail categories: host-count cap, public-range restriction with an explicit override, authorization acknowledgement, and optional permitted-target allowlist.
- **Outputs:** JSON and CSV export; support both a format-selector style and shorthand flag style, with conflicting format choices rejected clearly.
- **Interrupts:** Ctrl-C should stop new work cleanly, cancel/stop outstanding work as safely as practical, and emit completed partial results.
- **Deferred:** banner grabbing, IPv6, and retry/baseline techniques for interpreting timeouts as filtered.

### Important wording correction

**“Typer + Rich + Pydantic is the agreed checkpoint architecture; argparse was considered and not chosen.”**

An earlier starter scaffold used `argparse`; it was a preliminary teaching scaffold from before the architecture checkpoint, not the selected final CLI stack. Do not treat that old scaffold as the canonical implementation. If it is reused, migrate it to the agreed stack and preserve the core/CLI separation.

---

## 3. Inspiration and lessons from the reference scanner

The reference repository was a C++20 project using Boost.Asio asynchronous TCP connects. The earlier review identified these relevant concepts and potential pitfalls:

- A TCP connect scanner can operate without raw-packet privileges and can be a sensible V1 default.
- Asynchronous work should have a deliberate concurrency limit and clear lifecycle/cancellation rules.
- A refused connection is evidence that something responded at the destination IP; it is not evidence that the host is absent.
- A connect timeout is ambiguous: it can mean packet loss, filtering, routing problems, or an unresponsive endpoint. It should not automatically mean the host is down or the port is filtered.
- Invalid or inverted port ranges must fail validation rather than accidentally expanding to all ports.
- A single-port input must scan only that port, not an unintended range.
- Banner collection needs its own deadline and cleanup behavior if it is ever introduced.
- Service names associated with port numbers are hints, not proof of which service is actually running.
- DNS names can return multiple addresses, and the tool must not silently discard valid IPv4 answers.
- Socket errors should not all be labelled `closed`; classify a known refusal separately and preserve unexpected failures as errors.

A limited local TCP experiment was discussed in the earlier project work: a temporary loopback listener accepted a connection, while selected unused loopback ports refused connections. This was not a successful build or full benchmark of the reference binary. **Do not claim that the reference project or the future NetScope implementation has been fully built, tested, or benchmarked unless that work is actually performed.**

---

## 4. V1 feature boundaries

### Included

1. Single target scanning: IPv4 literals and hostnames.
2. Port specifications: individual ports, comma-separated lists, and inclusive ranges, e.g. `22,80,443,8000-8010`.
3. IPv4 CIDR discovery using configured TCP-connect probes.
4. DNS resolution to all IPv4 answers, with per-IP results.
5. Five-state port result vocabulary.
6. Host discovery states grounded in probe evidence.
7. Scope validation and safety guardrails.
8. Moderate, bounded concurrency and a global probe-start rate limit.
9. Configurable connection timeout.
10. Rich terminal presentation and JSON/CSV exports.
11. Ctrl-C handling that retains completed results.
12. Unit, integration, and controlled benchmark coverage.

### Deferred

- Banner grabbing / service fingerprinting. Revisit in V2; if introduced, make it opt-in or otherwise explicit and give it a separate deadline so a slow banner read cannot occupy a scanner slot indefinitely.
- IPv6 support.
- Timeout retries and known-closed-port baseline comparisons to distinguish filtered from inconclusive.
- ICMP discovery and ARP/local-link discovery. These were discussed as possible future additions, not V1 requirements.
- A built-in FastAPI server. Keep the engine API-ready, but do not couple V1 to an HTTP server.

---

## 5. Port-state taxonomy and rules

The agreed result vocabulary has five states:

| State | Meaning in the result | V1 classification rule |
|---|---|---|
| `open` | A TCP connection was established. | A successful TCP connect is positive evidence that the port accepted a connection at probe time. |
| `closed` | The destination actively refused the TCP connection. | Classify explicit connection-refused conditions as closed, including the platform-specific Windows refusal code where relevant (commonly `winerror == 10061`). **[Superseded detail — see §20.3: asyncio's Proactor loop reports refusal as `winerror 1225`; both codes are mapped.]** |
| `filtered` | There is reliable evidence that a network/security policy is preventing the probe from reaching or receiving a normal response. | Do **not** infer this from timeout alone. With the V1 TCP-connect-only method and no retry/baseline analysis, this state may be rare or not emitted unless a sufficiently reliable, platform-aware signal is defined. The exact explicit-signal mapping remains an implementation detail to validate. **[Resolved — see §20.1: reserved, never emitted in V1.]** |
| `inconclusive` | The probe did not provide enough evidence to classify the port. | Timeout is `inconclusive`; no “filtered” upgrade based on timeout, retries, or baseline comparison in V1. |
| `error` | The probe could not be classified because of an unexpected local/runtime/socket/DNS or other operational error. | Preserve a machine-readable error code/type and a short human-readable message. Do not collapse arbitrary errors into `closed`. |

### Classification principles

- `closed` is not equivalent to “host down.” A refused TCP connection is itself evidence of reachability/response.
- Timeout is ambiguous and must remain `inconclusive` in V1.
- Preserve the actual error context, but avoid dumping uncontrolled stack traces into ordinary terminal output.
- Keep the distinction between a **port result** and a **host-discovery verdict**. A host can be responsive even when a specific port is closed.
- Do not claim to detect `filtered` unless the implementation has an explicit, documented basis for that classification.

---

## 6. Host-discovery verdict logic

Discovery is evidence-based and uses several TCP-connect probes, not a single Boolean “alive/dead” test.

### Agreed core rule

- If **any** discovery probe yields a TCP response, including an explicit connection refusal, mark the IP `responsive`.
- If every configured probe times out, mark it `no_response`—not “dead” and not automatically `filtered`.
- If resolution or a local/operational failure prevents the discovery operation from being performed, preserve an `error` result instead of disguising it as `no_response`.
- Retain per-probe evidence so callers can understand why the host verdict was chosen.

### Candidate host-discovery states

- `responsive`: a probe produced evidence of a TCP-level response (successful connect or clear connection refusal).
- `no_response`: the probes completed without positive response evidence, such as all timing out. This is an absence of observed evidence, not proof of host absence.
- `error`: the tool could not make a valid determination because a local, DNS, configuration, or operational error prevented the probes or resolution.

The precise boundary for partial probe errors (e.g. one port times out while another yields a local socket error) should be implemented and tested consistently. A sensible rule is: any positive TCP response wins as `responsive`; if none, report `error` when an operational failure prevents a meaningful assessment; otherwise report `no_response` when all probes simply fail to respond.

### Probe port configuration

- Discovery ports are **user-configurable**, with a documented default of `22,80,443`.
- A host counts as responsive if any configured probe yields positive TCP response evidence.
- Record which ports were probed and the evidence/outcome for each probe.
- A default list is a practical probe set, not a guarantee that every live host will respond. Hosts that filter those ports may receive `no_response` despite being online.

---

## 7. Scope guardrails and authorization model

Four guardrail categories are part of the agreed design:

1. **Host-count cap** for a CIDR scan.
2. **Public IPv4 restriction by default**, with an explicit override flag.
3. **Explicit authorization acknowledgement**.
4. **Optional allowlist file** defining permitted IPs/CIDRs.

The categories are agreed; some detailed policy choices still need to be written into the implementation spec and tests. The following are the current recommended defaults, not all separately locked by a user selection. **[Update: the detailed choices were confirmed in Phase 0 — see §20.]**

### 7.1 Host-count cap

- Recommended default: **256 target host addresses per scan**.
- Make the limit configurable in a future-proof way, but do not let a CLI user silently bypass the default without an explicit option and clear warning/documentation.
- Validate the CIDR and compute the effective target set **before** starting network activity.
- Count the actual addresses scheduled for probing, and document treatment of IPv4 network/broadcast addresses. A typical implementation should use the standard library's IPv4 network host iteration semantics rather than blindly probing the network and broadcast addresses of ordinary subnets.
- Reject oversized ranges early, with a clear error and no partial surprise scan.

### 7.2 Public, private, and special IPv4 ranges

Recommended classification policy:

- Treat globally routable IPv4 destinations as **public** and require an explicit public-target override (suggested flag: `--allow-public`).
- Do not confuse RFC1918 private address space with proof of authorization; private targets still require the acknowledgement and must pass all other checks.
- Explicitly classify loopback, link-local, unspecified, multicast, reserved, and broadcast addresses; do not let a vague “not RFC1918 = public” shortcut decide policy.
- Loopback should remain usable for safe local testing.
- Decide and document a strict default for link-local and other special-purpose targets before implementation; conservative recommendation is to reject special-purpose ranges other than loopback unless the operator explicitly selects a supported/tested use case.
- A public-target override must never bypass the host cap, input validation, authorization acknowledgement, or configured allowlist.

This policy needs unit tests covering representative addresses from RFC1918, loopback, link-local (`169.254.0.0/16`), globally routable space, multicast, unspecified, and reserved ranges. Use Python's `ipaddress` properties as a useful building block, but verify that the chosen property semantics cover the policy we actually intend.

### 7.3 Authorization acknowledgement

- Require explicit acknowledgement before active scanning (recommended flag: `--yes-authorized`) on **every scan**, not just CIDR scans. This is the recommended interpretation of the guardrail; ensure the final CLI help describes it clearly.
- Do not treat this flag as proof of authorization. It is a deliberate confirmation to reduce accidental scanning.
- Non-network validation/help commands should not require the acknowledgement.

### 7.4 Allowlist file

Recommended V1 format: plain-text file with one IPv4 address or CIDR per line; ignore blank lines and comment lines beginning with `#`; reject malformed entries with line-numbered errors. Example:

```text
# NetScope permitted lab ranges
127.0.0.1
192.168.10.0/24
10.20.0.15
```

- Suggested option: `--allowlist path/to/targets.txt`.
- Match both a single target and every address expanded from a CIDR against the permitted entries.
- If an allowlist is explicitly configured, any out-of-scope target should cause the operation to be rejected before probing begins; do not silently scan only the allowed subset unless a partial-scope mode is deliberately designed later.
- Keep allowlist evaluation separate from public-range policy and the authorization acknowledgement; all enabled checks must pass.
- Recommended simplification: allow IPs/CIDRs in the allowlist for V1, not hostnames, so authorization policy can be evaluated after/alongside DNS resolution in a deterministic way.

### 7.5 Guardrail validation order

Before opening sockets:

1. Parse and validate the target, ports, timeout, concurrency, rate settings, and output format.
2. Resolve hostnames to IPv4 addresses (if a hostname was supplied); preserve DNS failure as a structured error.
3. Expand and validate CIDR targets; calculate the effective scan size and enforce the cap.
4. Classify address ranges and apply public/special-range rules.
5. Apply the configured allowlist.
6. Require acknowledgement for an active scan.
7. Only then schedule network probes.

The exact user-experience ordering of acknowledgement versus DNS/target checks may be tuned, but **no socket probes should start until the scan configuration and scope pass validation**.

---

## 8. Concurrency, timeout, and rate limiting

### Agreed/recommended working defaults

- Maximum concurrent connections: **100**.
- Per-connection TCP connect timeout: **1 second**. **[Kept, conditional on the Windows mitigation in §20.3.]**
- Both values should be configurable and validated.
- The defaults are moderate starting points, not claims of being optimal. Tune them through controlled benchmarking and document that network latency and target behavior affect results.

### Rate limiting

Concurrency is not the same as rate limiting: 100 outstanding connections can still start many probes in a short burst. Include a configurable global limit on probe starts as well as the concurrency semaphore.

- A starting proposal is **50 probe starts per second**, configurable and to be tuned after benchmarks. This is a recommendation, not a separately confirmed final number.
- Apply the limit across the whole scan, not independently per host, otherwise a CIDR scan can multiply the intended rate.
- Use monotonic time for rate scheduling and elapsed-time measurement.
- Keep scanning bounded: no unbounded task creation for large CIDRs; schedule targets/probes through a bounded worker/semaphore/queue design.
- Rate limiting should be tested independently of concurrency limits.

### Time accounting

Record at least per-probe elapsed time in milliseconds. Distinguish a connection timeout from a scan-wide execution limit or cancellation. A future banner-read timeout would be separate from the TCP connect timeout, but banner grabbing is deferred.

---

## 9. DNS and target semantics

- V1 is IPv4-only.
- A literal IPv4 target is scanned directly after validation.
- A hostname may resolve to several IPv4 addresses: scan **all** returned IPv4 addresses and report each separately.
- Do not silently select only the first DNS answer.
- If DNS resolution fails or returns no usable IPv4 result, produce a structured `error` result/report with a useful error code/message; do not mark the host `no_response` because no network probe was actually completed.
- Preserve both the original input target (hostname or literal) and the concrete IPv4 address used for each probe.
- Define deterministic duplicate handling: if a resolver returns duplicate IPv4 answers, deduplicate targets for probing but preserve the original hostname in results.
- Use an explicit resolver seam so DNS failure and multiple-answer cases can be tested without relying on public DNS.

---

## 10. Default ports and port input

- If the user supplies a host but no ports, scan the documented small default list: **22, 80, 443**.
- Do not default to the top 100 ports in V1. A larger common-port list can be an explicitly chosen preset later.
- Accept single ports, comma-separated port lists, and inclusive ranges, e.g.:
  - `--ports 80`
  - `--ports 22,80,443`
  - `--ports 8000-8010`
  - `--ports 22,80,8000-8010`
- Validate range bounds `1..65535`, reject empty list elements, malformed ranges, and inverted ranges, and deduplicate port values.
- Never let invalid input silently fall back to all 1–65535 ports.
- Service names associated with ports may be shown as hints later, but a port number alone does not prove which application/service is running.

---

## 11. Result model and output contract

Pydantic models should be the typed contract shared by the CLI, engine, test suite, and eventual API. Exact field names can be finalized while implementing, but keep the following semantics and information.

### 11.1 Per-port result — proposed fields

- `target_input`: original IPv4 literal or hostname supplied by the caller.
- `ip`: concrete IPv4 address probed.
- `port`: TCP port number.
- `state`: one of `open`, `closed`, `filtered`, `inconclusive`, `error`.
- `elapsed_ms`: measured probe duration.
- `error_code`: optional stable/socket/DNS/platform code, where available.
- `error_message`: optional concise human-readable details.
- `timestamp` or scan start time: use a consistent timezone-aware representation for report metadata/results as appropriate.

A report envelope should also retain scan-level metadata such as tool/version, original target(s), ports, timeout, concurrency/rate settings, start/end time, number of attempted/completed results, and whether the run was interrupted/partial.

### 11.2 Per-host discovery result — proposed fields

- `target_input`: original IP, hostname, or CIDR context.
- `ip`: concrete IPv4 address assessed.
- `state`: `responsive`, `no_response`, or `error`.
- `probed_ports`: configured ports actually attempted (or a structured list of per-port probe evidence).
- `evidence`: per-probe outcome, elapsed time, and optional error context.
- `elapsed_ms`: total duration spent evaluating that address.
- `error_code` / `error_message`: optional structured error information.

The evidence list matters: a host verdict should be explainable from recorded probe outcomes, not merely a bare label.

### 11.3 DNS failures

Represent DNS failures as structured `error` results/reports with the input hostname and reason. They are not port states, because there is no concrete destination port to classify. The CLI must not pretend each requested port timed out if resolution failed before any connect attempt.

### 11.4 JSON and CSV

- Support both JSON and CSV.
- Recommended primary interface: `--format json|csv`; also support convenience flags `--json` and `--csv` as aliases for the format selection.
- Reject contradictory simultaneous choices, such as `--json --csv`, with a clear validation message.
- JSON should preserve nested metadata and discovery evidence.
- CSV should use a documented flat, stable schema (one row per port probe for port scans; one row per address/probe or a deliberately documented flattening of evidence for discovery). Repeat target/IP fields as needed and preserve error/state columns.
- Write file export atomically where practical so a Ctrl-C does not leave a corrupted file; if using stdout, clearly separate machine-readable output from diagnostic/logging text.
- Rich human output is the default interactive presentation; machine-readable output should not be polluted with terminal styling or log messages.

CSV schema details and whether export defaults to stdout versus a file path remain implementation choices. Document them before the first stable release.

---

## 12. Ctrl-C, cancellation, and partial results

Ctrl-C should stop the scan cleanly and preserve completed work.

Recommended lifecycle:

1. Stop scheduling new targets/probes as soon as interruption is detected.
2. Cancel outstanding work or allow only a bounded cleanup window; ensure sockets/tasks are closed/cancelled safely.
3. Keep results that have already completed; do not invent results for probes that never ran.
4. Mark the report as interrupted/partial and include completed-versus-requested counts.
5. Emit the partial report in the selected output format (including valid JSON/CSV), and then return an interruption exit code such as `130`.
6. Keep logs and progress messaging out of JSON/CSV stdout.

The precise policy for probes already in flight must be tested under cancellation so shutdown does not leak tasks or leave the terminal unusable.

---

## 13. Engine/CLI/API boundary

This boundary is a core architectural requirement because a FastAPI wrapper is planned later.

### Principle

The **engine does not know about Typer, Rich, CLI arguments, terminal state, or HTTP requests**. The CLI validates/parses user input and formats results; the engine accepts validated configuration/models and returns structured result models. A future FastAPI route can call the same engine directly.

### Suggested engine surface

Use asynchronous, typed functions along these lines (names are proposals, not frozen API names):

```python
async def scan_ports(config: ScanConfig) -> ScanReport: ...

async def discover_hosts(config: DiscoveryConfig) -> DiscoveryReport: ...
```

Possible internal seams:

- `resolve_ipv4(target)`: resolve a host input to deduplicated IPv4 targets with structured resolution failures.
- `scan_port(ip, port, timeout)`: perform one TCP connect attempt and return a `PortResult`.
- `scan_ports(config)`: orchestrate per-host/per-port work under concurrency and rate limits.
- `discover_hosts(config)`: expand/validate the CIDR, schedule configured probe ports, retain probe evidence, and derive each host verdict.
- `output_json(report)` / `output_csv(report)`: serialize without embedding terminal formatting into the engine.

The names and dataclass/Pydantic details may change, but maintain the boundary and semantics. Public engine functions should be independently testable and reusable from other Python code.

### Suggested module layout

```text
netscope/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── netscope/
│       ├── __init__.py
│       ├── cli.py              # Typer app and command wiring only
│       ├── config.py           # validated scan/discovery configuration
│       ├── models.py           # Pydantic config and result/report models
│       ├── resolver.py         # IPv4 DNS resolution and normalization
│       ├── scanner.py          # TCP connect probe and scan orchestration
│       ├── discovery.py        # CIDR expansion, probe evidence, host verdicts
│       ├── rate_limit.py       # shared global start-rate limiter
│       ├── output.py           # JSON/CSV serialization
│       └── logging_config.py   # logging levels/format setup
└── tests/
    ├── test_config.py
    ├── test_resolver.py
    ├── test_scanner.py
    ├── test_discovery.py
    ├── test_guardrails.py
    ├── test_output.py
    ├── test_cancellation.py
    └── test_benchmark_smoke.py
```

Use `src/` layout and an installable package. Exact module splitting can be kept smaller if implementation complexity does not justify every file, but avoid merging CLI rendering with the network engine.

---

## 14. CLI shape and housekeeping

### Package and runtime

- Distribution/package name: `netscope`.
- Console command: `netscope`.
- Python baseline suggested by the earlier scaffold: **Python 3.11+**; keep the declared minimum explicit in `pyproject.toml`.
- Dependencies for the selected architecture: Typer, Rich, Pydantic. Test tooling: pytest and pytest-asyncio (or an equivalent async test setup).
- Use `pyproject.toml`, build/install the package (editable installs are convenient for development), and provide documented Windows PowerShell commands because the initial development context used Windows.

### Suggested command shape

A subcommand-oriented CLI is a good fit for future growth, e.g.:

```text
netscope scan <target> [--ports 22,80,443] [--timeout 1.0]
netscope discover <ipv4-cidr> [--discovery-ports 22,80,443]
```

Possible common options include concurrency, rate, output format/path, allowlist, public-target override, authorization acknowledgement, and verbosity. This command syntax is a proposal; settle it consistently when implementing. Do not duplicate validation logic between commands.

### Exit-code proposal

Document a stable exit-code contract. Candidate scheme: **[Resolved — see §20.5.]**

- `0`: scan/discovery completed normally, even if ports are closed or a host has `no_response` (those are valid observations).
- `1`: completed operation contains one or more operational/target errors, if the CLI decides partial target errors should be reflected in the process result.
- `2`: invalid command-line arguments or configuration/scope validation failure.
- `130`: interrupted by Ctrl-C after partial results are emitted.

If a fatal engine failure needs its own code, define it deliberately. The key is to distinguish valid negative observations from invalid input, operational failure, and user interruption. Avoid making exit-code semantics depend on terminal styling.

### Logging verbosity

- Default: concise progress/summary messages.
- `--verbose`: diagnostic/debug detail useful for troubleshooting.
- `--quiet`: warnings/errors only, with progress disabled.
- Send logs/progress to stderr when stdout is being used for JSON/CSV.
- Never use `--verbose` as a substitute for a stable structured error model.

### Configuration validation

Reject empty targets/port sets, invalid port values/ranges, non-positive timeouts, invalid concurrency/rate limits, malformed CIDRs, and incompatible output flags before scanning. Defaults should be visible in help and report metadata.

---

## 15. Testing strategy

Tests should be deterministic wherever possible and must not require scanning arbitrary public systems.

### Unit tests

- Port parser: single port, list, range, duplicates, whitespace, empty parts, malformed values, inverted ranges, and out-of-range ports.
- Pydantic configuration validation: target, timeout, concurrency, rate, port values, and output-format combinations.
- IPv4 target/CIDR parsing: valid addresses, invalid input, CIDR expansion, cap enforcement, loopback/private/public/special-address policy.
- Allowlist parser: comments, blanks, single addresses, CIDRs, malformed line numbers, and out-of-allowlist rejection.
- State classification: successful connect → `open`; connection refused → `closed`; timeout → `inconclusive`; unexpected socket errors → `error`; never timeout → `filtered` by default.
- Discovery verdicts: refusal counts as responsive; any positive TCP response wins; all timeouts → `no_response`; operational/DNS errors → `error`.
- DNS handling: multiple IPv4 answers are all scanned, duplicate answers deduplicate, IPv6-only DNS answers do not silently turn into an IPv6 scan, and resolution errors are structured.
- JSON and CSV schemas, escaping, no ANSI styling in structured files/stdout, and partial-report shape.
- Rate limiter independently of the concurrency semaphore.

### Local integration tests

- Start an asyncio TCP server bound to `127.0.0.1` on an ephemeral port; scan it and verify `open`.
- Pick an unused loopback port and verify an explicit refusal is classified `closed` (account for the tiny race in which another process might claim the port).
- Use a controlled/mocked connection function to test timeout deterministically.
- Use an injected/mock resolver to test DNS failures and multiple IPv4 addresses without relying on external DNS.
- Start a local target and trigger cancellation to validate partial results and cleanup.

An earlier proposed test scaffold used `pytest` and `pytest-asyncio`; it had not been executed in the user's Windows environment. Treat it as draft test code, not evidence that tests have passed. One caveat from that scaffold: monkeypatching `netscope.scanner.asyncio.open_connection` changes a shared `asyncio` module attribute while the patch is active; an injected connection function or a narrower patch point is cleaner as the code matures.

### Benchmark protocol

Define a repeatable run with:

1. A local controlled fixture or isolated, authorized lab with known labels for open, closed/refused, simulated timeout, and error cases.
2. A fixed target list/CIDR, fixed port list, fixed timeout, fixed concurrency, and fixed global rate cap recorded in report metadata.
3. Warm-up where relevant, followed by multiple measured runs using the same configuration.
4. Ground truth recorded separately from scanner output to avoid using the scanner's own classification as the reference label.
5. Results for accuracy/classification confusion, throughput/probes per second, per-probe latency (including percentiles such as p50/p95), total runtime, completeness, error counts, and CPU/memory if practical.
6. Comparisons across concurrency/rate settings while staying inside the lab or authorized scope.

Do not benchmark by repeatedly scanning arbitrary third-party/public targets. Use controlled lab infrastructure or explicitly authorized targets.

### Evaluation metrics

- Open/closed classification accuracy on controlled ports.
- Host discovery behavior against known responsive targets and controlled no-response/error cases.
- False `filtered` classifications (target should be zero for timeout-only cases in V1).
- Throughput and end-to-end duration.
- Latency distribution (p50/p95), timeout frequency, operational error frequency, and completed-result count after Ctrl-C.
- Resource use and stability at several bounded concurrency/rate settings.

---

## 16. Development roadmap

Recommended sequence:

1. **Freeze specification:** decide the remaining guardrail edge cases, schema details, command shape, exact exit codes, and starter rate limit.
2. **Create package skeleton:** `pyproject.toml`, `src/netscope`, Typer app, Pydantic models, dependency setup, and test layout.
3. **Implement and test validation:** port parser, target/IP/CIDR parser, configuration validation, scope rules, allowlist, and defaults before networking.
4. **Implement one TCP probe:** explicit success/refusal/timeout/error classification, timing, and deterministic cleanup.
5. **Implement single-target scanning:** all resolved IPv4 addresses, configured ports, bounded concurrency, global start-rate limiting, structured report.
6. **Implement output and cancellation:** human-readable Rich output, JSON/CSV contracts, Ctrl-C partial reporting and stable exit codes.
7. **Implement CIDR discovery:** expansion under cap, configurable TCP probes, retained evidence, and `responsive`/`no_response`/`error` verdicts.
8. **Build tests/integration tests:** run the test suite locally and fix Windows-specific socket behavior as required.
9. **Benchmark in controlled ground-truth setup:** measure classification, throughput, latency, completeness, and resource use; adjust defaults based on evidence.
10. **Document V1:** README usage, safety model, scope flags, states, limitations, output schemas, exit codes, installation, tests, and benchmark method.
11. **Post-V1:** consider banner collection, IPv6, and more nuanced filtered-state evidence; later add FastAPI around the stable engine API.

The build follows the phase plan recorded in §21 (Phase 0–8), which reorders items 6–7 so discovery precedes the CLI and cancellation.

---

## 17. Remaining details to finalize before implementation is considered specified

**[Status: items 1–7 and 10 were confirmed by the user in Phase 0 (§20). Items 8, 9, 11 and 12 were decided during implementation and are recorded in §20/§21.]**

These details have candidate recommendations, but should not be silently treated as user-locked decisions unless explicitly confirmed later:

1. **`filtered` emission rule:** specify the exact platform/network signals that count as direct evidence. In V1, never infer it from timeout alone; it is acceptable to leave it effectively unused until reliable evidence exists.
2. **Special-address policy:** exact handling for link-local, reserved, multicast, unspecified, directed-broadcast, and other special IPv4 ranges. Recommendation: explicit classification; permit loopback for local tests and conservatively reject unsupported special-purpose targets.
3. **Authorization flag coverage:** recommendation is to require `--yes-authorized` on every active scan, not only CIDR discovery.
4. **Cap semantics:** confirm whether `256` means expanded probe targets (host addresses) and how network/broadcast addresses are counted; recommended default is 256 actual hosts to be probed.
5. **Rate cap:** concurrency 100 and timeout 1 second are working defaults; `50` probe starts/second is only a proposed starting global rate and should be benchmarked.
6. **Allowlist error behavior:** recommendation is all-or-nothing rejection if any requested target falls outside the allowlist; do not silently trim the requested scope.
7. **CLI syntax:** choose between a subcommand form (`netscope scan ...`, `netscope discover ...`) and a simpler positional-target form; subcommands are recommended for growth.
8. **Result timestamps and report envelope:** finalize exact field names, timezone format, partial/interrupted indicator, scan metadata, and JSON versioning.
9. **CSV shape and file destinations:** define stable columns for port scans and discovery reports, stdout/file behavior, and handling of incompatible output flags.
10. **Exit codes:** finalize the distinction between completed-with-errors and fatal runtime failure while retaining `2` for usage/configuration failure and `130` for Ctrl-C as proposed.
11. **DNS and partial resolution:** define the report behavior if a hostname returns some valid IPv4 addresses alongside unsupported/invalid answers or if only IPv6 addresses are returned.
12. **Python/dependency versions:** the previous draft proposed Python 3.11+ and pytest/pytest-asyncio. Confirm minimum versions in `pyproject.toml` during implementation.

---

## 18. Principles that must not be lost

- **Evidence, not assumptions:** a refusal is evidence of a responding endpoint; a timeout is inconclusive, not proof of death or filtering.
- **No silent scope expansion:** invalid input must fail closed. Never turn bad port ranges into a full-port scan or scan extra CIDR targets silently.
- **Preflight before probing:** validate target scope, host cap, public-address policy, acknowledgement, and allowlist before sockets are opened.
- **Keep result semantics honest:** distinguish a port outcome, host verdict, DNS failure, and internal/operational error.
- **Bound network activity twice:** concurrency and global rate limiting solve different problems; include both.
- **Keep the engine reusable:** Typer and Rich belong in the CLI layer; Pydantic models and asynchronous engine functions form the reusable contract for future FastAPI use.
- **Make interruption useful:** retain and export completed results after Ctrl-C, and label the report as partial.
- **Test with ground truth:** local controlled servers and injected DNS/network behavior come before assumptions about external hosts.
- **Do not overstate completion:** code previously supplied was a draft scaffold and was not verified in the user's environment; only mark tests/benchmarks complete when actually run.
- **Be explicit about deferrals:** banner grabbing, IPv6, and timeout-to-filtered evidence are V2/later concerns.

---

## 19. Compact handoff summary

Build **NetScope V1** as an installable Python IPv4 TCP-connect scanning and CIDR discovery CLI using **Typer + Rich + Pydantic**, with `22,80,443` as the documented default ports. Scan all IPv4 addresses returned by DNS. Use the five port states `open`, `closed`, `filtered`, `inconclusive`, and `error`; a timeout is always `inconclusive` in V1, an explicit refusal is `closed` for port scanning and positive evidence for host discovery, and arbitrary socket errors remain `error`. CIDR discovery uses configurable TCP probe ports and reports `responsive`, `no_response`, or `error` based on retained evidence. Apply the four scope guardrails: a host-count cap (recommended 256), block public ranges unless explicitly overridden, require an authorization acknowledgement, and support a plain-text IP/CIDR allowlist. Start with concurrency 100 and a 1-second connect timeout; add global rate limiting (50 probe starts/second is a benchmarkable proposal). Support JSON and CSV, graceful Ctrl-C with partial results, documented exit codes, and clear logging verbosity. Keep the async engine independent of Typer/Rich so FastAPI can wrap it later. Defer banners, IPv6, and retry/baseline inference for `filtered`. Validate first, test locally against known ground truth, and benchmark before claiming performance or accuracy.

**Source-of-truth note:** When later discussion updates one of these choices, update this capsule and clearly mark the prior decision as superseded rather than letting contradictory versions coexist.

---

## 20. Frozen decisions (Phase 0, confirmed by the user 2026-10-23)

These supersede the "recommended"/"open" wording elsewhere in this capsule where they differ.

### 20.1 `filtered`

Reserved in the vocabulary and schema, **never emitted in V1**. No signal (timeout, ICMP unreachable, or otherwise) maps to it. Tests assert that no code path produces it.

### 20.2 Special-address policy

Each target IPv4 address is classified by an explicit, ordered table (not by a "not RFC 1918 = public" shortcut):

| Class | Ranges | V1 policy |
|---|---|---|
| `loopback` | 127.0.0.0/8 | allowed |
| `private` | 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 (RFC 6598 shared/CGNAT) | allowed |
| `public` | anything not listed here | requires `--allow-public` |
| `link_local` | 169.254.0.0/16 | rejected, no override |
| `multicast` | 224.0.0.0/4 | rejected, no override |
| `unspecified` | 0.0.0.0/8 ("this network") | rejected, no override |
| `broadcast` | 255.255.255.255/32 | rejected, no override |
| `reserved` | 240.0.0.0/4 (except the broadcast address) | rejected, no override |
| `special` | 192.0.0.0/24, 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 198.18.0.0/15, 192.88.99.0/24 | rejected, no override |

Directed broadcast: a CIDR's network and broadcast addresses are excluded by host expansion (§20.4). A single-IP target cannot be checked for directed broadcast because the netmask is unknown; this is documented as a limitation.

### 20.3 Windows refused-connect measurement and mitigation

Measured on the development machine (Windows 11 26200, Python 3.12.0, asyncio ProactorEventLoop), 10 runs each (5 for LAN):

| Probe | Default stack | With `SIO_TCP_INITIAL_RTO` (no SYN retransmit) |
|---|---|---|
| 127.0.0.1, unused port (refused) | p50 2,036 ms (2,020–2,059) | p50 0.3 ms (max 0.8) |
| `localhost`, any address family (refused) | p50 4,079 ms (tries `::1` then `127.0.0.1`) | n/a (V1 resolves IPv4 only) |
| `localhost`, AF_INET only (refused) | p50 2,045 ms | — |
| 127.0.0.1, listening port (open) | p50 1.1 ms | p50 1.0 ms |
| LAN gateway 192.168.1.1:80 (open) | p50 15.3 ms | p50 3.3 ms |
| LAN gateway 192.168.1.1:81 (refused) | p50 2,079 ms | p50 9.8 ms |

Findings and decision:

- Windows retries the SYN after a RST, so every refusal costs ~2 s. With the 1 s timeout a closed port would be misreported as `inconclusive`.
- **Decision:** keep the **1 s default timeout** and, on Windows, apply `SIO_TCP_INITIAL_RTO` with `MaxSynRetransmissions = TCP_INITIAL_RTO_NO_SYN_RETRANSMISSIONS` to every probe socket. Not a user-facing flag; the engine config field `fast_refusal` (default `True`) exists for tests/benchmarks. Report metadata records whether the mitigation was active.
- **Trade-off (documented):** a single lost SYN is no longer retried on Windows, so it surfaces as `inconclusive` (never as `closed`/`filtered`).
- On the Proactor loop a refusal surfaces as `ConnectionRefusedError` with **`winerror 1225`** (ERROR_CONNECTION_REFUSED), not `10061`. The classifier maps `ECONNREFUSED`, `winerror 10061` and `winerror 1225` to `closed`.
- V1 resolves hostnames with `AF_INET` only, which avoids the `::1`-first double penalty for `localhost`.

### 20.4 Host cap

Default **256**, counting **probe-target hosts**: for a CIDR, Python `ipaddress` `.hosts()` semantics (network and broadcast excluded for /0–/30; /31 and /32 count every address), so a /24 is 254 hosts. For `scan`, the cap counts the distinct resolved IPv4 addresses. Raising it requires `--max-hosts N` (logged as a warning above 256; hard ceiling 65,536). Oversized scopes are rejected before any network activity.

### 20.5 Exit codes

| Code | Meaning |
|---|---|
| `0` | Completed; every result is a valid observation (`open`, `closed`, `inconclusive`, `responsive`, `no_response`). |
| `1` | Completed, but at least one result has state `error` (port probe error, host `error`, or DNS/target error). |
| `2` | Invalid arguments, configuration, or scope/preflight rejection. Nothing was probed. |
| `3` | Unexpected fatal runtime failure (bug or environment failure outside the result model). |
| `130` | Interrupted by Ctrl-C; the partial report was emitted. Takes precedence over `1`. |

### 20.6 Other confirmations

- CLI uses subcommands: `netscope scan <target>` and `netscope discover <cidr>`.
- `--yes-authorized` is required for **every** active scan (both subcommands). `--help`/`--version` never require it.
- Allowlist is all-or-nothing: any out-of-allowlist target rejects the whole run.
- Concurrency 100 and rate 50 probe starts/s remain starting defaults until Phase 8 benchmarks.

---

## 21. Build log

Each phase ends with passing tests, an update here, and a commit.

### Phase 0 — spec freeze and Windows measurement (done)

- Decisions recorded in §20. Measurement scripts were throwaway (not committed); the numbers are in §20.3.
- Environment: no `nmap` installed on the dev machine, so the optional nmap comparison in Phase 8 is not available locally.
