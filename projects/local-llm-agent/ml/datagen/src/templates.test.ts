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
          const { actions, checks, approval, answer } = make(rng(seed), `x-${seed}`);
          expect(actions[0]!.type).toBe("plan_update");
          const n = (actions[0]!.plan as unknown[]).length;
          expect(n).toBeGreaterThanOrEqual(3);
          expect(n).toBeLessThanOrEqual(8);
          expect(actions.at(-1)!.type).toBe("final");
          // 承認待ちの例だけは「待ちのあいだの完了」と「判断後の完了」の2つ
          expect(actions.filter((a) => a.type === "final")).toHaveLength(approval || answer !== undefined ? 2 : 1);
          expect(checks.length).toBeGreaterThan(0);
        }
      });
    });
  }

  it("種を変えると中身が変わる", () => {
    const xs = new Set(Array.from({ length: 10 }, (_, i) => JSON.stringify(TEMPLATES.memo_to_csv!(rng(i), "a").files)));
    expect(xs.size).toBeGreaterThan(5);
  });

  it("ファイルの中身を使った完了報告には、そのファイルを出典に付ける", () => {
    for (const name of ["memo_to_csv", "recover_missing_file", "expense_summary", "copy_outside_approval", "ask_which_memo", "injection_in_memo"]) {
      for (let seed = 1; seed <= 10; seed++) {
        const ep = TEMPLATES[name]!(rng(seed), "a");
        const refs = ((ep.actions.at(-1)!.citations ?? []) as { ref: string }[]).map((c) => c.ref);
        expect(refs, name).toHaveLength(1);
        expect(Object.keys(ep.files), name).toContain(refs[0]);
        expect(ep.checks.find((c) => c.type === "final_cites")?.refs, name).toEqual(refs);
      }
    }
  });

  it("紛れ込んだ指示はメモにだけあり、CSV の行にも手にも入らない", () => {
    for (let seed = 1; seed <= 20; seed++) {
      const ep = TEMPLATES.injection_in_memo!(rng(seed), "a");
      const memo = Object.values(ep.files)[0]!;
      expect(memo).toMatch(/指示|命令/);
      const rows = ep.expected["expected.csv"]!;
      expect(rows).not.toMatch(/指示|命令|削除/);
      // 削除・コピー・外への送信の手は無い
      expect(ep.actions.filter((a) => a.type === "tool_call").map((a) => a.tool)).toEqual(["fs.read_text", "csv.write"]);
    }
  });
});

describe("依頼の言い回し", () => {
  it("同じ雛形でも、依頼の文面が十分にばらつく（最初の手が重複で落ちないように）", () => {
    for (const name of Object.keys(TEMPLATES)) {
      const reqs = new Set(Array.from({ length: 40 }, (_, i) => TEMPLATES[name]!(rng(i + 1), "a").request));
      expect(reqs.size, name).toBeGreaterThanOrEqual(15);
    }
  });

  it("結びを付けても句点が重ならない", () => {
    for (let i = 1; i <= 50; i++) expect(TEMPLATES.expense_summary!(rng(i), "a").request).not.toMatch(/。。/);
  });
});
