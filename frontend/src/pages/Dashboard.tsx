import { api } from "../api";
import { Alert, EmptyState, Loading, StatusBadge } from "../components/common";
import { formatTime, kindLabel, shortId } from "../format";
import { useResource } from "../hooks";
import { href } from "../router";
import type { Page, Scan, Stats } from "../types";

interface DashboardData {
  stats: Stats;
  recent: Page<Scan>;
}

const RECENT = 8;

export function Dashboard() {
  const { data, error, loading } = useResource<DashboardData>(
    async () => {
      const [stats, recent] = await Promise.all([api.stats(), api.listScans({ limit: RECENT })]);
      return { stats, recent };
    },
    [],
    { pollMs: 2000, poll: (d) => !!d && d.stats.by_status.queued + d.stats.by_status.running > 0 },
  );

  if (loading && !data) return <Loading label="Loading dashboard…" />;
  if (error && !data) return <Alert kind="error" title="Could not load the dashboard">{error.message}</Alert>;
  if (!data) return null;

  const { stats, recent } = data;
  const s = stats.by_status;
  const cards = [
    { label: "Stored scans", value: stats.total },
    { label: "Completed", value: s.completed },
    { label: "Running / queued", value: s.running + s.queued },
    { label: "Cancelled / failed", value: s.cancelled + s.failed },
  ];

  return (
    <section>
      <div className="page-head">
        <h1>Dashboard</h1>
        <a className="button primary" href={href({ page: "new" })}>
          New scan
        </a>
      </div>
      <div className="cards">
        {cards.map((c) => (
          <div className="card" key={c.label}>
            <div className="card-value" data-testid={`stat-${c.label}`}>
              {c.value}
            </div>
            <div className="card-label">{c.label}</div>
          </div>
        ))}
      </div>

      <h2>Recent scans</h2>
      {recent.items.length === 0 ? (
        <EmptyState title="No scans yet">
          <p className="muted">
            Start with your own machine: <a href={href({ page: "new" })}>scan 127.0.0.1</a>. Only scan systems you own or are
            authorized to test.
          </p>
        </EmptyState>
      ) : (
        <ScanTable scans={recent.items} />
      )}
      {recent.total > RECENT ? (
        <p>
          <a href={href({ page: "history" })}>View all {recent.total} scans →</a>
        </p>
      ) : null}
    </section>
  );
}

export function ScanTable({ scans }: { scans: Scan[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Target</th>
            <th>Type</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Created</th>
            <th>ID</th>
          </tr>
        </thead>
        <tbody>
          {scans.map((scan) => (
            <tr key={scan.id}>
              <td>
                <a className="mono" href={href({ page: "scan", id: scan.id })}>
                  {scan.target}
                </a>
              </td>
              <td>{kindLabel(scan.kind)}</td>
              <td>
                <StatusBadge status={scan.status} partial={scan.partial} />
              </td>
              <td className="small">
                {scan.progress.completed}
                {scan.progress.determinate ? ` / ${scan.progress.total}` : ""} {scan.progress.unit}
              </td>
              <td className="small">{formatTime(scan.created_at)}</td>
              <td className="mono small">{shortId(scan.id)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
