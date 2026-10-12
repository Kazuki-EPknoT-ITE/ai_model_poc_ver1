import { validateDef, validateToolArgs } from "@lla/protocol";
import { describe, expect, it } from "vitest";
import { TEMPLATES, rng } from "./templates.js";

describe("手順例の雛形", () => {
  for (const [name, make] of Object.entries(TEMPLATES)) {
    describe(name, () => {
      const seeds = Array.from({ length: 30 }, (_, i) => i + 1);

      it("同じ種なら同じ手順例になる", () => {
        expect(make(rng(7), "a")).toEqual(make(rng(7), "a"));
      });

      it("どの手もアクションの形式と各ツールの引数の形式に合う", () => {
        for (const seed of seeds) {
          const ep = make(rng(seed), `x-${seed}`);
          for (const a of ep.actions) {
            const v = validateDef("Action", a);
            expect(v.errors, JSON.stringify(a)).toEqual([]);
            if (a.type === "tool_call") expect(validateToolArgs(a.tool as string, a.args).errors).toEqual([]);
          }
        }
      });

      it("最初は計画（3〜8項目）、最後は完了報告（system プロンプトの決まりどおり）", () => {
        for (const seed of seeds) {
          const { actions, checks } = make(rng(seed), `x-${seed}`);
          expect(actions[0]!.type).toBe("plan_update");
          const n = (actions[0]!.plan as unknown[]).length;
          expect(n).toBeGreaterThanOrEqual(3);
          expect(n).toBeLessThanOrEqual(8);
          expect(actions.at(-1)!.type).toBe("final");
          expect(actions.filter((a) => a.type === "final")).toHaveLength(1);
          expect(checks.length).toBeGreaterThan(0);
        }
      });
    });
  }

  it("種を変えると中身が変わる", () => {
    const xs = new Set(Array.from({ length: 10 }, (_, i) => JSON.stringify(TEMPLATES.memo_to_csv!(rng(i), "a").files)));
    expect(xs.size).toBeGreaterThan(5);
  });
});
