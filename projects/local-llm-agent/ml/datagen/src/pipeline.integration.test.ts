// 実行と検証の流れ（モック Gateway・ヘッドレス Runtime・office-tools を実際に動かす）
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { runEpisode } from "./pipeline.js";
import { TEMPLATES, rng } from "./templates.js";

const work = mkdtempSync(join(tmpdir(), "lla-datagen-test-"));
afterAll(() => rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));

describe("runEpisode", () => {
  it("正しい手順は合格し、1手ごとの学習データになる", async () => {
    const out: string[] = [];
    const ep = TEMPLATES.memo_to_csv!(rng(11), "ok-11");
    const r = await runEpisode(ep, work, out);
    expect(r.reason).toBeUndefined();
    expect(r.ok).toBe(true);
    expect(out).toHaveLength(ep.actions.length);
    const first = JSON.parse(out[0]!) as { messages: { role: string; content: string }[]; completion: string };
    expect(first.messages[0]!.role).toBe("system");
    expect(first.messages.some((m) => m.role === "user" && m.content.includes(ep.request))).toBe(true);
    expect(JSON.parse(first.completion)).toEqual(ep.actions[0]);
  }, 120_000);

  it("成果物が正解と違う手順は不合格にし、学習データに入れない", async () => {
    const out: string[] = [];
    const ep = TEMPLATES.memo_to_csv!(rng(12), "bad-12");
    const write = ep.actions.find((a) => a.tool === "csv.write") as { args: { rows: unknown[][] } };
    write.args.rows = write.args.rows.slice(1); // 1品目書き漏らす
    const r = await runEpisode(ep, work, out);
    expect(r.ok).toBe(false);
    expect(out).toHaveLength(0);
  }, 120_000);
});

describe("checkEvalOverlap", () => {
  it("評価セットと同じファイルを検出し、datagen の請求書は重ならない", async () => {
    const { checkEvalOverlap } = await import("./overlap.js");
    const { createHash } = await import("node:crypto");
    const { readFileSync, readdirSync } = await import("node:fs");
    const { uvPython } = await import("../../../eval/harness/src/env.js");
    // 評価セットと同じ作り方（通し番号 1 から）の請求書 → 重複として検出される
    const dir = join(work, "eval-like");
    expect((await uvPython(["-m", "lla_eval", "gen-invoices", dir, "--count", "3", "--no-expected"])).code).toBe(0);
    const evalHashes = readdirSync(dir)
      .filter((f) => f.endsWith(".pdf"))
      .map((f) => createHash("sha256").update(readFileSync(join(dir, f))).digest("hex"));
    const hit = await checkEvalOverlap(evalHashes);
    expect(hit.overlap.length).toBeGreaterThan(0);
    expect(hit.checked).toContain("tool_tasks_sample");
    // datagen の請求書の手順例 → 重ならない
    const out: string[] = [];
    const r = await runEpisode(TEMPLATES.invoices_to_excel!(rng(13), "inv-13"), work, out);
    expect(r.ok).toBe(true);
    expect((await checkEvalOverlap(r.inputHashes ?? [])).overlap).toEqual([]);
  }, 180_000);
});

describe("学習データの匿名化", () => {
  it("PC の一時フォルダ・利用者名・手順例のフォルダが学習データに残らない", async () => {
    const { userInfo } = await import("node:os");
    const { FAKE_EPISODE_DIR } = await import("./pipeline.js");
    const out: string[] = [];
    // 作業フォルダ外のパスも出る例（承認待ちのコピー）と、ファイル一覧の例
    for (const ep of [TEMPLATES.copy_outside_approval!(rng(21), "anon-21"), TEMPLATES.ask_which_memo!(rng(22), "anon-22")]) {
      expect((await runEpisode(ep, work, out)).ok).toBe(true);
    }
    const all = out.join("\n");
    expect(all).not.toContain(work.split("\\").join("\\\\"));
    expect(all).not.toContain("lla-datagen-test-");
    expect(all.toLowerCase()).not.toContain(userInfo().username.toLowerCase());
    expect(all).toContain(JSON.stringify(FAKE_EPISODE_DIR).slice(1, -1));
  }, 180_000);
});
