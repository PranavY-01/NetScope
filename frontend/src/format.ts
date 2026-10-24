export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return "—";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function formatSeconds(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 120) return `${seconds.toFixed(1)} s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

/** Display-only: collapse consecutive ports into ranges ("1-1024, 8080") and cap the length. */
export function formatPorts(ports: number[], maxParts = 12): string {
  const sorted = [...ports].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (const port of sorted.slice(1).concat(Number.NaN)) {
    if (start === undefined || prev === undefined) break;
    if (port === prev + 1) {
      prev = port;
      continue;
    }
    parts.push(start === prev ? String(start) : `${start}-${prev}`);
    start = port;
    prev = port;
  }
  if (parts.length <= maxParts) return parts.join(", ");
  return `${parts.slice(0, maxParts).join(", ")}, … (+${parts.length - maxParts} more)`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function kindLabel(kind: string): string {
  return kind === "discover" ? "Host discovery" : "Port scan";
}
