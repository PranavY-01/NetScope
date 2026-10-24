import type { ReactNode } from "react";
import type { Progress, ScanStatus } from "../types";
import { Icon, type IconName } from "./Icon";

const STATUS: Record<ScanStatus, { label: string; icon: IconName }> = {
  queued: { label: "Queued", icon: "clock" },
  running: { label: "Running", icon: "activity" },
  completed: { label: "Completed", icon: "checkCircle" },
  cancelled: { label: "Cancelled", icon: "ban" },
  failed: { label: "Failed", icon: "xCircle" },
};

export const STATUS_TONES: Record<ScanStatus, string> = {
  queued: "tone-warning",
  running: "tone-info",
  completed: "tone-success",
  cancelled: "tone-caution",
  failed: "tone-danger",
};

export function StatusBadge({ status, partial = false }: { status: ScanStatus; partial?: boolean }) {
  const s = STATUS[status];
  return (
    <span className={`badge status-${status}`} data-testid="status-badge">
      {status === "running" ? <span className="live-dot" aria-hidden="true" /> : <Icon name={s.icon} />}
      {s.label}
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

export const STATE_TONES: Record<string, string> = {
  open: "tone-success",
  responsive: "tone-success",
  closed: "tone-neutral",
  inconclusive: "tone-warning",
  no_response: "tone-warning",
  filtered: "tone-violet",
  error: "tone-danger",
};

export function StateBadge({ state }: { state: string }) {
  return (
    <span className={`badge state ${STATE_TONES[state] ?? "tone-neutral"}`} title={STATE_HELP[state]}>
      <span className="dot" aria-hidden="true" />
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
        <p className="progress-label muted">Queued: waiting for a free scan slot.</p>
      </div>
    );
  }
  if (!progress.determinate) {
    return (
      <div className="progress">
        <div className={`progress-track${active ? " indeterminate" : ""}`} role="progressbar" aria-busy={active} />
        <p className="progress-label muted">
          {completed} {unit} completed{active ? " (total unknown)" : ""}
        </p>
      </div>
    );
  }
  const percent = Math.min(100, Math.floor((completed / total) * 100));
  return (
    <div className={`progress${active ? " is-active" : ""}`}>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={completed}
        aria-label={`${completed} of ${total} ${unit}`}
      >
        <div className={`progress-fill ${STATUS_TONES[status]}`} style={{ width: `${percent}%` }} />
      </div>
      <p className="progress-label muted">
        {completed} of {total} {unit} ({percent}%)
      </p>
    </div>
  );
}

const ALERT_ICONS: Record<"error" | "warning" | "info", IconName> = { error: "xCircle", warning: "alert", info: "info" };

export function Alert({ kind, title, children }: { kind: "error" | "warning" | "info"; title: string; children?: ReactNode }) {
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <Icon name={ALERT_ICONS[kind]} className="alert-icon" />
      <div className="alert-body">
        <strong>{title}</strong>
        {children ? <div>{children}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({ title, icon = "radar", children }: { title: string; icon?: IconName; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} className="empty-icon" />
      <p className="empty-title">{title}</p>
      {children}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="loading muted" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function PageHeader({ eyebrow, title, mono = false, actions }: { eyebrow?: ReactNode; title: string; mono?: boolean; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="page-title">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1 className={mono ? "mono" : undefined}>{title}</h1>
      </div>
      {actions ? <div className="actions">{actions}</div> : null}
    </div>
  );
}
