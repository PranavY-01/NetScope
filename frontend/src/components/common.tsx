import type { ReactNode } from "react";
import type { Progress, ScanStatus } from "../types";

const STATUS_LABELS: Record<ScanStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function StatusBadge({ status, partial = false }: { status: ScanStatus; partial?: boolean }) {
  return (
    <span className={`badge status-${status}`} data-testid="status-badge">
      {STATUS_LABELS[status]}
      {partial && status !== "completed" ? " · partial" : ""}
    </span>
  );
}

const STATE_HELP: Record<string, string> = {
  open: "A TCP connection was established from the scanner's position.",
  closed: "The connection was actively refused.",
  inconclusive: "No answer in time (or unreachable): not enough evidence to classify.",
  filtered: "Reserved; never reported by this version.",
  error: "A local or operational failure prevented classification.",
  responsive: "At least one probe got a TCP-level answer (a connection or a refusal).",
  no_response: "No TCP answer on the probed ports. The host may still be up.",
};

export function StateBadge({ state }: { state: string }) {
  return (
    <span className={`badge state-${state}`} title={STATE_HELP[state]}>
      {state.replace("_", " ")}
    </span>
  );
}

export function ProgressBar({ progress, status }: { progress: Progress; status: ScanStatus }) {
  const active = status === "queued" || status === "running";
  const { completed, total, unit } = progress;
  if (status === "queued") {
    return (
      <div className="progress" aria-label="Waiting for a free scan slot">
        <div className="progress-track indeterminate" role="progressbar" aria-busy="true" />
        <p className="muted">Queued: waiting for a free scan slot.</p>
      </div>
    );
  }
  if (!progress.determinate) {
    return (
      <div className="progress">
        <div className={`progress-track${active ? " indeterminate" : ""}`} role="progressbar" aria-busy={active} />
        <p className="muted">
          {completed} {unit} completed{active ? " (total unknown)" : ""}
        </p>
      </div>
    );
  }
  const percent = Math.min(100, Math.floor((completed / total) * 100));
  return (
    <div className="progress">
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={completed}
        aria-label={`${completed} of ${total} ${unit}`}
      >
        <div className={`progress-fill status-${status}`} style={{ width: `${percent}%` }} />
      </div>
      <p className="muted">
        {completed} of {total} {unit} ({percent}%)
      </p>
    </div>
  );
}

export function Alert({ kind, title, children }: { kind: "error" | "warning" | "info"; title: string; children?: ReactNode }) {
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <strong>{title}</strong>
      {children ? <div>{children}</div> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {label}
    </p>
  );
}
