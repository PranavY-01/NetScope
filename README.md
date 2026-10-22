# NetScope

An evidence-based IPv4 TCP port scanner and host-discovery tool for authorized networks, built in Python with a reusable async engine.

> **Status: planning / pre-implementation.** No released code yet. Everything below describes the planned V1 design. Commands, flag names, defaults, and output schemas are proposals until the first implementation lands and are marked as such. See [Roadmap](#roadmap).

---

## Overview

NetScope discovers which IPv4 hosts respond on an authorized network and which TCP ports accept, refuse, or ignore connections. It uses ordinary TCP connect probes, so no raw-packet privileges are needed.

The goal is not just to report which ports respond, but to show how the tool handles uncertainty, validates its inputs, controls its own network activity and produces reproducible results.

## Responsible use

Only scan systems and networks that you own or have explicit permission to test. Scanning without authorization may be illegal and may violate acceptable-use policies.

NetScope's safety controls (host cap, public-range restriction, allowlist, authorization flag) reduce the chance of accidental misuse. They cannot prove that you are authorized, and they do not replace your own responsibility.

## Planned V1 features

- Installable Python package with a `netscope` console command (Python 3.11+).
- TCP connect scanning of a single IPv4 address or hostname.
- Hostnames that resolve to several IPv4 addresses: every address is scanned and reported separately.
- IPv4 CIDR host discovery using configurable TCP-connect probes and evidence-based verdicts.
- Port specs as single ports, lists, and inclusive ranges, for example `22,80,443,8000-8010`.
- Scope guardrails enforced before any socket is opened.
- Bounded concurrency plus a global probe-start rate limit.
- Typed Pydantic result models with JSON and CSV export.
- Graceful Ctrl-C handling that keeps and exports completed partial results.
- A scan engine independent of the CLI so a FastAPI wrapper can use it later.

## Design principles

- **Evidence, not assumptions.** A refused connection is evidence that something responded. A timeout is ambiguous, so it is never treated as proof that a host is dead or that a port is filtered.
- **Fail closed.** Invalid input is rejected. A malformed or inverted port range never silently expands to all 65,535 ports.
- **Preflight before probing.** Scope, host cap, public-address policy, allowlist, and authorization are all validated before the first connection attempt.
- **Honest result semantics.** Port outcomes, host verdicts, DNS failures, and internal errors are distinct things and are reported as such.
- **Bound activity twice.** A concurrency limit and a global start-rate limit solve different problems, so both are included.
- **Reusable engine.** Typer and Rich live in the CLI layer only. The engine takes validated configuration and returns typed results.

## Port states

| State | Meaning |
|---|---|
| `open` | A TCP connection was established. |
| `closed` | The destination actively refused the connection. |
| `filtered` | Reserved for cases with direct, documented evidence of filtering. **Not inferred from timeouts in V1**, so it may rarely or never be emitted. |
| `inconclusive` | Not enough evidence to classify, for example a connect timeout. |
| `error` | An unexpected local, socket, or operational error prevented classification. A machine-readable code and short message are preserved. |

Port states describe what the scanner observed from its own network position at probe time. They are not guarantees about the target.

## Host-discovery verdicts

| Verdict | Meaning |
|---|---|
| `responsive` | At least one probe produced a TCP-level response, including an explicit connection refusal. |
| `no_response` | Every probe failed to respond (for example, all timed out). This is absence of evidence, not proof the host is down. |
| `error` | A DNS, configuration, or local failure prevented a meaningful assessment. |

Per-probe evidence (port, outcome, elapsed time) is retained so each verdict can be explained. Discovery ports are user-configurable with a documented default of `22,80,443`. A host that filters all probed ports can still be online and report `no_response`.

## Safety guardrails

All checks must pass before any probe is scheduled.

1. **Host-count cap:** 256 targets per scan by default, enforced from the CIDR size before network activity.
2. **Public-range restriction:** globally routable IPv4 targets are refused unless an explicit override flag is passed. Loopback, link-local, and other special ranges are classified explicitly rather than by a "not RFC 1918" shortcut.
3. **Authorization acknowledgement:** an explicit flag (planned: `--yes-authorized`) is required for every scan.
4. **Allowlist:** an optional plain-text file of permitted IPv4 addresses and CIDRs, one per line, with `#` comments. If any requested target falls outside it, the whole scan is rejected rather than silently trimmed.

The public-range override never bypasses the host cap, input validation, the acknowledgement, or the allowlist.

## Planned CLI (proposal)

```text
netscope scan <target> [--ports 22,80,443] [--timeout 1.0]
netscope discover <ipv4-cidr> [--discovery-ports 22,80,443]
```

Common options planned for both commands: concurrency, probe-start rate, output format and path, allowlist file, public-target override, authorization acknowledgement, and `--verbose` / `--quiet`.

## Working defaults

These are starting points to be tuned through controlled benchmarking, not claims of optimality.

| Setting | Default |
|---|---|
| Ports when none given | `22, 80, 443` |
| Discovery probe ports | `22, 80, 443` |
| Maximum concurrent connections | 100 |
| Probe starts per second (global) | 50 |
| Connect timeout | 1 second (under review, see below) |
| Host cap | 256 |

## Output and exit codes

- **Formats:** JSON (nested metadata and discovery evidence) and CSV (flat, documented schema). Selected with `--format json|csv`, with `--json` and `--csv` as shorthand. Contradictory choices are rejected.
- **Streams:** machine-readable output goes to stdout or a file, while logs and progress go to stderr, so JSON and CSV stay clean and pipeable.
- **Interrupts:** Ctrl-C stops new work, keeps completed results, marks the report as partial, and exits with `130`.

Proposed exit codes (to be finalized):

| Code | Meaning |
|---|---|
| `0` | Completed normally. Closed ports and `no_response` hosts are valid observations. |
| `1` | Completed, but with operational or target errors. |
| `2` | Invalid arguments, configuration, or scope validation failure. |
| `130` | Interrupted by Ctrl-C after partial results were emitted. |

## Planned architecture

```text
netscope/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── netscope/
│       ├── cli.py              # Typer app and command wiring only
│       ├── config.py           # validated scan/discovery configuration
│       ├── models.py           # Pydantic config and result/report models
│       ├── resolver.py         # IPv4 DNS resolution and normalization
│       ├── scanner.py          # TCP connect probe and scan orchestration
│       ├── discovery.py        # CIDR expansion, probe evidence, verdicts
│       ├── rate_limit.py       # shared global start-rate limiter
│       ├── output.py           # JSON/CSV serialization
│       └── logging_config.py   # logging levels and format
└── tests/
```

Module splitting may be simplified during implementation, but CLI rendering is not to be merged with the network engine.

## Testing and evaluation

- **Unit tests:** port parser, configuration validation, address-range policy, allowlist parser, state classification, discovery verdicts, DNS handling (via an injected resolver), output schemas, and the rate limiter.
- **Local integration tests:** an asyncio server on `127.0.0.1` (`open`), an unused loopback port (`closed`), mocked connections for deterministic timeouts, and cancellation with partial results.
- **Benchmarks:** fixed targets, ports, timeout, concurrency, and rate, with ground truth recorded separately from scanner output. Metrics: classification accuracy, throughput, latency (p50/p95), completeness, error counts, and resource use.
- **Rule:** benchmarks run only against local fixtures or authorized lab targets, never arbitrary third-party systems.

## Platform notes

On Windows, a connect to a closed port can take well over a second to report a refusal, because the TCP stack retries the SYN after a reset. With a short timeout, refused ports could be reported as `inconclusive` instead of `closed`. The default timeout and any mitigation will be settled by measurement before V1 defaults are frozen.

## Roadmap

1. Freeze the specification and measure Windows refused-connect latency.
2. Package skeleton, models, and test setup.
3. Validation, scope rules, and serializers (no networking).
4. Single TCP probe and classification.
5. Resolver, orchestration, concurrency, and rate limiting.
6. CIDR discovery.
7. CLI layer, outputs, and exit codes.
8. Ctrl-C partial-result handling.
9. Controlled benchmarks and default tuning.
10. Documentation and V1 release.


## Acknowledgements and licensing note

The reference project mentioned above is licensed AGPL-3.0. NetScope does not copy its code, and any future reuse would require reviewing and complying with that license. Conceptual inspiration is not permission to copy code.

