"""One TCP connect probe and the mapping from its outcome to a port state."""

import asyncio
import errno
import logging
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import cache
from ipaddress import IPv4Address

from netscope.models import PortResult, PortState

log = logging.getLogger(__name__)

ConnectFn = Callable[[str, int], Awaitable[None]]
"""Connect to (ip, port) and return on success; raise on failure. The caller applies the timeout."""

_WINDOWS = sys.platform == "win32"

# ConnectEx (asyncio's Proactor loop) reports refusal as ERROR_CONNECTION_REFUSED (1225), not
# WSAECONNREFUSED (10061); both are mapped (capsule §20.3).
WINERROR_REFUSED = frozenset({10061, 1225})
WINERROR_TIMEOUT = frozenset({10060, 121})
WINERROR_UNREACHABLE = frozenset({10051, 10065, 1231, 1232})
ERRNO_TIMEOUT = frozenset({errno.ETIMEDOUT})
ERRNO_UNREACHABLE = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH})

_MAX_MESSAGE = 200


if _WINDOWS:
    import ctypes
    from ctypes import wintypes

    _SIO_TCP_INITIAL_RTO = 0x98000011
    _TCP_INITIAL_RTO_UNSPECIFIED_RTT = 0xFFFF
    _TCP_INITIAL_RTO_NO_SYN_RETRANSMISSIONS = 0xFE

    class _TcpInitialRtoParameters(ctypes.Structure):
        _fields_ = [("Rtt", ctypes.c_ushort), ("MaxSynRetransmissions", ctypes.c_ubyte)]

    @cache
    def _wsa_ioctl():
        func = ctypes.WinDLL("ws2_32", use_last_error=True).WSAIoctl
        func.argtypes = [
            ctypes.c_size_t,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        func.restype = ctypes.c_int
        return func

    def disable_syn_retransmit(sock: socket.socket) -> None:
        """Stop Windows retrying the SYN after a RST, so a refusal reports in ms instead of ~2 s."""
        params = _TcpInitialRtoParameters(_TCP_INITIAL_RTO_UNSPECIFIED_RTT, _TCP_INITIAL_RTO_NO_SYN_RETRANSMISSIONS)
        returned = wintypes.DWORD(0)
        rc = _wsa_ioctl()(
            sock.fileno(),
            _SIO_TCP_INITIAL_RTO,
            ctypes.byref(params),
            ctypes.sizeof(params),
            None,
            0,
            ctypes.byref(returned),
            None,
            None,
        )
        if rc != 0:
            code = ctypes.get_last_error()
            raise OSError(code, f"SIO_TCP_INITIAL_RTO failed (WSA error {code})")

else:

    def disable_syn_retransmit(sock: socket.socket) -> None:
        """Not needed outside Windows: other stacks report a refusal immediately."""


@cache
def fast_refusal_active() -> bool:
    """Whether the Windows mitigation can be applied on this host. Opens one local socket, no traffic."""
    if not _WINDOWS:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            disable_syn_retransmit(sock)
        except OSError as exc:
            log.warning("Windows fast-refusal mitigation unavailable (%s); refusals may take ~2 s", exc)
            return False
    return True


def make_tcp_connect(fast_refusal: bool = True) -> ConnectFn:
    apply_mitigation = fast_refusal and fast_refusal_active()

    async def tcp_connect(ip: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            if apply_mitigation:
                disable_syn_retransmit(sock)
            await loop.sock_connect(sock, (ip, port))
        finally:
            sock.close()

    return tcp_connect


def _errno_name(code: int) -> str:
    """Prefer the portable name: Windows lists 10060 as WSAETIMEDOUT though errno.ETIMEDOUT == 10060."""
    name = errno.errorcode.get(code)
    if name is None:
        return str(code)
    if name.startswith("WSA") and getattr(errno, name[3:], None) == code:
        return name[3:]
    return name


def error_code(exc: BaseException) -> str:
    winerror = getattr(exc, "winerror", None)
    if winerror:
        return f"winerror:{winerror}"
    code = getattr(exc, "errno", None)
    if code is not None:
        return f"errno:{_errno_name(code)}"
    return f"exception:{type(exc).__name__}"


def _message(exc: BaseException) -> str:
    text = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    return text[:_MAX_MESSAGE]


def classify_exception(exc: Exception) -> tuple[PortState, str, str]:
    """Map a connect failure to (state, error_code, message). Never returns FILTERED (capsule §20.1)."""
    code = error_code(exc)
    winerror = getattr(exc, "winerror", None)
    err = getattr(exc, "errno", None)
    if isinstance(exc, ConnectionRefusedError) or winerror in WINERROR_REFUSED or err == errno.ECONNREFUSED:
        return PortState.CLOSED, code, "connection refused"
    if isinstance(exc, TimeoutError) or winerror in WINERROR_TIMEOUT or err in ERRNO_TIMEOUT:
        return PortState.INCONCLUSIVE, code, f"operating system connect timeout: {_message(exc)}"
    if winerror in WINERROR_UNREACHABLE or err in ERRNO_UNREACHABLE:
        # A routing/ARP-level signal, not a TCP response and not proof of filtering.
        return PortState.INCONCLUSIVE, code, f"unreachable: {_message(exc)}"
    return PortState.ERROR, code, _message(exc)


async def probe_port(
    target_input: str,
    ip: IPv4Address,
    port: int,
    *,
    timeout: float,
    connect: ConnectFn,
) -> PortResult:
    """Make one connect attempt. Cancellation propagates; every other outcome becomes a PortResult."""
    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    deadline = asyncio.timeout(timeout)
    state, code, message = PortState.OPEN, None, None
    try:
        async with deadline:
            await connect(str(ip), port)
    except TimeoutError as exc:
        if deadline.expired():
            state, code, message = PortState.INCONCLUSIVE, "timeout", f"no response within {timeout:g}s"
        else:
            state, code, message = classify_exception(exc)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, never raised
        state, code, message = classify_exception(exc)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    return PortResult(
        target_input=target_input,
        ip=ip,
        port=port,
        state=state,
        elapsed_ms=elapsed_ms,
        started_at=started_at,
        error_code=code,
        error_message=message,
    )
