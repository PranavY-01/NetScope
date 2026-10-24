import { useState } from "react";
import { api } from "../api";
import { Alert, EmptyState, Loading } from "../components/common";
import { useResource } from "../hooks";
import { href } from "../router";
import { SCAN_STATUSES, type ScanKind, type ScanStatus } from "../types";
import { ScanTable } from "./Dashboard";

const PAGE = 25;

export function History() {
  const [status, setStatus] = useState<ScanStatus | "">("");
  const [kind, setKind] = useState<ScanKind | "">("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, error, loading } = useResource(
    () => api.listScans({ limit: PAGE, offset, status, kind, q: search.trim() }),
    [offset, status, kind, search],
    { pollMs: 2000, poll: (page) => !!page?.items.some((s) => s.status === "queued" || s.status === "running") },
  );

  const filtered = status !== "" || kind !== "" || search.trim() !== "";
  const reset = (fn: () => void) => {
    fn();
    setOffset(0);
  };

  return (
    <section>
      <div className="page-head">
        <h1>Scan history</h1>
        <a className="button primary" href={href({ page: "new" })}>
          New scan
        </a>
      </div>
      <div className="filters">
        <input
          type="search"
          placeholder="Search target or id"
          aria-label="Search history"
          value={search}
          maxLength={100}
          onChange={(e) => reset(() => setSearch(e.target.value))}
        />
        <select aria-label="Filter by status" value={status} onChange={(e) => reset(() => setStatus(e.target.value as ScanStatus | ""))}>
          <option value="">Any status</option>
          {SCAN_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select aria-label="Filter by type" value={kind} onChange={(e) => reset(() => setKind(e.target.value as ScanKind | ""))}>
          <option value="">Any type</option>
          <option value="scan">Port scan</option>
          <option value="discover">Host discovery</option>
        </select>
      </div>

      {error ? <Alert kind="error" title="Could not load history">{error.message}</Alert> : null}
      {loading && !data ? (
        <Loading label="Loading history…" />
      ) : data && data.items.length === 0 ? (
        <EmptyState title={filtered ? "No scans match these filters" : "No scans yet"}>
          {filtered ? null : (
            <p className="muted">
              <a href={href({ page: "new" })}>Start a scan</a> to build up a history.
            </p>
          )}
        </EmptyState>
      ) : data ? (
        <>
          <ScanTable scans={data.items} />
          <div className="pager">
            <button className="button subtle" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </span>
            <button className="button subtle" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}
