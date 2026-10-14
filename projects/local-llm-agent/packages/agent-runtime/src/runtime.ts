// エージェント実行部（設計書 §5）。Electron に依存しない。Main とは RuntimeCommand / RuntimeEvent でやり取りする。
import { existsSync } from "node:fs";
import { extname } from "node:path";
import { validateDef, type ApprovalRequest, type RuntimeCommand, type RuntimeEvent, type ToolCatalogEntry, type ToolResult } from "@lla/protocol";
import { BatchRunner, substituteResultOf } from "./agent/batch.js";
import { computeDiff, findIntent, type ArtifactDiff } from "./agent/diff.js";
import { buildActionSchema } from "./agent/grammar.js";
import { WriteCoordinator } from "./agent/intents.js";
import { retryForever } from "./agent/retry.js";
import { DEFAULT_STALL, detectStall, type StallThresholds } from "./agent/stall.js";
import {
  CancelledError,
  GatewayError,
  JobExpiredError,
  TransientError,
  type GatewayClient,
  type GatewayConfig,
} from "./gateway/client.js";
import { resolveRealPath } from "./policy/paths.js";
import { decide, rememberRuleFor, verifyUnchanged, type ApprovalReason, type NetLevel, type PolicyContext, type ResolvedPath } from "./policy/policy.js";
import { buildMessages, compactionCandidates, mechanicalSummary, messagesTokens, renderToolResult } from "./prompt/builder.js";
import { compactPrompt, replanBlock } from "./prompt/templates.js";
import type { MessageRow, RunRow, RunStore, StepRow } from "./store/store.js";
import type { ToolRouter } from "./tools/router.js";
import { errResult, type ToolContext } from "./tools/types.js";
import { nowIso, sha256, stableStringify, ulid } from "./util.js";
import { testNow } from "./testhooks.js";

export interface RuntimeOptions {
  store: RunStore;
  gateway: GatewayClient;
  router: ToolRouter;
  appDataDir: string;
  emit: (ev: RuntimeEvent) => void;
  lockWaitMs?: number;
  stall?: StallThresholds;
  longRunMinutes?: number;
  retryBaseMs?: number;
  compactRatio?: number;
  maxReplans?: number;
}

type Citation = { kind: "file" | "web" | "rag"; ref: string; location?: string };

type Action =
  | { type: "tool_call"; thought?: string; tool: string; args: Record<string, unknown> }
  | { type: "plan_update"; plan: { id: string; title: string; status: string }[]; note?: string }
  | { type: "ask_user"; question: string; choices?: string[] }
  | { type: "final"; summary: string; outputs: unknown[]; failures: unknown[]; needs_review: unknown[] };

interface RunHandle {
  runId: string;
  conversationId: string;
  abort: AbortController;
  wakers: (() => void)[];
  replanSince: number; // この手順番号以降で停滞を判定する
  nextPrompt: string | undefined;
  done: Promise<void>;
}

const MODEL_FOR: Record<string, string> = { auto: "local-12b", fast: "local-12b", accurate: "local-26b" };
/** 同じ引数なら必ず同じ結果になるエラー（繰り返しても通らない）。ファイルが無い・ロック中・時間切れは状況で変わるので含めない */
const DETERMINISTIC_ERRORS = new Set(["INVALID_ARGS", "UNKNOWN_TOOL", "BLOCK_NOT_FOUND", "FORBIDDEN_PATH", "UNSUPPORTED_FORMAT", "RESULT_TOO_LARGE", "NET_DISABLED", "DLP_BLOCKED", "DOMAIN_BLOCKED"]);
/** この回数だけ同じ失敗をした呼び出しは、次からは実行しない */
const REPEAT_FAILURE_LIMIT = 2;

export class AgentRuntime {
  private config: GatewayConfig | null = null;
  private readonly runs = new Map<string, RunHandle>();
  private readonly coordinator: WriteCoordinator;
  private readonly secrets = new Map<string, string>(); // password_ref → 値（メモリ上だけ。PDF-06）
  private readonly opts: Required<Omit<RuntimeOptions, "lockWaitMs">> & { lockWaitMs?: number };
  private stopped = false;

  constructor(options: RuntimeOptions) {
    this.opts = {
      stall: DEFAULT_STALL,
      longRunMinutes: 120,
      retryBaseMs: 1000,
      compactRatio: 0.75,
      maxReplans: 2,
      ...options,
    };
    this.coordinator = new WriteCoordinator(options.store, {
      appDataDir: options.appDataDir,
      ...(options.lockWaitMs !== undefined ? { lockWaitMs: options.lockWaitMs } : {}),
    });
  }

  private get store(): RunStore {
    return this.opts.store;
  }

  // ------------------------------------------------------------------ 起動・終了
  /** 途中で止まった書き込みを解決し、未完了の run を再開する（RUN-02）。 */
  async start(): Promise<{ warnings: string[] }> {
    // 予備機への切替・切り戻しを画面に知らせる（OPS-03）。進行中の推論は、新しい接続先で問い合わせ直す
    this.opts.gateway.onSwitch((url, index) =>
      this.emitGlobal("server.status", { state: index === 0 ? "primary" : "failover", endpoint: "gateway", url }),
    );
    const warnings = [...(await this.opts.router.init())];
    await this.refreshConfig();
    const resumed = await this.coordinator.resumeOpen();
    warnings.push(...resumed.warnings);
    for (const run of this.store.activeRuns()) this.spawn(run);
    return { warnings };
  }

  async shutdown(): Promise<void> {
    this.stopped = true;
    for (const h of this.runs.values()) {
      h.abort.abort(new CancelledError("終了します"));
      h.wakers.splice(0).forEach((w) => w());
    }
    await Promise.allSettled([...this.runs.values()].map((h) => h.done));
    await this.opts.router.close();
  }

  private async refreshConfig(): Promise<GatewayConfig> {
    this.config = await retryForever(() => this.opts.gateway.config(), (e) => this.classify(e), {
      baseMs: this.opts.retryBaseMs,
      onRetry: (attempt, delay, cause) => this.emitGlobal("server.status", { state: "offline", endpoint: "gateway", attempt, next_in_ms: delay, cause }),
    });
    return this.config;
  }

  // ------------------------------------------------------------------ イベント
  private emit(conversationId: string, type: string, payload: Record<string, unknown>): void {
    const row = this.store.appendEvent(conversationId, type, payload);
    this.opts.emit({ v: 1, id: ulid(), seq: row.seq, conversation_id: conversationId, type: type as RuntimeEvent["type"], payload });
  }
  private emitGlobal(type: string, payload: Record<string, unknown>): void {
    this.opts.emit({ v: 1, id: ulid(), seq: 1, type: type as RuntimeEvent["type"], payload });
  }
  private setState(run: RunRow, state: string, detail?: Record<string, unknown>): void {
    if (run.state !== state) this.store.updateRun(run.id, { state });
    run.state = state;
    this.emit(run.conversation_id, "run.state", { run_id: run.id, state, ...(detail ? { detail } : {}) });
  }
  private note(run: RunRow, content: string, stepId: string | null = null): void {
    this.store.addMessage({ conversation_id: run.conversation_id, run_id: run.id, step_id: stepId, role: "system_note", content, tokens: Math.ceil(content.length / 1.5) });
  }

  // ------------------------------------------------------------------ コマンド
  async handle(cmd: RuntimeCommand): Promise<unknown> {
    const v = validateDef("RuntimeCommand", cmd);
    if (!v.ok) throw new Error(`不正なコマンドです: ${v.errors[0]?.message ?? ""}`);
    const p = cmd.payload as Record<string, unknown>;
    switch (cmd.type) {
      case "run.start":
        return this.startRun(p as never);
      case "run.pause":
        return this.pause(String(p.run_id), true);
      case "run.resume":
        return this.pause(String(p.run_id), false);
      case "run.stop":
        return this.stopRun(String(p.run_id));
      case "approval.decide":
        return this.decideApproval(p as never);
      case "question.answer":
        return this.answerQuestion(p as never);
      case "undo.run":
        return this.coordinator.undoRun(String(p.run_id), p.paths as string[] | undefined);
      case "events.since":
        return this.store.eventsSince(String(p.conversation_id), Number(p.seq)).map((e) => ({
          v: 1,
          id: `${e.conversation_id}:${e.seq}`,
          seq: e.seq,
          conversation_id: e.conversation_id,
          type: e.type,
          payload: JSON.parse(e.payload) as unknown,
        }));
      case "settings.update":
        for (const [k, val] of Object.entries(p)) this.store.setSetting(k, val);
        return { ok: true };
      case "settings.get":
        return {
          net_level: this.store.getSetting("net_level", "off"),
          model_default: this.store.getSetting("model_default", "auto"),
          notify_long_run_minutes: this.store.getSetting("notify_long_run_minutes", this.opts.longRunMinutes),
        };
      case "config.get": {
        const c = this.config ?? (await this.refreshConfig());
        return {
          org: c.org,
          models: c.models,
          escalation_target: c.escalation_target,
          net_level_cap: c.net.level_cap,
          features: c.features,
          macros: c.macros ?? [],
        };
      }
      case "artifact.diff":
        return this.diff(String(p.run_id), String(p.path));
      case "conversation.list":
        return this.store.listConversations();
      case "conversation.get":
        return {
          conversation: this.store.getConversation(String(p.conversation_id)),
          messages: this.store.liveMessages(String(p.conversation_id)).filter((m) => m.role !== "system_note"),
        };
      case "conversation.search":
        return this.store.searchConversations(String(p.q ?? ""));
      case "conversation.delete":
        this.store.updateConversation(String(p.conversation_id), { deleted_at: nowIso() });
        return { ok: true };
      case "feedback.submit":
        return this.submitFeedback(p as { run_id: string; rating: "good" | "bad"; comment?: string; consent: boolean });
    }
    return undefined;
  }

  /**
   * 評価ボタン（ADM-06）。同意があるときだけ、この作業のやり取り（利用者の依頼・モデルの出力・ツールの結果）を添える。
   * 同意がなければ評価と感想だけを送る。中身の保管とマスキングは Gateway が行う。
   */
  private async submitFeedback(p: { run_id: string; rating: "good" | "bad"; comment?: string; consent: boolean }): Promise<unknown> {
    const run = this.store.getRun(p.run_id);
    if (!run) throw new Error("作業が見つかりません");
    let transcript: { role: string; content: string }[] = [];
    if (p.consent) {
      const rows = this.store.db
        .prepare("SELECT role, content FROM messages WHERE run_id = ? AND role IN ('user','assistant','tool') ORDER BY created_at, rowid")
        .all(run.id) as { role: string; content: string }[];
      transcript = rows.slice(-200).map((r) => ({ role: r.role, content: r.content.slice(0, 50_000) }));
    }
    return this.opts.gateway.post("/v1/feedback", {
      run_id: run.id,
      rating: p.rating,
      ...(p.comment ? { comment: p.comment } : {}),
      consent: p.consent,
      transcript,
    });
  }

  /** 差分プレビュー（AGT-03）。 */
  private async diff(runId: string, path: string): Promise<ArtifactDiff> {
    const run = this.store.getRun(runId);
    if (!run) throw new Error("作業が見つかりません");
    const conv = this.store.getConversation(run.conversation_id)!;
    const intent = findIntent(this.store, runId, path, conv.workspace);
    if (!intent) throw new Error("このファイルの変更記録がありません");
    const ctx: ToolContext = { workspace: conv.workspace, conversationId: conv.id, runId, stepId: `${runId}:diff` };
    return computeDiff(intent, path, (name, args, c) => this.opts.router.call(name, args, c), ctx);
  }

  private startRun(p: { conversation_id: string; text: string; attachments: string[]; workspace: string; model: string }): { run_id: string; followup: boolean } {
    let conv = this.store.getConversation(p.conversation_id);
    if (!conv) conv = this.store.createConversation({ id: p.conversation_id, title: p.text.slice(0, 40), workspace: p.workspace, model_pref: p.model });
    const text = p.attachments.length ? `${p.text}\n\n（添付）${p.attachments.join(", ")}` : p.text;
    const active = this.store.activeRunFor(conv.id);
    if (active) {
      // 実行中の会話への追加の指示（プロトコル §5.1）
      this.store.addMessage({ conversation_id: conv.id, run_id: active.id, step_id: null, role: "user", content: text, tokens: Math.ceil(text.length / 1.5) });
      this.wake(active.id);
      return { run_id: active.id, followup: true };
    }
    const model = this.pickModel(p.model);
    const run = this.store.tx(() => {
      const r = this.store.createRun({ conversation_id: conv.id, model_current: model });
      this.store.addMessage({ conversation_id: conv.id, run_id: r.id, step_id: null, role: "user", content: text, tokens: Math.ceil(text.length / 1.5) });
      return r;
    });
    this.setState(run, "planning");
    this.spawn(run);
    return { run_id: run.id, followup: false };
  }

  private pickModel(pref: string): string {
    const want = MODEL_FOR[pref] ?? "local-12b";
    const ok = this.config?.models.find((m) => m.id === want)?.available;
    return ok ? want : "local-12b";
  }

  private pause(runId: string, paused: boolean): { ok: boolean } {
    const run = this.store.getRun(runId);
    if (!run || run.ended_at) return { ok: false };
    this.store.updateRun(runId, { paused: paused ? 1 : 0 });
    if (paused) this.setState(run, "paused");
    this.wake(runId);
    return { ok: true };
  }

  private stopRun(runId: string): { ok: boolean; artifacts: unknown[] } {
    const run = this.store.getRun(runId);
    if (!run || run.ended_at) return { ok: false, artifacts: [] };
    this.store.updateRun(runId, { ended_at: nowIso(), end_reason: "user_stop" });
    const artifacts = this.store.intentsForRun(runId).filter((i) => i.status === "committed").map((i) => ({ path: i.final_path ?? i.path, op: i.op }));
    this.setState(run, "stopped", { artifacts });
    const h = this.runs.get(runId);
    h?.abort.abort(new CancelledError("利用者が停止しました"));
    this.wake(runId);
    return { ok: true, artifacts };
  }

  private decideApproval(p: { approval_id: string; decision: "approve" | "reject"; remember?: string }): { ok: boolean } {
    const a = this.store.findApproval(p.approval_id);
    if (!a || a.decision) return { ok: false };
    const remember = p.remember && !a.tainted ? p.remember : null; // 汚染中は「この run の間は承認」を使えない（R10）
    this.store.updateApproval(a.conversation_id, a.id, { decision: p.decision, remember, decided_at: nowIso() });
    this.wake(a.run_id);
    return { ok: true };
  }

  private answerQuestion(p: { question_id: string; answer: string; secret?: boolean }): { ok: boolean } {
    const q = this.store.findQuestion(p.question_id);
    if (!q || q.answered_at) return { ok: false };
    let answer = p.answer;
    if (p.secret || q.secret) {
      const ref = `pw-${this.secrets.size + 1}`;
      this.secrets.set(ref, p.answer);
      answer = `***（${ref}）`; // 実値は保存しない（PDF-06）
    }
    this.store.updateQuestion(q.conversation_id, q.id, { answer, answered_at: nowIso() });
    this.wake(q.run_id);
    return { ok: true };
  }

  // ------------------------------------------------------------------ 実行ループ
  private wake(runId: string): void {
    this.runs.get(runId)?.wakers.splice(0).forEach((w) => w());
  }
  private waitWake(h: RunHandle, timeoutMs = 60_000): Promise<void> {
    return new Promise((resolve) => {
      const t = setTimeout(resolve, timeoutMs);
      h.wakers.push(() => {
        clearTimeout(t);
        resolve();
      });
    });
  }

  private spawn(run: RunRow): void {
    if (this.runs.has(run.id)) return;
    const h: RunHandle = {
      runId: run.id,
      conversationId: run.conversation_id,
      abort: new AbortController(),
      wakers: [],
      replanSince: run.next_step_seq,
      nextPrompt: undefined,
      done: Promise.resolve(),
    };
    this.runs.set(run.id, h);
    h.done = this.drive(h)
      .catch((e: unknown) => {
        if (!(e instanceof CancelledError) && !h.abort.signal.aborted) {
          this.note(this.store.getRun(run.id)!, `[event] 内部エラー: ${(e as Error).message}`);
        }
      })
      .finally(() => this.runs.delete(run.id));
  }

  private async drive(h: RunHandle): Promise<void> {
    let failures = 0;
    while (!this.stopped) {
      try {
        if (!(await this.iterate(h))) return;
        failures = 0;
      } catch (e) {
        if (e instanceof CancelledError || h.abort.signal.aborted || this.stopped) return;
        // 想定外の例外でも作業は終了させない（5.4）。記録して間隔を空けて続ける
        failures++;
        const run = this.store.getRun(h.runId);
        if (!run || run.ended_at) return;
        this.note(run, `[event] 内部エラーが発生したため、やり直します: ${(e as Error).message}`);
        this.emit(run.conversation_id, "retry", { attempt: failures, next_in_ms: Math.min(60_000, this.opts.retryBaseMs * 2 ** failures), cause: "TOOL_FAILED" });
        await new Promise((r) => setTimeout(r, Math.min(60_000, this.opts.retryBaseMs * 2 ** failures)));
      }
    }
  }

  /** 1手分の処理。run が終わっていれば false。 */
  private async iterate(h: RunHandle): Promise<boolean> {
    {
      const run = this.store.getRun(h.runId);
      if (!run || run.ended_at) return false;
      if (run.paused) {
        this.setState(run, "paused");
        await this.waitWake(h);
        return true;
      }
      const conv = this.store.getConversation(run.conversation_id)!;
      await this.deliverDecisions(run, conv.workspace, h);
      this.checkLongRun(run);

      const last = this.store.lastStep(run.id);
      if (last && ["dispatched", "policy_checked", "tool_running"].includes(last.phase) && last.action) {
        await this.dispatch(run, last, JSON.parse(last.action) as Action, h); // 再開: 推論済みの手を実行する
        return true;
      }
      let step: StepRow;
      if (last && last.phase === "inferring") step = last; // 再開: 同じ冪等キーで問い合わせる
      else {
        this.checkStall(run, h);
        step = this.store.createStep(run.id, run.next_step_seq);
      }
      const action = await this.infer(run, step, h);
      if (action) await this.dispatch(run, step, action, h);
      return true;
    }
  }

  private netLevel(): NetLevel {
    const want = this.store.getSetting<NetLevel>("net_level", "off");
    const cap = this.config?.net.level_cap ?? "off";
    const order: NetLevel[] = ["off", "search", "browse"];
    return order[Math.min(order.indexOf(want), order.indexOf(cap))] ?? "off";
  }

  private enabledTools(): ToolCatalogEntry[] {
    const f = this.config?.features ?? { rag: false, web: false, ocr: false };
    return this.opts.router.enabled({ netLevel: this.netLevel(), features: f });
  }

  private memoryView(run: RunRow, workspace: string) {
    const userMsgs = this.store.liveMessages(run.conversation_id).filter((m) => m.role === "user" && m.run_id === run.id);
    return {
      request: userMsgs[0]?.content ?? "",
      followups: userMsgs.slice(1).map((m) => m.content),
      workspace,
      plan: this.store.getPlan(run.id) as { id: string; title: string; status: string }[],
      facts: this.store.listMemory(run.conversation_id),
      pending: [
        ...this.store.pendingApprovals(run.id).map((a) => ({ id: a.id, summary: `${(JSON.parse(a.request) as ApprovalRequest).tool} の承認待ち` })),
        ...this.store.pendingQuestions(run.id).map((q) => ({ id: q.id, summary: `質問「${q.question.slice(0, 40)}」の回答待ち` })),
      ],
      artifacts: this.store.intentsForRun(run.id).filter((i) => i.status === "committed").map((i) => ({ op: i.op, path: i.final_path ?? i.path })),
    };
  }

  private budget(model: string): number {
    return this.config?.models.find((m) => m.id === model)?.context_budget ?? 32768;
  }

  private buildFor(run: RunRow, workspace: string, next?: string) {
    const conv = this.store.getConversation(run.conversation_id)!;
    const tools = this.enabledTools();
    const messages = buildMessages({
      orgName: this.config?.org.name ?? "",
      instructions: this.config?.instructions.body ?? "",
      netEnabled: this.netLevel() !== "off",
      tainted: Boolean(conv.tainted),
      memory: this.memoryView(run, workspace),
      tools,
      history: this.store.liveMessages(run.conversation_id).filter((m) => m.run_id === run.id || m.role === "system_note"),
      ...(next ? { next } : {}),
    });
    return { messages, tools };
  }

  private classify(e: unknown): { transient: boolean; cause: string; hintMs?: number } {
    if (e instanceof TransientError) return { transient: true, cause: e.code, ...(e.retryAfterMs ? { hintMs: e.retryAfterMs } : {}) };
    if (e instanceof JobExpiredError) return { transient: true, cause: "TIMEOUT" };
    return { transient: false, cause: "" };
  }

  /** 推論1手。形式が不正なら差し戻しの注記を残して null を返す（AGT-05）。 */
  private async infer(run: RunRow, step: StepRow, h: RunHandle): Promise<Action | null> {
    const conv = this.store.getConversation(run.conversation_id)!;
    await this.maybeCompact(run, conv.workspace, h);
    const next = h.nextPrompt;
    h.nextPrompt = undefined;
    const isFirst = step.seq === 1;
    this.setState(run, isFirst ? "planning" : "inferring");
    let attempt = step.attempt;
    const output = await retryForever(
      async () => {
        const fresh = this.store.getRun(run.id)!;
        const { messages, tools } = this.buildFor(fresh, conv.workspace, next);
        try {
          const r = await this.opts.gateway.infer(
            `${step.id}:${attempt}`,
            {
              model: fresh.model_current,
              messages,
              response_format: { type: "json_schema", schema: buildActionSchema(tools) },
              max_tokens: 2048,
              temperature: 0.2,
              priority_hint: fresh.low_priority ? "low" : isFirst ? "interactive" : "normal",
              purpose: "agent",
            },
            {
              signal: h.abort.signal,
              onJob: (jobId) => this.store.updateStep(step.id, { gateway_job_id: jobId, attempt }),
              onQueue: (position, eta) => this.emit(run.conversation_id, "queue", { position, eta_seconds: eta }),
              onToken: (delta) => this.emit(run.conversation_id, "token", { run_id: run.id, step_id: step.id, delta }),
              onReset: () => this.emit(run.conversation_id, "token", { run_id: run.id, step_id: step.id, delta: "", reset: true }),
            },
          );
          if (fresh.state === "reconnecting") this.setState(fresh, isFirst ? "planning" : "inferring");
          return r.output;
        } catch (e) {
          if (e instanceof JobExpiredError) attempt++;
          if (e instanceof GatewayError && e.code === "CONTEXT_TOO_LARGE") {
            await this.compact(this.store.getRun(run.id)!, conv.workspace, h, true);
            throw new TransientError("SERVER_BUSY", "文脈を圧縮して再投入します");
          }
          if (e instanceof GatewayError && e.status === 400 && fresh.model_current !== "local-12b") {
            this.store.updateRun(run.id, { model_current: "local-12b" }); // 使えないモデルは既定に戻す
            throw new TransientError("SERVER_BUSY", "既定のモデルで再投入します");
          }
          throw e;
        }
      },
      (e) => this.classify(e),
      {
        baseMs: this.opts.retryBaseMs,
        signal: h.abort.signal,
        onRetry: (n, delay, cause) => {
          this.setState(this.store.getRun(run.id)!, "reconnecting");
          this.emit(run.conversation_id, "retry", { attempt: n, next_in_ms: delay, cause });
        },
      },
    );

    let action: Action | null = null;
    let problem = "";
    try {
      const parsed = JSON.parse(output) as Action;
      const v = validateDef("Action", parsed);
      if (!v.ok) problem = v.errors[0]?.message ?? "Action の形式に合いません";
      else if (parsed.type === "tool_call" && !this.enabledTools().some((t) => t.name === parsed.tool)) problem = `${parsed.tool} は現在使えません`;
      else action = parsed;
    } catch {
      problem = "JSON として読めません";
    }
    if (!action) {
      this.store.tx(() => {
        this.store.updateStep(step.id, { phase: "failed_permanent", action: output.slice(0, 4000), error_code: "INVALID_ARGS", ended_at: nowIso() });
        this.note(run, `[event] 直前の出力は形式が不正でした（${problem}）。決められた JSON の形式で、次の1手を返してください。`, step.id);
      });
      this.emit(run.conversation_id, "step", { run_id: run.id, step_id: step.id, kind: "system", status: "error", started_at: step.started_at, result_preview: problem });
      return null;
    }
    this.store.tx(() => {
      this.store.updateStep(step.id, { phase: "dispatched", action: JSON.stringify(action) });
      this.store.addMessage({ conversation_id: run.conversation_id, run_id: run.id, step_id: step.id, role: "assistant", content: JSON.stringify(action), tokens: Math.ceil(output.length / 1.5) });
    });
    return action;
  }

  private async dispatch(run: RunRow, step: StepRow, action: Action, h: RunHandle): Promise<void> {
    const kind = action.type === "tool_call" ? "tool" : action.type === "plan_update" ? "plan" : action.type === "ask_user" ? "ask" : "final";
    this.emit(run.conversation_id, "step", {
      run_id: run.id,
      step_id: step.id,
      kind,
      ...(action.type === "tool_call" ? { tool: action.tool, args_preview: JSON.stringify(action.args).slice(0, 500) } : {}),
      status: "running",
      started_at: step.started_at,
    });
    switch (action.type) {
      case "plan_update":
        this.store.tx(() => {
          this.store.setPlan(run.id, action.plan);
          this.store.updateStep(step.id, { phase: "committed", ended_at: nowIso() });
        });
        this.emit(run.conversation_id, "plan", { run_id: run.id, plan: action.plan });
        this.emitStepDone(run, step, kind, "ok");
        return;
      case "ask_user": {
        // 回答待ちの質問があるあいだは、新しい質問を出さない（同じ質問を繰り返すモデルがある。質問は一度に1つ）
        const waiting = this.store.pendingQuestions(run.id)[0];
        if (waiting) {
          this.store.tx(() => {
            this.finishStepMessage(run, step, "ask_user", errResult("APPROVAL_REQUIRED", `質問(${waiting.id})の回答待ちです。質問は繰り返しません。回答が不要な作業を先に進め、ほかに無ければ待ちの完了（final）を返してください`, { question_id: waiting.id }), "deferred");
          });
          this.emitStepDone(run, step, kind, "deferred");
          return;
        }
        const qid = this.store.nextShortId("questions", "q");
        const secret = /パスワード|password/i.test(action.question) ? 1 : 0;
        this.store.tx(() => {
          this.store.addQuestion({ id: qid, conversation_id: run.conversation_id, run_id: run.id, step_id: step.id, question: action.question, choices: action.choices ? JSON.stringify(action.choices) : null, secret });
          this.finishStepMessage(run, step, "ask_user", errResult("APPROVAL_REQUIRED", `質問(${qid})の回答待ちです。回答が不要な作業を先に進めてください`, { question_id: qid }), "deferred");
        });
        this.emit(run.conversation_id, "question", { question_id: qid, run_id: run.id, question: action.question, ...(action.choices ? { choices: action.choices } : {}), secret_hint: Boolean(secret) });
        this.emit(run.conversation_id, "notify", { kind: "approval", title: "質問があります", body: action.question.slice(0, 200) });
        this.emitStepDone(run, step, kind, "deferred");
        return;
      }
      case "final": {
        const pa = this.store.pendingApprovals(run.id).length;
        const pq = this.store.pendingQuestions(run.id).length;
        if (pa + pq > 0) {
          this.store.tx(() => {
            this.store.updateStep(step.id, { phase: "committed", ended_at: nowIso() });
            this.note(run, `[event] 承認待ち・質問待ちが残っているため、まだ完了できません（承認 ${pa} 件、質問 ${pq} 件）。回答が届いたら続きを行ってください。`, step.id);
          });
          this.setState(run, pa ? "waiting_approval" : "waiting_question", { pending: pa + pq });
          while (!h.abort.signal.aborted && this.store.pendingApprovals(run.id).length + this.store.pendingQuestions(run.id).length === pa + pq) {
            await this.waitWake(h);
            const r = this.store.getRun(run.id);
            if (!r || r.ended_at) return;
          }
          return;
        }
        // 推論の間に届いた承認・回答がまだ反映されていない → 先に実行してから、改めて完了させる
        // （承認済みの操作を実行しないまま終わらないように。試験仕様 §4 FI-19）
        const undelivered = this.store.undeliveredApprovals(run.id).length + this.store.undeliveredAnswers(run.id).length;
        if (undelivered > 0) {
          this.store.tx(() => {
            this.store.updateStep(step.id, { phase: "committed", ended_at: nowIso() });
            this.note(run, `[event] 完了の前に、届いた承認・回答（${undelivered} 件）を反映します。結果を確認してから完了してください。`, step.id);
          });
          return;
        }
        this.store.tx(() => {
          this.store.updateStep(step.id, { phase: "committed", ended_at: nowIso() });
          this.store.updateRun(run.id, { ended_at: nowIso(), end_reason: "final", final_action: JSON.stringify(action) });
        });
        this.emitStepDone(run, step, kind, "ok");
        const citations = this.checkCitations(run, (action as { citations?: Citation[] }).citations ?? []);
        this.setState(run, "done", { summary: action.summary, ...(citations.length ? { citations } : {}) });
        this.emit(run.conversation_id, "notify", { kind: "done", title: "作業が完了しました", body: action.summary.slice(0, 300) });
        return;
      }
      case "tool_call":
        await this.toolCall(run, step, action, h);
        return;
    }
  }

  private emitStepDone(run: RunRow, step: StepRow, kind: string, status: string, extra: Record<string, unknown> = {}): void {
    this.emit(run.conversation_id, "step", { run_id: run.id, step_id: step.id, kind, status, started_at: step.started_at, ended_at: nowIso(), ...extra });
  }

  private policyContext(run: RunRow, workspace: string): PolicyContext {
    const conv = this.store.getConversation(run.conversation_id)!;
    const remember = this.rememberRules(run.id);
    return { workspace, tainted: Boolean(conv.tainted), netLevel: this.netLevel(), appDataDir: this.opts.appDataDir, remember };
  }

  private rememberRules(runId: string) {
    const rows = this.store.db
      .prepare(`SELECT * FROM approvals WHERE run_id = @runId AND decision = 'approve' AND remember = 'this_run'`)
      .all({ runId }) as { request: string }[];
    return rows.flatMap((r) => {
      const req = JSON.parse(r.request) as ApprovalRequest;
      return rememberRuleFor(req.tool, req.reason as ApprovalReason, req.paths.map((p) => ({ ...p, pointer: [], write: true })));
    });
  }

  /** convert.* の dst 既定値などを補う（ポリシー判定の前に確定させる）。 */
  private normalizeArgs(tool: string, args: Record<string, unknown>): Record<string, unknown> {
    const a = { ...args };
    const ext: Record<string, string> = { "convert.to_docx": ".docx", "convert.to_xlsx": ".xlsx", "convert.to_pdf": ".pdf" };
    if (ext[tool] && !a.dst) {
      const src = String(a.src);
      a.dst = src.slice(0, src.length - extname(src).length) + ext[tool];
    }
    if (tool === "data.transform") return substituteResultOf(this.store, a);
    return a;
  }

  private async toolCall(run: RunRow, step: StepRow, action: Extract<Action, { type: "tool_call" }>, h: RunHandle): Promise<void> {
    const conv = this.store.getConversation(run.conversation_id)!;
    const spec = this.opts.router.spec(action.tool);
    const ctx: ToolContext = {
      workspace: conv.workspace,
      conversationId: conv.id,
      runId: run.id,
      stepId: step.id,
      signal: h.abort.signal,
      secrets: Object.fromEntries(this.secrets),
    };
    if (!spec) {
      this.finishTool(run, step, action, errResult("UNKNOWN_TOOL", `${action.tool} は使えません`));
      return;
    }
    const args = this.normalizeArgs(action.tool, action.args);
    const d = decide(spec, args, this.policyContext(run, conv.workspace));
    const digest = sha256(spec.name + stableStringify(args));
    this.store.updateStep(step.id, { phase: "policy_checked", tool: spec.name, args_digest: digest });
    // 同じ引数で同じ失敗を繰り返している呼び出しは実行しない（小さいモデルが同じ誤りを繰り返して止まらなくなるのを防ぐ）
    const repeated = this.repeatedFailure(run, step, digest);
    if (repeated) {
      this.finishTool(
        run,
        step,
        action,
        errResult(
          repeated.code as never,
          `この呼び出しは同じ引数で既に${repeated.count}回失敗しています（${repeated.code}）。同じ呼び出しは実行しません。エラーの内容に従って引数を直すか、別のツール・別の方法に変えてください`,
        ),
      );
      return;
    }
    if (d.kind === "deny") {
      this.finishTool(run, step, action, errResult(d.code, d.message));
      return;
    }
    if (d.kind === "approve") {
      // 同じ操作がすでに承認待ちなら、新しい承認依頼を作らない（承認を待つあいだに同じ依頼を繰り返すモデルがある）
      const same = this.store
        .pendingApprovals(run.id)
        .find((a) => {
          const r = JSON.parse(a.request) as ApprovalRequest;
          return r.tool === spec.name && stableStringify(r.args) === stableStringify(args);
        });
      if (same) {
        this.finishTool(
          run,
          step,
          action,
          errResult("APPROVAL_REQUIRED", `この操作は既に承認待ち(${same.id})です。同じ依頼は繰り返しません。承認不要な作業を先に進め、ほかに無ければ待ちの完了（final）を返してください`, { approval_id: same.id }),
          "deferred",
        );
        return;
      }
      const id = this.store.nextShortId("approvals", "ap");
      const req: ApprovalRequest = {
        approval_id: id,
        run_id: run.id,
        step_id: step.id,
        tool: spec.name,
        args,
        reason: d.reason,
        paths: d.paths.map((p) => ({ path: p.path, real_path: p.real_path, inside_workspace: p.inside_workspace })),
        tainted: Boolean(conv.tainted),
        created_at: nowIso(),
      };
      this.store.addApproval({ id, conversation_id: conv.id, run_id: run.id, step_id: step.id, request: JSON.stringify(req), tainted: conv.tainted });
      this.finishTool(run, step, action, errResult("APPROVAL_REQUIRED", `承認待ち(${id})。承認不要な作業を先に進めてください`, { approval_id: id }), "deferred");
      this.emit(conv.id, "approval.request", req as unknown as Record<string, unknown>);
      this.emit(conv.id, "notify", { kind: "approval", title: "承認が必要な操作があります", body: `${spec.name}: ${req.paths.map((p) => p.path).join(", ")}`.slice(0, 300) });
      return;
    }
    this.store.updateStep(step.id, { phase: "tool_running" });
    this.setState(run, "tool_running", { tool: spec.name });
    const result = await this.execute(spec, args, d.paths, ctx, run);
    this.finishTool(run, step, action, result);
  }

  /**
   * 同じ (ツール, 引数) が、同じ引数なら必ず同じ結果になるエラーで2回以上失敗していれば、そのエラーと回数を返す。
   * ファイルが無い・ロック中など、状況が変われば通りうるエラーは対象にしない。
   */
  private repeatedFailure(run: RunRow, step: StepRow, digest: string): { code: string; count: number } | null {
    const fails = this.store
      .recentSteps(run.id, 40)
      .filter((s) => s.id !== step.id && s.args_digest === digest && s.error_code && DETERMINISTIC_ERRORS.has(s.error_code));
    if (fails.length < REPEAT_FAILURE_LIMIT) return null;
    return { code: fails[fails.length - 1]!.error_code!, count: fails.length };
  }

  /** ツールの実行（書き込みの調整・一時的な失敗の再試行込み）。 */
  private async execute(spec: ToolCatalogEntry, args: Record<string, unknown>, paths: ResolvedPath[], ctx: ToolContext, run: RunRow): Promise<ToolResult> {
    // 判定（や承認待ち）の間にフォルダがジャンクションなどに差し替えられていないか（PX-08）
    const changed = verifyUnchanged(paths, ctx.workspace);
    if (changed) return errResult("FORBIDDEN_PATH", `判定の後に ${changed} の実体が変わったため、実行しませんでした。もう一度確認してください`);
    if (spec.name === "batch.run") {
      const runner = new BatchRunner(this.store, (name, a) => this.executeInternal(name, a, ctx, run), {
        onProgress: (batchId, done, total, failed) => this.emit(run.conversation_id, "batch_progress", { batch_id: batchId, done, total, failed }),
      });
      return this.writeThrough(spec, args, paths, ctx, run, () => runner.run(args as never, ctx));
    }
    return this.writeThrough(spec, args, paths, ctx, run, (a) => this.opts.router.call(spec.name, a, ctx));
  }

  /** 一括処理の中から呼ぶツール。承認が必要な操作は失敗として扱う（一括処理は作業フォルダ内で完結させる）。 */
  private async executeInternal(name: string, args: Record<string, unknown>, ctx: ToolContext, run: RunRow): Promise<ToolResult> {
    const spec = this.opts.router.spec(name);
    if (!spec) return errResult("UNKNOWN_TOOL", `${name} は使えません`);
    const a = this.normalizeArgs(name, args);
    const d = decide(spec, a, this.policyContext(run, ctx.workspace));
    if (d.kind === "deny") return errResult(d.code, d.message);
    if (d.kind === "approve") return errResult("FORBIDDEN_PATH", "一括処理では作業フォルダ外のファイルを扱えません");
    return this.writeThrough(spec, a, d.paths, ctx, run, (x) => this.opts.router.call(name, x, ctx));
  }

  private writeThrough(
    spec: ToolCatalogEntry,
    args: Record<string, unknown>,
    paths: ResolvedPath[],
    ctx: ToolContext,
    run: RunRow,
    invoke: (a: Record<string, unknown>) => Promise<ToolResult>,
  ): Promise<ToolResult> {
    return retryForever(
      async () => {
        const r = spec.name === "batch.run" ? await invoke(args) : await this.coordinator.execute(spec, args, paths, ctx, invoke);
        if (!r.ok && r.error?.kind === "transient") throw Object.assign(new TransientError("SERVER_BUSY", r.error.message, r.error.retry_after_ms), { toolCode: r.error.code });
        return r;
      },
      (e) => (e instanceof TransientError ? { transient: true, cause: (e as TransientError & { toolCode?: string }).toolCode ?? e.code } : { transient: false, cause: "" }),
      {
        baseMs: this.opts.retryBaseMs,
        ...(ctx.signal ? { signal: ctx.signal } : {}),
        onRetry: (n, delay, cause) => this.emit(run.conversation_id, "retry", { attempt: n, next_in_ms: delay, cause }),
      },
    );
  }

  private finishStepMessage(run: RunRow, step: StepRow, tool: string, result: ToolResult, phase: "committed" | "deferred"): void {
    const body = renderToolResult(step.seq, tool, result, Boolean(this.opts.router.spec(tool)?.untrusted_output));
    const digest = sha256(stableStringify(result.data ?? {}) + (result.untrusted ?? []).map((u) => u.text).join(""));
    this.store.updateStep(step.id, {
      phase,
      result: JSON.stringify(result),
      result_digest: result.ok ? digest : null,
      error_code: result.ok ? null : (result.error?.code ?? null),
      ended_at: nowIso(),
    });
    this.store.addMessage({ conversation_id: run.conversation_id, run_id: run.id, step_id: step.id, role: "tool", content: body, tokens: result.tokens_estimate });
  }

  private finishTool(run: RunRow, step: StepRow, action: Extract<Action, { type: "tool_call" }>, result: ToolResult, phase: "committed" | "deferred" = "committed"): void {
    const conv = this.store.getConversation(run.conversation_id)!;
    const spec = this.opts.router.spec(action.tool);
    this.store.tx(() => {
      this.finishStepMessage(run, step, action.tool, result, phase);
      for (const a of result.artifacts ?? []) {
        this.store.addAudit({ at: nowIso(), action: `file.${a.op}`, target: a.path, result: "ok", detail: { tool: action.tool, run_id: run.id } });
      }
      // Web・外部の情報を読んだら、会話が終わるまで汚染扱い（R10）
      if (result.ok && (spec?.effect === "net" || (result.untrusted ?? []).some((u) => u.source === "web")) && !conv.tainted) {
        this.store.updateConversation(conv.id, { tainted: 1, tainted_at: nowIso() });
      }
    });
    for (const a of result.artifacts ?? []) this.emit(run.conversation_id, "artifact", { run_id: run.id, artifact: a });
    if (spec?.effect === "net") {
      this.emit(run.conversation_id, "net.activity", {
        kind: action.tool === "web.search" ? "search" : "fetch",
        ...(action.args.query ? { query: action.args.query } : {}),
        ...(action.args.url ? { url: action.args.url } : {}),
        ...(result.ok ? {} : { blocked: result.error?.code }),
      });
    }
    this.emitStepDone(run, step, "tool", phase === "deferred" ? "deferred" : result.ok ? "ok" : "error", {
      tool: action.tool,
      result_preview: result.ok ? JSON.stringify(result.data ?? {}).slice(0, 300) : `${result.error?.code}: ${result.error?.message}`.slice(0, 300),
    });
  }

  /** 承認・却下・回答を、次の推論の前に差し込む（プロトコル §4、prompts §4）。 */
  private async deliverDecisions(run: RunRow, workspace: string, h: RunHandle): Promise<void> {
    for (const a of this.store.undeliveredApprovals(run.id)) {
      const req = JSON.parse(a.request) as ApprovalRequest;
      if (a.decision === "reject") {
        this.store.tx(() => {
          this.store.updateApproval(a.conversation_id, a.id, { delivered: 1 });
          this.note(run, `[event] 承認 ${a.id}（${req.tool}）は利用者に却下されました（APPROVAL_REJECTED）。別の方法を検討してください。`);
        });
        continue;
      }
      const spec = this.opts.router.spec(req.tool)!;
      const ctx: ToolContext = { workspace, conversationId: run.conversation_id, runId: run.id, stepId: a.step_id, signal: h.abort.signal, secrets: Object.fromEntries(this.secrets) };
      // 承認後も禁止領域などの判定はやり直す（承認しても禁止領域は操作させない。SEC-03）
      const d = decide(spec, req.args, { ...this.policyContext(run, workspace), remember: [] });
      let result: ToolResult;
      if (d.kind === "deny") result = errResult(d.code, d.message);
      else {
        this.setState(run, "tool_running", { tool: spec.name, approval_id: a.id });
        result = await this.execute(spec, req.args as Record<string, unknown>, d.paths, ctx, run);
      }
      this.store.tx(() => {
        this.store.updateApproval(a.conversation_id, a.id, { delivered: 1, executed_step: a.step_id });
        this.note(run, `[event] 承認 ${a.id} が承認され、${req.tool} を実行しました:\n${renderToolResult(0, req.tool, result, Boolean(spec.untrusted_output))}`);
        for (const art of result.artifacts ?? []) this.store.addAudit({ at: nowIso(), action: `file.${art.op}`, target: art.path, result: "ok", detail: { tool: req.tool, approval: a.id } });
      });
      for (const art of result.artifacts ?? []) this.emit(run.conversation_id, "artifact", { run_id: run.id, artifact: art });
    }
    for (const q of this.store.undeliveredAnswers(run.id)) {
      this.store.tx(() => {
        this.store.updateQuestion(q.conversation_id, q.id, { delivered: 1 });
        this.note(run, `[event] 質問 ${q.id} への回答: 「${q.answer}」`);
      });
    }
  }

  /** 停滞の検知と段階的な対処（設計書 §5.6）。 */
  private checkStall(run: RunRow, h: RunHandle): void {
    const steps = this.store.recentSteps(run.id, 40).filter((s) => s.seq >= h.replanSince);
    const v = detectStall(steps, this.opts.stall);
    if (!v.stalled) return;
    h.replanSince = run.next_step_seq;
    const target = this.config?.escalation_target ?? null;
    const canEscalate = target !== null && run.model_current !== target && this.config?.models.find((m) => m.id === target)?.available;
    const progress = (this.store.getPlan(run.id) as { title: string; status: string }[]).map((p) => `${p.title}:${p.status}`).join(" / ") || "（計画なし）";
    if (run.replan_count < (canEscalate ? 1 : this.opts.maxReplans)) {
      this.store.updateRun(run.id, { replan_count: run.replan_count + 1, stall_level: 1 });
      h.nextPrompt = replanBlock(v.summary ?? "", progress);
      this.setState(run, "replanning", { signal: v.signal });
      return;
    }
    if (canEscalate && run.stall_level < 2) {
      this.store.updateRun(run.id, { model_current: target!, escalated_at: nowIso(), stall_level: 2 });
      h.nextPrompt = replanBlock(v.summary ?? "", progress);
      this.note(run, `[event] 高精度モデル（${target}）に切り替えて続けます。`);
      this.setState(run, "replanning", { signal: v.signal, escalated_to: target });
      return;
    }
    // 立て直しても進まない: 通知して低優先度で続ける（R3, R9）。終了はしない
    if (run.stall_level < 3) {
      this.store.updateRun(run.id, { stall_level: 3, low_priority: 1 });
      this.emit(run.conversation_id, "notify", { kind: "stalled", title: "作業が進んでいません", body: `${v.summary ?? ""}。低優先度で続けています。指示を追加するか、停止してください。` });
    }
    h.nextPrompt = replanBlock(v.summary ?? "", progress);
    this.setState(run, "stalled", { signal: v.signal });
  }

  /**
   * 完了報告の出典（AGT-07, NET-05）を、この会話で実際に読んだものと照らす。
   * 読んだ記録（ツール結果）に無い出典は verified: false にして、画面で「確認できない出典」と示す（作り話の出典を見分ける）。
   */
  private checkCitations(run: RunRow, citations: Citation[]): (Citation & { verified: boolean })[] {
    if (citations.length === 0) return [];
    const conv = this.store.getConversation(run.conversation_id);
    // モデル自身の出力（完了報告を含む）は除き、ツールの結果と利用者の発言だけと照らす
    const rows = this.store.db
      .prepare("SELECT content FROM messages WHERE conversation_id = ? AND role <> 'assistant'")
      .all(run.conversation_id) as { content: string }[];
    const seen = rows.map((r) => r.content).join("\n").normalize("NFC");
    const norm = (s: string) => s.normalize("NFC").replace(/\\/g, "/");
    const seenSlash = seen.replace(/\\\\/g, "/").replace(/\\/g, "/");
    return citations.slice(0, 50).map((c) => {
      let verified = seen.includes(c.ref.normalize("NFC")) || seenSlash.includes(norm(c.ref));
      if (!verified && c.kind === "file" && conv) {
        try {
          verified = existsSync(resolveRealPath(c.ref, conv.workspace));
        } catch {
          verified = false;
        }
      }
      return { ...c, verified };
    });
  }

  private checkLongRun(run: RunRow): void {
    if (run.long_run_notified_at) return;
    const minutes = this.store.getSetting("notify_long_run_minutes", this.opts.longRunMinutes);
    if (testNow() - Date.parse(run.started_at) < minutes * 60_000) return;
    this.store.updateRun(run.id, { long_run_notified_at: nowIso() });
    this.emit(run.conversation_id, "notify", { kind: "long_run", title: "作業が長時間続いています", body: `${minutes}分を超えました。実行は続けています。` });
  }

  private async maybeCompact(run: RunRow, workspace: string, h: RunHandle): Promise<void> {
    const { messages } = this.buildFor(run, workspace);
    if (messagesTokens(messages) > this.budget(run.model_current) * this.opts.compactRatio) await this.compact(run, workspace, h, false);
  }

  /**
   * 文脈の圧縮（AGT-06、prompts §7）。固定メモリは毎回入れ直すので失われない。
   * 古い手順を要約し、それでも予算に収まらなければ残す手数を減らし、最後はツール結果を切り詰める。
   */
  private async compact(run: RunRow, workspace: string, h: RunHandle, force: boolean): Promise<void> {
    const target = this.budget(run.model_current) * (force ? 0.6 : this.opts.compactRatio);
    const over = () => messagesTokens(this.buildFor(this.store.getRun(run.id)!, workspace).messages) > target;
    for (const keep of force ? [3, 1] : [6, 3, 1]) {
      const history = this.store.liveMessages(run.conversation_id).filter((m) => m.run_id === run.id);
      const cands = compactionCandidates(history, keep);
      if (cands.length === 0) continue;
      this.setState(run, "compacting");
      await this.summarize(run, cands, h);
      if (!over()) return;
    }
    // 直近の手順だけでも予算を超える: 大きいツール結果から切り詰める（必要なら読み直せる）
    const tools = this.store
      .liveMessages(run.conversation_id)
      .filter((m) => m.run_id === run.id && m.role === "tool" && m.content.length > 1200)
      .sort((a, b) => b.content.length - a.content.length);
    for (const m of tools) {
      if (!over()) return;
      this.setState(run, "compacting");
      const head = m.content.slice(0, 1000);
      this.store.db
        .prepare("UPDATE messages SET content = @c, tokens = @t WHERE id = @id")
        .run({ id: m.id, c: `${head}\n…（長いため省略しました。必要なら読み直してください）\n[/tool_result]`, t: Math.ceil(1100 / 1.5) });
    }
  }

  private async summarize(run: RunRow, cands: MessageRow[], h: RunHandle): Promise<void> {
    const text = cands.map((m) => m.content.slice(0, 600)).join("\n");
    let summary: string;
    try {
      const r = await this.opts.gateway.infer(
        `${run.id}:compact:${cands[0]!.id}`,
        { model: run.model_current, messages: [{ role: "user", content: compactPrompt(text) }], max_tokens: 1500, temperature: 0.1, purpose: "compact" },
        { signal: h.abort.signal },
      );
      summary = r.output.trim() || mechanicalSummary(cands);
    } catch (e) {
      if (e instanceof CancelledError) throw e;
      summary = mechanicalSummary(cands);
    }
    this.store.tx(() => {
      const m = this.store.addMessage({ conversation_id: run.conversation_id, run_id: run.id, step_id: null, role: "system_note", content: `[summary] ${summary}`, tokens: Math.ceil(summary.length / 1.5) });
      this.store.markCompacted(cands.map((c) => c.id), m.id);
    });
  }
}

