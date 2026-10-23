import asyncio
import errno
import socket
import sys
from ipaddress import IPv4Address

import pytest
from timing import EARLY_WAKE_MS

from netscope.models import PortState
from netscope.probe import classify_exception, error_code, fast_refusal_active, make_tcp_connect, probe_port

LOOPBACK = IPv4Address("127.0.0.1")
WINDOWS = sys.platform == "win32"


class FakeWinError(OSError):
    """An OSError carrying a winerror attribute on any platform."""

    def __init__(self, winerror, err=None):
        super().__init__(err, f"fake winerror {winerror}")
        self.winerror = winerror


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def probe(port=80, *, connect, timeout=1.0):
    return await probe_port("t", LOOPBACK, port, timeout=timeout, connect=connect)


def raising(exc):
    async def connect(ip, port):
        raise exc

    return connect


# ---- classification table -------------------------------------------------------------------

CLASSIFICATION = [
    (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), PortState.CLOSED),
    (OSError(errno.ECONNREFUSED, "refused"), PortState.CLOSED),
    (FakeWinError(10061), PortState.CLOSED),
    (FakeWinError(1225, errno.EINVAL), PortState.CLOSED),  # what the Proactor loop actually raises
    (TimeoutError(errno.ETIMEDOUT, "timed out"), PortState.INCONCLUSIVE),
    (FakeWinError(10060), PortState.INCONCLUSIVE),
    (FakeWinError(121), PortState.INCONCLUSIVE),
    (OSError(errno.EHOSTUNREACH, "no route to host"), PortState.INCONCLUSIVE),
    (OSError(errno.ENETUNREACH, "network unreachable"), PortState.INCONCLUSIVE),
    (FakeWinError(1231), PortState.INCONCLUSIVE),
    (FakeWinError(1232), PortState.INCONCLUSIVE),
    (OSError(errno.EMFILE, "too many open files"), PortState.ERROR),
    (OSError(errno.EADDRNOTAVAIL, "cannot assign address"), PortState.ERROR),
    (OSError(errno.ECONNRESET, "reset"), PortState.ERROR),
    (PermissionError(errno.EACCES, "denied"), PortState.ERROR),
    (FakeWinError(10013), PortState.ERROR),
    (RuntimeError("bug"), PortState.ERROR),
]


@pytest.mark.parametrize(("exc", "expected"), CLASSIFICATION)
def test_classify_exception(exc, expected):
    state, code, message = classify_exception(exc)
    assert state is expected
    assert code and message


@pytest.mark.parametrize("code", sorted(errno.errorcode))
def test_no_errno_ever_maps_to_filtered(code):
    assert classify_exception(OSError(code, "x"))[0] is not PortState.FILTERED


@pytest.mark.parametrize("winerror", [121, 1225, 1231, 1232, 1236, 10013, 10050, 10051, 10060, 10061, 10064, 10065])
def test_no_winerror_ever_maps_to_filtered(winerror):
    assert classify_exception(FakeWinError(winerror))[0] is not PortState.FILTERED


def test_error_codes_are_stable_strings():
    assert error_code(FakeWinError(1225)) == "winerror:1225"
    assert error_code(OSError(errno.ECONNRESET, "x")) == "errno:ECONNRESET"
    assert error_code(OSError(99999, "x")) == "errno:99999"
    assert error_code(RuntimeError("x")) == "exception:RuntimeError"


# ---- probe_port with injected connect functions ---------------------------------------------


async def test_success_is_open():
    async def ok(ip, port):
        assert (ip, port) == ("127.0.0.1", 80)

    result = await probe(connect=ok)
    assert result.state is PortState.OPEN
    assert result.error_code is None
    assert result.ip == LOOPBACK and result.target_input == "t"
    assert result.started_at.tzinfo is not None


async def test_deadline_timeout_is_inconclusive_never_filtered():
    async def hang(ip, port):
        await asyncio.sleep(30)

    result = await probe(connect=hang, timeout=0.05)
    assert result.state is PortState.INCONCLUSIVE
    assert result.state is not PortState.FILTERED
    assert result.error_code == "timeout"
    assert 50 - EARLY_WAKE_MS <= result.elapsed_ms < 2000


async def test_os_level_timeout_is_inconclusive():
    result = await probe(connect=raising(TimeoutError(errno.ETIMEDOUT, "timed out")))
    assert result.state is PortState.INCONCLUSIVE
    assert result.error_code == "errno:ETIMEDOUT"


async def test_refusal_is_closed():
    result = await probe(connect=raising(FakeWinError(1225, errno.EINVAL)))
    assert result.state is PortState.CLOSED
    assert result.error_code == "winerror:1225"


async def test_unexpected_failure_is_error_with_context():
    result = await probe(connect=raising(ValueError("weird")))
    assert result.state is PortState.ERROR
    assert result.error_code == "exception:ValueError"
    assert result.error_message == "weird"


async def test_cancellation_propagates():
    started = asyncio.Event()

    async def hang(ip, port):
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(probe(connect=hang, timeout=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_elapsed_is_measured():
    async def slow(ip, port):
        await asyncio.sleep(0.05)

    result = await probe(connect=slow)
    assert result.state is PortState.OPEN
    assert result.elapsed_ms >= 50 - EARLY_WAKE_MS


# ---- real sockets on loopback ----------------------------------------------------------------


@pytest.fixture
async def listener():
    async def handle(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


@pytest.mark.integration
async def test_real_listener_is_open(listener):
    result = await probe(listener, connect=make_tcp_connect())
    assert result.state is PortState.OPEN


@pytest.mark.integration
async def test_real_unused_port_is_closed_quickly():
    port = unused_port()  # tiny race: another process could claim it before we connect
    result = await probe(port, connect=make_tcp_connect())
    assert result.state is PortState.CLOSED, result
    # With the Windows mitigation the refusal is fast; without it Windows takes ~2 s.
    assert result.elapsed_ms < 500


@pytest.mark.integration
@pytest.mark.skipif(not WINDOWS, reason="Windows-specific SYN retransmission behaviour")
async def test_windows_without_mitigation_refusal_exceeds_short_timeout():
    """Documents why the mitigation exists: without it a refused port looks inconclusive."""
    assert fast_refusal_active()
    result = await probe(unused_port(), connect=make_tcp_connect(fast_refusal=False), timeout=0.5)
    assert result.state is PortState.INCONCLUSIVE


@pytest.mark.skipif(WINDOWS, reason="non-Windows")
def test_fast_refusal_inactive_off_windows():
    assert fast_refusal_active() is False
