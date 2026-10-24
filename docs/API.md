# NetScope web API

Base URL: `http://127.0.0.1:8765` (by default; `netscope serve --port` changes it). Interactive OpenAPI documentation: `/api/docs`; schema: `/api/openapi.json`. Models: `src/netscope/api/schemas.py`.

## Conventions

- **Format:** JSON in and out. Timestamps are ISO 8601 UTC.
- **Mutations** (`POST`, `DELETE`) must send `X-NetScope-Client: <any value>`; otherwise the server returns 403 `missing_client_header`.
- **Host header:** requests must address the server by a loopback name (`127.0.0.1`, `localhost`, `::1`).
- **Scan IDs:** 32 lowercase hex characters.
- **Errors** always look like this:

  ```json
  { "error": { "code": "public_target", "message": "public (globally routable) targets require --allow-public: 8.8.8.8" } }
  ```

  Validation errors add `"fields": [{"field": "ports", "message": "..."}]`.

| Status | Meaning |
|---|---|
| 400 | Scope/policy rejection. `code` is one of `not_authorized`, `host_cap`, `special_address`, `shared_address`, `public_target`, `allowlist`, `probe_limit`. |
| 403 | Missing `X-NetScope-Client` header. |
| 404 | Unknown scan. |
| 409 | Wrong state: `not_active` (cancel), `scan_active` (delete/export while running), `no_report`. |
| 413 | Body over 64 KB. |
| 422 | Invalid input: `invalid_request` (schema), `invalid_config` (ports/target/allowlist), `too_many_ports`. |
| 429 | `capacity`: too many queued/running scans. |
| 503 | `shutting_down`. |
| 500 | `internal_error` (details logged server-side only). |

## Endpoints

### `GET /api/health`
`{"status": "ok", "version": "2.0.0.dev0"}`. Never starts a scan.

### `GET /api/limits`
The server's bounds and defaults, used by the UI form:
`max_ports`, `max_probes`, `max_hosts`, `max_timeout_s`, `max_concurrency`, `max_rate_per_s`, `max_allowlist_entries`, `max_running_jobs`, `max_queued_jobs`, `global_rate_per_s`, `defaults`.

### `GET /api/stats`
`{"total": 5, "by_status": {"queued": 0, "running": 1, "completed": 3, "cancelled": 1, "failed": 0}}`, counted from the database.

### `POST /api/scans` → 201
Validates the request, runs the engine's preflight (acknowledgement → DNS → cap → address policy → allowlist), then queues the scan. A long scan never runs inside the request.

```json
{
  "kind": "scan",                 // "scan" (one host) or "discover" (IPv4 CIDR)
  "target": "192.168.1.20",       // IPv4/hostname for scan; CIDR for discover
  "ports": "22,80,443",           // default "22,80,443"
  "timeout": 1.0,                 // (0, 10]
  "concurrency": 100,             // 1..256
  "rate": 50,                     // (0, 500]
  "max_hosts": 256,               // 1..1024
  "allow_public": false,
  "allow_shared": false,
  "allowlist": null,              // or ["10.0.0.0/24", "192.168.1.5"], up to 1024 entries
  "authorized": true              // required; false → 400 not_authorized, no DNS lookup
}
```

Unknown fields are rejected (422). The response is a **Scan** (below).

### `GET /api/scans`
Query: `limit` (1–100, default 25), `offset`, `status` (`queued|running|completed|cancelled|failed`), `kind` (`scan|discover`), `q` (substring of target or id, ≤ 100 chars). Newest first.
`{"items": [Scan, ...], "total": 12, "limit": 25, "offset": 0}`

### `GET /api/scans/{id}` → Scan

```json
{
  "id": "b8a7cfc27f8c4a1f98062cbe2784969b",
  "kind": "scan",
  "target": "localhost",
  "status": "completed",
  "created_at": "...", "started_at": "...", "finished_at": "...",
  "progress": { "completed": 2, "total": 2, "unit": "probes", "determinate": true },
  "settings": { "ports": [54084, 54085], "timeout_s": 1.0, "concurrency": 100, "rate_per_s": 50.0,
                "max_hosts": 256, "allow_public": false, "allow_shared": false, "allowlist": null,
                "fast_refusal_requested": true, "requested_probes": 2 },
  "resolved_ips": ["127.0.0.1"],
  "target_errors": [],
  "summary": { "open": 1, "closed": 1 },
  "estimate": { "probes": 2, "min_seconds": 0.02, "all_timeout_seconds": 1.02,
                "bottleneck_if_all_time_out": "rate limit", "assumptions": ["..."] },
  "error": null,
  "partial": false,
  "exportable": true
}
```

- **`progress.unit`:** `probes` for scans and `hosts` for discovery (a host counts once all of its probes finish). When `total` is 0, `determinate` is false; for example, a DNS failure leaves nothing to probe.
- **`partial`:** the scan ended (cancelled, failed or interrupted) before every probe completed.
- **`error`:** set for failed scans (`engine_error`, `server_restart`).
- **`estimate`:** uses the smaller of the scan's rate and the server's global rate.

### `GET /api/scans/{id}/hosts`
Results recorded so far, also while running. Query: `limit` (1–200, default 50), `offset`, `state` (filter).

- Scans group port results by address and page by address. `state` keeps addresses with at least one port in that state, and shows only those ports.
  `{"kind": "scan", "items": [{"ip": "10.0.0.1", "ports": [PortResult, ...]}], "total": 1, "limit": 50, "offset": 0}`
- Discovery returns host verdicts with evidence. `state` filters by verdict.
  `{"kind": "discover", "items": [HostResult, ...], ...}`

`PortResult`: `target_input, ip, port, state, elapsed_ms, started_at, error_code, error_message`.
`HostResult`: `target_input, ip, state, probed_ports, evidence[{port, state, elapsed_ms, started_at, error_code, error_message}], elapsed_ms, error_code, error_message`.

### `POST /api/scans/{id}/cancel` → 202
`{"id": "...", "status": "running", "cancel_requested": true}`

The request starts cancellation; it does not wait for it. Poll the scan until `status` is `cancelled`. Completed results are kept. Returns 409 `not_active` if the scan has already finished.

### `DELETE /api/scans/{id}` → 204
Deletes one finished scan and its results. Returns 409 `scan_active` for queued/running scans; cancel them first.

### `GET /api/scans/{id}/export?format=json|csv`
The same JSON/CSV the CLI produces (see the User Guide for the CSV columns), rebuilt from stored results. It downloads as `netscope-<kind>-<id prefix>.<format>`. Returns 409 while the scan is active, or for scans stopped by a server crash before a report was recorded (`no_report`).

## Example (PowerShell)

```powershell
$h = @{ "X-NetScope-Client" = "cli" }
$body = @{ kind = "scan"; target = "127.0.0.1"; ports = "22,80,443"; authorized = $true } | ConvertTo-Json
$scan = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/scans -Headers $h -ContentType application/json -Body $body
Invoke-RestMethod http://127.0.0.1:8765/api/scans/$($scan.id)
```
