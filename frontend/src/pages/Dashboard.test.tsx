import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import scans from "../test/fixtures/scans.json";
import scansEmpty from "../test/fixtures/scans_empty.json";
import stats from "../test/fixtures/stats.json";
import statsEmpty from "../test/fixtures/stats_empty.json";
import { mockFetch } from "../test/mockFetch";
import { Dashboard } from "./Dashboard";
import { History } from "./History";

describe("Dashboard", () => {
  it("shows counts derived from the API", async () => {
    mockFetch({ "GET /api/stats": { body: stats }, "GET /api/scans": { body: scans } });
    render(<Dashboard />);
    const s = stats.by_status;
    expect(await screen.findByTestId("stat-Stored scans")).toHaveTextContent(String(stats.total));
    expect(screen.getByTestId("stat-Completed")).toHaveTextContent(String(s.completed));
    expect(screen.getByTestId("stat-Running / queued")).toHaveTextContent(String(s.running + s.queued));
    expect(screen.getByTestId("stat-Cancelled / failed")).toHaveTextContent(String(s.cancelled + s.failed));
    expect(screen.getAllByTestId("status-badge").map((b) => b.textContent)).toEqual(
      expect.arrayContaining(["Running", "Queued", "Completed"]),
    );
  });

  it("shows an empty state when nothing has been scanned", async () => {
    mockFetch({ "GET /api/stats": { body: statsEmpty }, "GET /api/scans": { body: scansEmpty } });
    render(<Dashboard />);
    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
    expect(screen.getByTestId("stat-Stored scans")).toHaveTextContent("0");
    expect(screen.queryByRole("img", { name: /Stored scans by status/ })).not.toBeInTheDocument();
  });

  it("charts the real status counts", async () => {
    mockFetch({ "GET /api/stats": { body: stats }, "GET /api/scans": { body: scans } });
    render(<Dashboard />);
    const s = stats.by_status;
    const expected = (["queued", "running", "completed", "cancelled", "failed"] as const)
      .filter((k) => s[k] > 0)
      .map((k) => `${k[0]!.toUpperCase()}${k.slice(1)} ${s[k]}`)
      .join(", ");
    expect(await screen.findByRole("img", { name: `Stored scans by status: ${expected}` })).toBeInTheDocument();
  });

  it("shows a loading state, then an error if the server is unreachable", async () => {
    mockFetch({ "GET /api/stats": { status: 500, body: { error: { code: "internal_error", message: "internal server error" } } }, "GET /api/scans": { body: scansEmpty } });
    render(<Dashboard />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading/);
    expect(await screen.findByText("Could not load the dashboard")).toBeInTheDocument();
  });
});

describe("History", () => {
  it("lists stored scans with links to their details", async () => {
    mockFetch({ "GET /api/scans": { body: scans } });
    render(<History />);
    const first = scans.items[0]!;
    const links = await screen.findAllByRole("link", { name: first.target });
    expect(links.some((a) => a.getAttribute("href") === `#/scans/${first.id}`)).toBe(true);
    expect(screen.getByText(`1–${scans.total} of ${scans.total}`)).toBeInTheDocument();
  });

  it("shows an empty history", async () => {
    mockFetch({ "GET /api/scans": { body: scansEmpty } });
    render(<History />);
    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
  });
});
