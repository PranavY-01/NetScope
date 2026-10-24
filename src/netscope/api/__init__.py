"""Local FastAPI application around the NetScope engine. Requires the `web` extra."""

from netscope.api.app import create_app

__all__ = ["create_app"]
