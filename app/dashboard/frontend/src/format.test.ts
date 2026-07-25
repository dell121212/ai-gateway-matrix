import { describe, expect, it } from "vitest";
import { formatCredits } from "./api";

describe("formatCredits", () => {
  it("formats settled and estimated", () => {
    expect(formatCredits(2_500_000)).toContain("2.5");
    expect(formatCredits(2_500_000, true).startsWith("~")).toBe(true);
  });
});
