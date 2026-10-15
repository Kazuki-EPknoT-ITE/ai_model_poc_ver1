// 依頼ごとのまとめ（会話を主役にした表示）と、完了報告の Markdown の解析
import { describe, expect, it } from "vitest";
import type { RuntimeEvent } from "@lla/protocol";
import { applyEvent, emptyConversation, groupTurns, stepTarget } from "../src/renderer/state.js";
import { parseInline, parseMarkdown } from "../src/renderer/markdown.js";

let seq = 0;
const ev = (type: string, payload: Record<string, unknown>): RuntimeEvent => ({ v: 1, id: `e${++seq}`, seq, conversation_id: "c", type: type as RuntimeEvent["type"], payload });

describe("runs and turns", () => {
  it("keeps each run's answer and tags steps and net activity with the run", () => {
    seq = 0;
    const v = [
      ev("run.state", { run_id: "r1", state: "planning" }),
      ev("step", { run_id: "r1", step_id: "s1", kind: "tool", tool: "fs.list", status: "ok", started_at: "t" }),
      ev("net.activity", { kind: "search", query: "q" }),
      ev("run.state", { run_id: "r1", state: "done", detail: { summary: "一つ目", unresolved: [{ tool: "xlsx.write_cells", error_code: "FILE_LOCKED", message: "使用中" }] } }),
      ev("run.state", { run_id: "r2", state: "planning" }),
      ev("step", { run_id: "r2", step_id: "s2", kind: "tool", tool: "fs.stat", status: "running", started_at: "t" }),
    ].reduce(applyEvent, emptyConversation());
    expect(v.runs.map((r) => [r.runId, r.state, r.finalSummary])).toEqual([
      ["r1", "done", "一つ目"],
      ["r2", "planning", null],
    ]);
    expect(v.runs[0]!.unresolved).toEqual([{ tool: "xlsx.write_cells", error_code: "FILE_LOCKED", message: "使用中" }]);
    expect(v.runs[1]!.unresolved).toEqual([]);
    expect(v.steps.map((s) => s.runId)).toEqual(["r1", "r2"]);
    expect(v.net[0]!.runId).toBe("r1");
  });

  it("keeps the final report fields (outputs, failures, needs_review) of a done run", () => {
    seq = 0;
    const v = [
      ev("run.state", { run_id: "r1", state: "planning" }),
      ev("run.state", {
        run_id: "r1",
        state: "done",
        detail: { summary: "s", outputs: [{ path: "a.csv", description: "集計" }], failures: [{ item: "b.pdf", reason: "読めない" }], needs_review: [{ path: "c.xlsx", location: "B3", reason: "金額が合わない" }] },
      }),
    ].reduce(applyEvent, emptyConversation());
    expect(v.runs[0]).toMatchObject({
      outputs: [{ path: "a.csv", description: "集計" }],
      failures: [{ item: "b.pdf", reason: "読めない" }],
      needsReview: [{ path: "c.xlsx", location: "B3", reason: "金額が合わない" }],
      unresolved: [],
    });
  });

  it("attaches messages to runs, including ones just sent without a run id", () => {
    seq = 0;
    let v = [ev("run.state", { run_id: "r1", state: "done", detail: { summary: "a" } })].reduce(applyEvent, emptyConversation());
    const msgs = [
      { id: "m1", content: "一つ目", run_id: "r1" },
      { id: "local", content: "二つ目", run_id: null },
    ];
    // 送ったばかり（まだ作業が始まっていない）→ 作業待ちの1件
    expect(groupTurns(msgs, v).map((t) => [t.runId, t.messages.map((m) => m.id)])).toEqual([
      ["r1", ["m1"]],
      [null, ["local"]],
    ]);
    // 作業が始まると、その作業に結び付く
    v = applyEvent(v, ev("run.state", { run_id: "r2", state: "planning" }));
    expect(groupTurns(msgs, v).map((t) => [t.runId, t.messages.map((m) => m.id)])).toEqual([
      ["r1", ["m1"]],
      ["r2", ["local"]],
    ]);
    // 実行中に送った追加の指示は、実行中の作業に付く
    const more = [...msgs, { id: "local2", content: "追加", run_id: null }];
    expect(groupTurns(more, v).at(-1)!.messages.map((m) => m.id)).toEqual(["local", "local2"]);
  });

  it("extracts a short target from the args preview", () => {
    expect(stepTarget('{"path":"請求書\\\\a.pdf","pages":[1]}')).toBe("請求書\\a.pdf");
    expect(stepTarget('{"query":"旅費 規程"}')).toBe("旅費 規程");
    expect(stepTarget('{"rows":[1,2]}')).toBe("");
    expect(stepTarget(undefined)).toBe("");
  });
});

describe("markdown", () => {
  it("parses paragraphs, lists, tables and code without interpreting HTML", () => {
    const md = ["まとめました。", "次の行", "", "1. **請求書** を読む", "2. `送金明細.xlsx` に書く", "   - 3件", "", "| 品名 | 金額 |", "|---|---:|", "| 塗装 | 1,200 |", "", "<b>太字ではない</b>"].join("\n");
    const b = parseMarkdown(md);
    expect(b.map((x) => x.t)).toEqual(["p", "ol", "table", "p"]);
    expect(b[0]).toEqual({ t: "p", lines: ["まとめました。", "次の行"] });
    const ol = b[1] as Extract<(typeof b)[number], { t: "ol" }>;
    expect(ol.items.map((i) => i.text)).toEqual(["**請求書** を読む", "`送金明細.xlsx` に書く"]);
    expect(ol.items[1]!.children!.items[0]!.text).toBe("3件");
    expect(b[2]).toMatchObject({ head: ["品名", "金額"], align: [null, "right"], rows: [["塗装", "1,200"]] });
    expect(b[3]).toEqual({ t: "p", lines: ["<b>太字ではない</b>"] });
    expect(parseInline("**A** と `b` と *c*")).toEqual([
      { t: "b", v: "A" },
      { t: "text", v: " と " },
      { t: "code", v: "b" },
      { t: "text", v: " と *c*" },
    ]);
  });

  it("treats '・' bullets as a list and keeps fenced code as is", () => {
    const b = parseMarkdown("・一つ\n・二つ\n\n```\n<x>\n```");
    expect(b[0]).toMatchObject({ t: "ul", items: [{ text: "一つ" }, { text: "二つ" }] });
    expect(b[1]).toEqual({ t: "code", text: "<x>" });
  });
});
