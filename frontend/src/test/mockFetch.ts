import { vi } from "vitest";

export interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body?: unknown } | undefined;

/** Route fetch calls by "METHOD /path" prefix (query string ignored). Unmatched calls fail loudly. */
export function mockFetch(routes: Record<string, Handler | { status?: number; body?: unknown }>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    const headers = (init.headers ?? {}) as Record<string, string>;
    const call: Call = { method, url, headers, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const path = url.split("?")[0];
    const key = Object.keys(routes)
      .filter((k) => {
        const [m, p] = k.split(" ");
        return m === method && p !== undefined && (p.endsWith("*") ? path!.startsWith(p.slice(0, -1)) : path === p);
      })
      .sort((a, b) => b.length - a.length)[0];
    if (!key) throw new Error(`unexpected fetch: ${method} ${url}`);
    const route = routes[key]!;
    const result = typeof route === "function" ? route(call) : route;
    const status = result?.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(result?.body ?? {}), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
