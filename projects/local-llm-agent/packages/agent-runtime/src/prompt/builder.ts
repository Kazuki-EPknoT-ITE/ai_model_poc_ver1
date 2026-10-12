// プロンプトの組み立て（local_llm_prompts.md §1）。
import type { ToolCatalogEntry, ToolResult } from "@lla/protocol";
import type { ChatMessage } from "../gateway/client.js";
import type { MessageRow } from "../store/store.js";
import { estimateTokens } from "../util.js";
import {
  NEXT,
  SYSTEM_BASE,
  SYSTEM_NET,
  SYSTEM_TAINTED,
  memoryBlock,
  profileBlock,
  toolsBlock,
  wrapUntrusted,
  type MemoryView,
} from "./templates.js";

export interface PromptInput {
  orgName: string;
  instructions: string;
  netEnabled: boolean;
  tainted: boolean;
  memory: MemoryView;
  tools: ToolCatalogEntry[];
  history: MessageRow[]; // 圧縮されていないメッセージ（古い順）。要約は role=system_note で "[summary]" から始まる
  next?: string; // 既定は NEXT
}

/** ツール結果をモデルに渡す形にする（prompts §4）。外部由来のテキストは必ず <untrusted> で包む。 */
export function renderToolResult(stepSeq: number, tool: string, r: ToolResult, untrustedOutput = false): string {
  const head = `[tool_result step=${stepSeq} tool=${tool} ok=${r.ok}]`;
  const body: Record<string, unknown> = r.ok ? {} : { error: { code: r.error?.code, message: r.error?.message } };
  if (r.continuation) body.continuation = r.continuation;
  if (r.warnings?.length) body.warnings = r.warnings;
  if (r.artifacts?.length) body.artifacts = r.artifacts.map((a) => ({ path: a.path, op: a.op }));
  const parts = [head];
  if (r.ok && untrustedOutput && r.data && Object.keys(r.data).length) {
    // Excel の値や PDF の表など、data にも文書の中身が入るツールは data ごと外部由来として包む
    if (Object.keys(body).length) parts.push(JSON.stringify(body));
    parts.push(wrapUntrusted("file", `${tool}:data`, JSON.stringify(r.data)));
  } else {
    if (r.ok) Object.assign(body, r.data ?? {});
    parts.push(JSON.stringify(body));
  }
  for (const u of r.untrusted ?? []) parts.push(wrapUntrusted(u.source, u.ref, u.text));
  parts.push("[/tool_result]");
  return parts.join("\n");
}

export function toolLines(tools: ToolCatalogEntry[]): { name: string; description: string; args: string[] }[] {
  return tools.map((t) => {
    const s = t.args_schema as { properties?: Record<string, unknown>; anyOf?: { properties?: Record<string, unknown> }[] };
    const props = s.properties ?? s.anyOf?.[1]?.properties ?? {};
    return { name: t.name, description: t.description.slice(0, 60), args: Object.keys(props).slice(0, 6) };
  });
}

export function buildMessages(p: PromptInput): ChatMessage[] {
  const system =
    SYSTEM_BASE + (p.netEnabled ? SYSTEM_NET : "") + (p.tainted ? SYSTEM_TAINTED : "") + profileBlock(p.orgName, p.instructions);
  const summaries = p.history.filter((m) => m.role === "system_note" && m.content.startsWith("[summary]"));
  const live = p.history.filter((m) => !(m.role === "system_note" && m.content.startsWith("[summary]")));
  const head = [memoryBlock(p.memory), toolsBlock(toolLines(p.tools))];
  if (summaries.length) head.push(`# これまでの経緯（要約）\n${summaries.map((s) => s.content.replace(/^\[summary\]\s*/, "")).join("\n")}`);

  const raw: ChatMessage[] = [{ role: "system", content: system }, { role: "user", content: head.join("\n\n") }];
  for (const m of live) {
    // 依頼原文と追加の指示は MEMORY に入っているので、履歴からは省く
    if (m.role === "user") continue;
    raw.push({ role: m.role === "assistant" ? "assistant" : "user", content: m.content });
  }
  raw.push({ role: "user", content: p.next ?? NEXT });
  return mergeAlternating(raw);
}

/** Gemma のチャットテンプレートは user / assistant の交互を要求するため、同じ役割が続いたら結合する。 */
export function mergeAlternating(msgs: ChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of msgs) {
    const last = out[out.length - 1];
    if (last && last.role === m.role && m.role !== "system") last.content += `\n\n${m.content}`;
    else out.push({ ...m });
  }
  return out;
}

export function messagesTokens(msgs: ChatMessage[]): number {
  // 画像の部品は1枚 300 トークンとして数える（Gateway の見積もりと同じ）
  return msgs.reduce(
    (n, m) =>
      n + (typeof m.content === "string" ? estimateTokens(m.content) : m.content.reduce((k, p) => k + (p.type === "text" ? estimateTokens(p.text) : 300), 0)),
    0,
  );
}

/** 圧縮の対象（直近 keepSteps 手より古い、要約以外のメッセージ）。 */
export function compactionCandidates(history: MessageRow[], keepSteps = 6): MessageRow[] {
  const stepIds = [...new Set(history.filter((m) => m.step_id).map((m) => m.step_id!))];
  const keep = new Set(stepIds.slice(-keepSteps));
  return history.filter(
    (m) => m.role !== "user" && !(m.role === "system_note" && m.content.startsWith("[summary]")) && !(m.step_id && keep.has(m.step_id)) && m.step_id !== null,
  );
}

/** 要約の生成に失敗したときの機械的な要約（prompts §7）。 */
export function mechanicalSummary(msgs: MessageRow[]): string {
  return msgs
    .filter((m) => m.role === "tool")
    .map((m) => m.content.split("\n")[0]!.replace(/^\[tool_result /, "- ").replace(/\]$/, ""))
    .join("\n");
}
