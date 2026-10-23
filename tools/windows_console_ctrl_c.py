"""Manual verification (Windows): deliver a genuine console Ctrl-C to running netscope scans.

Each scan runs in its own console window. A helper process attaches to that console and calls
GenerateConsoleCtrlEvent(CTRL_C_EVENT), the same event a Ctrl-C keypress produces. The script then
checks the exit code is 130 and the partial JSON/CSV parses.

Usage (PowerShell, from the repo root):
    .\\.venv\\Scripts\\python.exe tools\\windows_console_ctrl_c.py .\\.venv\\Scripts\\netscope.exe

It scans only 127.0.0.1, slowly (5 probes/s), and stops each run after about 3 seconds.
"""

import csv
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def send_ctrl_c(pid: int) -> None:
    """Run in a helper process: attach to the target's console and raise Ctrl-C there."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        sys.exit(f"AttachConsole failed: {ctypes.get_last_error()}")
    k32.SetConsoleCtrlHandler(None, True)  # this helper ignores the event it generates
    if not k32.GenerateConsoleCtrlEvent(0, 0):  # CTRL_C_EVENT to every process on that console
        sys.exit(f"GenerateConsoleCtrlEvent failed: {ctypes.get_last_error()}")


def run(netscope: str, workdir: Path, fmt: str, use_file: bool) -> bool:
    out_path = workdir / f"out.{fmt}"
    stdout_path = workdir / f"stdout.{fmt}"
    stderr_path = workdir / f"stderr_{fmt}_{use_file}.txt"
    args = [netscope, "scan", "127.0.0.1", "-p", "1-200", "--rate", "5", "--yes-authorized", "--format", fmt]
    if use_file:
        args += ["-o", str(out_path)]
    with open(stdout_path, "w", encoding="utf-8") as so, open(stderr_path, "w", encoding="utf-8") as se:
        child = subprocess.Popen(args, stdout=so, stderr=se, creationflags=subprocess.CREATE_NEW_CONSOLE)
        time.sleep(3)
        helper = subprocess.run([sys.executable, __file__, "--send", str(child.pid)], capture_output=True, text=True)
        if helper.returncode:
            print("helper failed:", helper.stdout, helper.stderr)
        code = child.wait(timeout=30)
    text = (out_path if use_file else stdout_path).read_text(encoding="utf-8")
    if fmt == "json":
        md = json.loads(text)["metadata"]
        detail = f"interrupted={md['interrupted']} completed={md['completed_probes']}/{md['requested_probes']}"
    else:
        rows = list(csv.DictReader(text.splitlines()))
        detail = f"{len(rows)} csv rows"
    ok = code == 130
    print(f"{'PASS' if ok else 'FAIL'} {fmt} via {'file' if use_file else 'stdout'}: exit={code} {detail}")
    return ok


def main() -> int:
    if sys.platform != "win32":
        sys.exit("Windows only")
    if sys.argv[1] == "--send":
        send_ctrl_c(int(sys.argv[2]))
        return 0
    with tempfile.TemporaryDirectory(prefix="netscope-ctrlc-") as tmp:
        results = [run(sys.argv[1], Path(tmp), fmt, use_file) for use_file in (False, True) for fmt in ("json", "csv")]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
