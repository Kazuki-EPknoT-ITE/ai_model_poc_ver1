// 画面の状態。Runtime のイベントを会話ごとに畳み込む（Renderer は状態を持たず、開き直したら events.since で取り直す）。
import type { RuntimeEvent } from "@lla/protocol";

export interface StepView {
  stepId: string;
  /** どの作業（run）の手順か。手順を依頼ごとにまとめて表示するために使う */
  runId?: string;
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
export interface CitationView {
  kind: "file" | "web" | "rag";
  ref: string;
  location?: string;
  /** この会話で実際に読んだ記録と照らせたか（false は「確認できない出典」） */
  verified: boolean;
}
/** 1回の依頼（run）ごとの結果。会話に複数の依頼があっても、それぞれの答えを残す */
export interface RunView {
  runId: string;
  state: string;
  finalSummary: string | null;
  citations: CitationView[];
  /** 失敗したまま終わった書き込み（done の detail.unresolved）。答えが「できました」でも必ず見せる */
  unresolved: UnresolvedView[];
  /** 完了報告の成果物・できなかったこと・要確認（done の detail。空なら来ない） */
  outputs: { path: string; description?: string }[];
  failures: { item: string; reason: string }[];
  needsReview: { path: string; location?: string; reason: string }[];
}
export interface UnresolvedView {
  tool: string;
  error_code: string;
  message: string;
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
  net: { kind: string; query?: string; url?: string; blocked?: string; runId?: string }[];
  batch: { done: number; total: number; failed: number } | null;
  finalSummary: string | null;
  /** 完了報告の出典（AGT-07, NET-05） */
  citations: CitationView[];
  /** 推論中の出力（APP-01: 逐次表示）。手順が確定したら消える */
  live: { stepId: string; raw: string } | null;
  /** 依頼ごとの結果（現れた順） */
  runs: RunView[];
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
  citations: [],
  live: null,
  runs: [],
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
        next.citations = [];
        next.plan = [];
        next.batch = null;
      }
      next.state = String(p.state);
      next.stateDetail = (p.detail as Record<string, unknown>) ?? {};
      if (next.state === "done") {
        next.finalSummary = String(next.stateDetail.summary ?? "");
        next.citations = (next.stateDetail.citations as CitationView[] | undefined) ?? [];
      }
      if (!["reconnecting"].includes(next.state)) next.retry = null;
      if (["tool_running", "done", "stopped"].includes(next.state)) next.queue = null;
      const done = next.state === "done";
      const ri = c.runs.findIndex((r) => r.runId === runId);
      const prev: RunView = ri >= 0 ? c.runs[ri]! : { runId, state: next.state, finalSummary: null, citations: [], unresolved: [], outputs: [], failures: [], needsReview: [] };
      const d = next.stateDetail;
      const list = <T,>(x: unknown): T[] => (Array.isArray(x) ? (x as T[]) : []);
      const report = done
        ? {
            finalSummary: next.finalSummary,
            citations: next.citations,
            unresolved: list<UnresolvedView>(d.unresolved),
            outputs: list<RunView["outputs"][number]>(d.outputs),
            failures: list<RunView["failures"][number]>(d.failures),
            needsReview: list<RunView["needsReview"][number]>(d.needs_review),
          }
        : {};
      const rv: RunView = { ...prev, state: next.state, ...report };
      next.runs = ri >= 0 ? c.runs.map((r, j) => (j === ri ? rv : r)) : [...c.runs, rv];
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
        ...(p.run_id ? { runId: String(p.run_id) } : {}),
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
      next.net = [...c.net, { ...(p as ConversationView["net"][number]), ...(c.runId ? { runId: c.runId } : {}) }];
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

/** 実行中の手順を、いま何をしているかの言葉で表す（作業の経過の1行に出す） */
export const TOOL_ACTIVITY: Record<string, string> = {
  "fs.list": "フォルダの中身を確認しています…",
  "fs.stat": "ファイルの情報を確認しています…",
  "fs.read_text": "テキストを読んでいます…",
  "fs.search": "ファイルを探しています…",
  "fs.mkdir": "フォルダを作っています…",
  "fs.copy": "コピーしています…",
  "fs.move": "移動しています…",
  "fs.delete": "削除しています…",
  "docx.read": "Word を読んでいます…",
  "docx.outline": "Word の見出しを確認しています…",
  "docx.create": "Word の文書を作っています…",
  "docx.edit": "Word を書き換えています…",
  "docx.diff": "Word の差分を確認しています…",
  "xlsx.info": "Excel の構成を確認しています…",
  "xlsx.read_range": "Excel を読んでいます…",
  "xlsx.write_cells": "Excel を書き換えています…",
  "xlsx.write_table": "Excel に表を書いています…",
  "xlsx.create": "Excel のファイルを作っています…",
  "xlsx.recalc": "Excel を再計算しています…",
  "csv.read": "CSV を読んでいます…",
  "csv.write": "CSV を書いています…",
  "data.transform": "集計・突合しています…",
  "pdf.info": "PDF の構成を確認しています…",
  "pdf.read_pages": "PDF を読んでいます…",
  "pdf.extract_tables": "PDF の表を取り出しています…",
  "pdf.ocr": "文字を読み取っています（OCR）…",
  "invoice.extract": "請求書を読み取っています…",
  "batch.run": "まとめて処理しています…",
  "memory.note": "メモしています…",
  "rag.search": "社内文書を検索しています…",
  "web.search": "Web を検索しています…",
  "web.fetch": "Web ページを読んでいます…",
};

/** 計画の各項目の状態（色だけで伝えないための文字） */
export const PLAN_STATUS_LABEL: Record<string, string> = { todo: "未着手", doing: "作業中", done: "完了", skipped: "省略" };

const TARGET_KEYS = /"(path|src|query|url|file|dir|pattern|sheet|key)"\s*:\s*"((?:[^"\\]|\\.)*)/;

/** 手順の引数のプレビュー（JSON の先頭）から、対象（パス・検索語・URL など）を短く取り出す */
export function stepTarget(argsPreview: string | undefined): string {
  if (!argsPreview) return "";
  const m = TARGET_KEYS.exec(argsPreview);
  if (!m) return "";
  return m[2]!.replace(/\\(.)/g, "$1");
}

/** 画面に出す1回分のやり取り（依頼と、その作業） */
export interface Turn {
  key: string;
  messages: { id: string; content: string }[];
  runId: string | null;
}

/**
 * 依頼（user のメッセージ）と作業（run）を、表示の単位にまとめる。
 * 保存済みのメッセージは run_id で結び付ける。送ったばかりで run_id の無いメッセージは、
 * まだ依頼の無い作業か、実行中の作業（追加の指示）に結び付け、どちらも無ければ作業待ちの1件にする。
 */
export function groupTurns(messages: { id: string; content: string; run_id: string | null }[], view: ConversationView): Turn[] {
  const turns: Turn[] = view.runs.map((r) => ({ key: r.runId, messages: [], runId: r.runId }));
  const byRun = new Map(turns.map((t) => [t.runId!, t]));
  const loose: Turn[] = [];
  for (const m of messages) {
    const msg = { id: m.id, content: m.content };
    const t = m.run_id ? byRun.get(m.run_id) : undefined;
    if (t) {
      t.messages.push(msg);
      continue;
    }
    const empty = turns.find((x) => x.messages.length === 0);
    if (empty) {
      empty.messages.push(msg);
      continue;
    }
    const last = turns[turns.length - 1];
    const active = last && !["done", "stopped"].includes(view.runs[view.runs.length - 1]!.state);
    if (active && loose.length === 0) last.messages.push(msg);
    else loose.push({ key: m.id, messages: [msg], runId: null });
  }
  return [...turns, ...loose];
}

export const REASON_LABEL: Record<string, string> = {
  outside_workspace: "作業フォルダの外の操作",
  delete: "削除",
  move: "移動",
  plugin_write: "プラグインによる書き込み",
  tainted_outside: "Web の情報を読んだ後の、作業フォルダの外の操作",
};
