import { useMemo, useState } from "react";
import type { AddressResults, HostResult, PortResult } from "../types";
import { StateBadge } from "./common";
import { Icon } from "./Icon";

type PortSort = "address" | "state" | "time";

function ipKey(ip: string): number {
  return ip.split(".").reduce((acc, octet) => acc * 256 + Number(octet), 0);
}

function detail(code: string | null, message: string | null): string {
  return [code, message].filter(Boolean).join(" — ");
}

export function PortResultsTable({ items }: { items: AddressResults[] }) {
  const [sort, setSort] = useState<PortSort>("address");
  const rows = useMemo(() => {
    const flat: PortResult[] = items.flatMap((group) => group.ports);
    const sorted = [...flat];
    if (sort === "address") sorted.sort((a, b) => ipKey(a.ip) - ipKey(b.ip) || a.port - b.port);
    if (sort === "state") sorted.sort((a, b) => a.state.localeCompare(b.state) || ipKey(a.ip) - ipKey(b.ip) || a.port - b.port);
    if (sort === "time") sorted.sort((a, b) => b.elapsed_ms - a.elapsed_ms);
    return sorted;
  }, [items, sort]);

  return (
    <div className="table-wrap">
      <div className="table-tools">
        <label className="inline-control">
          <Icon name="sliders" />
          Sort by{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value as PortSort)} aria-label="Sort port results">
            <option value="address">Address, port</option>
            <option value="state">State</option>
            <option value="time">Probe time (slowest first)</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>Address</th>
            <th className="num">Port</th>
            <th>State</th>
            <th className="num">Probe time</th>
            <th>Evidence / error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.ip}:${r.port}`}>
              <td className="mono">{r.ip}</td>
              <td className="num mono">{r.port}</td>
              <td>
                <StateBadge state={r.state} />
              </td>
              <td className="num">{r.elapsed_ms.toFixed(1)} ms</td>
              <td className="muted small">{detail(r.error_code, r.error_message)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HostResultsTable({ items }: { items: HostResult[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Address</th>
            <th>Observation</th>
            <th>Per-probe evidence</th>
            <th className="num">Time</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {items.map((h) => (
            <tr key={h.ip}>
              <td className="mono">{h.ip}</td>
              <td>
                <StateBadge state={h.state} />
              </td>
              <td>
                <ul className="evidence">
                  {h.evidence.map((e) => (
                    <li key={e.port} title={detail(e.error_code, e.error_message) || undefined}>
                      <span className="mono">{e.port}</span> <StateBadge state={e.state} />
                    </li>
                  ))}
                </ul>
              </td>
              <td className="num">{h.elapsed_ms.toFixed(1)} ms</td>
              <td className="muted small">{detail(h.error_code, h.error_message)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
