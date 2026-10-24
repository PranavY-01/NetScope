import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import limits from "./test/fixtures/limits.json";
import scansEmpty from "./test/fixtures/scans_empty.json";
import statsEmpty from "./test/fixtures/stats_empty.json";
import { mockFetch } from "./test/mockFetch";
import { initialTheme, THEME_KEY } from "./theme";

const root = document.documentElement;

function mockApi() {
  return mockFetch({
    "GET /api/stats": { body: statsEmpty },
    "GET /api/scans": { body: scansEmpty },
    "GET /api/limits": { body: limits },
  });
}

describe("initial theme", () => {
  it("prefers the theme already applied by theme-init.js", () => {
    root.dataset.theme = "light";
    expect(initialTheme()).toBe("light");
  });

  it("falls back to a saved choice, then the system preference", () => {
    window.localStorage.setItem(THEME_KEY, "light");
    expect(initialTheme()).toBe("light");
    window.localStorage.clear();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("light"), addEventListener() {}, removeEventListener() {} }));
    expect(initialTheme()).toBe("light");
    vi.unstubAllGlobals();
  });

  it("defaults to dark when nothing says otherwise", () => {
    expect(initialTheme()).toBe("dark");
  });
});

describe("theme toggle", () => {
  it("switches the whole document theme, persists it, and updates its label", async () => {
    mockApi();
    render(<App />);
    const user = userEvent.setup();
    const toggle = screen.getByRole("button", { name: "Switch to light theme" });
    expect(root.dataset.theme).toBe("dark");

    await user.click(toggle);
    expect(root.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBe(toggle);

    await user.click(toggle);
    expect(root.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("is keyboard operable", async () => {
    mockApi();
    render(<App />);
    const user = userEvent.setup();
    screen.getByRole("button", { name: "Switch to light theme" }).focus();
    await user.keyboard("{Enter}");
    expect(root.dataset.theme).toBe("light");
    await user.keyboard(" ");
    expect(root.dataset.theme).toBe("dark");
  });

  it("restores a saved choice on the next visit", () => {
    window.localStorage.setItem(THEME_KEY, "light");
    mockApi();
    render(<App />);
    expect(root.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
  });

  it("cross-fades colours only briefly", async () => {
    mockApi();
    render(<App />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(root.classList.contains("theme-switching")).toBe(true);
    await waitFor(() => expect(root.classList.contains("theme-switching")).toBe(false), { timeout: 1000 });
  });

  it("keeps unsaved form input when the theme changes", async () => {
    window.location.hash = "#/new";
    const { calls } = mockApi();
    render(<App />);
    const user = userEvent.setup();
    const target = await screen.findByLabelText(/Target/);
    await user.type(target, "10.9.8.7");
    await user.click(screen.getByLabelText(/Allow shared address space/));
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(screen.getByLabelText(/Target/)).toHaveValue("10.9.8.7");
    expect(screen.getByLabelText(/Allow shared address space/)).toBeChecked();
    expect(screen.getByLabelText(/I own every target/)).not.toBeChecked();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });
});
