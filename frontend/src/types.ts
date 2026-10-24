// Mirrors src/netscope/api/schemas.py and src/netscope/models.py. tests/test_frontend_contract.py
// checks the fixtures in src/test/fixtures against the Python models, so drift fails a test.

export type ScanKind = "scan" | "discover";
export type ScanStatus = "queued" | "running" | "completed" | "cancelled" | "failed";
export type PortState = "open" | "closed" | "filtered" | "inconclusive" | "error";
export type HostState = "responsive" | "no_response" | "error";

export const SCAN_STATUSES: ScanStatus[] = ["queued", "running", "completed", "cancelled", "failed"];
export const ACTIVE_STATUSES: ScanStatus[] = ["queued", "running"];

export interface TargetError {
  target_input: string;
  error_code: string;
  error_message: string;
}

export interface Progress {
  completed: number;
  total: number;
  unit: "probes" | "hosts";
  determinate: boolean;
}

export interface DurationEstimate {
  probes: number;
  min_seconds: number;
  all_timeout_seconds: number;
  bottleneck_if_all_time_out: string;
  assumptions: string[];
}

export interface ScanFailure {
  code: string;
  message: string;
}

export interface Scan {
  id: string;
  kind: ScanKind;
  target: string;
  status: ScanStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: Progress;
  settings: Record<string, unknown>;
  resolved_ips: string[];
  target_errors: TargetError[];
  summary: Record<string, number>;
  estimate: DurationEstimate | null;
  error: ScanFailure | null;
  partial: boolean;
  exportable: boolean;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PortResult {
  target_input: string;
  ip: string;
  port: number;
  state: PortState;
  elapsed_ms: number;
  started_at: string;
  error_code: string | null;
  error_message: string | null;
}

export interface ProbeEvidence {
  port: number;
  state: PortState;
  elapsed_ms: number;
  started_at: string;
  error_code: string | null;
  error_message: string | null;
}

export interface HostResult {
  target_input: string;
  ip: string;
  state: HostState;
  probed_ports: number[];
  evidence: ProbeEvidence[];
  elapsed_ms: number;
  error_code: string | null;
  error_message: string | null;
}

export interface AddressResults {
  ip: string;
  ports: PortResult[];
}

export type ResultsPage =
  | ({ kind: "scan" } & Page<AddressResults>)
  | ({ kind: "discover" } & Page<HostResult>);

export interface Stats {
  total: number;
  by_status: Record<ScanStatus, number>;
}

export interface Limits {
  max_ports: number;
  max_probes: number;
  max_hosts: number;
  max_timeout_s: number;
  max_concurrency: number;
  max_rate_per_s: number;
  max_allowlist_entries: number;
  max_running_jobs: number;
  max_queued_jobs: number;
  global_rate_per_s: number;
  defaults: { ports: string; timeout: number; concurrency: number; rate: number; max_hosts: number };
}

export interface ScanCreate {
  kind: ScanKind;
  target: string;
  ports: string;
  timeout: number;
  concurrency: number;
  rate: number;
  max_hosts: number;
  allow_public: boolean;
  allow_shared: boolean;
  allowlist: string[] | null;
  authorized: boolean;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface ErrorBody {
  error: { code: string; message: string; fields?: FieldError[] };
}
