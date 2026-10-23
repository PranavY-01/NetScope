"""Ground-truth benchmark for NetScope's scan engine (loopback only).

Fixture, per run:
  * open    - real asyncio listeners on 127.0.0.1 ephemeral ports
  * closed  - ephemeral ports bound then released, so a real connect is refused
  * timeout - simulated lab drop: the connect seam hangs (loopback cannot drop SYNs)
  * error   - simulated local failure: the connect seam raises EMFILE
Ground truth is written to its own file before any scan runs and is never derived from scanner output.

Usage:
    python benchmarks/bench.py                      # default sweep
    python benchmarks/bench.py --quick              # smaller sweep for a smoke check
    python benchmarks/bench.py --mitigation-check   # Windows: fast refusal on vs off
"""

import argparse
import asyncio
import errno
import json
import platform
import random
import socket
import statistics
import sys
import time
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path

from netscope import __version__
from netscope.config import ScanConfig
from netscope.models import PortState
from netscope.probe import make_tcp_connect
from netscope.scanner import iter_scan, prepare_scan

RESULTS_DIR = Path(__file__).parent / "results"
EXPECTED = {"open": PortState.OPEN, "closed": PortState.CLOSED, "timeout": PortState.INCONCLUSIVE, "error": PortState.ERROR}


def _released_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def build_fixture(n_open: int, n_closed: int, n_timeout: int, n_error: int, seed: int):
    async def handle(reader, writer):
        writer.close()

    servers = [await asyncio.start_server(handle, "127.0.0.1", 0, backlog=512) for _ in range(n_open)]
    truth: dict[int, str] = {s.sockets[0].getsockname()[1]: "open" for s in servers}
    for label, count in (("closed", n_closed), ("timeout", n_timeout), ("error", n_error)):
        while count:
            port = _released_port()
            if port not in truth:
                truth[port] = label
                count -= 1
    rng = random.Random(seed)
    order = list(truth)
    rng.shuffle(order)
    return servers, {p: truth[p] for p in order}


def lab_connect(truth: dict[int, str], fast_refusal: bool):
    real = make_tcp_connect(fast_refusal)

    async def connect(ip: str, port: int) -> None:
        label = truth[port]
        if label == "timeout":
            await asyncio.sleep(3600)
        if label == "error":
            raise OSError(errno.EMFILE, "simulated: too many open files")
        await real(ip, port)

    return connect


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


async def one_run(truth, concurrency, rate, timeout, fast_refusal):
    cfg = ScanConfig(
        target="127.0.0.1",
        ports=tuple(truth),
        timeout=timeout,
        concurrency=concurrency,
        rate=rate,
        authorized=True,
        fast_refusal=fast_refusal,
    )
    plan = await prepare_scan(cfg)
    connect = lab_connect(truth, fast_refusal)
    cpu0, t0 = time.process_time(), time.perf_counter()
    results = []
    async with aclosing(iter_scan(plan, connect=connect)) as stream:
        async for result in stream:
            results.append(result)
    wall = time.perf_counter() - t0
    cpu = time.process_time() - cpu0

    confusion: dict[str, dict[str, int]] = {}
    latency: dict[str, list[float]] = {}
    correct = 0
    for r in results:
        label = truth[r.port]
        confusion.setdefault(label, {}).setdefault(r.state.value, 0)
        confusion[label][r.state.value] += 1
        latency.setdefault(label, []).append(r.elapsed_ms)
        correct += r.state is EXPECTED[label]
    return {
        "concurrency": concurrency,
        "rate": rate,
        "timeout": timeout,
        "fast_refusal": fast_refusal,
        "probes": len(truth),
        "completed": len(results),
        "accuracy": round(correct / len(truth), 4),
        "false_filtered": sum(r.state is PortState.FILTERED for r in results),
        "duration_s": round(wall, 3),
        "throughput_pps": round(len(results) / wall, 1),
        "cpu_s": round(cpu, 3),
        "latency_ms": {k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 3)} for k, v in latency.items()},
        "confusion": confusion,
    }


def summarize(runs):
    keys = ("duration_s", "throughput_pps", "accuracy", "cpu_s")
    out = {k: round(statistics.median(r[k] for r in runs), 4) for k in keys}
    out["duration_s_spread"] = [min(r["duration_s"] for r in runs), max(r["duration_s"] for r in runs)]
    out["false_filtered"] = sum(r["false_filtered"] for r in runs)
    out["complete"] = all(r["completed"] == r["probes"] for r in runs)
    for label in ("open", "closed"):
        values = [r["latency_ms"][label]["p95"] for r in runs if label in r["latency_ms"]]
        out[f"{label}_p95_ms"] = round(max(values), 3) if values else None
    return out


async def main(args):
    servers, truth = await build_fixture(args.open, args.closed, args.timeouts, args.errors, args.seed)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(exist_ok=True)
    counts = {label: sum(1 for v in truth.values() if v == label) for label in EXPECTED}
    (RESULTS_DIR / f"{stamp}-ground-truth.json").write_text(json.dumps({"ports": truth, "counts": counts}, indent=2))

    if args.mitigation_check:
        grid = [(100, 1000, True), (100, 1000, False)]
    else:
        grid = [(c, r, True) for c in args.concurrency for r in args.rate]
    meta = {
        "netscope": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "timeout_s": args.timeout,
        "repeats": args.repeats,
        "fixture": counts,
    }
    print(f"fixture {counts} timeout={args.timeout}s repeats={args.repeats} ({meta['platform']})")
    print(f"{'conc':>5} {'rate':>6} {'fast':>5} {'dur_s':>7} {'spread':>13} {'pps':>7} {'acc':>6} {'cpu_s':>6} {'open95':>7} {'clsd95':>7} filt")
    sweep = []
    try:
        for concurrency, rate, fast in grid:
            await one_run(truth, concurrency, rate, args.timeout, fast)  # warm-up, discarded
            runs = [await one_run(truth, concurrency, rate, args.timeout, fast) for _ in range(args.repeats)]
            s = summarize(runs)
            sweep.append({"concurrency": concurrency, "rate": rate, "fast_refusal": fast, "summary": s, "runs": runs})
            spread = f"{s['duration_s_spread'][0]:.2f}-{s['duration_s_spread'][1]:.2f}"
            print(
                f"{concurrency:>5} {rate:>6g} {str(fast):>5} {s['duration_s']:>7.2f} {spread:>13} {s['throughput_pps']:>7.1f} "
                f"{s['accuracy']:>6.3f} {s['cpu_s']:>6.2f} {s['open_p95_ms'] or 0:>7.1f} {s['closed_p95_ms'] or 0:>7.1f} {s['false_filtered']}"
            )
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
    out = RESULTS_DIR / f"{stamp}-sweep.json"
    out.write_text(json.dumps({"meta": meta, "sweep": sweep}, indent=2))
    print(f"results: {out}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--open", type=int, default=60)
    p.add_argument("--closed", type=int, default=80)
    p.add_argument("--timeouts", type=int, default=50)
    p.add_argument("--errors", type=int, default=10)
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--concurrency", type=int, nargs="+", default=[10, 50, 100, 200])
    p.add_argument("--rate", type=float, nargs="+", default=[50, 200, 1000, 5000])
    p.add_argument("--quick", action="store_true")
    p.add_argument("--mitigation-check", action="store_true")
    args = p.parse_args()
    if args.quick:
        args.concurrency, args.rate, args.repeats = [10, 100], [50, 1000], 2
    return args


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
