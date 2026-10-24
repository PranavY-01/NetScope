# Development

Commands are for Windows PowerShell from the repository root; Linux/macOS equivalents are noted.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"                 # engine + CLI + web backend + pytest/httpx

cd frontend
npm ci                                  # exact versions from package-lock.json
cd ..
```

| Component | Requirement | Developed with |
|---|---|---|
| Python | 3.11+ | 3.12.0 |
| Node.js | a current LTS (CI uses 24) | 24.19 |

`pyproject.toml` extras:
- **`web`:** FastAPI and uvicorn. The CLI does not need them.
- **`dev`:** pytest, pytest-asyncio and httpx, plus `web`.

## Running

### Backend + built UI (what an end user runs)

```powershell
cd frontend; npm run build; cd ..
netscope serve                          # http://127.0.0.1:8765, history in .\netscope.db
netscope serve --db C:\path\scans.db --port 8800 --max-jobs 1 -v
```

- **Loopback only:** `serve` refuses non-loopback `--host` values.
- **Database:** it creates the database file if it is missing, and refuses a file that is not a NetScope database or has another schema version.
- **No build present:** without a `frontend/dist` build it serves the API only, and logs a warning saying so.

### UI development with hot reload

Run in two terminals:

```powershell
netscope serve                          # terminal 1: API on 127.0.0.1:8765
cd frontend; npm run dev                # terminal 2: http://127.0.0.1:5173
```

Vite proxies `/api` to `http://127.0.0.1:8765`, overridable with the `NETSCOPE_API` environment variable. The browser therefore stays same-origin, and the server needs no CORS.

### CLI

```powershell
netscope scan 127.0.0.1 --yes-authorized
netscope discover 127.0.0.0/29 --yes-authorized --json
```

## Tests

```powershell
python -m pytest                         # all Python tests
python -m pytest -m "not integration"    # without real loopback sockets
python -m pytest tests/test_api.py -v    # one area
cd frontend; npm test                    # Vitest + Testing Library (jsdom)
cd frontend; npm run build               # tsc type-check + production build
```

| Area | Files |
|---|---|
| Engine | `test_config`, `test_guardrails`, `test_probe`, `test_resolver`, `test_rate_limit`, `test_scanner`, `test_discovery`, `test_estimate`, `test_output` |
| CLI and cancellation | `test_cli`, `test_cancellation`, `test_smoke` |
| Web backend | `test_api` (HTTP, through FastAPI's TestClient), `test_jobs` (job manager and store) |
| Contract | `test_frontend_contract`: checks the frontend fixtures against the API models |
| Benchmark smoke | `test_benchmark_smoke` |
| Frontend | `frontend/src/**/*.test.ts(x)` |

**Rules:**
- Tests never contact public addresses.
- Resolvers and connect functions are injected through seams: `resolver=`, `connect_factory=`, and `netscope.probe.make_tcp_connect` for the CLI.
- Tests marked `integration` use loopback listeners only.
- `ResourceWarning`s are errors, so leaked sockets or database connections fail the suite.
- Lower-bound timing assertions use `tests/timing.py`. On Windows with Python 3.11/3.12, asyncio timers can fire up to two 15.6 ms ticks early.

### Frontend fixtures

The UI tests run against real API responses saved in `frontend/src/test/fixtures/`. After changing an API model, regenerate them:

```powershell
.\.venv\Scripts\python.exe tools\make_frontend_fixtures.py
```

The script runs the API in-process with a scripted network, so it sends no real traffic. `tests/test_frontend_contract.py` fails if a fixture no longer matches the models.

## Manual checks

- **Ctrl-C on Windows:** `.\.venv\Scripts\python.exe tools\windows_console_ctrl_c.py .\.venv\Scripts\netscope.exe` delivers a genuine console `CTRL_C_EVENT` to running CLI scans.
- **Benchmark:** `python benchmarks\bench.py --quick` runs the loopback ground-truth fixture (see the design log for the protocol).

## CI

`.github/workflows/tests.yml` runs two jobs:
- **Python:** the full suite on Windows and Ubuntu, Python 3.11–3.13.
- **Frontend:** `npm ci`, `npm test` and `npm run build` on Ubuntu with Node 24.

## Data and generated files

All of these are excluded by `.gitignore`:
- local databases (`*.db`, `*-wal`, `*-shm`);
- `frontend/node_modules/` and `frontend/dist/`;
- benchmark result JSON files.
