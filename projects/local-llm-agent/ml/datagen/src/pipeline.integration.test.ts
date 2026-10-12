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
