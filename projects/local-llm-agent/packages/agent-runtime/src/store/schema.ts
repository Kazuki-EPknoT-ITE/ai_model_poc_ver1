// Run Store の DDL（local_llm_data_model.md §2.2）。マイグレーションは配列の順に1回ずつ適用する。
export const MIGRATIONS: string[] = [
  /* 001 */ `
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  workspace TEXT NOT NULL,
  model_pref TEXT NOT NULL DEFAULT 'auto' CHECK (model_pref IN ('auto','fast','accurate')),
  tainted INTEGER NOT NULL DEFAULT 0,
  tainted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  state TEXT NOT NULL,
  model_current TEXT NOT NULL,
  escalated_at TEXT,
  stall_level INTEGER NOT NULL DEFAULT 0,
  replan_count INTEGER NOT NULL DEFAULT 0,
  low_priority INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT CHECK (end_reason IN ('final','user_stop')),
  long_run_notified_at TEXT,
  context_tokens INTEGER NOT NULL DEFAULT 0,
  next_step_seq INTEGER NOT NULL DEFAULT 1,
  final_action TEXT
);
CREATE INDEX runs_active ON runs(state) WHERE ended_at IS NULL;
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  run_id TEXT REFERENCES runs(id),
  step_id TEXT,
  role TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system_note')),
  content TEXT NOT NULL,
  tokens INTEGER NOT NULL,
  compacted_into TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX messages_conv ON messages(conversation_id, created_at);
CREATE TABLE steps (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  seq INTEGER NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('inferring','dispatched','policy_checked','deferred','tool_running','committed','failed_permanent')),
  attempt INTEGER NOT NULL DEFAULT 0,
  gateway_job_id TEXT,
  action TEXT,
  tool TEXT,
  args_digest TEXT,
  result TEXT,
  result_digest TEXT,
  error_code TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  UNIQUE (run_id, seq)
);
CREATE TABLE intents (
  id TEXT PRIMARY KEY,
  step_id TEXT NOT NULL,
  batch_item_id TEXT,
  path TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('create','overwrite','delete','move','copy')),
  op_digest TEXT NOT NULL,
  pre_hash TEXT,
  post_hash TEXT,
  backup_path TEXT,
  staging_path TEXT,
  final_path TEXT,
  status TEXT NOT NULL CHECK (status IN ('prepared','staged','committed','conflict','undone')),
  created_at TEXT NOT NULL,
  committed_at TEXT
);
CREATE INDEX intents_open ON intents(status) WHERE status IN ('prepared','staged');
CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id),
  step_id TEXT NOT NULL,
  request TEXT NOT NULL,
  tainted INTEGER NOT NULL,
  decision TEXT CHECK (decision IN ('approve','reject')),
  remember TEXT,
  decided_at TEXT,
  executed_step TEXT,
  delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE questions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id),
  step_id TEXT NOT NULL,
  question TEXT NOT NULL,
  choices TEXT,
  secret INTEGER NOT NULL DEFAULT 0,
  answer TEXT,
  answered_at TEXT,
  delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE batches (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  step_id TEXT NOT NULL,
  spec TEXT NOT NULL,
  total INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','writing_output','done')),
  output_intent_id TEXT,
  created_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE batch_items (
  id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES batches(id),
  idx INTEGER NOT NULL,
  item TEXT NOT NULL,
  item_hash TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','running','ok','failed')),
  result TEXT,
  error_code TEXT,
  error_reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE (batch_id, idx)
);
CREATE TABLE memory (
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (conversation_id, key)
);
CREATE TABLE plans (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  plan TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE events (
  conversation_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (conversation_id, seq)
);
CREATE TABLE audit_outbox (
  id TEXT PRIMARY KEY,
  record TEXT NOT NULL,
  sent_at TEXT
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
`,
];
