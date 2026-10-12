// 画面の状態。Runtime のイベントを会話ごとに畳み込む（Renderer は状態を持たず、開き直したら events.since で取り直す）。
import type { RuntimeEvent } from "@lla/protocol";

export interface StepView {
  stepId: string;
  kind: "tool" | "plan" | "final" | "ask" | "system";
  tool?: string;
  argsPreview?: string;
  resultPreview?: string;
  status: "running" | "ok" | "error" | "deferred";
  startedAt: string;
}
export interface ApprovalView {
  approval_id: string;
  tool: string;
  reason: string;
  paths: { path: string; real_path: string; inside_workspace: boolean }[];
  tainted: boolean;
  decided?: "approve" | "reject";
}
export interface QuestionView {
  question_id: string;
  question: string;
  choices?: string[];
  secret: boolean;
  answered?: boolean;
}
export interface ArtifactView {
  run_id: string;
  path: string;
  op: string;
  renamed_from?: string;
}
export interface ConversationView {
  lastSeq: number;
  runId: string | null;
  state: string | null;
  stateDetail: Record<string, unknown>;
  steps: StepView[];
  plan: { id: string; title: string; status: string }[];
  approvals: ApprovalView[];
  questions: QuestionView[];
  artifacts: ArtifactView[];
  queue: { position: number; eta_seconds: number } | null;
  retry: { attempt: number; next_in_ms: number; cause: string } | null;
  net: { kind: string; query?: string; url?: string; blocked?: string }[];
  batch: { done: number; total: number; failed: number } | null;
  finalSummary: string | null;
  /** 推論中の出力（APP-01: 逐次表示）。手順が確定したら消える */
  live: { stepId: string; raw: string } | null;
}

export const emptyConversation = (): ConversationView => ({
  lastSeq: 0,
  runId: null,
  state: null,
  stateDetail: {},
  steps: [],
  plan: [],
  approvals: [],
  questions: [],
  artifacts: [],
  queue: null,
  retry: null,
  net: [],
  batch: null,
  finalSummary: null,
  live: null,
});

/** 1件のイベントを畳み込む（同じ seq は二重に適用しない）。 */
export function applyEvent(c: ConversationView, e: RuntimeEvent): ConversationView {
  if (e.seq <= c.lastSeq) return c;
  const p = e.payload as Record<string, unknown>;
  const next: ConversationView = { ...c, lastSeq: e.seq };
  switch (e.type) {
    case "run.state": {
      const runId = String(p.run_id);
      if (runId !== c.runId) {
        next.runId = runId;
        next.finalSummary = null;
        next.plan = [];
        next.batch = null;
      }
      next.state = String(p.state);
      next.stateDetail = (p.detail as Record<string, unknown>) ?? {};
      if (next.state === "done") next.finalSummary = String(next.stateDetail.summary ?? "");
      if (!["reconnecting"].includes(next.state)) next.retry = null;
      if (["tool_running", "done", "stopped"].includes(next.state)) next.queue = null;
      if (["done", "stopped", "tool_running"].includes(next.state)) next.live = null;
      break;
    }
    case "token": {
      const stepId = String(p.step_id);
      const base = c.live && c.live.stepId === stepId && !p.reset ? c.live.raw : "";
      next.live = { stepId, raw: base + String(p.delta ?? "") };
      break;
    }
    case "step": {
      const s: StepView = {
        stepId: String(p.step_id),
        kind: p.kind as StepView["kind"],
        status: p.status as StepView["status"],
        startedAt: String(p.started_at),
        ...(p.tool ? { tool: String(p.tool) } : {}),
        ...(p.args_preview ? { argsPreview: String(p.args_preview) } : {}),
        ...(p.result_preview ? { resultPreview: String(p.result_preview) } : {}),
      };
      const i = c.steps.findIndex((x) => x.stepId === s.stepId && x.kind === s.kind);
      next.steps = i >= 0 ? c.steps.map((x, j) => (j === i ? { ...x, ...s, tool: s.tool ?? x.tool, argsPreview: s.argsPreview ?? x.argsPreview } : x)) : [...c.steps, s];
      // 推論が終わって手順が決まった（出力は手順カードや完了報告で表示する）
      if (c.live?.stepId === s.stepId) next.live = null;
      break;
    }
    case "plan":
      next.plan = p.plan as ConversationView["plan"];
      break;
    case "approval.request":
      next.approvals = [...c.approvals, p as unknown as ApprovalView];
      break;
    case "question":
      next.questions = [...c.questions, { question_id: String(p.question_id), question: String(p.question), choices: p.choices as string[] | undefined, secret: Boolean(p.secret_hint) }];
      break;
    case "artifact": {
      const a = p.artifact as { path: string; op: string; renamed_from?: string };
      next.artifacts = [...c.artifacts.filter((x) => x.path !== a.path), { run_id: String(p.run_id), ...a }];
      break;
    }
    case "queue":
      next.queue = { position: Number(p.position), eta_seconds: Number(p.eta_seconds) };
      break;
    case "retry":
      next.retry = { attempt: Number(p.attempt), next_in_ms: Number(p.next_in_ms), cause: String(p.cause) };
      break;
    case "net.activity":
      next.net = [...c.net, p as ConversationView["net"][number]];
      break;
    case "batch_progress":
      next.batch = { done: Number(p.done), total: Number(p.total), failed: Number(p.failed) };
      break;
  }
  return next;
}

const READABLE_KEYS = /"(summary|thought|question|note)"\s*:\s*"/g;

/**
 * 推論中の出力（Action の JSON の途中）から、人が読める部分（summary / thought / question / note の値）を取り出す。
 * 書きかけの文字列も途中まで返す。読める部分がまだ無ければ空文字。
 */
export function readableFromPartial(raw: string): string {
  let start = -1;
  for (const m of raw.matchAll(READABLE_KEYS)) start = (m.index ?? 0) + m[0].length;
  if (start < 0) return "";
  let out = "";
  for (let i = start; i < raw.length; i++) {
    const ch = raw[i]!;
    if (ch === '"') break;
    if (ch !== "\\") {
      out += ch;
      continue;
    }
    const nx = raw[i + 1];
    if (nx === undefined) break; // エスケープの途中で切れている
    i++;
    if (nx === "n") out += "\n";
    else if (nx === "t") out += "\t";
    else if (nx === "u") {
      const hex = raw.slice(i + 1, i + 5);
      if (!/^[0-9a-fA-F]{4}$/.test(hex)) break;
      out += String.fromCharCode(parseInt(hex, 16));
      i += 4;
    } else out += nx;
  }
  return out;
}

export const STATE_LABEL: Record<string, string> = {
  planning: "計画を立てています…",
  inferring: "考えています…",
  tool_running: "作業しています…",
  waiting_approval: "承認待ちがあります",
  waiting_question: "質問への回答待ちです",
  compacting: "これまでの経緯を整理しています…",
  replanning: "方針を立て直しています…",
  stalled: "作業が進んでいません。低優先度で続けています",
  paused: "一時停止中",
  reconnecting: "サーバーに再接続しています…",
  done: "完了しました",
  stopped: "停止しました",
};

export const TOOL_LABEL: Record<string, string> = {
  "fs.list": "フォルダの一覧",
  "fs.stat": "ファイル情報",
  "fs.read_text": "テキストを読む",
  "fs.search": "ファイル検索",
  "fs.mkdir": "フォルダを作る",
  "fs.copy": "コピー",
  "fs.move": "移動",
  "fs.delete": "削除",
  "docx.read": "Word を読む",
  "docx.outline": "Word の見出し",
  "docx.create": "Word を作る",
  "docx.edit": "Word を編集",
  "docx.diff": "Word の差分",
  "xlsx.info": "Excel の構成",
  "xlsx.read_range": "Excel を読む",
  "xlsx.write_cells": "Excel に書く",
  "xlsx.write_table": "Excel に表を書く",
  "xlsx.create": "Excel を作る",
  "xlsx.recalc": "Excel を再計算",
  "csv.read": "CSV を読む",
  "csv.write": "CSV を書く",
  "data.transform": "集計・突合",
  "pdf.info": "PDF の構成",
  "pdf.read_pages": "PDF を読む",
  "pdf.extract_tables": "PDF の表を抽出",
  "pdf.ocr": "OCR",
  "invoice.extract": "請求書の読み取り",
  "batch.run": "一括処理",
  "memory.note": "メモ",
  "rag.search": "社内文書検索",
  "web.search": "Web 検索",
  "web.fetch": "Web ページを読む",
};

export const REASON_LABEL: Record<string, string> = {
  outside_workspace: "作業フォルダの外の操作",
  delete: "削除",
  move: "移動",
  plugin_write: "プラグインによる書き込み",
  tainted_outside: "Web の情報を読んだ後の、作業フォルダの外の操作",
};
