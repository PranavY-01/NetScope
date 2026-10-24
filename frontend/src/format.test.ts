import { describe, expect, it } from "vitest";
import { formatPorts } from "./format";

describe("formatPorts (display only)", () => {
  it.each([
    [[22, 80, 443], "22, 80, 443"],
    [[1, 2, 3, 4, 5], "1-5"],
    [[443, 22, 23, 24, 80], "22-24, 80, 443"],
    [[8080], "8080"],
    [[], ""],
  ])("formats %j as %s", (ports, text) => expect(formatPorts(ports)).toBe(text));

  it("collapses a full range scan", () => {
    expect(formatPorts(Array.from({ length: 3000 }, (_, i) => i + 1))).toBe("1-3000");
  });

  it("caps very long lists and says how many parts are hidden", () => {
    const odd = Array.from({ length: 20 }, (_, i) => i * 2 + 1);
    expect(formatPorts(odd, 3)).toBe("1, 3, 5, … (+17 more)");
  });
});
