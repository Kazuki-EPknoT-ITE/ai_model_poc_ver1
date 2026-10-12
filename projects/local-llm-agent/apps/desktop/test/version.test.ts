// 版の比較（ADM-05 の自動更新）
import { describe, expect, it } from "vitest";
import { isNewer, parseVersion } from "../src/shared/version.js";

describe("version", () => {
  it("compares numerically", () => {
    expect(isNewer("0.2.0", "0.1.9")).toBe(true);
    expect(isNewer("0.10.0", "0.9.9")).toBe(true);
    expect(isNewer("1.0.0", "1.0.0")).toBe(false);
    expect(isNewer("0.1.0", "0.2.0")).toBe(false);
  });

  it("never updates on malformed versions", () => {
    expect(parseVersion("v1.0.0")).toBeNull();
    expect(isNewer("1.0", "0.1.0")).toBe(false);
    expect(isNewer("9.9.9-beta", "0.1.0")).toBe(false);
  });
});
