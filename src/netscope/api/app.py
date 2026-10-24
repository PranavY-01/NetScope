"""FastAPI application: HTTP wiring only. Scanning, scope policy, jobs and persistence live elsewhere.

Security model (local-first): bind to loopback (enforced by `netscope serve`), accept only loopback
Host headers (DNS-rebinding defence), require a custom header on every mutating request (CSRF
defence: a cross-site page cannot send it without a CORS preflight, and no CORS is enabled), cap
request bodies, and send restrictive security headers. There is no user authentication, so the
server must never be exposed beyond the local machine.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from netscope import __version__, resolver as resolver_module
from netscope.api.schemas import (
    MAX_BODY_BYTES,
    MAX_PAGE,
    MAX_PORTS,
    MAX_PROBES,
    AddressResults,
    CancelOut,
    DiscoveryResultsPage,
    Health,
    Limits,
    Progress,
    ScanCreate,
    ScanFailure,
    ScanOut,
    ScanPage,
    ScanResultsPage,
    ScanStatus,
    Stats,
)
from netscope.config import ConfigError, DiscoveryConfig, ScanConfig
from netscope.discovery import prepare_discovery
from netscope.estimate import DurationEstimate
from netscope.guardrails import ScopeError, parse_allowlist
from netscope.jobs import CapacityError, ConnectFactory, JobLimits, JobManager, ShuttingDownError
from netscope.output import render
from netscope.resolver import Resolver
from netscope.scanner import prepare_scan
from netscope.store import ScanRow, ScanStore

log = logging.getLogger(__name__)

CLIENT_HEADER = "X-NetScope-Client"
SCAN_ID = Annotated[str, PathParam(pattern=r"^[0-9a-f]{32}$", description="Scan identifier")]
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_FIELD_NAMES = {"network": "target"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields


def _error(status: int, code: str, message: str, fields: list[dict[str, str]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if fields:
        body["fields"] = fields
    return JSONResponse({"error": body}, status_code=status)


def _validation_fields(errors: Sequence[dict[str, Any]], skip_prefix: tuple[str, ...] = ()) -> list[dict[str, str]]:
    fields = []
    for error in errors:
        loc = [str(p) for p in error["loc"] if str(p) not in skip_prefix]
        name = ".".join(loc) or "body"
        fields.append({"field": _FIELD_NAMES.get(name, name), "message": error["msg"].removeprefix("Value error, ")})
    return fields


class GuardMiddleware(BaseHTTPMiddleware):
    """CSRF header, body-size cap and security headers. Host checking is TrustedHostMiddleware's job."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in _UNSAFE_METHODS:
            if request.headers.get(CLIENT_HEADER) is None:
                response = _error(403, "missing_client_header", f"mutating requests must send the {CLIENT_HEADER} header")
                return self._secure(response)
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return self._secure(_error(413, "body_too_large", f"request bodies are limited to {MAX_BODY_BYTES} bytes"))
        return self._secure(await call_next(request))

    @staticmethod
    def _secure(response: Response) -> Response:
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if response.headers.get("content-type", "").startswith("application/json"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _scan_out(row: ScanRow, manager: JobManager) -> ScanOut:
    status, completed = row.status, row.completed
    live = manager.live(row.id)
    if live is not None:
        status, completed = live.status, live.completed
    terminal = status in ("completed", "cancelled", "failed")
    return ScanOut(
        id=row.id,
        kind=row.kind,
        target=row.target,
        status=status,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at if terminal else None,
        progress=Progress(
            completed=completed,
            total=row.total,
            unit="probes" if row.kind == "scan" else "hosts",
            determinate=row.total > 0,
        ),
        settings=row.settings,
        resolved_ips=row.resolved_ips,
        target_errors=row.target_errors,
        summary=row.summary,
        estimate=DurationEstimate(**row.estimate) if row.estimate else None,
        error=ScanFailure(code=row.error_code, message=row.error_message or "") if row.error_code else None,
        partial=terminal and (row.interrupted or completed < row.total),
        exportable=terminal and row.metadata is not None,
    )


def create_app(
    *,
    db_path: Path | str,
    static_dir: Path | None = None,
    limits: JobLimits = JobLimits(),
    connect_factory: ConnectFactory | None = None,
    resolver: Resolver | None = None,
    allowed_hosts: Sequence[str] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    """Build the app. `connect_factory` and `resolver` are test seams; production uses the engine defaults."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = ScanStore(db_path)
        store.initialize()
        recovered = store.recover_interrupted()
        if recovered:
            log.warning("marked %d scan(s) left running by a previous process as failed", recovered)
        manager = JobManager(store, limits=limits, connect_factory=connect_factory)
        app.state.store, app.state.manager = store, manager
        try:
            yield
        finally:
            await manager.shutdown()
            store.close()

    app = FastAPI(
        title="NetScope",
        version=__version__,
        description="Local API for authorized TCP port scanning and host discovery.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(GuardMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.fields)

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(request: Request, exc: RequestValidationError):
        return _error(422, "invalid_request", "the request is invalid", _validation_fields(exc.errors(), ("body", "query", "path")))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error(500, "internal_error", "internal server error")

    def store_of(request: Request) -> ScanStore:
        return request.app.state.store

    def manager_of(request: Request) -> JobManager:
        return request.app.state.manager

    async def scan_row(request: Request, scan_id: str) -> ScanRow:
        row = await asyncio.to_thread(store_of(request).get_scan, scan_id)
        if row is None:
            raise ApiError(404, "not_found", f"no scan with id {scan_id}")
        return row

    @app.get("/api/health", response_model=Health)
    async def health() -> Health:
        return Health(status="ok", version=__version__)

    @app.get("/api/limits", response_model=Limits)
    async def get_limits() -> Limits:
        return Limits(
            max_running_jobs=limits.max_running,
            max_queued_jobs=limits.max_queued,
            global_rate_per_s=limits.global_rate,
        )

    @app.get("/api/stats", response_model=Stats)
    async def stats(request: Request) -> Stats:
        counts = await asyncio.to_thread(store_of(request).status_counts)
        return Stats(total=sum(counts.values()), by_status=counts)

    @app.post("/api/scans", response_model=ScanOut, status_code=201)
    async def create_scan(request: Request, body: ScanCreate) -> ScanOut:
        fields: dict[str, Any] = dict(
            ports=body.ports,
            timeout=body.timeout,
            concurrency=body.concurrency,
            rate=body.rate,
            max_hosts=body.max_hosts,
            allow_public=body.allow_public,
            allow_shared=body.allow_shared,
            authorized=body.authorized,
        )
        try:
            if body.allowlist is not None:
                fields["allowlist"] = parse_allowlist("\n".join(body.allowlist), "allowlist")
            if body.kind == "scan":
                config: ScanConfig | DiscoveryConfig = ScanConfig(target=body.target, **fields)
            else:
                config = DiscoveryConfig(network=body.target, **fields)
        except ValidationError as exc:
            raise ApiError(422, "invalid_config", "the scan settings are invalid", _validation_fields(exc.errors())) from None
        except ConfigError as exc:
            raise ApiError(422, "invalid_config", str(exc)) from None
        if len(config.ports) > MAX_PORTS:
            raise ApiError(422, "too_many_ports", f"{len(config.ports)} ports requested; the web API allows at most {MAX_PORTS}")

        # The same engine preflight as the CLI: acknowledgement first, then DNS (scan only), cap,
        # address policy and allowlist on the resolved addresses. The plan pins those addresses.
        try:
            if isinstance(config, ScanConfig):
                plan = await prepare_scan(config, resolver=resolver or resolver_module.system_resolver)
            else:
                plan = prepare_discovery(config)
        except ScopeError as exc:
            raise ApiError(400, exc.code, exc.message) from None
        if plan.requested_probes > MAX_PROBES:
            raise ApiError(
                400, "probe_limit", f"{plan.requested_probes} probes requested; the web API allows at most {MAX_PROBES} per scan"
            )
        manager = manager_of(request)
        try:
            scan_id = await manager.submit(plan)
        except CapacityError as exc:
            raise ApiError(429, "capacity", str(exc)) from None
        except ShuttingDownError as exc:
            raise ApiError(503, "shutting_down", str(exc)) from None
        return _scan_out(await scan_row(request, scan_id), manager)

    @app.get("/api/scans", response_model=ScanPage)
    async def list_scans(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
        status: ScanStatus | None = None,
        kind: Annotated[str | None, Query(pattern="^(scan|discover)$")] = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> ScanPage:
        rows, total = await asyncio.to_thread(
            store_of(request).list_scans, limit=limit, offset=offset, status=status, kind=kind, search=q or None
        )
        manager = manager_of(request)
        return ScanPage(items=[_scan_out(r, manager) for r in rows], total=total, limit=limit, offset=offset)

    @app.get("/api/scans/{scan_id}", response_model=ScanOut)
    async def get_scan(request: Request, scan_id: SCAN_ID) -> ScanOut:
        return _scan_out(await scan_row(request, scan_id), manager_of(request))

    @app.get("/api/scans/{scan_id}/hosts", response_model=ScanResultsPage | DiscoveryResultsPage)
    async def get_hosts(
        request: Request,
        scan_id: SCAN_ID,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        state: Annotated[str | None, Query(max_length=20, pattern="^[a-z_]+$")] = None,
    ) -> ScanResultsPage | DiscoveryResultsPage:
        """Observations recorded so far (also while running). Scan results are grouped by address."""
        row = await scan_row(request, scan_id)
        store = store_of(request)
        if row.kind == "scan":
            ips, total = await asyncio.to_thread(store.port_result_ips, scan_id, state=state, limit=limit, offset=offset)
            results = await asyncio.to_thread(store.port_results, scan_id, row.target, ips=ips, state=state)
            grouped: dict[str, list] = {ip: [] for ip in ips}
            for result in results:
                grouped[str(result.ip)].append(result)
            items = [AddressResults(ip=ip, ports=ports) for ip, ports in grouped.items()]
            return ScanResultsPage(kind="scan", items=items, total=total, limit=limit, offset=offset)
        hosts, total = await asyncio.to_thread(
            store.host_results, scan_id, row.target, state=state, limit=limit, offset=offset
        )
        return DiscoveryResultsPage(kind="discover", items=hosts, total=total, limit=limit, offset=offset)

    @app.post("/api/scans/{scan_id}/cancel", response_model=CancelOut, status_code=202)
    async def cancel_scan(request: Request, scan_id: SCAN_ID) -> CancelOut:
        row = await scan_row(request, scan_id)
        manager = manager_of(request)
        if not await manager.cancel(scan_id):
            raise ApiError(409, "not_active", f"scan {scan_id} is already {row.status}")
        live = manager.live(scan_id)
        return CancelOut(id=scan_id, status=live.status if live else row.status, cancel_requested=True)

    @app.delete("/api/scans/{scan_id}", status_code=204)
    async def delete_scan(request: Request, scan_id: SCAN_ID) -> Response:
        await scan_row(request, scan_id)
        if manager_of(request).live(scan_id) is not None or not await asyncio.to_thread(store_of(request).delete_scan, scan_id):
            raise ApiError(409, "scan_active", "a queued or running scan cannot be deleted; cancel it first")
        return Response(status_code=204)

    @app.get("/api/scans/{scan_id}/export")
    async def export_scan(
        request: Request,
        scan_id: SCAN_ID,
        format: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
    ) -> Response:
        """The same JSON/CSV a CLI run would produce, rebuilt from the stored results."""
        row = await scan_row(request, scan_id)
        if manager_of(request).live(scan_id) is not None or row.status in ("queued", "running"):
            raise ApiError(409, "scan_active", "export is available once the scan has finished")
        report = await asyncio.to_thread(store_of(request).report, scan_id)
        if report is None:
            raise ApiError(409, "no_report", "this scan stopped before a report could be recorded (e.g. server restart)")
        media = "application/json" if format == "json" else "text/csv; charset=utf-8"
        filename = f"netscope-{row.kind}-{scan_id[:12]}.{format}"
        return Response(
            content=render(report, format),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if static_dir is not None and (Path(static_dir) / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app
