"""Typer command-line interface. Wiring and presentation only; scanning lives in the engine modules."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.text import Text

from netscope import __version__, probe, resolver
from netscope.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_HOSTS,
    DEFAULT_PORTS,
    DEFAULT_RATE_PER_S,
    DEFAULT_TIMEOUT_S,
    ConfigError,
    DiscoveryConfig,
    ScanConfig,
)
from netscope.console import render_discovery, render_scan
from netscope.discovery import DiscoveryPlan, build_discovery_report, iter_discovery, prepare_discovery
from netscope.guardrails import ScopeError, load_allowlist
from netscope.logging_config import NORMAL, QUIET, VERBOSE, configure_logging
from netscope.models import DiscoveryReport, ScanReport
from netscope.output import render, write_atomic
from netscope.scanner import ScanPlan, build_scan_report, iter_scan, prepare_scan
from netscope.scheduler import RunTimer

log = logging.getLogger("netscope.cli")

EXIT_OK = 0
EXIT_RESULT_ERRORS = 1
EXIT_USAGE = 2
EXIT_FATAL = 3
EXIT_INTERRUPTED = 130

app = typer.Typer(
    name="netscope",
    help="Evidence-based IPv4 TCP port scanning and host discovery for authorized networks.",
    no_args_is_help=True,
    add_completion=False,
)


class OutputFormat(StrEnum):
    json = "json"
    csv = "csv"


_DEFAULT_PORT_SPEC = ",".join(map(str, DEFAULT_PORTS))
_FIELD_FLAGS = {
    "ports": "--ports",
    "timeout": "--timeout",
    "concurrency": "--concurrency",
    "rate": "--rate",
    "max_hosts": "--max-hosts",
    "target": "TARGET",
    "network": "NETWORK",
}

TimeoutOpt = Annotated[float, typer.Option("--timeout", "-t", help="TCP connect timeout per probe, in seconds.")]
ConcurrencyOpt = Annotated[int, typer.Option("--concurrency", "-c", help="Maximum probes in flight at once.")]
RateOpt = Annotated[float, typer.Option("--rate", "-r", help="Maximum probe starts per second, across the whole run.")]
MaxHostsOpt = Annotated[
    int, typer.Option("--max-hosts", help="Host cap. Raising it above the default is logged as a warning.")
]
AllowPublicOpt = Annotated[
    bool, typer.Option("--allow-public", help="Permit globally routable targets. Never bypasses other checks.")
]
AllowlistOpt = Annotated[
    Optional[Path],
    typer.Option("--allowlist", help="File of permitted IPv4 addresses/CIDRs; any target outside it rejects the run."),
]
AuthorizedOpt = Annotated[
    bool,
    typer.Option(
        "--yes-authorized",
        help="Required. Confirms you own or are explicitly authorized to test every target. Not proof of authorization.",
    ),
]
FormatOpt = Annotated[Optional[OutputFormat], typer.Option("--format", "-f", help="Machine-readable output format.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Shorthand for --format json.")]
CsvOpt = Annotated[bool, typer.Option("--csv", help="Shorthand for --format csv.")]
OutputOpt = Annotated[
    Optional[Path],
    typer.Option("--output", "-o", help="Write the report to this file (atomically) instead of stdout."),
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging on stderr.")]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only; no progress bar.")]


@dataclass(frozen=True)
class _Output:
    fmt: str | None
    path: Path | None
    quiet: bool


@dataclass(frozen=True)
class _Job:
    """The scan/discover differences, so both commands share one execution path."""

    noun: str
    prepare: Callable[[], Any]
    iterate: Callable[[Any], AsyncIterator]
    build: Callable[..., ScanReport | DiscoveryReport]
    total: Callable[[Any], int]
    describe: Callable[[Any], str]


def _err_console() -> Console:
    return Console(stderr=True)


def _fail(message: str) -> None:
    _err_console().print(Text.assemble(("error: ", "bold red"), message))


def _resolve_output(
    fmt: OutputFormat | None, as_json: bool, as_csv: bool, output: Path | None, quiet: bool
) -> _Output:
    chosen = {name for name, flag in (("json", as_json), ("csv", as_csv)) if flag}
    if fmt is not None:
        chosen.add(fmt.value)
    if len(chosen) > 1:
        raise typer.BadParameter(
            f"conflicting output formats ({' and '.join(sorted(chosen))}); choose one",
            param_hint="'--format' / '--json' / '--csv'",
        )
    selected = chosen.pop() if chosen else None
    if output is not None and selected is None:
        suffix = output.suffix.lower().lstrip(".")
        if suffix not in ("json", "csv"):
            raise typer.BadParameter(
                "cannot infer the format from the file name; add --format json or --format csv",
                param_hint="'--output'",
            )
        selected = suffix
    return _Output(fmt=selected, path=output, quiet=quiet)


def _verbosity(verbose: bool, quiet: bool) -> int:
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive", param_hint="'--verbose' / '--quiet'")
    return VERBOSE if verbose else QUIET if quiet else NORMAL


def _config_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        field = ".".join(str(p) for p in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        parts.append(f"{_FIELD_FLAGS.get(field, field)}: {message}")
    return "; ".join(parts)


def _build_config(model: type, allowlist: Path | None, **fields: Any) -> Any:
    if allowlist is not None:
        fields["allowlist"] = load_allowlist(allowlist)
    if fields["max_hosts"] > DEFAULT_MAX_HOSTS:
        log.warning("host cap raised from %d to %d by --max-hosts", DEFAULT_MAX_HOSTS, fields["max_hosts"])
    return model(**fields)


@contextmanager
def _progress(console: Console, total: int, noun: str, enabled: bool) -> Iterator[Callable[[], None]]:
    if not enabled or not console.is_terminal or total == 0:
        yield lambda: None
        return
    columns = (TextColumn(f"[bold]{noun}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task(noun, total=total)
        yield lambda: progress.advance(task)


async def _consume(stream: AsyncIterator, sink: list, advance: Callable[[], None]) -> None:
    async with aclosing(stream) as results:
        async for item in results:
            sink.append(item)
            advance()


def _emit(report: ScanReport | DiscoveryReport, out: _Output) -> None:
    if out.fmt is None:
        console = Console()
        if isinstance(report, ScanReport):
            render_scan(console, report)
        else:
            render_discovery(console, report)
        return
    text = render(report, out.fmt)
    if out.path is None:
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        write_atomic(out.path, text)
        log.info("wrote %s report to %s", out.fmt, out.path)


def _execute(job: _Job, out: _Output) -> int:
    try:
        plan = asyncio.run(job.prepare())
    except ScopeError as exc:
        _fail(f"{exc.message} [{exc.code}]")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _fail("interrupted before probing started; nothing was scanned")
        return EXIT_INTERRUPTED

    total = job.total(plan)
    log.info(job.describe(plan))
    collected: list = []
    timer = RunTimer()
    interrupted = False
    with _progress(_err_console(), total, job.noun, enabled=not out.quiet) as advance:
        try:
            asyncio.run(_consume(job.iterate(plan), collected, advance))
        except KeyboardInterrupt:
            interrupted = True

    report = job.build(plan, collected, timer=timer, interrupted=interrupted)
    _emit(report, out)
    if interrupted:
        log.warning("interrupted: %d of %d %s completed; partial report emitted", len(collected), total, job.noun)
        return EXIT_INTERRUPTED
    return EXIT_RESULT_ERRORS if report.has_errors else EXIT_OK


def _run_command(build_job: Callable[[], _Job], out: _Output) -> None:
    try:
        job = build_job()
    except ValidationError as exc:
        _fail(_config_message(exc))
        raise typer.Exit(EXIT_USAGE) from None
    except ConfigError as exc:
        _fail(str(exc))
        raise typer.Exit(EXIT_USAGE) from None
    try:
        code = _execute(job, out)
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the exit-code contract
        log.debug("fatal error", exc_info=True)
        _fail(f"internal error: {type(exc).__name__}: {exc}")
        code = EXIT_FATAL
    raise typer.Exit(code)


def _settings_line(config: ScanConfig | DiscoveryConfig) -> str:
    return f"timeout {config.timeout:g}s, concurrency {config.concurrency}, rate {config.rate:g}/s"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"netscope {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
    ] = None,
) -> None:
    """Only scan systems you own or are explicitly authorized to test."""


@app.command()
def scan(
    target: Annotated[
        str, typer.Argument(metavar="TARGET", help="IPv4 address or hostname. Every resolved IPv4 address is scanned.")
    ],
    ports: Annotated[str, typer.Option("--ports", "-p", help="Ports, lists and ranges, e.g. 22,80,8000-8010.")] = _DEFAULT_PORT_SPEC,
    timeout: TimeoutOpt = DEFAULT_TIMEOUT_S,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    rate: RateOpt = DEFAULT_RATE_PER_S,
    max_hosts: MaxHostsOpt = DEFAULT_MAX_HOSTS,
    allow_public: AllowPublicOpt = False,
    allowlist: AllowlistOpt = None,
    yes_authorized: AuthorizedOpt = False,
    fmt: FormatOpt = None,
    as_json: JsonOpt = False,
    as_csv: CsvOpt = False,
    output: OutputOpt = None,
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """TCP connect scan of one IPv4 address or hostname."""
    configure_logging(_verbosity(verbose, quiet), _err_console())
    out = _resolve_output(fmt, as_json, as_csv, output, quiet)

    def build() -> _Job:
        config = _build_config(
            ScanConfig,
            allowlist,
            target=target,
            ports=ports,
            timeout=timeout,
            concurrency=concurrency,
            rate=rate,
            max_hosts=max_hosts,
            allow_public=allow_public,
            authorized=yes_authorized,
        )

        def describe(plan: ScanPlan) -> str:
            addresses = ", ".join(map(str, plan.ips)) or "no addresses"
            return (
                f"scanning {config.target} ({addresses}): {len(config.ports)} ports, "
                f"{plan.requested_probes} probes; {_settings_line(config)}"
            )

        return _Job(
            noun="probes",
            prepare=lambda: prepare_scan(config, resolver=resolver.system_resolver),
            iterate=lambda plan: iter_scan(plan, connect=probe.make_tcp_connect(config.fast_refusal)),
            build=build_scan_report,
            total=lambda plan: plan.requested_probes,
            describe=describe,
        )

    _run_command(build, out)


@app.command()
def discover(
    network: Annotated[
        str, typer.Argument(metavar="NETWORK", help="IPv4 CIDR, e.g. 192.168.1.0/24 (a bare address means /32).")
    ],
    discovery_ports: Annotated[
        str, typer.Option("--discovery-ports", "-p", help="TCP ports probed on each host.")
    ] = _DEFAULT_PORT_SPEC,
    timeout: TimeoutOpt = DEFAULT_TIMEOUT_S,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    rate: RateOpt = DEFAULT_RATE_PER_S,
    max_hosts: MaxHostsOpt = DEFAULT_MAX_HOSTS,
    allow_public: AllowPublicOpt = False,
    allowlist: AllowlistOpt = None,
    yes_authorized: AuthorizedOpt = False,
    fmt: FormatOpt = None,
    as_json: JsonOpt = False,
    as_csv: CsvOpt = False,
    output: OutputOpt = None,
    verbose: VerboseOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """Evidence-based TCP host discovery across an IPv4 CIDR."""
    configure_logging(_verbosity(verbose, quiet), _err_console())
    out = _resolve_output(fmt, as_json, as_csv, output, quiet)

    def build() -> _Job:
        config = _build_config(
            DiscoveryConfig,
            allowlist,
            network=network,
            ports=discovery_ports,
            timeout=timeout,
            concurrency=concurrency,
            rate=rate,
            max_hosts=max_hosts,
            allow_public=allow_public,
            authorized=yes_authorized,
        )

        async def prepare() -> DiscoveryPlan:
            return prepare_discovery(config)

        def describe(plan: DiscoveryPlan) -> str:
            return (
                f"discovering {config.network}: {plan.requested_hosts} hosts x {len(config.ports)} ports, "
                f"{plan.requested_probes} probes; {_settings_line(config)}"
            )

        return _Job(
            noun="hosts",
            prepare=prepare,
            iterate=lambda plan: iter_discovery(plan, connect=probe.make_tcp_connect(config.fast_refusal)),
            build=build_discovery_report,
            total=lambda plan: plan.requested_hosts,
            describe=describe,
        )

    _run_command(build, out)


def main() -> None:
    app()
