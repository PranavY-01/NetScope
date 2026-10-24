import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import discoverCompleted from "../test/fixtures/discover_completed.json";
import resultsDiscover from "../test/fixtures/results_discover.json";
import resultsScan from "../test/fixtures/results_scan.json";
import scanCancelled from "../test/fixtures/scan_cancelled.json";
import scanCompleted from "../test/fixtures/scan_completed.json";
import scanQueued from "../test/fixtures/scan_queued.json";
import scanRunning from "../test/fixtures/scan_running.json";
import { mockFetch } from "../test/mockFetch";
import { ScanDetail } from "./ScanDetail";

const emptyResults = { kind: "scan", items: [], total: 0, limit: 100, offset: 0 };

describe("Scan detail", () => {
  it("shows a completed scan with results, settings and export links", async () => {
    mockFetch({ "GET /api/scans/*": { body: scanCompleted }, [`GET /api/scans/${scanCompleted.id}/hosts`]: { body: resultsScan } });
    render(<ScanDetail id={scanCompleted.id} />);
    expect(await screen.findByTestId("status-badge")).toHaveTextContent("Completed");
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 4);
    expect(within(table).getByText("open")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute("href", `/api/scans/${scanCompleted.id}/export?format=json`);
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", `/api/scans/${scanCompleted.id}/export?format=csv`);
    expect(screen.getByText("4 of 4 probes (100%)")).toBeInTheDocument();
    expect(screen.getByText(/Estimated duration: at least/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel scan" })).not.toBeInTheDocument();
  });

  it("shows live progress and cancels a running scan, waiting for the server to confirm", async () => {
    let cancelled = false;
    const { calls } = mockFetch({
      "GET /api/scans/*": () => ({ body: cancelled ? scanCancelled : scanRunning }),
      [`GET /api/scans/${scanRunning.id}/hosts`]: { body: emptyResults },
      [`POST /api/scans/${scanRunning.id}/cancel`]: () => {
        cancelled = true;
        return { status: 202, body: { id: scanRunning.id, status: "running", cancel_requested: true } };
      },
    });
    render(<ScanDetail id={scanRunning.id} />);
    expect(await screen.findByText("3 of 40 probes (7%)")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Export JSON" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel scan" }));
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/cancel"))).toBe(true);
    await waitFor(() => expect(screen.getByTestId("status-badge")).toHaveTextContent("Cancelled · partial"));
    expect(screen.getByText("Partial results")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export JSON" })).toBeInTheDocument();
  });

  it("shows an indeterminate state for a queued scan instead of a percentage", async () => {
    mockFetch({ "GET /api/scans/*": { body: scanQueued }, [`GET /api/scans/${scanQueued.id}/hosts`]: { body: emptyResults } });
    render(<ScanDetail id={scanQueued.id} />);
    expect(await screen.findByText(/waiting for a free scan slot/)).toBeInTheDocument();
    expect(screen.queryByText(/%\)/)).not.toBeInTheDocument();
  });

  it("shows discovery verdicts with their per-probe evidence and filters by state", async () => {
    const { calls } = mockFetch({
      "GET /api/scans/*": { body: discoverCompleted },
      [`GET /api/scans/${discoverCompleted.id}/hosts`]: { body: resultsDiscover },
    });
    render(<ScanDetail id={discoverCompleted.id} />);
    expect(await screen.findByText("Host observations")).toBeInTheDocument();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("responsive").length).toBe(4);
    expect(within(table).getByText("no response")).toBeInTheDocument();
    expect(screen.getByText(/does not mean the host is down/)).toBeInTheDocument();
    await userEvent.setup().selectOptions(screen.getByLabelText("Filter by state"), "error");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/hosts") && c.url.includes("state=error"))).toBe(true));
  });

  it("deletes a finished scan after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = mockFetch({
      "GET /api/scans/*": { body: scanCompleted },
      [`GET /api/scans/${scanCompleted.id}/hosts`]: { body: resultsScan },
      "DELETE /api/scans/*": { status: 204 },
    });
    render(<ScanDetail id={scanCompleted.id} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(window.location.hash).toBe("#/scans"));
    expect(calls.find((c) => c.method === "DELETE")!.headers["X-NetScope-Client"]).toBe("web");
  });

  it("reports an unknown scan", async () => {
    mockFetch({ "GET /api/scans/*": { status: 404, body: { error: { code: "not_found", message: "no scan with that id" } } } });
    render(<ScanDetail id={"0".repeat(32)} />);
    expect(await screen.findByText("Scan not found")).toBeInTheDocument();
  });
});
