import { describe, expect, it } from "vitest";
import { formatCost, formatTokens } from "./api";

describe("formatCost", () => {
  it("formats settled and estimated", () => {
    expect(formatCost(2_500_000)).toContain("2.5");
    expect(formatCost(2_500_000, true).startsWith("~$")).toBe(true);
  });
});

describe("formatTokens", () => {
  it("keeps small totals exact and formats millions compactly", () => {
    expect(formatTokens(219)).toBe("219");
    expect(formatTokens(3_349_532)).toContain("3.35M");
  });
});
