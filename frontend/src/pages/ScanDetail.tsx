import { useState } from "react";
import { api, ApiError } from "../api";
import { Alert, Loading, ProgressBar, StateBadge, StatusBadge } from "../components/common";
import { HostResultsTable, PortResultsTable } from "../components/ResultsTables";
import { formatDuration, formatSeconds, formatTime, kindLabel } from "../format";
import { useResource } from "../hooks";
import { href, navigate } from "../router";
import { ACTIVE_STATUSES, type ResultsPage, type Scan } from "../types";

const PAGE = 100;
const POLL_MS = 1000;

const isActive = (scan: Scan | null) => !!scan && ACTIVE_STATUSES.includes(scan.status);

export function ScanDetail({ id }: { id: string }) {
  const scanResource = useResource(() => api.getScan(id), [id], { pollMs: POLL_MS, poll: isActive });
  const scan = scanResource.data;
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Results refresh whenever the scan's completed count changes, so polling the scan drives both.
  const results = useResource<ResultsPage>(() => api.getResults(id, { limit: PAGE, offset, state: state || undefined }), [
    id,
    offset,
    state,
    scan?.progress.completed,
    scan?.status,
  ]);

  if (scanResource.loading && !scan) return <Loading label="Loading scan…" />;
  if (scanResource.error && !scan) {
    return (
      <Alert kind="error" title={scanResource.error.status === 404 ? "Scan not found" : "Could not load this scan"}>
        <p>{scanResource.error.message}</p>
        <p>
          <a href={href({ page: "history" })}>Back to history</a>
        </p>
      </Alert>
    );
  }
  if (!scan) return null;

  const active = isActive(scan);

  async function onCancel() {
    setActionError(null);
    setCancelling(true);
    try {
      await api.cancelScan(id);
      scanResource.reload();
    } catch (err) {
      setCancelling(false);
      setActionError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    }
  }

  async function onDelete() {
    if (!window.confirm("Delete this scan and all of its results? This cannot be undone.")) return;
    setActionError(null);
    try {
      await api.deleteScan(id);
      navigate({ page: "history" });
    } catch (err) {
      setActionError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    }
  }

  const stateOptions = scan.kind === "scan" ? ["open", "closed", "inconclusive", "error"] : ["responsive", "no_response", "error"];
  const page = results.data;

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="muted small">{kindLabel(scan.kind)}</p>
          <h1 className="mono">{scan.target}</h1>
        </div>
        <div className="actions">
          {active ? (
            <button className="button danger" onClick={onCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel scan"}
            </button>
          ) : null}
          {scan.exportable ? (
            <>
              <a className="button" href={api.exportUrl(id, "json")} download>
                Export JSON
              </a>
              <a className="button" href={api.exportUrl(id, "csv")} download>
                Export CSV
              </a>
            </>
          ) : null}
          {!active ? (
            <button className="button subtle" onClick={onDelete}>
              Delete
            </button>
          ) : null}
        </div>
      </div>

      {actionError ? <Alert kind="error" title="Action failed">{actionError.message}</Alert> : null}
      {scanResource.error ? <Alert kind="warning" title="Lost contact with the server; retrying">{scanResource.error.message}</Alert> : null}

      <div className="panel">
        <div className="status-line">
          <StatusBadge status={scan.status} partial={scan.partial} />
          <span className="mono small muted">id {scan.id}</span>
        </div>
        <ProgressBar progress={scan.progress} status={scan.status} />
        <dl className="kv">
          <dt>Created</dt>
          <dd>{formatTime(scan.created_at)}</dd>
          <dt>Started</dt>
          <dd>{formatTime(scan.started_at)}</dd>
          <dt>Finished</dt>
          <dd>{formatTime(scan.finished_at)}</dd>
          <dt>Duration</dt>
          <dd>{formatDuration(scan.started_at, scan.finished_at)}</dd>
          {scan.kind === "scan" ? (
            <>
              <dt>Resolved addresses</dt>
              <dd className="mono">{scan.resolved_ips.join(", ") || "none"}</dd>
            </>
          ) : null}
        </dl>
      </div>

      {scan.partial ? (
        <Alert kind="warning" title="Partial results">
          This scan stopped before every probe finished. Results shown are only the ones that completed; nothing was invented for the
          rest.
        </Alert>
      ) : null}
      {scan.error ? (
        <Alert kind="error" title={`Scan ${scan.status}: ${scan.error.code}`}>
          {scan.error.message}
        </Alert>
      ) : null}
      {scan.target_errors.map((e) => (
        <Alert key={e.error_code + e.target_input} kind="error" title={`Target error: ${e.target_input}`}>
          <span className="mono small">{e.error_code}</span> {e.error_message}
        </Alert>
      ))}

      <div className="two-col">
        <div className="panel">
          <h2>Summary</h2>
          {Object.keys(scan.summary).length === 0 ? (
            <p className="muted">{active ? "No results yet." : "No results recorded."}</p>
          ) : (
            <ul className="summary">
              {Object.entries(scan.summary).map(([key, count]) => (
                <li key={key}>
                  <StateBadge state={key} /> <strong>{count}</strong>
                </li>
              ))}
            </ul>
          )}
          <Interpretation kind={scan.kind} />
        </div>
        <div className="panel">
          <h2>Effective settings</h2>
          <Settings scan={scan} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{scan.kind === "scan" ? "Port observations" : "Host observations"}</h2>
          <label>
            Show{" "}
            <select
              value={state}
              aria-label="Filter by state"
              onChange={(e) => {
                setState(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">all</option>
              {stateOptions.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
        </div>
        {results.error && !page ? <Alert kind="error" title="Could not load results">{results.error.message}</Alert> : null}
        {!page ? (
          <Loading label="Loading results…" />
        ) : page.items.length === 0 ? (
          <p className="muted">{active ? "Waiting for results…" : "No observations match."}</p>
        ) : page.kind === "scan" ? (
          <PortResultsTable items={page.items} />
        ) : (
          <HostResultsTable items={page.items} />
        )}
        {page && page.total > PAGE ? (
          <div className="pager">
            <button className="button subtle" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total} {page.kind === "scan" ? "addresses" : "hosts"}
            </span>
            <button className="button subtle" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function Interpretation({ kind }: { kind: Scan["kind"] }) {
  return (
    <details className="interpretation">
      <summary>How to read these results</summary>
      <ul className="small">
        <li>Results are observations from this machine's network position at probe time, not guarantees.</li>
        <li>
          <strong>open</strong>: a TCP connection succeeded. <strong>closed</strong>: the connection was refused.{" "}
          <strong>inconclusive</strong>: no answer in time, or unreachable. A timeout does not prove a host is offline.
        </li>
        {kind === "discover" ? (
          <li>
            <strong>responsive</strong> means some probe got a TCP-level answer. It does not identify a specific device, and
            <strong> no_response</strong> does not mean the host is down.
          </li>
        ) : null}
        <li>Firewalls, NAT, proxies and load balancers can answer on a host's behalf or drop probes, changing what is observed.</li>
        <li>No service names are shown: a port number alone does not prove which service is listening.</li>
      </ul>
    </details>
  );
}

function Settings({ scan }: { scan: Scan }) {
  const s = scan.settings;
  const list = (value: unknown) => (Array.isArray(value) ? value.join(", ") : String(value ?? "—"));
  const allowlist = s.allowlist as string[] | null | undefined;
  return (
    <>
      <dl className="kv">
        <dt>Ports</dt>
        <dd className="mono">{list(s.ports)}</dd>
        <dt>Timeout</dt>
        <dd>{String(s.timeout_s)} s</dd>
        <dt>Concurrency</dt>
        <dd>{String(s.concurrency)}</dd>
        <dt>Rate</dt>
        <dd>{String(s.rate_per_s)} probe starts/s</dd>
        <dt>Host cap</dt>
        <dd>{String(s.max_hosts)}</dd>
        <dt>Public override</dt>
        <dd>{s.allow_public ? "yes" : "no"}</dd>
        <dt>Shared-space override</dt>
        <dd>{s.allow_shared ? "yes" : "no"}</dd>
        <dt>Allowlist</dt>
        <dd className="mono">{allowlist ? allowlist.join(", ") : "not used"}</dd>
        <dt>Planned probes</dt>
        <dd>{String(s.requested_probes)}</dd>
      </dl>
      {scan.estimate ? (
        <details className="estimate">
          <summary>
            Estimated duration: at least {formatSeconds(scan.estimate.min_seconds)}; about{" "}
            {formatSeconds(scan.estimate.all_timeout_seconds)} if every probe times out
          </summary>
          <ul className="small muted">
            {scan.estimate.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}
