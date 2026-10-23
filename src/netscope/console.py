"""Human-readable Rich rendering of reports (CLI layer only).

Values are added as Text, never as markup, so hostnames or error messages cannot inject styling.
"""

from rich.console import Console
from rich.table import Table
from rich.text import Text

from netscope.models import DiscoveryReport, HostState, PortState, ScanReport

# Rows of the most common uninformative state are collapsed into the summary above this count.
COLLAPSE_THRESHOLD = 25

STYLES = {
    PortState.OPEN: "bold green",
    PortState.CLOSED: "dim",
    PortState.FILTERED: "yellow",
    PortState.INCONCLUSIVE: "yellow",
    PortState.ERROR: "bold red",
    HostState.RESPONSIVE: "bold green",
    HostState.NO_RESPONSE: "dim",
    HostState.ERROR: "bold red",
}


def _state(state: PortState | HostState) -> Text:
    return Text(state.value, style=STYLES[state])


def _detail(code: str | None, message: str | None) -> Text:
    parts = [p for p in (code, message) if p]
    return Text(" - ".join(parts), style="dim")


def _summary(counts: dict, duration_ms: float, interrupted: bool, done: int, requested: int, noun: str) -> Text:
    text = Text()
    for state, count in counts.items():
        if count:
            text.append(f"{count} ", style="bold")
            text.append(state.value, style=STYLES[state])
            text.append("  ")
    text.append(f"in {duration_ms / 1000:.2f}s", style="dim")
    if interrupted:
        text.append(f"\nINTERRUPTED: partial results, {done} of {requested} {noun} completed", style="bold yellow")
    return text


def render_scan(console: Console, report: ScanReport) -> None:
    md = report.metadata
    header = Text("NetScope scan of ", style="bold")
    header.append(md.target_input)
    if report.resolved_ips and [str(ip) for ip in report.resolved_ips] != [md.target_input]:
        header.append(f" ({', '.join(map(str, report.resolved_ips))})", style="dim")
    console.print(header)
    for error in report.target_errors:
        console.print(Text.assemble(("target error: ", "bold red"), f"{error.target_input}: ", _detail(error.error_code, error.error_message)))

    closed = [r for r in report.results if r.state is PortState.CLOSED]
    collapse = len(closed) > COLLAPSE_THRESHOLD
    rows = [r for r in report.results if not (collapse and r.state is PortState.CLOSED)]
    if rows:
        table = Table(show_edge=False, pad_edge=False, header_style="bold")
        table.add_column("IP")
        table.add_column("Port", justify="right")
        table.add_column("State")
        table.add_column("ms", justify="right")
        table.add_column("Detail", overflow="fold")
        for r in rows:
            detail = None if r.state in (PortState.OPEN, PortState.CLOSED) else _detail(r.error_code, r.error_message)
            table.add_row(str(r.ip), str(r.port), _state(r.state), f"{r.elapsed_ms:.1f}", detail)
        console.print(table)
    if collapse:
        console.print(Text(f"{len(closed)} closed ports not listed; use --format json or csv for every row.", style="dim"))
    console.print(_summary(report.summary, md.duration_ms, md.interrupted, md.completed_probes, md.requested_probes, "probes"))


def _evidence(host) -> Text:
    text = Text()
    for probe in host.evidence:
        text.append(f"{probe.port}:")
        text.append(probe.state.value, style=STYLES[probe.state])
        text.append(" ")
    return text


def render_discovery(console: Console, report: DiscoveryReport) -> None:
    md = report.metadata
    console.print(Text.assemble(("NetScope discovery of ", "bold"), md.network, (f"  probe ports {','.join(map(str, md.discovery_ports))}", "dim")))
    silent = [h for h in report.results if h.state is HostState.NO_RESPONSE]
    collapse = len(silent) > COLLAPSE_THRESHOLD
    rows = [h for h in report.results if not (collapse and h.state is HostState.NO_RESPONSE)]
    if rows:
        table = Table(show_edge=False, pad_edge=False, header_style="bold")
        table.add_column("IP")
        table.add_column("Verdict")
        table.add_column("Evidence")
        table.add_column("ms", justify="right")
        table.add_column("Detail", overflow="fold")
        for h in rows:
            table.add_row(str(h.ip), _state(h.state), _evidence(h), f"{h.elapsed_ms:.1f}", _detail(h.error_code, h.error_message))
        console.print(table)
    if collapse:
        console.print(Text(f"{len(silent)} no_response hosts not listed; use --format json or csv for every row.", style="dim"))
    console.print(_summary(report.summary, md.duration_ms, md.interrupted, md.completed_hosts, md.requested_hosts, "hosts"))
    console.print(Text("no_response means no TCP answer on the probed ports, not that the host is down.", style="dim"))
