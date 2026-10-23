# NetScope

An evidence-based IPv4 TCP port scanner and host-discovery tool for authorized networks, built in Python with a reusable async engine.

> **Status: V1 feature-complete (`1.0.0.dev0`).** Tested on Windows 11 with Python 3.12 (494 tests). A Linux + Windows CI matrix is configured but has not yet run. Design decisions, measurements, and the build log are in [docs/NetScope_V1_Memory_Capsule.md](docs/NetScope_V1_Memory_Capsule.md).

---

## Overview

NetScope finds which IPv4 hosts respond on an authorized network and which TCP ports accept, refuse, or ignore connections. It uses ordinary TCP connect probes, so it needs no raw-packet privileges.

The aim is not only to report which ports respond. It is to handle uncertainty honestly, validate input before acting, keep its own network activity bounded, and produce reproducible results.

## Responsible use

Only scan systems and networks that you own or have explicit permission to test. Scanning without authorization may be illegal and may violate acceptable-use policies.

NetScope's safety controls (host cap, address-range policy, allowlist, authorization flag) reduce the chance of accidental misuse. They cannot prove that you are authorized, and they do not replace your own responsibility.

## Install

Requires Python 3.11+.

```powershell
git clone https://github.com/PranavY-01/NetScope.git
cd NetScope
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
netscope --help
```

## Usage

For a step-by-step walkthrough (installing, every option, reading results, and fixing errors), see the **[User Guide](docs/USER_GUIDE.md)**.

```text
netscope scan TARGET [OPTIONS]        # one IPv4 address or hostname (every resolved IPv4 address)
netscope discover NETWORK [OPTIONS]   # an IPv4 CIDR, e.g. 192.168.1.0/24
```

Examples:

```powershell
# Scan the default ports (22, 80, 443) on a lab host
netscope scan 192.168.1.20 --yes-authorized

# Ports as single values, lists and ranges; JSON to stdout
netscope scan router.lan -p 22,80,8000-8010 --yes-authorized --json

# Discover responsive hosts in a /24 and write CSV atomically to a file
netscope discover 192.168.1.0/24 --yes-authorized -o hosts.csv

# Restrict scope to an allowlist file
netscope discover 10.20.0.0/28 --yes-authorized --allowlist lab-scope.txt
```

| Option | Default | Meaning |
|---|---|---|
| `-p`, `--ports` / `--discovery-ports` | `22,80,443` | Ports, lists and inclusive ranges. Malformed or inverted specs are rejected, never widened. |
| `-t`, `--timeout` | `1.0` | TCP connect timeout per probe, in seconds. |
| `-c`, `--concurrency` | `100` | Maximum probes in flight. |
| `-r`, `--rate` | `50` | Maximum probe starts per second across the whole run (exact over time; an individual start can come up to ~31 ms early on Windows with Python 3.11/3.12). |
| `--max-hosts` | `256` | Host cap. Raising it is logged as a warning (ceiling 65,536). |
| `--allow-public` | off | Permit globally routable targets. Never bypasses the other checks. |
| `--allowlist FILE` | none | Permitted IPv4 addresses/CIDRs; any target outside it rejects the whole run. |
| `--yes-authorized` | **required** | Confirms you are authorized to test every target. |
| `-f`, `--format json\|csv`, `--json`, `--csv` | human table | Machine-readable output. Conflicting choices are rejected. |
| `-o`, `--output FILE` | stdout | Write the report atomically; the format can be inferred from `.json`/`.csv`. |
| `-v`, `--verbose` / `-q`, `--quiet` | info | Debug logging / warnings only. Logs always go to stderr. |

## Port states

| State | Meaning |
|---|---|
| `open` | A TCP connection was established. |
| `closed` | The destination actively refused the connection. |
| `filtered` | Reserved in the schema. **Never emitted in V1**, because a TCP connect alone gives no reliable evidence of filtering. |
| `inconclusive` | Not enough evidence: a connect timeout, or a host/network-unreachable signal. The error code is kept. |
| `error` | A local or operational failure (e.g. too many open files) prevented classification. A machine-readable code and short message are kept. |

Port states describe what the scanner observed from its own network position at probe time. They are not guarantees about the target.

## Host-discovery verdicts

| Verdict | Rule |
|---|---|
| `responsive` | At least one probe got a TCP-level response: a connection **or an explicit refusal**. |
| `error` | No TCP response, and at least one probe failed with a local/operational error, so absence cannot be asserted. |
| `no_response` | Every probe was inconclusive (e.g. all timed out). This is absence of evidence, not proof the host is down. |

Each host's per-probe evidence (port, state, timing, error code) is kept in the report. All configured ports are probed on every host.

## Safety guardrails

All checks run in this order before any probe socket is opened. Any failure exits with code `2`.

1. **Input validation:** ports, target or CIDR syntax, timeout/concurrency/rate bounds, output flags, allowlist file syntax. A CIDR with host bits set is rejected with a suggested correction.
2. **Name resolution** (`scan` only), IPv4 answers only.
3. **Host cap:** 256 probe-target hosts by default, counted with `ipaddress` `.hosts()` semantics (a /24 is 254 hosts). Oversized ranges are rejected without being enumerated.
4. **Address policy**, from an explicit range table:
   - Loopback (127/8) and private space (RFC 1918, plus 100.64/10) are allowed.
   - Public addresses require `--allow-public`.
   - Link-local, multicast, `0.0.0.0/8`, broadcast, `240/4` and documentation/benchmarking ranges are always rejected.

   Python's `ipaddress.is_global` alone would treat multicast as public, so it is deliberately not relied on.
5. **Allowlist:** all-or-nothing.
6. **Authorization acknowledgement:** `--yes-authorized` on every scan.

## Output and exit codes

- **JSON** carries a metadata envelope (`tool`, `version`, `schema_version`, `command`, settings, UTC timestamps, `duration_ms`, `interrupted`, requested/completed counts), plus results, DNS `target_errors`, and a per-state `summary`.
- **CSV** has one row per probe.
  - Scan columns: `target_input, ip, port, state, elapsed_ms, error_code, error_message, started_at`.
  - Discovery columns: `target_input, ip, host_state, host_elapsed_ms, host_error_code, host_error_message, port, probe_state, probe_elapsed_ms, probe_error_code, probe_error_message, probe_started_at`.
- **Streams:** JSON/CSV go to stdout or to the `-o` file; logs and progress go to stderr, so output stays pipeable.
- **Ctrl-C:** stops new work, cancels in-flight probes, emits a partial report of the completed results (`interrupted: true`), and exits `130`. Nothing is invented for probes or hosts that did not finish.

| Code | Meaning |
|---|---|
| `0` | Completed. Closed ports, inconclusive probes and `no_response` hosts are valid observations. |
| `1` | Completed, but at least one result is `error` (probe error, host error, or DNS failure). |
| `2` | Invalid arguments, configuration, or scope rejection. Nothing was probed. |
| `3` | Unexpected internal failure. |
| `130` | Interrupted by Ctrl-C; partial report emitted. |

## Library use

The engine has no dependency on Typer or Rich, so a future API can call it directly:

```python
import asyncio
from netscope.config import ScanConfig, DiscoveryConfig
from netscope.scanner import scan_ports
from netscope.discovery import discover_hosts

report = asyncio.run(scan_ports(ScanConfig(target="127.0.0.1", ports="22,80", authorized=True)))
print(report.model_dump_json(indent=2))
```

To keep partial results on cancellation, use `prepare_scan`, then `run_scan(plan, on_result=results.append)`, then `build_scan_report` (or the `discovery` equivalents). `on_result` is called synchronously as each probe finishes, so no completed result is lost. `iter_scan`/`iter_discovery` stream results as an async iterator, but drop anything not yet consumed when cancelled.

## Architecture

```text
src/netscope/
├── cli.py              # Typer commands, preflight wiring, exit codes
├── console.py          # Rich tables (CLI only)
├── logging_config.py   # stderr logging
├── config.py           # port/target/CIDR parsing, Pydantic configs
├── guardrails.py       # address table, host cap, allowlist, preflight order
├── models.py           # Pydantic result and report models
├── resolver.py         # IPv4-only DNS behind an injectable seam
├── probe.py            # one TCP connect + classification (+ Windows mitigation)
├── rate_limit.py       # scan-wide probe-start limiter
├── scheduler.py        # fixed worker pool; results yielded as they complete
├── scanner.py          # scan plan / iterate / report
├── discovery.py        # discovery plan / iterate / verdicts / report
└── output.py           # JSON/CSV serialisers, atomic writes
```

## Platform notes: Windows refused connections

By default, Windows retries the SYN after receiving a RST, so a connection to a closed port takes about **2 seconds** to report a refusal (measured: 2,036 ms on loopback, 2,079 ms on the LAN). With a 1 s timeout every closed port would look `inconclusive`.

NetScope applies `SIO_TCP_INITIAL_RTO` (no SYN retransmissions) to each probe socket on Windows. With it, refusals report in 0.3 ms on loopback and 10 ms on the LAN. The trade-off is that a single lost SYN is not retried and shows up as `inconclusive`. Report metadata records whether the mitigation was active. Other platforms report refusals immediately and need no mitigation.

## Testing

```powershell
python -m pytest            # unit + loopback integration tests
python -m pytest -m "not integration"   # no real sockets
```

Tests cover:

- Table-driven parsers and the address policy, run with socket creation forbidden.
- The classification table: no errno or Windows code ever yields `filtered`.
- DNS via an injected resolver.
- The rate limiter on its own.
- In-flight and task-count bounds across a /24.
- Discovery verdicts, including mixed timeout + error.
- A spy proving no probe socket opens before preflight passes.
- ANSI-free JSON from a real subprocess.
- Ctrl-C partial reports, from the engine up to a real SIGINT in a subprocess.

On Windows, `tools/windows_console_ctrl_c.py` delivers a genuine console `CTRL_C_EVENT` to running scans as a manual check.

## Benchmarks

`benchmarks/bench.py` runs a ground-truth fixture on loopback:

- 60 real listeners
- 80 refused ports
- 50 simulated drops
- 10 simulated local errors

It sweeps concurrency × rate, with a warm-up and 3 measured runs per setting. Ground truth is written before scanning.

Results on Windows 11 / Python 3.12:

- **Accuracy was 100%** with 0 false `filtered` in every configuration.
- Without the Windows mitigation, accuracy fell to 60%, because every closed port became `inconclusive`.
- **Concurrency** stops mattering once it reaches about rate × timeout (50 at the defaults): 50, 100 and 200 measured the same, while 10 was a bottleneck.
- **Throughput** follows the rate cap up to about 1,000 probes/s. Above about 200/s, NetScope's own bursts inflate the measured latency.

The defaults (timeout 1 s, concurrency 100, rate 50/s) were kept on that evidence. The rate stays conservative because loopback cannot measure impact on real targets. Full tables and limitations are in the [capsule](docs/NetScope_V1_Memory_Capsule.md#phase-8--benchmark-and-tune-done-not-committed).

Benchmarks only ever target local fixtures or authorized lab hosts.

## Not in V1

IPv6, banner grabbing/service detection, SYN or other raw-packet scanning, ICMP/ARP discovery, retries or baselines that would justify a `filtered` state, and a FastAPI service (the engine is ready to be wrapped).

## Acknowledgements and licensing note

NetScope was conceptually inspired by the beginner `simple-port-scanner` project in `CarterPerez-dev/Cybersecurity-Projects`, which is licensed AGPL-3.0. NetScope does not copy its code, and any future reuse would require reviewing and complying with that license. Conceptual inspiration is not permission to copy code.
