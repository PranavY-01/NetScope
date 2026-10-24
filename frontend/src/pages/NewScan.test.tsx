import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import errorPublic from "../test/fixtures/error_public.json";
import errorValidation from "../test/fixtures/error_validation.json";
import limits from "../test/fixtures/limits.json";
import scanCompleted from "../test/fixtures/scan_completed.json";
import { mockFetch } from "../test/mockFetch";
import { NewScan, validateForm, type FormState } from "./NewScan";
import type { Limits } from "../types";

const base: FormState = {
  kind: "scan",
  target: "127.0.0.1",
  ports: "22,80,443",
  timeout: "1",
  concurrency: "100",
  rate: "50",
  maxHosts: "256",
  allowPublic: false,
  allowShared: false,
  allowlist: "",
  authorized: true,
};

describe("validateForm", () => {
  const l = limits as Limits;
  it("accepts a valid scan", () => expect(validateForm(base, l)).toEqual({}));
  it("requires the authorization acknowledgement", () =>
    expect(validateForm({ ...base, authorized: false }, l).authorized).toMatch(/authorized/));
  it("rejects malformed ports", () => expect(validateForm({ ...base, ports: "80,,90" }, l).ports).toBeDefined());
  it("points CIDR targets to discovery", () => expect(validateForm({ ...base, target: "10.0.0.0/24" }, l).target).toMatch(/discovery/));
  it("requires a CIDR for discovery", () => expect(validateForm({ ...base, kind: "discover", target: "host.lan" }, l).target).toBeDefined());
  it("enforces server bounds", () => {
    const errors = validateForm({ ...base, timeout: "99", concurrency: "0", rate: "100000", maxHosts: "5000" }, l);
    expect(Object.keys(errors).sort()).toEqual(["concurrency", "maxHosts", "rate", "timeout"]);
  });
});

async function fillTarget(target: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Target/), target);
  return user;
}

describe("New scan form", () => {
  it("blocks submission without the acknowledgement and sends nothing", async () => {
    const { calls } = mockFetch({ "GET /api/limits": { body: limits } });
    render(<NewScan />);
    const user = await fillTarget("127.0.0.1");
    await user.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText(/must confirm that you are authorized/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("submits the effective settings and opens the new scan", async () => {
    const { calls } = mockFetch({
      "GET /api/limits": { body: limits },
      "POST /api/scans": { status: 201, body: scanCompleted },
    });
    render(<NewScan />);
    const user = await fillTarget("127.0.0.1");
    await user.click(screen.getByLabelText(/Allow shared address space/));
    await user.click(screen.getByLabelText(/I own every target/));
    await user.click(screen.getByRole("button", { name: "Start scan" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/scans/${scanCompleted.id}`));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.headers["X-NetScope-Client"]).toBe("web");
    expect(post.body).toEqual({
      kind: "scan",
      target: "127.0.0.1",
      ports: "22,80,443",
      timeout: 1,
      concurrency: 100,
      rate: 50,
      max_hosts: 256,
      allow_public: false,
      allow_shared: true,
      allowlist: null,
      authorized: true,
    });
  });

  it("shows the server's scope rejection", async () => {
    mockFetch({ "GET /api/limits": { body: limits }, "POST /api/scans": { status: 400, body: errorPublic } });
    render(<NewScan />);
    const user = await fillTarget("8.8.8.8");
    await user.click(screen.getByLabelText(/I own every target/));
    await user.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText("The server rejected this scan")).toBeInTheDocument();
    expect(screen.getByText(/require --allow-public/)).toBeInTheDocument();
    expect(screen.getByText("public_target")).toBeInTheDocument();
  });

  it("maps server field errors onto the form", async () => {
    mockFetch({ "GET /api/limits": { body: limits }, "POST /api/scans": { status: 422, body: errorValidation } });
    render(<NewScan />);
    const user = await fillTarget("127.0.0.1");
    await user.click(screen.getByLabelText(/I own every target/));
    await user.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText(/inverted port range/)).toBeInTheDocument();
  });

  it("previews the settings that will be submitted", async () => {
    mockFetch({ "GET /api/limits": { body: limits } });
    render(<NewScan />);
    await fillTarget("10.1.2.3");
    expect(screen.getByText("Effective settings")).toBeInTheDocument();
    expect(screen.getAllByText("10.1.2.3").length).toBeGreaterThan(0);
    expect(screen.getByText("none")).toBeInTheDocument();
  });
});
