// 結合試験（I）: Runtime ＋ モック Gateway（試験仕様 §2.3）
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { validateDef } from "@lla/protocol";
import { cmd, makeRig, waitFor, type Rig } from "./harness.js";

let rig: Rig | null = null;
afterEach(async () => {
  await rig?.close();
  rig = null;
});

const states = (r: Rig) => r.events.filter((e) => e.type === "run.state").map((e) => (e.payload as { state: string }).state);
const runEnded = (r: Rig, runId: string) => r.store.getRun(runId)?.ended_at;
const requests = (r: Rig) =>
  readFileSync(r.mock.record, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as { method: string; path: string; idempotency_key: string | null; status: number });

async function start(r: Rig, text = "フォルダを確認して") {
  return (await r.runtime.handle(cmd("run.start", { conversation_id: "cv1", text, attachments: [], workspace: r.ws, model: "auto" }))) as {
    run_id: string;
  };
}

describe("runtime integration", () => {
  it("I-basic: plan → tools → final, events are valid and memory is kept", async () => {
    rig = await makeRig("basic.yaml");
    writeFileSync(join(rig.ws, "memo.txt"), "メモの本文");
    const { run_id } = await start(rig);
    await waitFor(() => runEnded(rig!, run_id), 30_000, "run end");
    const run = rig.store.getRun(run_id)!;
    expect(run.end_reason).toBe("final");
    expect(states(rig)).toContain("done");
    for (const e of rig.events) expect(validateDef("RuntimeEvent", e).errors, String(e.type)).toEqual([]);
    expect(rig.store.listMemory("cv1")).toEqual([{ key: "件数", value: "1件" }]);
    // ファイルの本文は <untrusted> で包まれてモデルに渡っている
    const toolMsg = rig.store.liveMessages("cv1").find((m) => m.content.includes("tool=fs.read_text"))!;
    expect(toolMsg.content).toContain('<untrusted source="file" ref="memo.txt#L1-L1">');
    expect(rig.events.some((e) => e.type === "notify" && (e.payload as { kind: string }).kind === "done")).toBe(true);
  }, 60_000);

  it("RUN-02: restarting mid-run resumes without re-running committed steps", async () => {
    rig = await makeRig("basic.yaml");
    writeFileSync(join(rig.ws, "memo.txt"), "x");
    const { run_id } = await start(rig);
    await waitFor(() => (rig!.store.lastStep(run_id)?.seq ?? 0) >= 3, 30_000, "step 3");
    await rig.restart();
    await waitFor(() => runEnded(rig!, run_id), 30_000, "run end after restart");
    expect(rig.store.getRun(run_id)!.end_reason).toBe("final");
    // 同じ Idempotency-Key で202（新規作成）が2回起きていない＝同じ手を2回実行していない
    const created = requests(rig).filter((r) => r.path === "/v1/steps" && r.status === 202).map((r) => r.idempotency_key);
    expect(new Set(created).size).toBe(created.length);
    const seqs = rig.store.db.prepare("SELECT seq FROM steps WHERE run_id = ? ORDER BY seq").all(run_id) as { seq: number }[];
    expect(seqs.map((s) => s.seq)).toEqual([1, 2, 3, 4, 5]);
  }, 90_000);

  it("I-08: approval-required work is deferred, other steps continue, and approval executes it", async () => {
    rig = await makeRig("approval.yaml");
    writeFileSync(join(rig.ws, "a.txt"), "A");
    const { run_id } = await start(rig, "a.txt を外にコピーして");
    const req = await waitFor(() => rig!.events.find((e) => e.type === "approval.request"), 30_000, "approval");
    expect(req.payload).toMatchObject({ approval_id: "ap-1", tool: "fs.copy", reason: "outside_workspace", tainted: false });
    await waitFor(() => existsSync(join(rig!.ws, "out")), 30_000, "mkdir while waiting");
    await waitFor(() => states(rig!).includes("waiting_approval"), 30_000, "waiting state");
    expect(existsSync(join(rig.outside, "a-copy.txt"))).toBe(false);
    await rig.runtime.handle(cmd("approval.decide", { approval_id: "ap-1", decision: "approve" }));
    await waitFor(() => runEnded(rig!, run_id), 30_000, "run end");
    expect(readFileSync(join(rig.outside, "a-copy.txt"), "utf8")).toBe("A");
    expect(JSON.parse(rig.store.getRun(run_id)!.final_action!).summary).toBe("コピーしました。");
  }, 90_000);

  it("AGT-05 / RUN-03: 503 and a dropped stream are retried, malformed output is sent back", async () => {
    rig = await makeRig("faults.yaml");
    const { run_id } = await start(rig);
    await waitFor(() => runEnded(rig!, run_id), 30_000, "run end");
    expect(rig.events.filter((e) => e.type === "retry").length).toBeGreaterThanOrEqual(2);
    expect(JSON.parse(rig.store.getRun(run_id)!.final_action!).summary).toBe("回復しました。");
    const bad = rig.store.db.prepare("SELECT * FROM steps WHERE run_id = ? AND phase = 'failed_permanent'").all(run_id);
    expect(bad).toHaveLength(1);
  }, 60_000);

  it("RUN-05 / R3: repetition triggers replanning twice, then stalled + low priority, and the run continues", async () => {
    rig = await makeRig("stall.yaml");
    writeFileSync(join(rig.ws, "memo.txt"), "x");
    const { run_id } = await start(rig);
    await waitFor(() => runEnded(rig!, run_id), 60_000, "run end");
    const s = states(rig);
    expect(s.filter((x) => x === "replanning")).toHaveLength(2);
    expect(s).toContain("stalled");
    expect(rig.store.getRun(run_id)!).toMatchObject({ low_priority: 1, stall_level: 3, end_reason: "final" });
    expect(rig.events.some((e) => e.type === "notify" && (e.payload as { kind: string }).kind === "stalled")).toBe(true);
    // 立て直しの指示がプロンプトに入った（モックが受け取った依頼の中身は記録しないので、Run Store 側で確認）
    expect(rig.store.getRun(run_id)!.replan_count).toBe(2);
  }, 90_000);

  it("I-09 / R10: after reading the web, outside writes require a tainted approval", async () => {
    rig = await makeRig("net-taint.yaml");
    writeFileSync(join(rig.ws, "a.txt"), "A");
    await rig.runtime.handle(cmd("settings.update", { net_level: "search" }));
    await start(rig, "調べてコピーして");
    const req = await waitFor(() => rig!.events.find((e) => e.type === "approval.request"), 30_000, "approval");
    expect(req.payload).toMatchObject({ reason: "tainted_outside", tainted: true });
    expect(rig.store.getConversation("cv1")!.tainted).toBe(1);
    const net = rig.events.find((e) => e.type === "net.activity")!;
    expect(net.payload).toMatchObject({ kind: "search", query: "船級 規則 改正" });
    // 検索結果の本文は untrusted として渡っている
    expect(rig.store.liveMessages("cv1").some((m) => m.content.includes('<untrusted source="web" ref="https://example.go.jp/a">'))).toBe(true);
    // 汚染中は「この run の間は承認」を受け付けない
    await rig.runtime.handle(cmd("approval.decide", { approval_id: "ap-1", decision: "approve", remember: "this_run" }));
    expect(rig.store.findApproval("ap-1")!.remember).toBeNull();
  }, 60_000);

  it("I-07: context compaction keeps pinned memory", async () => {
    rig = await makeRig("compact.yaml", { stall: { repeatWindow: 12, repeatCount: 99, noProgressWindow: 99, errorStreak: 99 } });
    writeFileSync(join(rig.ws, "big.txt"), Array.from({ length: 120 }, (_, i) => `LINE-${i} ${"あ".repeat(20)}`).join("\n"));
    const { run_id } = await start(rig);
    await waitFor(() => runEnded(rig!, run_id), 60_000, "run end");
    expect(states(rig)).toContain("compacting");
    const all = rig.store.db.prepare("SELECT content, compacted_into FROM messages WHERE conversation_id = 'cv1'").all() as {
      content: string;
      compacted_into: string | null;
    }[];
    expect(all.some((m) => m.content.startsWith("[summary]"))).toBe(true);
    expect(all.some((m) => m.compacted_into !== null)).toBe(true);
    expect(rig.store.listMemory("cv1")).toEqual([{ key: "出力先", value: "出力/結果.xlsx" }]);
  }, 90_000);

  it("RUN-11: stop ends the run with user_stop and reports changes", async () => {
    rig = await makeRig("approval.yaml");
    writeFileSync(join(rig.ws, "a.txt"), "A");
    const { run_id } = await start(rig);
    await waitFor(() => rig!.events.find((e) => e.type === "approval.request"), 30_000, "approval");
    const r = (await rig.runtime.handle(cmd("run.stop", { run_id }))) as { ok: boolean };
    expect(r.ok).toBe(true);
    expect(rig.store.getRun(run_id)!.end_reason).toBe("user_stop");
    expect(states(rig)).toContain("stopped");
  }, 60_000);
});
