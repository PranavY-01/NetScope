"""Logging setup for the CLI. Logs always go to stderr so stdout can carry JSON/CSV."""

import logging

from rich.console import Console
from rich.logging import RichHandler

QUIET = logging.WARNING
NORMAL = logging.INFO
VERBOSE = logging.DEBUG


def configure_logging(level: int, console: Console) -> None:
    logger = logging.getLogger("netscope")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = RichHandler(
        console=console,
        show_time=False,
        show_path=level <= VERBOSE,
        markup=False,
        rich_tracebacks=False,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
