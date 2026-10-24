import { useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "../api";
import { Alert } from "../components/common";
import { useResource } from "../hooks";
import { navigate } from "../router";
import type { Limits, ScanCreate, ScanKind } from "../types";

const FALLBACK_LIMITS: Limits = {
  max_ports: 4096,
  max_probes: 20000,
  max_hosts: 1024,
  max_timeout_s: 10,
  max_concurrency: 256,
  max_rate_per_s: 500,
  max_allowlist_entries: 1024,
  max_running_jobs: 2,
  max_queued_jobs: 8,
  global_rate_per_s: 500,
  defaults: { ports: "22,80,443", timeout: 1, concurrency: 100, rate: 50, max_hosts: 256 },
};

export interface FormState {
  kind: ScanKind;
  target: string;
  ports: string;
  timeout: string;
  concurrency: string;
  rate: string;
  maxHosts: string;
  allowPublic: boolean;
  allowShared: boolean;
  allowlist: string;
  authorized: boolean;
}

export type FieldErrors = Partial<Record<keyof FormState | "form", string>>;

const PORT_SPEC = /^\s*\d{1,5}(\s*-\s*\d{1,5})?(\s*,\s*\d{1,5}(\s*-\s*\d{1,5})?)*\s*$/;

/** Convenience checks only: the server re-validates everything and its decision is final. */
export function validateForm(form: FormState, limits: Limits): FieldErrors {
  const errors: FieldErrors = {};
  const target = form.target.trim();
  if (!target) errors.target = "Enter a target.";
  else if (form.kind === "scan" && target.includes("/")) errors.target = "A port scan takes one host. Use host discovery for CIDR ranges.";
  else if (form.kind === "discover" && !/^[\d.]+(\/\d{1,2})?$/.test(target)) errors.target = "Enter an IPv4 CIDR such as 192.168.1.0/24.";
  if (!PORT_SPEC.test(form.ports)) errors.ports = "Use ports, lists and ranges, e.g. 22,80,8000-8010.";
  const number = (value: string, min: number, max: number, integer: boolean) => {
    const n = Number(value);
    return value.trim() !== "" && Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n));
  };
  if (!number(form.timeout, 0.001, limits.max_timeout_s, false)) errors.timeout = `Between 0 and ${limits.max_timeout_s} seconds.`;
  if (!number(form.concurrency, 1, limits.max_concurrency, true)) errors.concurrency = `Whole number from 1 to ${limits.max_concurrency}.`;
  if (!number(form.rate, 0.001, limits.max_rate_per_s, false)) errors.rate = `Between 0 and ${limits.max_rate_per_s} per second.`;
  if (!number(form.maxHosts, 1, limits.max_hosts, true)) errors.maxHosts = `Whole number from 1 to ${limits.max_hosts}.`;
  if (allowlistEntries(form.allowlist).length > limits.max_allowlist_entries) errors.allowlist = `At most ${limits.max_allowlist_entries} entries.`;
  if (!form.authorized) errors.authorized = "You must confirm that you are authorized to scan every target.";
  return errors;
}

function allowlistEntries(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
}

export function toRequest(form: FormState): ScanCreate {
  const entries = allowlistEntries(form.allowlist);
  return {
    kind: form.kind,
    target: form.target.trim(),
    ports: form.ports.trim(),
    timeout: Number(form.timeout),
    concurrency: Number(form.concurrency),
    rate: Number(form.rate),
    max_hosts: Number(form.maxHosts),
    allow_public: form.allowPublic,
    allow_shared: form.allowShared,
    allowlist: entries.length ? entries : null,
    authorized: form.authorized,
  };
}

const SERVER_FIELDS: Record<string, keyof FormState> = {
  target: "target",
  ports: "ports",
  timeout: "timeout",
  concurrency: "concurrency",
  rate: "rate",
  max_hosts: "maxHosts",
  allowlist: "allowlist",
  authorized: "authorized",
};

export function NewScan() {
  const limitsResource = useResource(() => api.limits(), []);
  const limits = limitsResource.data ?? FALLBACK_LIMITS;
  const d = limits.defaults;
  const [form, setForm] = useState<FormState>({
    kind: "scan",
    target: "",
    ports: d.ports,
    timeout: String(d.timeout),
    concurrency: String(d.concurrency),
    rate: String(d.rate),
    maxHosts: String(d.max_hosts),
    allowPublic: false,
    allowShared: false,
    allowlist: "",
    authorized: false,
  });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setServerError(null);
    const found = validateForm(form, limits);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSubmitting(true);
    try {
      const scan = await api.createScan(toRequest(form));
      navigate({ page: "scan", id: scan.id });
    } catch (err) {
      const apiError = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
      setServerError(apiError);
      const mapped: FieldErrors = {};
      for (const f of apiError.fields) {
        const key = SERVER_FIELDS[f.field];
        if (key) mapped[key] = f.message;
      }
      setErrors(mapped);
    } finally {
      setSubmitting(false);
    }
  }

  const preview = toRequest(form);
  const isScan = form.kind === "scan";

  return (
    <section>
      <div className="page-head">
        <h1>New scan</h1>
      </div>
      <form className="form" onSubmit={onSubmit} noValidate>
        <fieldset>
          <legend>Type</legend>
          <div className="segmented" role="radiogroup" aria-label="Scan type">
            <label className={isScan ? "active" : ""}>
              <input type="radio" name="kind" checked={isScan} onChange={() => set("kind", "scan")} /> Port scan (one host)
            </label>
            <label className={!isScan ? "active" : ""}>
              <input type="radio" name="kind" checked={!isScan} onChange={() => set("kind", "discover")} /> Host discovery (CIDR)
            </label>
          </div>
        </fieldset>

        <Field label={isScan ? "Target (IPv4 address or hostname)" : "Network (IPv4 CIDR)"} error={errors.target} htmlFor="target">
          <input
            id="target"
            className="mono"
            value={form.target}
            placeholder={isScan ? "127.0.0.1 or host.lan" : "192.168.1.0/24"}
            onChange={(e) => set("target", e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        <Field
          label={isScan ? "Ports" : "Probe ports (checked on every host)"}
          error={errors.ports}
          htmlFor="ports"
          hint="Single ports, lists and ranges. Invalid specs are rejected, never widened."
        >
          <input id="ports" className="mono" value={form.ports} onChange={(e) => set("ports", e.target.value)} spellCheck={false} />
        </Field>

        <details className="advanced">
          <summary>Timing and limits</summary>
          <div className="grid-4">
            <Field label="Timeout (s)" error={errors.timeout} htmlFor="timeout">
              <input id="timeout" type="number" step="0.1" min={0} max={limits.max_timeout_s} value={form.timeout} onChange={(e) => set("timeout", e.target.value)} />
            </Field>
            <Field label="Concurrency" error={errors.concurrency} htmlFor="concurrency">
              <input id="concurrency" type="number" min={1} max={limits.max_concurrency} value={form.concurrency} onChange={(e) => set("concurrency", e.target.value)} />
            </Field>
            <Field label="Rate (probes/s)" error={errors.rate} htmlFor="rate">
              <input id="rate" type="number" min={1} max={limits.max_rate_per_s} value={form.rate} onChange={(e) => set("rate", e.target.value)} />
            </Field>
            <Field label="Host cap" error={errors.maxHosts} htmlFor="maxHosts">
              <input id="maxHosts" type="number" min={1} max={limits.max_hosts} value={form.maxHosts} onChange={(e) => set("maxHosts", e.target.value)} />
            </Field>
          </div>
          <p className="muted small">
            The rate limit usually sets the pace: at {form.rate || "?"} probes/s, more concurrency does not make a scan faster. The
            server also applies a global cap of {limits.global_rate_per_s} probe starts/s across all running scans.
          </p>
        </details>

        <fieldset>
          <legend>Scope</legend>
          <label className="check">
            <input type="checkbox" checked={form.allowPublic} onChange={(e) => set("allowPublic", e.target.checked)} />
            Allow public (globally routable) addresses
          </label>
          <label className="check">
            <input type="checkbox" checked={form.allowShared} onChange={(e) => set("allowShared", e.target.checked)} />
            Allow shared address space 100.64.0.0/10 (carrier-grade NAT; may belong to other ISP customers)
          </label>
          <p className="muted small">
            Each override covers only its own range. Loopback and private ranges need neither; link-local, multicast, reserved and
            documentation ranges are always refused.
          </p>
          <Field label="Allowlist (optional)" error={errors.allowlist} htmlFor="allowlist" hint="One IPv4 address or CIDR per line. Any target outside it rejects the whole scan.">
            <textarea id="allowlist" className="mono" rows={3} value={form.allowlist} onChange={(e) => set("allowlist", e.target.value)} spellCheck={false} />
          </Field>
        </fieldset>

        <fieldset className={errors.authorized ? "invalid" : ""}>
          <legend>Authorization</legend>
          <label className="check">
            <input type="checkbox" checked={form.authorized} onChange={(e) => set("authorized", e.target.checked)} />I own every target
            or am explicitly authorized to test it.
          </label>
          {errors.authorized ? <p className="field-error">{errors.authorized}</p> : null}
        </fieldset>

        <div className="preview">
          <h2>Effective settings</h2>
          <dl className="kv">
            <dt>Type</dt>
            <dd>{isScan ? "Port scan" : "Host discovery"}</dd>
            <dt>Target</dt>
            <dd className="mono">{preview.target || "—"}</dd>
            <dt>Ports</dt>
            <dd className="mono">{preview.ports}</dd>
            <dt>Timeout / concurrency / rate</dt>
            <dd>
              {preview.timeout}s / {preview.concurrency} / {preview.rate} per s
            </dd>
            <dt>Host cap</dt>
            <dd>{preview.max_hosts}</dd>
            <dt>Overrides</dt>
            <dd>{[preview.allow_public && "public", preview.allow_shared && "shared"].filter(Boolean).join(", ") || "none"}</dd>
            <dt>Allowlist</dt>
            <dd>{preview.allowlist ? `${preview.allowlist.length} entries` : "not used"}</dd>
          </dl>
          <p className="muted small">The server validates these again and shows the final effective settings on the scan page.</p>
        </div>

        {serverError ? (
          <Alert kind="error" title="The server rejected this scan">
            <p>{serverError.message}</p>
            <p className="mono small">{serverError.code}</p>
          </Alert>
        ) : null}

        <div className="actions">
          <button className="button primary" type="submit" disabled={submitting}>
            {submitting ? "Starting…" : "Start scan"}
          </button>
        </div>
      </form>
    </section>
  );
}

function Field(props: { label: string; htmlFor: string; error?: string; hint?: string; children: ReactNode }) {
  return (
    <div className={`field${props.error ? " invalid" : ""}`}>
      <label htmlFor={props.htmlFor}>{props.label}</label>
      {props.children}
      {props.hint ? <p className="muted small">{props.hint}</p> : null}
      {props.error ? <p className="field-error">{props.error}</p> : null}
    </div>
  );
}
