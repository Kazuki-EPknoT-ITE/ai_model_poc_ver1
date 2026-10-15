// Runtime の土台の単体試験（U1）: Run Store / ポリシー / 意図記録 / 文法 / glob
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { open } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, sep } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getTool, validateDef, validateToolArgs, type ToolCatalogEntry } from "@lla/protocol";
import { SYSTEM_BASE } from "../src/prompt/templates.js";
import { withFormulaPrefix } from "../src/runtime.js";
import { Ajv2020 } from "ajv/dist/2020.js";
import { RunStore } from "../src/store/store.js";
import { decide, resolvePaths, type PolicyContext } from "../src/policy/policy.js";
import { resolveRealPath } from "../src/policy/paths.js";
import { WriteCoordinator } from "../src/agent/intents.js";
import { expandBatchItems, suggestGlobs } from "../src/agent/batch.js";
import { DEFAULT_STALL, detectStall } from "../src/agent/stall.js";
import type { StepRow } from "../src/store/store.js";
import { buildActionSchema } from "../src/agent/grammar.js";
import { globToRegExp } from "../src/tools/glob.js";
import { lineDiff } from "../src/agent/diff.js";
import { errResult, okResult, type ToolContext } from "../src/tools/types.js";
import { fileHash } from "../src/util.js";

let dir: string;
let ws: string;
let appData: string;
let store: RunStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "lla-rt-"));
  ws = join(dir, "ws");
  appData = join(dir, "appdata");
  mkdirSync(ws);
  mkdirSync(appData);
  store = RunStore.open(join(appData, "runs.db"), "test-key");
});
afterEach(() => {
  store.close();
  rmSync(dir, { recursive: true, force: true });
});

const tool = (name: string): ToolCatalogEntry => getTool(name)!;
const ctx = (over: Partial<PolicyContext> = {}): PolicyContext => ({
  workspace: ws,
  tainted: false,
  netLevel: "off",
  appDataDir: appData,
  remember: [],
  ...over,
});

describe("RunStore", () => {
  it("is encrypted on disk and migrates", () => {
    const c = store.createConversation({ title: "t", workspace: ws });
    store.addMessage({ conversation_id: c.id, run_id: null, step_id: null, role: "user", content: "秘密の依頼本文", tokens: 5 });
    store.close();
    expect(readFileSync(join(appData, "runs.db")).includes(Buffer.from("秘密の依頼本文"))).toBe(false);
    store = RunStore.open(join(appData, "runs.db"), "test-key");
    expect(store.liveMessages(c.id)[0]!.content).toBe("秘密の依頼本文");
    expect(() => RunStore.open(join(appData, "runs.db"), "wrong-key")).toThrow();
  });

  it("numbers events per conversation and returns them since a seq", () => {
    const c = store.createConversation({ title: "t", workspace: ws });
    for (let i = 0; i < 3; i++) store.appendEvent(c.id, "token", { i });
    expect(store.eventsSince(c.id, 1).map((e) => e.seq)).toEqual([2, 3]);
  });
});

describe("conversation search (APP-06)", () => {
  it("finds by title and treats % and _ literally", () => {
    store.createConversation({ id: "s1", title: "請求書の転記 100%", workspace: ws });
    store.createConversation({ id: "s2", title: "契約書の要約", workspace: ws });
    expect(store.searchConversations("請求書").map((c) => c.id)).toEqual(["s1"]);
    expect(store.searchConversations("100%").map((c) => c.id)).toEqual(["s1"]);
    expect(store.searchConversations("%")).toHaveLength(1);
    expect(store.searchConversations("_")).toHaveLength(0);
  });
});

describe("policy", () => {
  it("allows reads and writes inside the workspace", () => {
    expect(decide(tool("fs.list"), { path: "." }, ctx()).kind).toBe("allow");
    expect(decide(tool("xlsx.write_cells"), { path: "a.xlsx", sheet: "S", cells: [{ ref: "A1", value: 1 }] }, ctx()).kind).toBe("allow");
  });

  it("refuses a move or copy onto the same file without asking for approval", () => {
    writeFileSync(join(ws, "在庫.xlsx"), "x");
    for (const name of ["fs.move", "fs.copy"]) {
      const d = decide(tool(name), { src: "在庫.xlsx", dst: "在庫.xlsx" }, ctx());
      expect(d, name).toMatchObject({ kind: "deny", code: "INVALID_ARGS" });
      // 大文字・小文字や書き方が違っても同じファイルなら断る
      if (process.platform === "win32") expect(decide(tool(name), { src: "在庫.xlsx", dst: join(ws, "在庫.XLSX") }, ctx()).kind, name).toBe("deny");
    }
    expect(decide(tool("fs.copy"), { src: "在庫.xlsx", dst: "在庫_写し.xlsx" }, ctx()).kind).toBe("allow");
  });

  it("requires approval outside the workspace, for delete and move, and when tainted", () => {
    const outside = join(dir, "other", "a.xlsx");
    const d = decide(tool("xlsx.read_range"), { path: outside, range: "S!A1:B2" }, ctx());
    expect(d).toMatchObject({ kind: "approve", reason: "outside_workspace" });
    expect(decide(tool("fs.delete"), { path: "a.txt" }, ctx())).toMatchObject({ kind: "approve", reason: "delete" });
    expect(decide(tool("fs.move"), { src: "a.txt", dst: "b.txt" }, ctx())).toMatchObject({ kind: "approve", reason: "move" });
    expect(decide(tool("fs.copy"), { src: "a.txt", dst: outside }, ctx({ tainted: true }))).toMatchObject({
      kind: "approve",
      reason: "tainted_outside",
    });
  });

  it("blocks traversal out of the workspace and forbidden areas even with approval", () => {
    expect(decide(tool("fs.stat"), { path: "..\\..\\x.txt" }, ctx())).toMatchObject({ kind: "approve" });
    const win = process.env.SystemRoot ?? "C:\\Windows";
    expect(decide(tool("fs.stat"), { path: join(win, "win.ini") }, ctx())).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(decide(tool("fs.stat"), { path: join(appData, "runs.db") }, ctx())).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(decide(tool("fs.stat"), { path: "a.txt:secret" }, ctx())).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(decide(tool("fs.stat"), { path: "\\\\?\\C:\\Windows" }, ctx())).toMatchObject({ kind: "deny" });
    expect(decide(tool("fs.stat"), { path: "nul" }, ctx())).toMatchObject({ kind: "deny" });
  });

  it("resolves junctions to their real target", () => {
    const outside = join(dir, "outside");
    mkdirSync(outside);
    writeFileSync(join(outside, "x.txt"), "x");
    try {
      symlinkSync(outside, join(ws, "link"), "junction");
    } catch {
      return; // ジャンクションを作れない環境では省略
    }
    const real = resolveRealPath("link\\x.txt", ws);
    expect(real.toLowerCase()).toBe(join(outside, "x.txt").toLowerCase());
    expect(decide(tool("fs.read_text"), { path: "link\\x.txt" }, ctx())).toMatchObject({ kind: "approve", reason: "outside_workspace" });
  });

  it("is case-insensitive for the workspace boundary", () => {
    expect(decide(tool("fs.stat"), { path: ws.toUpperCase() + "\\a.txt" }, ctx()).kind).toBe("allow");
  });

  it("denies net tools above the allowed level", () => {
    expect(decide(tool("web.search"), { query: "x" }, ctx()).kind).toBe("deny");
    expect(decide(tool("web.search"), { query: "x" }, ctx({ netLevel: "search" })).kind).toBe("allow");
    expect(decide(tool("web.fetch"), { url: "https://a" }, ctx({ netLevel: "search" })).kind).toBe("deny");
  });

  it("marks write targets vs read sources", () => {
    const ps = resolvePaths(tool("fs.copy"), { src: "a", dst: "b" }, ws);
    expect(ps.find((p) => p.path === "a")!.write).toBe(false);
    expect(ps.find((p) => p.path === "b")!.write).toBe(true);
    const dt = resolvePaths(
      tool("data.transform"),
      { inputs: [{ name: "x", source: { csv: "in.csv" } }], steps: [], output: { xlsx: "o.xlsx", sheet: "S" } },
      ws,
    );
    expect(dt.map((p) => [p.path, p.write])).toEqual([
      ["in.csv", false],
      ["o.xlsx", true],
    ]);
  });
});

describe("停滞の検知", () => {
  it("同じ計画の出し直しは進捗とみなさない（中身が変わった計画だけが進捗）", () => {
    const plan = (status: string) => JSON.stringify({ type: "plan_update", plan: [{ id: "t1", title: "読む", status }] });
    const row = (seq: number, action: string, extra: Partial<StepRow> = {}) =>
      ({ seq, action, tool: null, args_digest: null, error_code: null, result: null, result_digest: null, ...extra }) as StepRow;
    const failing = (seq: number) =>
      row(seq, JSON.stringify({ type: "tool_call" }), { tool: "data.transform", args_digest: `d${seq}`, error_code: "INVALID_ARGS", result: JSON.stringify({ ok: false }) });
    // 最初に計画を出したあと、失敗と「同じ計画」を交互に8手 → 進捗なし
    const same = [row(-1, plan("doing")), ...Array.from({ length: 8 }, (_, i) => (i % 2 ? failing(i) : row(i, plan("doing"))))];
    expect(detectStall(same, { ...DEFAULT_STALL, repeatCount: 99, errorStreak: 99 })).toMatchObject({ stalled: true, signal: "no_progress" });
    // 計画の状態が進んでいれば進捗あり
    const moving = Array.from({ length: 8 }, (_, i) => (i % 2 ? failing(i) : row(i, plan(`s${i}`))));
    expect(detectStall(moving, { ...DEFAULT_STALL, repeatCount: 99, errorStreak: 99 }).stalled).toBe(false);
  });
});

describe("batch.run の対象の展開", () => {
  it("作業フォルダの中を絶対パスで指した glob も一致する。外を指すものは何も一致しない", async () => {
    mkdirSync(join(ws, "請求書"));
    for (const f of ["INV-001.pdf", "INV-002.pdf", "メモ.txt"]) writeFileSync(join(ws, "請求書", f), "x");
    const want = ["請求書/INV-001.pdf", "請求書/INV-002.pdf"];
    expect(await expandBatchItems("glob:請求書/*.pdf", ws)).toEqual(want);
    expect(await expandBatchItems(`glob:${join(ws, "請求書", "*.pdf")}`, ws)).toEqual(want);
    // 大文字・小文字の違い（Windows の実際のパス）でも同じ
    if (process.platform === "win32") expect(await expandBatchItems(`glob:${join(ws.toUpperCase(), "請求書", "*.pdf")}`, ws)).toEqual(want);
    writeFileSync(join(dir, "outside.pdf"), "x");
    expect(await expandBatchItems(`glob:${join(dir, "*.pdf")}`, ws)).toEqual([]);
    // 一致しないとき、同じ種類のファイルがあるフォルダを示す（一つ上のフォルダを指したときなど）
    expect(await suggestGlobs(`glob:${join(ws, "*.pdf")}`, ws)).toEqual(["glob:請求書/*.pdf"]);
    expect(await suggestGlobs("glob:*.xlsx", ws)).toEqual([]);
  });
});

describe("WriteCoordinator", () => {
  const tctx = (): ToolContext => {
    const c = store.getConversation("c1") ?? store.createConversation({ id: "c1", title: "t", workspace: ws });
    const run = store.createRun({ conversation_id: c.id, model_current: "local-12b" });
    const step = store.createStep(run.id, 1);
    return { workspace: ws, conversationId: c.id, runId: run.id, stepId: step.id };
  };
  const writeText = (text: string) => async (args: Record<string, unknown>) => {
    writeFileSync(String(args.path), text);
    return okResult({ artifact: { path: args.path } });
  };

  it("writes via staging, backs up the original and records hashes", async () => {
    const target = join(ws, "a.csv");
    writeFileSync(target, "old");
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("csv.write");
    const args = { path: "a.csv", rows: [["x"]], overwrite: true };
    const r = await wc.execute(t, args, resolvePaths(t, args, ws), c, writeText("new"));
    expect(r.ok).toBe(true);
    expect(readFileSync(target, "utf8")).toBe("new");
    const a = r.artifacts![0]!;
    expect(a).toMatchObject({ path: "a.csv", op: "overwrite" });
    expect(readFileSync(a.backup_path!, "utf8")).toBe("old");
    expect(JSON.stringify(r)).not.toContain(".~lla-");
    expect(readdirSync(ws)).toEqual(["a.csv"]);
    expect(store.intentsForRun(c.runId)[0]!.status).toBe("committed");
  });

  it("rewrites staging paths the tool returned relative to the workspace", async () => {
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("csv.write");
    const args = { path: "sub/一覧.csv", rows: [] };
    mkdirSync(join(ws, "sub"), { recursive: true });
    const r = await wc.execute(t, args, resolvePaths(t, args, ws), c, async (a) => {
      writeFileSync(String(a.path), "x");
      return okResult({ artifact: { path: relative(ws, String(a.path)).split(sep).join("/") } });
    });
    expect(JSON.stringify(r)).not.toContain(".~lla-");
    expect((r.data as { artifact: { path: string } }).artifact.path).toBe("sub/一覧.csv");
  });

  it("rewrites staging names in error messages too (the model must not see .~lla- names)", async () => {
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("csv.write");
    const args = { path: "sub/一覧.csv", rows: [] };
    mkdirSync(join(ws, "sub"), { recursive: true });
    const r = await wc.execute(t, args, resolvePaths(t, args, ws), c, async (a) =>
      errResult("INVALID_ARGS", `${relative(ws, String(a.path))} は既に存在します`),
    );
    expect(r.ok).toBe(false);
    expect(JSON.stringify(r)).not.toContain(".~lla-");
    expect(r.error?.message).toContain("一覧.csv は既に存在します");
  });

  it("undoes a run and refuses when the file was changed afterwards", async () => {
    const target = join(ws, "a.csv");
    writeFileSync(target, "old");
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("csv.write");
    const args = { path: "a.csv", rows: [], overwrite: true };
    await wc.execute(t, args, resolvePaths(t, args, ws), c, writeText("new"));
    await wc.execute(tool("csv.write"), { path: "b.csv", rows: [] }, resolvePaths(t, { path: "b.csv", rows: [] }, ws), c, writeText("b"));
    writeFileSync(join(ws, "b.csv"), "edited by user");
    const u = await wc.undoRun(c.runId);
    expect(readFileSync(target, "utf8")).toBe("old");
    expect(u.restored).toHaveLength(1);
    expect(u.warnings[0]).toContain("b.csv");
  });

  it("saves under another name when the target stays locked (R12)", async () => {
    const target = join(ws, "locked.csv");
    writeFileSync(target, "old");
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData, lockWaitMs: 300, now: () => new Date(2026, 9, 11, 15, 30) });
    const fh = await open(target, "r+"); // Windows ではこれで置き換えが EPERM になる
    try {
      const t = tool("csv.write");
      const args = { path: "locked.csv", rows: [], overwrite: true };
      const r = await wc.execute(t, args, resolvePaths(t, args, ws), c, writeText("new"));
      if (process.platform !== "win32") return;
      expect(r.ok).toBe(true);
      expect(r.warnings?.join()).toContain("別名で保存");
      expect(existsSync(join(ws, "locked (AI編集 2026-10-11 1530).csv"))).toBe(true);
    } finally {
      await fh.close();
    }
    expect(readFileSync(target, "utf8")).toBe("old");
  });

  it("resumes: staged + replaced → committed; staged + not replaced → replace; prepared → undone", async () => {
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    // 1) 置き換えまで済んだが committed を書く前に落ちた
    writeFileSync(join(ws, "done.txt"), "new");
    const h = await fileHash(join(ws, "done.txt"));
    const i1 = store.addIntent({ step_id: c.stepId, batch_item_id: null, path: join(ws, "done.txt"), op: "overwrite", op_digest: "d", pre_hash: "0".repeat(64), backup_path: null });
    store.updateIntent(i1.id, { status: "staged", post_hash: h });
    // 2) staging は書けたが置き換える前に落ちた
    writeFileSync(join(ws, "todo.txt"), "old");
    const stg = join(ws, ".~lla-x.todo.txt");
    writeFileSync(stg, "new2");
    const i2 = store.addIntent({ step_id: c.stepId, batch_item_id: null, path: join(ws, "todo.txt"), op: "overwrite", op_digest: "d", pre_hash: await fileHash(join(ws, "todo.txt")), backup_path: null, staging_path: stg });
    store.updateIntent(i2.id, { status: "staged", post_hash: await fileHash(stg) });
    // 3) ツールの書き込み中に落ちた
    const stg3 = join(ws, ".~lla-y.p.txt");
    writeFileSync(stg3, "partial");
    store.addIntent({ step_id: c.stepId, batch_item_id: null, path: join(ws, "p.txt"), op: "create", op_digest: "d", pre_hash: null, backup_path: null, staging_path: stg3 });

    const r = await wc.resumeOpen();
    expect(r.resolved).toBe(3);
    expect(readFileSync(join(ws, "todo.txt"), "utf8")).toBe("new2");
    expect(existsSync(stg3)).toBe(false);
    expect(store.intentsForRun(c.runId).map((i) => i.status)).toEqual(["committed", "committed", "undone"]);
    expect(store.openIntents()).toHaveLength(0);
  });

  it("does not write again when resuming a step whose write was already committed (FI-13)", async () => {
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("csv.write");
    const args = { path: "once.csv", rows: [["x"]] };
    let calls = 0;
    const append = async (a: Record<string, unknown>) => {
      calls++;
      writeFileSync(String(a.path), "row\n", { flag: "a" });
      return okResult({});
    };
    // 置き換えの後、committed を記録する前に落ちた状態を作る
    const first = await wc.execute(t, args, resolvePaths(t, args, ws), c, append);
    const intent = store.intentsForRun(c.runId)[0]!;
    store.updateIntent(intent.id, { status: "staged" });
    await wc.resumeOpen();
    expect(store.getIntent(intent.id)!.status).toBe("committed");
    // 同じ手順を再実行しても、ツールは呼ばれない
    const again = await wc.execute(t, args, resolvePaths(t, args, ws), c, append);
    expect(calls).toBe(1);
    expect(readFileSync(join(ws, "once.csv"), "utf8")).toBe("row\n");
    expect(again.ok).toBe(true);
    expect(again.warnings?.join()).toContain("やり直していません");
    expect(again.artifacts?.[0]).toMatchObject({ path: "once.csv", post_hash: first.artifacts![0]!.post_hash });
    // 別の手順なら、同じ操作でも実行する
    const c2 = tctx();
    await wc.execute(t, args, resolvePaths(t, args, ws), c2, append);
    expect(calls).toBe(2);
  });

  it("delete moves the file to the backup area and can be undone", async () => {
    writeFileSync(join(ws, "x.txt"), "x");
    const c = tctx();
    const wc = new WriteCoordinator(store, { appDataDir: appData });
    const t = tool("fs.delete");
    const r = await wc.execute(t, { path: "x.txt" }, resolvePaths(t, { path: "x.txt" }, ws), c, async () => okResult({}));
    expect(r.ok).toBe(true);
    expect(existsSync(join(ws, "x.txt"))).toBe(false);
    await wc.undoRun(c.runId);
    expect(readFileSync(join(ws, "x.txt"), "utf8")).toBe("x");
  });
});

describe("grammar (T-03)", () => {
  it("builds a oneOf that accepts only enabled tools with valid args", () => {
    const enabled = ["fs.list", "xlsx.write_cells", "memory.note"].map(tool);
    const schema = buildActionSchema(enabled);
    const ajv = new Ajv2020({ strict: false });
    const v = ajv.compile(schema);
    expect(v({ type: "tool_call", tool: "fs.list", args: { path: "." } })).toBe(true);
    expect(v({ type: "tool_call", tool: "web.search", args: { query: "x" } })).toBe(false);
    expect(v({ type: "tool_call", tool: "fs.list", args: { depth: 99 } })).toBe(false);
    expect(v({ type: "plan_update", plan: [{ id: "t1", title: "x", status: "todo" }] })).toBe(true);
    expect(v({ type: "final", summary: "x", outputs: [], failures: [], needs_review: [] })).toBe(true);
    expect(JSON.stringify(schema)).not.toContain("$ref");
    // 生成した Action はプロトコルの Action でもある
    expect(validateDef("Action", { type: "tool_call", tool: "fs.list", args: { path: "." } }).ok).toBe(true);
    expect(buildActionSchema(enabled)).toBe(schema); // キャッシュ
  });
});

describe("glob", () => {
  it("matches *, ? and **", () => {
    expect(globToRegExp("請求書/*.pdf").test("請求書/INV-001.pdf")).toBe(true);
    expect(globToRegExp("請求書/*.pdf").test("請求書/sub/INV.pdf")).toBe(false);
    expect(globToRegExp("**/*.xlsx").test("a/b/c.XLSX")).toBe(true);
    expect(globToRegExp("**/*.xlsx").test("c.xlsx")).toBe(true);
    expect(globToRegExp("INV-00?.pdf").test("INV-001.pdf")).toBe(true);
  });
});

describe("lineDiff", () => {
  it("reports inserted and deleted lines in order", () => {
    const d = lineDiff(["a", "b", "c"], ["a", "B", "c", "d"]);
    expect(d.changes).toEqual([
      { kind: "delete", location: "2行目", before: "b", after: null },
      { kind: "insert", location: "2行目", before: null, after: "B" },
      { kind: "insert", location: "4行目", before: null, after: "d" },
    ]);
    expect(lineDiff(["x"], ["x"]).changes).toEqual([]);
  });
});

describe("system プロンプトの例", () => {
  it("例に載せた手は、アクションの形式とツールの引数の形式に合う（モデルがそのまま真似しても通る）", () => {
    const examples = SYSTEM_BASE.split("\n")
      .map((l) => l.replace(/^\s*(\d+手目:\s*)?/, ""))
      .filter((l) => l.startsWith('{"type"'));
    expect(examples.length).toBeGreaterThanOrEqual(3);
    for (const ex of examples) {
      const a = JSON.parse(ex) as { type: string; tool?: string; args?: unknown };
      expect(validateDef("Action", a).errors, ex).toEqual([]);
      if (a.type === "tool_call") expect(validateToolArgs(a.tool!, a.args).errors, ex).toEqual([]);
    }
  });
});

describe("Excel の数式の正規化", () => {
  it("\"=\" が抜けた数式に \"=\" を補い、ほかの値は変えない", () => {
    const args = { path: "a.xlsx", sheet: "S", cells: [{ ref: "B5", formula: "SUM(B2:B4)" }, { ref: "B6", formula: "=B5*1.1" }, { ref: "A5", value: "合計" }] };
    expect(withFormulaPrefix(args)).toEqual({ ...args, cells: [{ ref: "B5", formula: "=SUM(B2:B4)" }, { ref: "B6", formula: "=B5*1.1" }, { ref: "A5", value: "合計" }] });
    const create = { path: "b.xlsx", sheets: [{ name: "S", rows: [[1]], formulas: [{ ref: "A2", formula: " A1*2 " }] }] };
    expect(withFormulaPrefix(create).sheets[0]!.formulas[0]!.formula).toBe("=A1*2");
  });
});
