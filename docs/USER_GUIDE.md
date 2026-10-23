# NetScope User Guide

A step-by-step guide to installing and running NetScope V1, reading its results, and fixing common errors.

> **Responsible use:** only scan machines and networks you own or are explicitly authorized to test. Your own computer (`127.0.0.1`) is always safe to practise on.

## Contents

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Check the installation](#3-check-the-installation)
4. [Scanning one machine](#4-scanning-one-machine)
5. [Choosing ports](#5-choosing-ports)
6. [Scanning by hostname](#6-scanning-by-hostname)
7. [Discovering machines on a network](#7-discovering-machines-on-a-network)
8. [Saving results](#8-saving-results)
9. [Speed and patience settings](#9-speed-and-patience-settings)
10. [Safety options](#10-safety-options)
11. [Log messages](#11-log-messages)
12. [Stopping a scan](#12-stopping-a-scan)
13. [Reading the results](#13-reading-the-results)
14. [Exit codes](#14-exit-codes)
15. [Common errors and fixes](#15-common-errors-and-fixes)
16. [Quick reference](#16-quick-reference)
17. [For developers](#17-for-developers)

---

## 1. Requirements

- Python 3.11 or newer (`python --version`)
- Git, to clone the repository
- Windows, Linux, or macOS. All commands below are for Windows PowerShell; Linux/macOS equivalents are noted where they differ.

No administrator rights are needed. NetScope uses ordinary TCP connections.

## 2. Installation

```powershell
git clone https://github.com/PranavY-01/NetScope.git
cd NetScope
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

On Linux/macOS, activate with `source .venv/bin/activate` instead.

Your prompt now starts with `(.venv)`. Activate the environment again in every new terminal before using NetScope:

```powershell
.\.venv\Scripts\Activate.ps1
```

> **If PowerShell blocks the activation script**, run this once and try again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

When you are finished, leave the environment with `deactivate`.

## 3. Check the installation

```powershell
netscope --version
netscope --help
netscope scan --help
netscope discover --help
```

Help and version commands never scan anything and never need `--yes-authorized`.

## 4. Scanning one machine

```powershell
netscope scan 127.0.0.1 --yes-authorized
```

This checks ports **22, 80 and 443** on your own machine and prints a table.

**`--yes-authorized` is required on every scan.** It confirms that you are allowed to test the target. Without it, NetScope refuses to run and sends no traffic.

## 5. Choosing ports

Use `-p` (or `--ports`):

```powershell
netscope scan 127.0.0.1 -p 135 --yes-authorized              # one port
netscope scan 127.0.0.1 -p 22,80,443,445 --yes-authorized    # a list
netscope scan 127.0.0.1 -p 1-1024 --yes-authorized           # a range
netscope scan 127.0.0.1 -p 22,80,8000-8010 --yes-authorized  # mixed
```

Ports must be 1–65535. Duplicates are removed. A mistake such as `100-1` or `80,,443` is rejected; NetScope never falls back to scanning every port.

## 6. Scanning by hostname

```powershell
netscope scan localhost --yes-authorized
netscope scan myrouter.lan --yes-authorized
```

If a name maps to several IPv4 addresses, every one is scanned and reported separately. IPv6 is not supported in V1.

## 7. Discovering machines on a network

`discover` answers "which machines here are responding?":

```powershell
netscope discover 127.0.0.0/29 --yes-authorized
```

Your own home network (only devices you are allowed to test):

```powershell
netscope discover 192.168.1.0/24 --yes-authorized
```

Each machine is checked on ports 22, 80 and 443 by default. Choose different ports with `-p` (or `--discovery-ports`):

```powershell
netscope discover 192.168.1.0/24 -p 22,80,443,445 --yes-authorized
```

A single address such as `192.168.1.10` is treated as a range of one (`/32`).

## 8. Saving results

Write to a file. The format comes from the extension, and the file is written safely, so it is never left half-written:

```powershell
netscope scan 127.0.0.1 -p 1-1024 --yes-authorized -o report.json
netscope discover 192.168.1.0/24 --yes-authorized -o hosts.csv
```

Print machine-readable output in the terminal instead of a table:

```powershell
netscope scan 127.0.0.1 --yes-authorized --json
netscope scan 127.0.0.1 --yes-authorized --csv
netscope scan 127.0.0.1 --yes-authorized --format json   # same as --json
```

Progress and log messages go to stderr, so JSON/CSV output can be piped or redirected cleanly:

```powershell
netscope scan 127.0.0.1 --yes-authorized --json > report.json
```

| Format | Contents |
|---|---|
| JSON | Full report: settings, timestamps, every result, per-host evidence, summary counts, and whether the run was interrupted |
| CSV | One row per port check, for spreadsheets. It has no settings or interruption flag, so check the exit code for that. |

## 9. Speed and patience settings

| Option | Default | Use it when |
|---|---|---|
| `-r`, `--rate` | 50 per second | You want it faster on your own LAN (e.g. `--rate 200`) |
| `-t`, `--timeout` | 1 second | The network is slow and you want to wait longer per port |
| `-c`, `--concurrency` | 100 | You want gentler scanning (fewer checks at once) |

```powershell
netscope discover 192.168.1.0/24 --yes-authorized --rate 200
netscope scan 10.0.0.5 -p 1-1024 --yes-authorized --timeout 2
```

Benchmarks showed no accuracy loss up to `--rate 200` on a local network. The default stays at 50 to be gentle on networks you do not fully control.

## 10. Safety options

Every check below runs **before** any network traffic. If one fails, nothing is scanned.

| Option | What it does |
|---|---|
| `--allowlist FILE` | Only allow targets listed in the file. If any target falls outside it, the whole run is refused. |
| `--allow-public` | Allow internet (public) addresses, which are blocked by default. |
| `--max-hosts N` | Raise the limit of 256 machines per run (a warning is shown). |

Example allowlist file `lab.txt`, with one address or range per line and `#` for comments:

```text
# my lab
127.0.0.1
192.168.1.0/24
10.20.0.15
```

```powershell
netscope discover 192.168.1.0/24 --yes-authorized --allowlist lab.txt
```

These addresses are **always** refused, even with `--allow-public`: link-local (`169.254.x.x`), multicast (`224.0.0.0`–`239.255.255.255`), `0.x.x.x`, broadcast (`255.255.255.255`), reserved (`240.0.0.0` and up), and documentation/benchmarking ranges.

## 11. Log messages

```powershell
netscope scan 127.0.0.1 --yes-authorized -v    # verbose: extra debug detail
netscope scan 127.0.0.1 --yes-authorized -q    # quiet: warnings and errors only
```

## 12. Stopping a scan

Press **Ctrl + C**. NetScope stops starting new checks and cancels the ones in progress. It still shows or saves every result that had finished, marked as partial, and exits with code `130`.

## 13. Reading the results

### Port states (`scan`)

| State | Meaning |
|---|---|
| `open` | A connection was made; something is listening |
| `closed` | The machine actively refused the connection |
| `inconclusive` | No answer in time (or the machine was unreachable), so no conclusion is drawn |
| `error` | A problem on your side prevented the check; the detail column explains it |
| `filtered` | Reserved for future versions; never shown in V1 |

### Host verdicts (`discover`)

| Verdict | Meaning |
|---|---|
| `responsive` | The machine answered on at least one port. A refusal counts as an answer. |
| `no_response` | No answer on the checked ports. The machine **may still be on**; it might ignore those ports. |
| `error` | A problem on your side prevented a proper check |

The table's **Evidence** column shows each port result behind a verdict, e.g. `22:inconclusive 80:open 443:closed`.

If more than 25 ports are `closed` (or more than 25 hosts are `no_response`), the table hides those rows and shows a count instead. Use `--json` or `--csv` to see every row.

## 14. Exit codes

Useful in scripts. In PowerShell, read the code with `$LASTEXITCODE`.

| Code | Meaning |
|---|---|
| `0` | Finished normally |
| `1` | Finished, but some results are `error` (including a name that could not be resolved) |
| `2` | Bad input, or a safety check refused the run. Nothing was scanned. |
| `3` | Unexpected internal failure |
| `130` | Stopped with Ctrl + C; partial results were kept |

## 15. Common errors and fixes

| The message says | Fix |
|---|---|
| `requires --yes-authorized` | Add `--yes-authorized` |
| `require --allow-public` | The target is on the internet; add the flag only if you are authorized |
| `special-purpose addresses cannot be scanned` | That address type is always blocked (see section 10) |
| `over the cap of 256` | Use a smaller range, or add `--max-hosts` |
| `has host bits set; did you mean …` | Use the suggested range, e.g. `192.168.1.0/24` instead of `192.168.1.5/24` |
| `inverted port range` | Put the smaller number first: `1-100`, not `100-1` |
| `looks like a CIDR range` | Use `discover` for ranges; `scan` takes one machine |
| `conflicting output formats` | Use only one of `--json`, `--csv`, `--format` |
| `cannot infer the format from the file name` | Name the file `.json` or `.csv`, or add `--format` |
| `targets outside the allowlist` | Add the target to the allowlist file, or narrow the range |
| `cannot resolve …` | Check the hostname spelling and your DNS/network connection |
| `netscope` is not recognized | Activate the virtual environment (section 2) |

### Windows note

Windows normally takes about 2 seconds to report a closed port. NetScope switches that delay off for its own connections, so closed ports are reported in milliseconds. A side effect is that a single lost network packet is not retried, so that check shows as `inconclusive` rather than `closed`.

## 16. Quick reference

```text
netscope scan TARGET [options]          one machine (IPv4 address or hostname)
netscope discover NETWORK [options]     a range, e.g. 192.168.1.0/24

  -p, --ports / --discovery-ports   ports to check           default 22,80,443
  -t, --timeout                     seconds per check        default 1
  -c, --concurrency                 checks at once           default 100
  -r, --rate                        checks started per sec   default 50
      --max-hosts                   machine limit            default 256
      --allow-public                allow internet addresses
      --allowlist FILE              restrict to listed targets
      --yes-authorized              REQUIRED confirmation
  -f, --format json|csv / --json / --csv    machine-readable output
  -o, --output FILE                 save to file (.json or .csv)
  -v, --verbose / -q, --quiet       more / fewer messages
```

## 17. For developers

```powershell
python -m pytest                              # full test suite
python -m pytest -m "not integration"         # skip tests that open real sockets
python benchmarks\bench.py --quick            # short accuracy/speed benchmark
python benchmarks\bench.py                    # full concurrency x rate sweep
python tools\windows_console_ctrl_c.py .\.venv\Scripts\netscope.exe   # Windows Ctrl-C check
```

Design decisions, measurements and the build log are in [NetScope_V1_Memory_Capsule.md](NetScope_V1_Memory_Capsule.md).

---

### Exporting this guide

- **On GitHub**, this file renders as a formatted page; share its link directly.
- **As PDF**, from VS Code: open the Markdown preview (`Ctrl+Shift+V`) and print it, or use a Markdown-to-PDF extension.
- **With [pandoc](https://pandoc.org/)**, if installed:

  ```powershell
  pandoc docs/USER_GUIDE.md -o NetScope_User_Guide.html --standalone --metadata title="NetScope User Guide"
  pandoc docs/USER_GUIDE.md -o NetScope_User_Guide.docx
  ```
