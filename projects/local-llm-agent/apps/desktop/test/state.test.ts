// 画面の状態の畳み込み（Renderer は状態を持たず、イベントから組み立て直す。UI 仕様 §3）
import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "@lla/protocol";
import { applyEvent, emptyConversation } from "../src/renderer/state.js";

let seq = 0;
const ev = (type: string, payload: Record<string, unknown>): RuntimeEvent => ({ v: 1, id: `e${++seq}`, seq, conversation_id: "c", type: type as RuntimeEvent["type"], payload });

describe("applyEvent", () => {
  it("folds a run into steps, plan, approvals and the final summary", () => {
    seq = 0;
    const events = [
      ev("run.state", { run_id: "r1", state: "planning" }),
      ev("plan", { run_id: "r1", plan: [{ id: "t1", title: "確認", status: "doing" }] }),
      ev("step", { run_id: "r1", step_id: "r1:2", kind: "tool", tool: "fs.copy", args_preview: "{}", status: "running", started_at: "t" }),
      ev("step", { run_id: "r1", step_id: "r1:2", kind: "tool", status: "deferred", started_at: "t" }),
      ev("approval.request", { approval_id: "ap-1", tool: "fs.copy", reason: "outside_workspace", paths: [], tainted: false }),
      ev("artifact", { run_id: "r1", artifact: { path: "a.xlsx", op: "create" } }),
      ev("artifact", { run_id: "r1", artifact: { path: "a.xlsx", op: "overwrite" } }),
      ev("run.state", { run_id: "r1", state: "done", detail: { summary: "完了" } }),
    ];
    const v = events.reduce(applyEvent, emptyConversation());
    expect(v.steps).toHaveLength(1);
    expect(v.steps[0]).toMatchObject({ tool: "fs.copy", status: "deferred", argsPreview: "{}" });
    expect(v.plan[0]!.title).toBe("確認");
    expect(v.approvals[0]!.approval_id).toBe("ap-1");
    expect(v.artifacts).toEqual([{ run_id: "r1", path: "a.xlsx", op: "overwrite" }]);
    expect(v.finalSummary).toBe("完了");
  });

  it("ignores events it has already applied (events.since after reconnect)", () => {
    seq = 0;
    const a = ev("plan", { run_id: "r1", plan: [{ id: "t1", title: "A", status: "doing" }] });
    const v1 = applyEvent(emptyConversation(), a);
    expect(applyEvent(v1, a)).toBe(v1);
  });

  it("clears retry and queue when work resumes, and resets on a new run", () => {
    seq = 0;
    let v = emptyConversation();
    v = applyEvent(v, ev("run.state", { run_id: "r1", state: "reconnecting" }));
    v = applyEvent(v, ev("retry", { attempt: 2, next_in_ms: 2000, cause: "SERVER_BUSY" }));
    v = applyEvent(v, ev("queue", { position: 3, eta_seconds: 6 }));
    expect(v.retry?.attempt).toBe(2);
    v = applyEvent(v, ev("run.state", { run_id: "r1", state: "tool_running" }));
    expect(v.retry).toBeNull();
    expect(v.queue).toBeNull();
    v = applyEvent(v, ev("plan", { run_id: "r1", plan: [{ id: "t", title: "x", status: "done" }] }));
    v = applyEvent(v, ev("run.state", { run_id: "r2", state: "planning" }));
    expect(v.runId).toBe("r2");
    expect(v.plan).toEqual([]);
  });
});
