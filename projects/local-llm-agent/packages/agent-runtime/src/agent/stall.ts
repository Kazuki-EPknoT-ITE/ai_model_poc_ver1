// 停滞の検知（設計書 §5.6、RUN-05）。
import type { StepRow } from "../store/store.js";

export interface StallThresholds {
  repeatWindow: number; // 直近何手を見るか
  repeatCount: number; // 同じ (tool, args) が何回あれば停滞か
  noProgressWindow: number; // 何手のあいだ進捗が無ければ停滞か
  errorStreak: number; // 同じツールの同じエラーが何回続けば停滞か
}
export const DEFAULT_STALL: StallThresholds = { repeatWindow: 12, repeatCount: 3, noProgressWindow: 8, errorStreak: 5 };

export interface StallVerdict {
  stalled: boolean;
  signal?: "repeat" | "no_progress" | "error_streak";
  summary?: string;
}

interface StepFacts {
  tool: string | null;
  argsDigest: string | null;
  errorCode: string | null;
  progressed: boolean;
}

/** 手順の記録から、進捗があったかを判定する材料を取り出す。 */
export function factsOf(step: StepRow, seenResults: Set<string>): StepFacts {
  let progressed = false;
  const action = safeParse(step.action) as { type?: string } | null;
  if (action?.type === "plan_update" || action?.type === "ask_user" || action?.type === "final") progressed = true;
  const r = safeParse(step.result) as { ok?: boolean; artifacts?: unknown[] } | null;
  if (r) {
    if (r.artifacts && r.artifacts.length > 0) progressed = true;
    if (r.ok && step.result_digest && !seenResults.has(step.result_digest)) progressed = true; // 新しい情報を読んだ
  }
  if (step.result_digest) seenResults.add(step.result_digest);
  return { tool: step.tool, argsDigest: step.args_digest, errorCode: step.error_code, progressed };
}

/**
 * steps は古い順。since 以降（前回の立て直し以降）の手順だけを見る。
 */
export function detectStall(steps: StepRow[], t: StallThresholds = DEFAULT_STALL): StallVerdict {
  const seen = new Set<string>();
  const facts = steps.map((s) => factsOf(s, seen));

  const recent = facts.slice(-t.repeatWindow).filter((f) => f.tool);
  const counts = new Map<string, number>();
  for (const f of recent) {
    const k = `${f.tool}|${f.argsDigest}`;
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  for (const [k, n] of counts) {
    if (n >= t.repeatCount) return { stalled: true, signal: "repeat", summary: `${k.split("|")[0]} を同じ引数で${n}回呼んでいます` };
  }

  const tail = facts.slice(-t.errorStreak);
  if (
    tail.length === t.errorStreak &&
    tail.every((f) => f.errorCode && f.tool === tail[0]!.tool && f.errorCode === tail[0]!.errorCode)
  ) {
    return { stalled: true, signal: "error_streak", summary: `${tail[0]!.tool} が ${tail[0]!.errorCode} で${t.errorStreak}回続けて失敗しています` };
  }

  const window = facts.slice(-t.noProgressWindow);
  if (window.length === t.noProgressWindow && window.every((f) => !f.progressed)) {
    return { stalled: true, signal: "no_progress", summary: `直近${t.noProgressWindow}手で進捗がありません` };
  }
  return { stalled: false };
}

function safeParse(s: string | null): unknown {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return null; // 形式不正で差し戻した出力など
  }
}
