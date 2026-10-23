"""Typer command-line interface. Wiring and presentation only; no scanning logic."""

from typing import Annotated, Optional

import typer

from netscope import __version__

app = typer.Typer(
    name="netscope",
    help="Evidence-based IPv4 TCP port scanning and host discovery for authorized networks.",
    no_args_is_help=True,
    add_completion=False,
)


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


def main() -> None:
    app()
