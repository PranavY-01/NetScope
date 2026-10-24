import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import errorPublic from "./test/fixtures/error_public.json";
import errorValidation from "./test/fixtures/error_validation.json";
import { mockFetch } from "./test/mockFetch";

describe("api client", () => {
  it("sends the client header on mutations only", async () => {
    const { calls } = mockFetch({
      "GET /api/stats": { body: { total: 0, by_status: {} } },
      "POST /api/scans/*": { status: 202, body: {} },
      "DELETE /api/scans/*": { status: 204 },
    });
    await api.stats();
    await api.cancelScan("a".repeat(32));
    await api.deleteScan("a".repeat(32));
    expect(calls[0]!.headers["X-NetScope-Client"]).toBeUndefined();
    expect(calls[1]!.headers["X-NetScope-Client"]).toBe("web");
    expect(calls[2]!.headers["X-NetScope-Client"]).toBe("web");
  });

  it("turns server errors into ApiError with code and fields", async () => {
    mockFetch({ "POST /api/scans": { status: 422, body: errorValidation } });
    const error = await api.createScan({} as never).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.code).toBe("invalid_config");
    expect(error.fields.map((f: { field: string }) => f.field)).toContain("ports");
  });

  it("keeps the policy code from a scope rejection", async () => {
    mockFetch({ "POST /api/scans": { status: 400, body: errorPublic } });
    const error = await api.createScan({} as never).catch((e) => e);
    expect(error.code).toBe("public_target");
    expect(error.message).toContain("--allow-public");
  });

  it("reports an unreachable server clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await api.stats().catch((e) => e);
    expect(error.code).toBe("network");
    expect(error.message).toContain("netscope serve");
  });

  it("builds query strings and export URLs safely", async () => {
    const { calls } = mockFetch({ "GET /api/scans": { body: { items: [], total: 0, limit: 25, offset: 0 } } });
    await api.listScans({ limit: 25, status: "", q: "a&b=c" });
    expect(calls[0]!.url).toBe("/api/scans?limit=25&q=a%26b%3Dc");
    expect(api.exportUrl("f".repeat(32), "csv")).toBe(`/api/scans/${"f".repeat(32)}/export?format=csv`);
  });
});
