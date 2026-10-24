import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Distribution } from "./Distribution";

describe("Distribution chart", () => {
  const segments = [
    { key: "open", label: "open", value: 3, tone: "tone-success" },
    { key: "closed", label: "closed", value: 1, tone: "tone-neutral" },
    { key: "error", label: "error", value: 0, tone: "tone-danger" },
  ];

  it("draws only non-zero segments, sized by their real counts, with readable tooltips", () => {
    const { container } = render(<Distribution title="Results" unit="probes" segments={segments} />);
    const parts = container.querySelectorAll<HTMLElement>(".dist-seg");
    expect(parts).toHaveLength(2);
    expect(parts[0]!.style.flexGrow).toBe("3");
    expect(parts[0]!.dataset.tip).toBe("open: 3 (75%)");
    expect(parts[1]!.dataset.tip).toBe("closed: 1 (25%)");
    expect(parts[0]).toHaveAttribute("tabindex", "0");
  });

  it("states its data as text for assistive technology and in the legend", () => {
    render(<Distribution title="Results" unit="probes" segments={segments} />);
    expect(screen.getByRole("img", { name: "Results: open 3, closed 1" })).toBeInTheDocument();
    expect(screen.getByText("4 probes")).toBeInTheDocument();
    expect(screen.getByText("error").closest("li")).toHaveClass("is-zero");
  });

  it("shows an empty track instead of inventing data", () => {
    const { container } = render(<Distribution title="Results" unit="probes" segments={segments.map((s) => ({ ...s, value: 0 }))} />);
    expect(container.querySelectorAll(".dist-seg")).toHaveLength(0);
    expect(screen.getByRole("img", { name: "Results: no probes yet" })).toBeInTheDocument();
  });
});
