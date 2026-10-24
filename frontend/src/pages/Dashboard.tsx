import { api } from "../api";
import { Alert, EmptyState, Loading, PageHeader, STATUS_TONES, StatusBadge } from "../components/common";
import { Distribution } from "../components/Distribution";
import { Icon, type IconName } from "../components/Icon";
import { formatTime, kindLabel, shortId } from "../format";
import { useResource } from "../hooks";
import { href } from "../router";
import { SCAN_STATUSES, type Page, type Scan, type Stats } from "../types";

interface DashboardData {
  stats: Stats;
  recent: Page<Scan>;
}

const RECENT = 8;
const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

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
  const active = s.running + s.queued;
  const cards: { label: string; value: number; icon: IconName; tone: string; live?: boolean }[] = [
    { label: "Stored scans", value: stats.total, icon: "database", tone: "tone-accent" },
    { label: "Completed", value: s.completed, icon: "checkCircle", tone: "tone-success" },
    { label: "Running / queued", value: active, icon: "activity", tone: "tone-info", live: s.running > 0 },
    { label: "Cancelled / failed", value: s.cancelled + s.failed, icon: "alert", tone: "tone-caution" },
  ];

  return (
    <section>
      <PageHeader
        eyebrow="Overview"
        title="Dashboard"
        actions={
          <a className="button primary" href={href({ page: "new" })}>
            <Icon name="plus" />
            New scan
          </a>
        }
      />
      <div className="cards">
        {cards.map((c) => (
          <div className={`card stat ${c.tone}`} key={c.label}>
            <span className="stat-icon">
              <Icon name={c.icon} />
            </span>
            <div>
              <div className="card-value" data-testid={`stat-${c.label}`}>
                {c.value}
              </div>
              <div className="card-label">
                {c.label}
                {c.live ? <span className="live-dot" title="A scan is running" aria-hidden="true" /> : null}
              </div>
            </div>
          </div>
        ))}
      </div>

      {stats.total > 0 ? (
        <div className="panel glass">
          <Distribution
            title="Stored scans by status"
            unit="scans"
            segments={SCAN_STATUSES.map((status) => ({
              key: status,
              label: STATUS_LABELS[status] ?? status,
              value: s[status],
              tone: STATUS_TONES[status],
            }))}
          />
        </div>
      ) : null}

      <div className="section-head">
        <h2>Recent scans</h2>
        {recent.total > RECENT ? (
          <a className="link-more" href={href({ page: "history" })}>
            View all {recent.total} scans <Icon name="chevronRight" />
          </a>
        ) : null}
      </div>
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
                <a className="mono target-link" href={href({ page: "scan", id: scan.id })}>
                  {scan.target}
                </a>
              </td>
              <td>
                <span className="kind">
                  <Icon name={scan.kind === "discover" ? "network" : "server"} />
                  {kindLabel(scan.kind)}
                </span>
              </td>
              <td>
                <StatusBadge status={scan.status} partial={scan.partial} />
              </td>
              <td className="small mono">
                {scan.progress.completed}
                {scan.progress.determinate ? ` / ${scan.progress.total}` : ""} <span className="muted">{scan.progress.unit}</span>
              </td>
              <td className="small mono muted">{formatTime(scan.created_at)}</td>
              <td className="mono small muted">{shortId(scan.id)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
