import { describe, expect, it } from "vitest";
import { href, parseRoute } from "./router";

describe("router", () => {
  it.each([
    ["", { page: "dashboard" }],
    ["#/", { page: "dashboard" }],
    ["#/new", { page: "new" }],
    ["#/scans", { page: "history" }],
    [`#/scans/${"a".repeat(32)}`, { page: "scan", id: "a".repeat(32) }],
    ["#/scans/not-an-id", { page: "not-found" }],
    ["#/scans/../../etc", { page: "not-found" }],
  ])("parses %s", (hash, route) => expect(parseRoute(hash)).toEqual(route));

  it("round-trips scan links", () => {
    const route = { page: "scan" as const, id: "b".repeat(32) };
    expect(parseRoute(href(route))).toEqual(route);
  });
});
