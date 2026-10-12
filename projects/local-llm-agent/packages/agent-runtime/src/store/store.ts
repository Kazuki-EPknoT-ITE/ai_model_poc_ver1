// Run Store（SQLCipher）。書き込みは Runtime プロセスだけが行う（データモデル仕様 §2）。
import Database from "better-sqlite3-multiple-ciphers";
import { MIGRATIONS } from "./schema.js";
import { nowIso, ulid } from "../util.js";

export interface ConversationRow {
  id: string;
  title: string;
  workspace: string;
  model_pref: "auto" | "fast" | "accurate";
  tainted: number;
  tainted_at: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}
export interface RunRow {
  id: string;
  conversation_id: string;
  state: string;
  model_current: string;
  escalated_at: string | null;
  stall_level: number;
  replan_count: number;
  low_priority: number;
  paused: number;
  started_at: string;
  ended_at: string | null;
  end_reason: "final" | "user_stop" | null;
  long_run_notified_at: string | null;
  context_tokens: number;
  next_step_seq: number;
  final_action: string | null;
}
export type StepPhase =
  | "inferring"
  | "dispatched"
  | "policy_checked"
  | "deferred"
  | "tool_running"
  | "committed"
  | "failed_permanent";
export interface StepRow {
  id: string;
  run_id: string;
  seq: number;
  phase: StepPhase;
  attempt: number;
  gateway_job_id: string | null;
  action: string | null;
  tool: string | null;
  args_digest: string | null;
  result: string | null;
  result_digest: string | null;
  error_code: string | null;
  started_at: string;
  ended_at: string | null;
}
export interface MessageRow {
  id: string;
  conversation_id: string;
  run_id: string | null;
  step_id: string | null;
  role: "user" | "assistant" | "tool" | "system_note";
  content: string;
  tokens: number;
  compacted_into: string | null;
  created_at: string;
}
export type IntentStatus = "prepared" | "staged" | "committed" | "conflict" | "undone";
export interface IntentRow {
  id: string;
  step_id: string;
  batch_item_id: string | null;
  path: string;
  op: "create" | "overwrite" | "delete" | "move" | "copy";
  op_digest: string;
  pre_hash: string | null;
  post_hash: string | null;
  backup_path: string | null;
  staging_path: string | null;
  final_path: string | null;
  status: IntentStatus;
  created_at: string;
  committed_at: string | null;
}
export interface ApprovalRow {
  id: string;
  conversation_id: string;
  run_id: string;
  step_id: string;
  request: string;
  tainted: number;
  decision: "approve" | "reject" | null;
  remember: string | null;
  decided_at: string | null;
  executed_step: string | null;
  delivered: number;
}
export interface QuestionRow {
  id: string;
  conversation_id: string;
  run_id: string;
  step_id: string;
  question: string;
  choices: string | null;
  secret: number;
  answer: string | null;
  answered_at: string | null;
  delivered: number;
}
export interface BatchRow {
  id: string;
  run_id: string;
  step_id: string;
  spec: string;
  total: number;
  status: "running" | "writing_output" | "done";
  output_intent_id: string | null;
  created_at: string;
  ended_at: string | null;
}
export interface BatchItemRow {
  id: string;
  batch_id: string;
  idx: number;
  item: string;
  item_hash: string | null;
  status: "pending" | "running" | "ok" | "failed";
  result: string | null;
  error_code: string | null;
  error_reason: string | null;
  attempts: number;
}
export interface EventRow {
  conversation_id: string;
  seq: number;
  type: string;
  payload: string;
  created_at: string;
}

type Params = Record<string, unknown>;

export class RunStore {
  readonly db: Database.Database;

  private constructor(db: Database.Database) {
    this.db = db;
  }

  /** key が null のときは暗号化しない（テスト専用）。 */
  static open(file: string, key: string | null): RunStore {
    const db = new Database(file);
    try {
      if (key !== null) {
        db.pragma("cipher='sqlcipher'");
        db.pragma(`key='${key.replace(/'/g, "''")}'`);
      }
      db.pragma("journal_mode = WAL");
      db.pragma("synchronous = FULL");
      db.pragma("foreign_keys = ON");
      const store = new RunStore(db);
      store.migrate();
      return store;
    } catch (e) {
      db.close(); // 鍵が違う等で失敗したらハンドルを残さない
      throw e;
    }
  }

  close(): void {
    this.db.close();
  }

  private migrate(): void {
    const version = this.db.pragma("user_version", { simple: true }) as number;
    for (let i = version; i < MIGRATIONS.length; i++) {
      this.db.transaction(() => {
        this.db.exec(MIGRATIONS[i]!);
        this.db.pragma(`user_version = ${i + 1}`);
      })();
    }
  }

  /** 1つの遷移を1トランザクションで確定させる（データモデル仕様 §2.3）。 */
  tx<T>(fn: () => T): T {
    return this.db.transaction(fn)();
  }

  private run(sql: string, p: Params = {}): Database.RunResult {
    return this.db.prepare(sql).run(p);
  }
  private get<T>(sql: string, p: Params = {}): T | undefined {
    return this.db.prepare(sql).get(p) as T | undefined;
  }
  private all<T>(sql: string, p: Params = {}): T[] {
    return this.db.prepare(sql).all(p) as T[];
  }
  private update(table: string, idCol: string, id: string, patch: Params): void {
    const keys = Object.keys(patch).filter((k) => patch[k] !== undefined);
    if (keys.length === 0) return;
    this.run(`UPDATE ${table} SET ${keys.map((k) => `${k} = @${k}`).join(", ")} WHERE ${idCol} = @__id`, {
      ...Object.fromEntries(keys.map((k) => [k, patch[k]])),
      __id: id,
    });
  }

  // ---- conversations ----
  createConversation(p: { id?: string; title: string; workspace: string; model_pref?: string }): ConversationRow {
    const now = nowIso();
    const id = p.id ?? ulid();
    this.run(
      `INSERT INTO conversations (id, title, workspace, model_pref, created_at, updated_at)
       VALUES (@id, @title, @workspace, @model_pref, @now, @now)`,
      { id, title: p.title, workspace: p.workspace, model_pref: p.model_pref ?? "auto", now },
    );
    return this.getConversation(id)!;
  }
  getConversation(id: string): ConversationRow | undefined {
    return this.get(`SELECT * FROM conversations WHERE id = @id`, { id });
  }
  listConversations(limit = 100): ConversationRow[] {
    return this.all(`SELECT * FROM conversations WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT @limit`, {
      limit,
    });
  }
  searchConversations(q: string, limit = 50): ConversationRow[] {
    return this.all(
      `SELECT DISTINCT c.* FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id AND m.role = 'user'
       WHERE c.deleted_at IS NULL AND (c.title LIKE @q ESCAPE '!' OR m.content LIKE @q ESCAPE '!') ORDER BY c.updated_at DESC LIMIT @limit`,
      // 「%」「_」を含む語もそのまま探す
      { q: `%${q.replace(/[!%_]/g, "!$&")}%`, limit },
    );
  }
  updateConversation(id: string, patch: Partial<ConversationRow>): void {
    this.update("conversations", "id", id, { ...patch, updated_at: nowIso() });
  }

  // ---- runs ----
  createRun(p: { conversation_id: string; model_current: string }): RunRow {
    const id = ulid();
    this.run(
      `INSERT INTO runs (id, conversation_id, state, model_current, started_at)
       VALUES (@id, @conversation_id, 'planning', @model_current, @now)`,
      { id, ...p, now: nowIso() },
    );
    return this.getRun(id)!;
  }
  getRun(id: string): RunRow | undefined {
    return this.get(`SELECT * FROM runs WHERE id = @id`, { id });
  }
  activeRuns(): RunRow[] {
    return this.all(`SELECT * FROM runs WHERE ended_at IS NULL ORDER BY started_at`);
  }
  activeRunFor(conversationId: string): RunRow | undefined {
    return this.get(`SELECT * FROM runs WHERE conversation_id = @c AND ended_at IS NULL`, { c: conversationId });
  }
  updateRun(id: string, patch: Partial<RunRow>): void {
    this.update("runs", "id", id, patch);
  }

  // ---- messages ----
  addMessage(p: Omit<MessageRow, "id" | "created_at" | "compacted_into">): MessageRow {
    const row: MessageRow = { ...p, id: ulid(), created_at: nowIso(), compacted_into: null };
    this.run(
      `INSERT INTO messages (id, conversation_id, run_id, step_id, role, content, tokens, compacted_into, created_at)
       VALUES (@id, @conversation_id, @run_id, @step_id, @role, @content, @tokens, @compacted_into, @created_at)`,
      row as unknown as Params,
    );
    return row;
  }
  /** 圧縮されていないメッセージを古い順に。 */
  liveMessages(conversationId: string): MessageRow[] {
    return this.all(
      `SELECT * FROM messages WHERE conversation_id = @c AND compacted_into IS NULL ORDER BY created_at, rowid`,
      { c: conversationId },
    );
  }
  markCompacted(ids: string[], into: string): void {
    const stmt = this.db.prepare(`UPDATE messages SET compacted_into = @into WHERE id = @id`);
    for (const id of ids) stmt.run({ id, into });
  }

  // ---- steps ----
  createStep(runId: string, seq: number): StepRow {
    const id = `${runId}:${seq}`;
    this.run(
      `INSERT INTO steps (id, run_id, seq, phase, started_at) VALUES (@id, @runId, @seq, 'inferring', @now)`,
      { id, runId, seq, now: nowIso() },
    );
    this.updateRun(runId, { next_step_seq: seq + 1 });
    return this.getStep(id)!;
  }
  getStep(id: string): StepRow | undefined {
    return this.get(`SELECT * FROM steps WHERE id = @id`, { id });
  }
  lastStep(runId: string): StepRow | undefined {
    return this.get(`SELECT * FROM steps WHERE run_id = @runId ORDER BY seq DESC LIMIT 1`, { runId });
  }
  recentSteps(runId: string, n: number): StepRow[] {
    return this.all(`SELECT * FROM steps WHERE run_id = @runId ORDER BY seq DESC LIMIT @n`, { runId, n }).reverse() as StepRow[];
  }
  updateStep(id: string, patch: Partial<StepRow>): void {
    this.update("steps", "id", id, patch);
  }

  // ---- intents ----
  addIntent(p: Omit<IntentRow, "id" | "created_at" | "committed_at" | "status" | "post_hash" | "final_path" | "staging_path"> & {
    id?: string;
    staging_path?: string | null;
  }): IntentRow {
    const row: IntentRow = {
      post_hash: null,
      final_path: null,
      staging_path: null,
      ...p,
      id: p.id ?? ulid(),
      status: "prepared",
      created_at: nowIso(),
      committed_at: null,
    };
    this.run(
      `INSERT INTO intents (id, step_id, batch_item_id, path, op, op_digest, pre_hash, post_hash, backup_path, staging_path,
                            final_path, status, created_at, committed_at)
       VALUES (@id, @step_id, @batch_item_id, @path, @op, @op_digest, @pre_hash, @post_hash, @backup_path, @staging_path,
               @final_path, @status, @created_at, @committed_at)`,
      row as unknown as Params,
    );
    return row;
  }
  getIntent(id: string): IntentRow | undefined {
    return this.get(`SELECT * FROM intents WHERE id = @id`, { id });
  }
  openIntents(): IntentRow[] {
    return this.all(`SELECT * FROM intents WHERE status IN ('prepared','staged') ORDER BY created_at`);
  }
  intentsForStep(stepId: string): IntentRow[] {
    return this.all(`SELECT * FROM intents WHERE step_id = @stepId ORDER BY created_at, rowid`, { stepId });
  }
  intentsForRun(runId: string): IntentRow[] {
    return this.all(`SELECT * FROM intents WHERE step_id LIKE @p ORDER BY created_at, rowid`, { p: `${runId}:%` });
  }
  updateIntent(id: string, patch: Partial<IntentRow>): void {
    this.update("intents", "id", id, patch);
  }

  // ---- approvals / questions ----
  /** 承認・質問の短い ID（ap-12 / q-3）。コマンドに会話 ID が含まれないため、全体で一意にする。 */
  nextShortId(table: "approvals" | "questions", prefix: string): string {
    const r = this.get<{ n: number }>(`SELECT COUNT(*) AS n FROM ${table}`);
    return `${prefix}-${(r?.n ?? 0) + 1}`;
  }
  addApproval(row: Omit<ApprovalRow, "decision" | "remember" | "decided_at" | "executed_step" | "delivered">): void {
    this.run(
      `INSERT INTO approvals (id, conversation_id, run_id, step_id, request, tainted)
       VALUES (@id, @conversation_id, @run_id, @step_id, @request, @tainted)`,
      row as unknown as Params,
    );
  }
  getApproval(conversationId: string, id: string): ApprovalRow | undefined {
    return this.get(`SELECT * FROM approvals WHERE conversation_id = @c AND id = @id`, { c: conversationId, id });
  }
  findApproval(id: string): ApprovalRow | undefined {
    return this.get(`SELECT * FROM approvals WHERE id = @id ORDER BY rowid DESC LIMIT 1`, { id });
  }
  pendingApprovals(runId: string): ApprovalRow[] {
    return this.all(`SELECT * FROM approvals WHERE run_id = @runId AND decision IS NULL`, { runId });
  }
  undeliveredApprovals(runId: string): ApprovalRow[] {
    return this.all(`SELECT * FROM approvals WHERE run_id = @runId AND decision IS NOT NULL AND delivered = 0`, { runId });
  }
  updateApproval(conversationId: string, id: string, patch: Partial<ApprovalRow>): void {
    const keys = Object.keys(patch);
    this.run(
      `UPDATE approvals SET ${keys.map((k) => `${k} = @${k}`).join(", ")} WHERE conversation_id = @__c AND id = @__id`,
      { ...(patch as Params), __c: conversationId, __id: id },
    );
  }
  addQuestion(row: Omit<QuestionRow, "answer" | "answered_at" | "delivered">): void {
    this.run(
      `INSERT INTO questions (id, conversation_id, run_id, step_id, question, choices, secret)
       VALUES (@id, @conversation_id, @run_id, @step_id, @question, @choices, @secret)`,
      row as unknown as Params,
    );
  }
  findQuestion(id: string): QuestionRow | undefined {
    return this.get(`SELECT * FROM questions WHERE id = @id ORDER BY rowid DESC LIMIT 1`, { id });
  }
  pendingQuestions(runId: string): QuestionRow[] {
    return this.all(`SELECT * FROM questions WHERE run_id = @runId AND answered_at IS NULL`, { runId });
  }
  undeliveredAnswers(runId: string): QuestionRow[] {
    return this.all(`SELECT * FROM questions WHERE run_id = @runId AND answered_at IS NOT NULL AND delivered = 0`, {
      runId,
    });
  }
  updateQuestion(conversationId: string, id: string, patch: Partial<QuestionRow>): void {
    const keys = Object.keys(patch);
    this.run(
      `UPDATE questions SET ${keys.map((k) => `${k} = @${k}`).join(", ")} WHERE conversation_id = @__c AND id = @__id`,
      { ...(patch as Params), __c: conversationId, __id: id },
    );
  }

  // ---- batches ----
  addBatch(row: Omit<BatchRow, "created_at" | "ended_at" | "output_intent_id">): void {
    this.run(
      `INSERT INTO batches (id, run_id, step_id, spec, total, status, created_at)
       VALUES (@id, @run_id, @step_id, @spec, @total, @status, @now)`,
      { ...(row as unknown as Params), now: nowIso() },
    );
  }
  getBatch(id: string): BatchRow | undefined {
    return this.get(`SELECT * FROM batches WHERE id = @id`, { id });
  }
  batchForStep(stepId: string): BatchRow | undefined {
    return this.get(`SELECT * FROM batches WHERE step_id = @stepId`, { stepId });
  }
  updateBatch(id: string, patch: Partial<BatchRow>): void {
    this.update("batches", "id", id, patch);
  }
  addBatchItems(batchId: string, items: string[]): void {
    const stmt = this.db.prepare(
      `INSERT INTO batch_items (id, batch_id, idx, item, status) VALUES (@id, @batchId, @idx, @item, 'pending')`,
    );
    items.forEach((item, idx) => stmt.run({ id: ulid(), batchId, idx, item }));
  }
  batchItems(batchId: string): BatchItemRow[] {
    return this.all(`SELECT * FROM batch_items WHERE batch_id = @batchId ORDER BY idx`, { batchId });
  }
  updateBatchItem(id: string, patch: Partial<BatchItemRow>): void {
    this.update("batch_items", "id", id, patch);
  }

  // ---- memory / plans ----
  setMemory(conversationId: string, key: string, value: string): void {
    this.run(
      `INSERT INTO memory (conversation_id, key, value, updated_at) VALUES (@c, @key, @value, @now)
       ON CONFLICT(conversation_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
      { c: conversationId, key, value, now: nowIso() },
    );
  }
  deleteMemory(conversationId: string, key: string): void {
    this.run(`DELETE FROM memory WHERE conversation_id = @c AND key = @key`, { c: conversationId, key });
  }
  listMemory(conversationId: string): { key: string; value: string }[] {
    return this.all(`SELECT key, value FROM memory WHERE conversation_id = @c ORDER BY updated_at`, { c: conversationId });
  }
  setPlan(runId: string, plan: unknown): void {
    this.run(
      `INSERT INTO plans (run_id, plan, updated_at) VALUES (@runId, @plan, @now)
       ON CONFLICT(run_id) DO UPDATE SET plan = excluded.plan, updated_at = excluded.updated_at`,
      { runId, plan: JSON.stringify(plan), now: nowIso() },
    );
  }
  getPlan(runId: string): unknown[] {
    const r = this.get<{ plan: string }>(`SELECT plan FROM plans WHERE run_id = @runId`, { runId });
    return r ? (JSON.parse(r.plan) as unknown[]) : [];
  }

  // ---- events ----
  appendEvent(conversationId: string, type: string, payload: unknown): EventRow {
    const r = this.get<{ m: number | null }>(`SELECT MAX(seq) AS m FROM events WHERE conversation_id = @c`, {
      c: conversationId,
    });
    const row: EventRow = {
      conversation_id: conversationId,
      seq: (r?.m ?? 0) + 1,
      type,
      payload: JSON.stringify(payload),
      created_at: nowIso(),
    };
    this.run(
      `INSERT INTO events (conversation_id, seq, type, payload, created_at) VALUES (@conversation_id, @seq, @type, @payload, @created_at)`,
      row as unknown as Params,
    );
    this.run(
      `DELETE FROM events WHERE conversation_id = @c AND seq <= @cut`,
      { c: conversationId, cut: row.seq - 5000 },
    );
    return row;
  }
  eventsSince(conversationId: string, seq: number): EventRow[] {
    return this.all(`SELECT * FROM events WHERE conversation_id = @c AND seq > @seq ORDER BY seq`, {
      c: conversationId,
      seq,
    });
  }

  // ---- audit / settings ----
  addAudit(record: Record<string, unknown>): void {
    this.run(`INSERT INTO audit_outbox (id, record) VALUES (@id, @record)`, {
      id: ulid(),
      record: JSON.stringify(record),
    });
  }
  unsentAudit(limit = 200): { id: string; record: string }[] {
    return this.all(`SELECT id, record FROM audit_outbox WHERE sent_at IS NULL LIMIT @limit`, { limit });
  }
  markAuditSent(ids: string[]): void {
    const stmt = this.db.prepare(`UPDATE audit_outbox SET sent_at = @now WHERE id = @id`);
    const now = nowIso();
    for (const id of ids) stmt.run({ id, now });
  }
  getSetting<T>(key: string, fallback: T): T {
    const r = this.get<{ value: string }>(`SELECT value FROM settings WHERE key = @key`, { key });
    return r ? (JSON.parse(r.value) as T) : fallback;
  }
  setSetting(key: string, value: unknown): void {
    this.run(
      `INSERT INTO settings (key, value) VALUES (@key, @value) ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
      { key, value: JSON.stringify(value) },
    );
  }
}
