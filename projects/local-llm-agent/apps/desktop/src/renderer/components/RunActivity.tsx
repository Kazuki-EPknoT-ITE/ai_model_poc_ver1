import { useId, useState } from "react";
import { PLAN_STATUS_LABEL, STATE_LABEL, TOOL_ACTIVITY, TOOL_LABEL, stepTarget, type ConversationView, type StepView } from "../state.js";
import { Icon, Spinner } from "./Icon.js";

const ACTIVE_WAIT = ["waiting_approval", "waiting_question", "paused", "stalled"];

/** 一覧に出す手順（計画の更新・完了報告は出さない。形式エラーは失敗したものだけ） */
export function listedSteps(steps: StepView[]): StepView[] {
  return steps.filter((s) => s.kind === "tool" || s.kind === "ask" || (s.kind === "system" && s.status === "error"));
}

function stepLabel(s: StepView): string {
  if (s.kind === "tool") return TOOL_LABEL[s.tool ?? ""] ?? s.tool ?? "ツール";
  if (s.kind === "ask") return "質問";
  return "出力の形式エラー（自動でやり直します）";
}

/** いま何をしているか（作業の経過の1行に出す言葉） */
function activityText(state: string, steps: StepView[], extra: Pick<ConversationView, "queue" | "retry">): string {
  if (extra.queue && extra.queue.position > 0) return `順番待ち ${extra.queue.position} 番目（約 ${extra.queue.eta_seconds} 秒）`;
  if (extra.retry) return `再接続中（${extra.retry.attempt} 回目）`;
  if (!ACTIVE_WAIT.includes(state)) {
    const run = [...steps].reverse().find((s) => s.status === "running" && s.kind === "tool");
    if (run) return TOOL_ACTIVITY[run.tool ?? ""] ?? `${TOOL_LABEL[run.tool ?? ""] ?? run.tool} を実行しています…`;
  }
  return STATE_LABEL[state] ?? state;
}

function StepStatus({ s }: { s: StepView }) {
  if (s.status === "ok")
    return (
      <span className="step-st ok">
        <Icon name="check" size={14} />
        <span className="sr-only">完了</span>
      </span>
    );
  if (s.status === "error")
    return (
      <span className="step-st error">
        <Icon name="alert" size={14} />
        失敗
      </span>
    );
  if (s.status === "deferred") return <span className="step-st deferred">保留中</span>;
  return (
    <span className="step-st running">
      <Spinner size={13} />
      <span className="sr-only">実行中</span>
    </span>
  );
}

/**
 * 1回の依頼の作業の経過を、1行の開閉ボタンにまとめる（手順のカードを並べない）。
 * 実行中はいまの作業と件数、終わったら「N件の手順を実行しました」。開くと計画と手順の一覧を出す。
 */
export function RunActivity({
  state,
  steps,
  plan,
  latest,
  queue,
  retry,
  batch,
}: {
  state: string;
  steps: StepView[];
  plan: ConversationView["plan"];
  latest: boolean;
  queue: ConversationView["queue"];
  retry: ConversationView["retry"];
  batch: ConversationView["batch"];
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const items = listedSteps(steps);
  const failed = items.filter((s) => s.status === "error").length;
  const ended = state === "done" || state === "stopped";
  const waiting = ACTIVE_WAIT.includes(state);
  const planDone = plan.filter((p) => p.status === "done" || p.status === "skipped").length;

  let headline: string;
  if (state === "done") headline = items.length ? `${items.length}件の手順を実行しました` : "完了しました";
  else if (state === "stopped") headline = items.length ? `停止しました（${items.length}件の手順を実行）` : "停止しました";
  else headline = activityText(state, steps, { queue: latest ? queue : null, retry: latest ? retry : null });

  const meta: string[] = [];
  if (!ended && items.length) meta.push(`${items.length}件の手順`);
  if (latest && plan.length && !ended) meta.push(`計画 ${planDone}/${plan.length}`);
  if (latest && batch) meta.push(`一括処理 ${batch.done} / ${batch.total}${batch.failed > 0 ? `（失敗 ${batch.failed}）` : ""}`);
  if (failed) meta.push(`失敗 ${failed} 件`);

  const expandable = items.length > 0 || (latest && plan.length > 0);
  const tone = state === "stalled" || waiting ? "wait" : state === "stopped" ? "stopped" : ended ? "done" : "running";

  return (
    <div className={`activity ${tone}`} {...(latest ? { "data-testid": "run-state", "data-state": state } : { "data-testid": "run-activity" })}>
      <button
        type="button"
        className="activity-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        disabled={!expandable}
        onClick={() => setOpen((o) => !o)}
        data-testid="steps-toggle"
      >
        <span className="activity-icon">
          {tone === "running" ? <Spinner /> : tone === "done" ? <Icon name="check" size={15} /> : tone === "wait" ? <Icon name={state === "paused" ? "pause" : "clock"} size={15} /> : <Icon name="stop" size={14} />}
        </span>
        <span className="activity-text">{headline}</span>
        {meta.length > 0 && <span className="activity-meta">{meta.join(" ・ ")}</span>}
        {expandable && <Icon name={open ? "chevronDown" : "chevronRight"} size={15} className="activity-chevron" />}
      </button>
      {open && (
        <section id={panelId} className="activity-panel" aria-label="手順">
          {latest && plan.length > 0 && (
            <ol className="plan" aria-label="計画">
              {plan.map((p) => (
                <li key={p.id} className={`plan-item ${p.status}`}>
                  <span className="plan-mark" aria-hidden="true">
                    {p.status === "done" ? <Icon name="check" size={13} /> : p.status === "doing" ? <Spinner size={12} /> : null}
                  </span>
                  <span className="plan-title">{p.title}</span>
                  <span className="plan-status">{PLAN_STATUS_LABEL[p.status] ?? p.status}</span>
                </li>
              ))}
            </ol>
          )}
          {items.length > 0 && (
            <ol className="steps">
              {items.map((s) => {
                const target = stepTarget(s.argsPreview);
                return (
                  <li key={`${s.stepId}:${s.kind}`}>
                    <details className={`step-row is-${s.status}`} data-testid="step-card">
                      <summary>
                        <StepStatus s={s} />
                        <span className="step-label">{stepLabel(s)}</span>
                        {target && <span className="step-target">{target}</span>}
                        {s.status === "error" && s.resultPreview && <span className="step-err">{s.resultPreview}</span>}
                      </summary>
                      {s.argsPreview && <pre className="pre">{s.argsPreview}</pre>}
                      {s.resultPreview && <pre className="pre result">{s.resultPreview}</pre>}
                    </details>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}
