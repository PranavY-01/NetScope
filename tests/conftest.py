import socket

import pytest


@pytest.fixture
def no_sockets(monkeypatch):
    """Fail the test if anything constructs a socket."""

    def forbidden(*args, **kwargs):
        raise AssertionError("a socket was opened in a test that must not touch the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
