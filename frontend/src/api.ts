import type { ErrorBody, FieldError, Limits, Page, ResultsPage, Scan, ScanCreate, ScanKind, ScanStatus, Stats } from "./types";

// Sent on every mutating request. The server rejects mutations without it, and a cross-site page
// cannot add a custom header without a CORS preflight, which the server never approves.
const CLIENT_HEADER = { "X-NetScope-Client": "web" };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly fields: FieldError[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") Object.assign(headers, CLIENT_HEADER);

  let response: Response;
  try {
    response = await fetch(path, { ...init, method, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the NetScope server. Is `netscope serve` running?");
  }
  if (response.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = (body as ErrorBody | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `Request failed with HTTP ${response.status}`,
      error?.fields ?? [],
    );
  }
  return body as T;
}

export interface ScanListQuery {
  limit?: number;
  offset?: number;
  status?: ScanStatus | "";
  kind?: ScanKind | "";
  q?: string;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const scanPath = (id: string) => `/api/scans/${encodeURIComponent(id)}`;

export const api = {
  limits: () => request<Limits>("/api/limits"),
  stats: () => request<Stats>("/api/stats"),
  listScans: (q: ScanListQuery = {}) =>
    request<Page<Scan>>(`/api/scans${query({ limit: q.limit, offset: q.offset, status: q.status, kind: q.kind, q: q.q })}`),
  getScan: (id: string) => request<Scan>(scanPath(id)),
  getResults: (id: string, q: { limit?: number; offset?: number; state?: string } = {}) =>
    request<ResultsPage>(`${scanPath(id)}/hosts${query(q)}`),
  createScan: (body: ScanCreate) => request<Scan>("/api/scans", { method: "POST", body: JSON.stringify(body) }),
  cancelScan: (id: string) => request<{ id: string; status: ScanStatus; cancel_requested: boolean }>(`${scanPath(id)}/cancel`, { method: "POST" }),
  deleteScan: (id: string) => request<void>(scanPath(id), { method: "DELETE" }),
  exportUrl: (id: string, format: "json" | "csv") => `${scanPath(id)}/export?format=${format}`,
};
