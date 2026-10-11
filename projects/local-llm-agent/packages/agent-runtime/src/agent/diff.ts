// 差分プレビュー（AGT-03、UI 仕様の差分画面）。変更前はバックアップ、変更後は現在のファイル。
import { readFile, stat } from "node:fs/promises";
import { extname, isAbsolute, join } from "node:path";
import type { ToolResult } from "@lla/protocol";
import { pathKey } from "../policy/paths.js";
import type { IntentRow, RunStore } from "../store/store.js";
import type { ToolContext } from "../tools/types.js";

export interface DiffChange {
  kind: "insert" | "delete" | "modify" | "add" | "remove";
  location: string;
  before: string | null;
  after: string | null;
}
export interface ArtifactDiff {
  kind: "docx" | "xlsx" | "text" | "binary";
  path: string;
  op: IntentRow["op"];
  changes: DiffChange[];
  truncated: boolean;
  note?: string;
}

export type CallTool = (name: string, args: Record<string, unknown>, ctx: ToolContext) => Promise<ToolResult>;

const TEXT_EXT = /\.(txt|md|csv|tsv|json|log|xml|html?)$/i;
const MAX_CHANGES = 500;
const MAX_CELLS = 2000;

/** run の中で、そのパスを最後に書き換えた意図記録。 */
export function findIntent(store: RunStore, runId: string, path: string, workspace: string): IntentRow | undefined {
  const key = pathKey(isAbsolute(path) ? path : join(workspace, path));
  return store
    .intentsForRun(runId)
    .filter((i) => (i.status === "committed" || i.status === "conflict") && (pathKey(i.final_path ?? i.path) === key || pathKey(i.path) === key))
    .pop();
}

export async function computeDiff(intent: IntentRow, display: string, call: CallTool, ctx: ToolContext): Promise<ArtifactDiff> {
  const before = intent.op === "create" ? null : intent.backup_path;
  const after = intent.op === "delete" ? null : (intent.final_path ?? intent.path);
  const ext = extname(intent.path).toLowerCase();
  const base = { path: display, op: intent.op, truncated: false };
  if (before && !(await exists(before))) {
    return { ...base, kind: "binary", changes: [], note: "バックアップの保持期間が過ぎたため、差分を表示できません" };
  }
  if (ext === ".docx") return { ...base, kind: "docx", ...(await docxDiff(before, after, call, ctx)) };
  if (ext === ".xlsx" || ext === ".xlsm") return { ...base, kind: "xlsx", ...(await xlsxDiff(before, after, call, ctx)) };
  if (TEXT_EXT.test(intent.path)) return { ...base, kind: "text", ...(await textDiff(before, after)) };
  const [a, b] = await Promise.all([before ? stat(before) : null, after ? stat(after).catch(() => null) : null]);
  return { ...base, kind: "binary", changes: [], note: `この形式は内容の差分を表示できません（${a?.size ?? 0} → ${b?.size ?? 0} バイト）` };
}

async function exists(p: string): Promise<boolean> {
  return stat(p)
    .then(() => true)
    .catch(() => false);
}

// ---------------------------------------------------------------- Word
async function docxDiff(before: string | null, after: string | null, call: CallTool, ctx: ToolContext) {
  if (!before || !after) {
    const target = (after ?? before)!;
    const r = await call("docx.read", { path: target, max_blocks: 200 }, ctx);
    if (!r.ok) return { changes: [], note: r.error?.message };
    const kind = after ? "insert" : "delete";
    const changes = (r.untrusted ?? []).slice(0, MAX_CHANGES).map((u) => ({
      kind,
      location: u.ref.split("#").pop() ?? "",
      before: after ? null : u.text,
      after: after ? u.text : null,
    })) as DiffChange[];
    return { changes, truncated: Boolean(r.continuation) };
  }
  const r = await call("docx.diff", { a: before, b: after, granularity: "paragraph" }, ctx);
  if (!r.ok) return { changes: [], note: r.error?.message };
  const texts = new Map((r.untrusted ?? []).map((u) => [u.ref, u.text]));
  const items = ((r.data as { changes?: { kind: string; a_id?: string; b_id?: string; text_ref?: string }[] }).changes ?? []).slice(0, MAX_CHANGES);
  const changes = items.map((c) => {
    const text = texts.get(c.text_ref ?? "") ?? "";
    const lines = text.split("\n");
    const minus = lines.filter((l) => l.startsWith("- ")).map((l) => l.slice(2)).join("\n");
    const plus = lines.filter((l) => l.startsWith("+ ")).map((l) => l.slice(2)).join("\n");
    return {
      kind: (c.kind === "insert" || c.kind === "delete" ? c.kind : "modify") as DiffChange["kind"],
      location: c.b_id ?? c.a_id ?? "",
      before: c.kind === "insert" ? null : minus,
      after: c.kind === "delete" ? null : plus,
    };
  });
  return { changes, truncated: Boolean(r.continuation) };
}

// ---------------------------------------------------------------- Excel
type Grid = Map<string, string>;

function colToNum(col: string): number {
  return [...col.toUpperCase()].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
}
function numToCol(n: number): string {
  let s = "";
  for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}
function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object" && v !== null && "date" in v) return String((v as { date: string }).date);
  return String(v);
}

async function readBook(path: string, call: CallTool, ctx: ToolContext): Promise<{ grid: Grid; truncated: boolean } | { error: string }> {
  const info = await call("xlsx.info", { path }, ctx);
  if (!info.ok) return { error: info.error?.message ?? "読めません" };
  const grid: Grid = new Map();
  let truncated = false;
  let budget = MAX_CELLS;
  for (const sh of (info.data as { sheets?: { name: string; used_range?: string }[] }).sheets ?? []) {
    if (!sh.used_range || budget <= 0) continue;
    const used = sh.used_range.includes(":") ? sh.used_range : `${sh.used_range}:${sh.used_range}`;
    const m = /^([A-Z]+)(\d+):/i.exec(used);
    if (!m) continue;
    const quoted = /^[A-Za-z0-9_]+$/.test(sh.name) ? sh.name : `'${sh.name.replace(/'/g, "''")}'`;
    const r = await call("xlsx.read_range", { path, range: `${quoted}!${used}`, mode: "values", max_cells: budget }, ctx);
    if (!r.ok) continue;
    if (r.continuation) truncated = true;
    const rows = ((r.data as { rows?: unknown[][] }).rows ?? []) as unknown[][];
    const c0 = colToNum(m[1]!);
    const r0 = Number(m[2]);
    rows.forEach((row, i) =>
      row.forEach((v, j) => {
        const t = cellText(v);
        if (t !== "") grid.set(`${sh.name}!${numToCol(c0 + j)}${r0 + i}`, t);
      }),
    );
    budget -= rows.reduce((n, row) => n + row.length, 0);
  }
  return { grid, truncated: truncated || budget <= 0 };
}

async function xlsxDiff(before: string | null, after: string | null, call: CallTool, ctx: ToolContext) {
  const empty = { grid: new Map() as Grid, truncated: false };
  const a = before ? await readBook(before, call, ctx) : empty;
  const b = after ? await readBook(after, call, ctx) : empty;
  if ("error" in a || "error" in b) return { changes: [], note: ("error" in a ? a.error : "") || ("error" in b ? b.error : "") };
  const keys = [...new Set([...a.grid.keys(), ...b.grid.keys()])].sort(cellOrder);
  const changes: DiffChange[] = [];
  for (const k of keys) {
    const x = a.grid.get(k) ?? null;
    const y = b.grid.get(k) ?? null;
    if (x === y) continue;
    changes.push({ kind: x === null ? "add" : y === null ? "remove" : "modify", location: k, before: x, after: y });
    if (changes.length >= MAX_CHANGES) break;
  }
  return { changes, truncated: a.truncated || b.truncated || changes.length >= MAX_CHANGES };
}

function cellOrder(p: string, q: string): number {
  const re = /^(.*)!([A-Z]+)(\d+)$/;
  const [, sa = "", ca = "", ra = "0"] = re.exec(p) ?? [];
  const [, sb = "", cb = "", rb = "0"] = re.exec(q) ?? [];
  return sa.localeCompare(sb) || Number(ra) - Number(rb) || colToNum(ca) - colToNum(cb);
}

// ---------------------------------------------------------------- テキスト
async function readText(p: string | null): Promise<string[]> {
  if (!p) return [];
  const buf = await readFile(p);
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buf).replace(/^\uFEFF/, "");
  } catch {
    text = new TextDecoder("shift_jis").decode(buf);
  }
  return text.split(/\r?\n/);
}

/** 行単位の差分（LCS）。大きいファイルは先頭だけ比べる。 */
export function lineDiff(a: string[], b: string[], limit = 2000): { changes: DiffChange[]; truncated: boolean } {
  const truncated = a.length > limit || b.length > limit;
  const x = a.slice(0, limit);
  const y = b.slice(0, limit);
  const n = x.length;
  const m = y.length;
  const dp: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i]![j] = x[i] === y[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
  const changes: DiffChange[] = [];
  let i = 0;
  let j = 0;
  while ((i < n || j < m) && changes.length < MAX_CHANGES) {
    if (i < n && j < m && x[i] === y[j]) {
      i++;
      j++;
    } else if (i < n && (j >= m || dp[i + 1]![j]! >= dp[i]![j + 1]!)) {
      changes.push({ kind: "delete", location: `${i + 1}行目`, before: x[i]!, after: null });
      i++;
    } else {
      changes.push({ kind: "insert", location: `${j + 1}行目`, before: null, after: y[j]! });
      j++;
    }
  }
  return { changes, truncated: truncated || changes.length >= MAX_CHANGES };
}

async function textDiff(before: string | null, after: string | null) {
  return lineDiff(await readText(before), await readText(after));
}
