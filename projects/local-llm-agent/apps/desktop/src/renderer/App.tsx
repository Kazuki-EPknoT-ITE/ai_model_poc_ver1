import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RuntimeCommand, RuntimeEvent } from "@lla/protocol";
import { applyEvent, emptyConversation, REASON_LABEL, STATE_LABEL, TOOL_LABEL, type ConversationView } from "./state.js";

const api = window.app;
let cmdSeq = 0;
const cmd = (type: string, payload: Record<string, unknown>): RuntimeCommand => ({ v: 1, id: `r${++cmdSeq}`, type: type as RuntimeCommand["type"], payload });

interface ConvRow {
  id: string;
  title: string;
  workspace: string;
  updated_at: string;
}
interface MessageRow {
  id: string;
  role: string;
  content: string;
  run_id: string | null;
}

export function App() {
  const [status, setStatus] = useState<{ loggedIn: boolean; user?: { display_name: string }; serverUrl: string } | null>(null);
  const refresh = useCallback(() => void api.auth.status().then(setStatus), []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  if (!status) return <div className="splash">読み込み中…</div>;
  if (!status.loggedIn) return <Login serverUrl={status.serverUrl} onDone={refresh} />;
  return <Main userName={status.user?.display_name ?? ""} onLogout={refresh} />;
}

function Login({ serverUrl, onDone }: { serverUrl: string; onDone: () => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await api.auth.login({ login, password, remember });
    setBusy(false);
    if (r.ok) onDone();
    else setError(r.error ?? "ログインに失敗しました");
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={submit} aria-label="ログイン">
        <h1>ローカルAIアシスタント</h1>
        <p className="muted">社内サーバー: {serverUrl}</p>
        <label>
          ログインID
          <input autoFocus value={login} onChange={(e) => setLogin(e.target.value)} name="login" autoComplete="username" />
        </label>
        <label>
          パスワード
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} name="password" autoComplete="current-password" />
        </label>
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          ログイン状態を保持する
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || !login || !password}>
          {busy ? "ログイン中…" : "ログイン"}
        </button>
      </form>
    </div>
  );
}

function Main({ userName, onLogout }: { userName: string; onLogout: () => void }) {
  const [convs, setConvs] = useState<ConvRow[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<string>("");
  const [views, setViews] = useState<Record<string, ConversationView>>({});
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [hostState, setHostState] = useState<string>("starting");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [netLevel, setNetLevel] = useState<"off" | "search" | "browse">("off");
  const [model, setModel] = useState<"auto" | "fast" | "accurate">("auto");
  const currentRef = useRef<string | null>(null);
  currentRef.current = current;

  const loadConvs = useCallback(async () => {
    const list = (await api.runtime.send(cmd("conversation.list", {}))) as ConvRow[];
    setConvs(list);
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    const r = (await api.runtime.send(cmd("conversation.get", { conversation_id: id }))) as { conversation?: ConvRow; messages: MessageRow[] };
    setMessages(r.messages.filter((m) => m.role === "user"));
    if (r.conversation) setWorkspace(r.conversation.workspace);
    const evs = (await api.runtime.send(cmd("events.since", { conversation_id: id, seq: 0 }))) as RuntimeEvent[];
    setViews((v) => ({ ...v, [id]: evs.reduce(applyEvent, emptyConversation()) }));
  }, []);

  useEffect(() => {
    const offEvent = api.runtime.onEvent((e) => {
      if (!e.conversation_id) return;
      const id = e.conversation_id;
      setViews((v) => ({ ...v, [id]: applyEvent(v[id] ?? emptyConversation(), e) }));
      if (e.type === "run.state") void loadConvs();
    });
    const offStatus = api.runtime.onStatus((s) => {
      setHostState(s.state);
      if (s.warnings?.length) setWarnings(s.warnings);
      if (s.state === "ready") {
        void loadConvs();
        if (currentRef.current) void loadConversation(currentRef.current);
      }
    });
    void loadConvs().then(() => setHostState("ready")).catch(() => undefined);
    return () => {
      offEvent();
      offStatus();
    };
  }, [loadConvs, loadConversation]);

  const view = current ? (views[current] ?? emptyConversation()) : emptyConversation();
  const running = view.state !== null && !["done", "stopped"].includes(view.state);

  const newConversation = () => {
    setCurrent(null);
    setMessages([]);
    setWorkspace("");
  };
  const openConversation = (id: string) => {
    setCurrent(id);
    void loadConversation(id);
  };
  const pickFolder = async () => {
    const f = await api.dialog.pickFolder();
    if (f) setWorkspace(f);
  };
  const changeNet = async (level: "off" | "search" | "browse") => {
    setNetLevel(level);
    await api.runtime.send(cmd("settings.update", { net_level: level }));
  };

  const send = async (text: string) => {
    const id = current ?? `cv-${Date.now().toString(36)}`;
    if (!current) setCurrent(id);
    setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", content: text, run_id: null }]);
    await api.runtime.send(cmd("run.start", { conversation_id: id, text, attachments: [], workspace, model }));
    void loadConvs();
  };

  return (
    <div className="shell">
      <aside className="sidebar" aria-label="会話とファイル">
        <div className="brand">
          <span className="dot" aria-hidden />
          ローカルAIアシスタント
        </div>
        <button className="primary block" onClick={newConversation}>
          ＋ 新しい会話
        </button>
        <nav className="conv-list" aria-label="会話の履歴">
          {convs.map((c) => (
            <button key={c.id} className={`conv ${c.id === current ? "active" : ""}`} onClick={() => openConversation(c.id)} title={c.workspace}>
              <span className="conv-title">{c.title}</span>
              {views[c.id]?.state && !["done", "stopped"].includes(views[c.id]!.state!) && <span className="badge run">実行中</span>}
            </button>
          ))}
          {convs.length === 0 && <p className="muted small">まだ会話はありません</p>}
        </nav>
        {workspace && <FileTree root={workspace} refreshKey={view.artifacts.length} />}
        <div className="user-row">
          <span>{userName}</span>
          <button
            className="link"
            onClick={async () => {
              if (running && !confirm("実行中の作業があります。ログアウトすると作業は止まります。よろしいですか？")) return;
              await api.auth.logout();
              onLogout();
            }}
          >
            ログアウト
          </button>
        </div>
      </aside>

      <main className="center">
        <header className="topbar">
          <button className="folder" onClick={pickFolder} disabled={Boolean(current && messages.length)} title="作業フォルダ">
            📁 {workspace || "作業フォルダを選ぶ"}
          </button>
          <div className="spacer" />
          <label className="inline">
            モデル
            <select value={model} onChange={(e) => setModel(e.target.value as typeof model)} aria-label="モデル">
              <option value="auto">自動</option>
              <option value="fast">高速</option>
              <option value="accurate">高精度</option>
            </select>
          </label>
          <label className="inline">
            ネット
            <select value={netLevel} onChange={(e) => void changeNet(e.target.value as typeof netLevel)} aria-label="ネットアクセス">
              <option value="off">オフ</option>
              <option value="search">検索のみ</option>
              <option value="browse">検索＋閲覧</option>
            </select>
          </label>
          <span className={`net-badge ${netLevel}`} data-testid="net-badge">
            {netLevel === "off" ? "ネット: オフ" : "ネット接続中"}
          </span>
          <span className={`server ${hostState}`} data-testid="server-status">
            {hostState === "ready" ? "サーバー接続" : hostState === "restarting" ? "再起動中" : hostState === "stopped" ? "停止" : "起動中"}
          </span>
        </header>
        {warnings.length > 0 && (
          <div className="warn-bar" role="status">
            {warnings.join(" / ")}
            <button className="link" onClick={() => setWarnings([])}>
              閉じる
            </button>
          </div>
        )}
        <Timeline view={view} messages={messages} />
        <Composer
          disabled={!workspace || hostState !== "ready"}
          placeholder={workspace ? "依頼を入力（Ctrl+Enter で送信）" : "先に作業フォルダを選んでください"}
          running={running}
          paused={view.state === "paused"}
          onSend={send}
          onPause={() => view.runId && void api.runtime.send(cmd(view.state === "paused" ? "run.resume" : "run.pause", { run_id: view.runId }))}
          onStop={() => view.runId && void api.runtime.send(cmd("run.stop", { run_id: view.runId }))}
        />
      </main>

      <aside className="panel" aria-label="計画と承認">
        <StatusCard view={view} />
        <Plan view={view} />
        <Approvals view={view} />
        <Questions view={view} />
        <Changes view={view} />
      </aside>
    </div>
  );
}

function FileTree({ root, refreshKey }: { root: string; refreshKey: number }) {
  const [entries, setEntries] = useState<{ name: string; path: string; dir: boolean }[]>([]);
  useEffect(() => {
    void api.workspace.list(root).then(setEntries);
  }, [root, refreshKey]);
  return (
    <section className="tree" aria-label="作業フォルダのファイル">
      <h2>ファイル</h2>
      <ul>
        {entries.map((e) => (
          <li key={e.path}>
            <button className="link file" onDoubleClick={() => !e.dir && void api.shell.openPath(e.path)} title="ダブルクリックで開く">
              {e.dir ? "📁" : "📄"} {e.name}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Timeline({ view, messages }: { view: ConversationView; messages: MessageRow[] }) {
  const end = useRef<HTMLDivElement>(null);
  // 新しい Chromium では scrollIntoView が Promise を返すので、戻り値を effect の後片付けとして返さない
  useEffect(() => {
    void end.current?.scrollIntoView({ block: "end" });
  }, [view.steps.length, messages.length, view.finalSummary]);
  const items = useMemo(() => view.steps.filter((s) => s.kind !== "system" || s.status === "error"), [view.steps]);
  return (
    <section className="timeline" aria-live="polite" aria-label="会話">
      {messages.length === 0 && items.length === 0 && (
        <div className="empty">
          <h2>何をしましょうか？</h2>
          <p className="muted">例: 「請求書フォルダのPDFを読んで、送金明細をExcelにまとめて」</p>
        </div>
      )}
      {messages.map((m) => (
        <div key={m.id} className="bubble user">
          {m.content}
        </div>
      ))}
      {items.map((s) => (
        <details key={`${s.stepId}:${s.kind}`} className={`step ${s.status}`} data-testid="step-card">
          <summary>
            <span className={`st ${s.status}`} aria-label={s.status} />
            {s.kind === "tool" ? (TOOL_LABEL[s.tool ?? ""] ?? s.tool) : s.kind === "plan" ? "計画を更新" : s.kind === "ask" ? "質問" : s.kind === "final" ? "完了報告" : "出力の形式エラー（自動でやり直します）"}
            {s.status === "deferred" && <span className="badge warn">保留中</span>}
          </summary>
          {s.argsPreview && <pre className="pre">{s.argsPreview}</pre>}
          {s.resultPreview && <pre className="pre result">{s.resultPreview}</pre>}
        </details>
      ))}
      {view.net.map((n, i) => (
        <div key={`net${i}`} className="net-line" data-testid="net-activity">
          🌐 {n.kind === "search" ? `検索: ${n.query}` : `閲覧: ${n.url}`} {n.blocked && <span className="badge err">送信しませんでした</span>}
        </div>
      ))}
      {view.finalSummary !== null && (
        <div className="bubble ai" data-testid="final-summary">
          {view.finalSummary}
        </div>
      )}
      <div ref={end} />
    </section>
  );
}

function Composer(p: {
  disabled: boolean;
  placeholder: string;
  running: boolean;
  paused: boolean;
  onSend: (t: string) => void;
  onPause: () => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const submit = () => {
    if (!text.trim() || p.disabled) return;
    p.onSend(text.trim());
    setText("");
  };
  return (
    <div className="composer">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={p.placeholder}
        disabled={p.disabled}
        aria-label="依頼"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-actions">
        {p.running && (
          <>
            <button onClick={p.onPause}>{p.paused ? "再開" : "一時停止"}</button>
            <button className="danger" onClick={p.onStop}>
              停止
            </button>
          </>
        )}
        <button className="primary" onClick={submit} disabled={p.disabled || !text.trim()}>
          {p.running ? "追加の指示を送る" : "送信"}
        </button>
      </div>
    </div>
  );
}

function StatusCard({ view }: { view: ConversationView }) {
  if (!view.state) return null;
  return (
    <section className={`card status ${view.state}`} data-testid="run-state" data-state={view.state}>
      <strong>{STATE_LABEL[view.state] ?? view.state}</strong>
      {view.queue && view.queue.position > 0 && (
        <p className="small">
          順番待ち {view.queue.position} 番目（約 {view.queue.eta_seconds} 秒）
        </p>
      )}
      {view.retry && <p className="small">再接続中（{view.retry.attempt} 回目）</p>}
      {view.batch && (
        <p className="small">
          一括処理 {view.batch.done} / {view.batch.total}
          {view.batch.failed > 0 && `（失敗 ${view.batch.failed}）`}
        </p>
      )}
    </section>
  );
}

function Plan({ view }: { view: ConversationView }) {
  if (view.plan.length === 0) return null;
  return (
    <section className="card">
      <h2>計画</h2>
      <ol className="plan">
        {view.plan.map((p) => (
          <li key={p.id} className={p.status}>
            {p.title}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Approvals({ view }: { view: ConversationView }) {
  const [decided, setDecided] = useState<Record<string, string>>({});
  const [remember, setRemember] = useState<Record<string, boolean>>({});
  const pending = view.approvals.filter((a) => !decided[a.approval_id]);
  if (pending.length === 0) return null;
  const decide = async (id: string, decision: "approve" | "reject", rem: boolean) => {
    setDecided((d) => ({ ...d, [id]: decision }));
    await api.runtime.send(cmd("approval.decide", { approval_id: id, decision, ...(rem ? { remember: "this_run" } : {}) }));
  };
  return (
    <section className="card approvals" aria-label="承認待ち">
      <h2>承認待ち（{pending.length}）</h2>
      {pending.map((a) => (
        <div key={a.approval_id} className="approval" data-testid="approval" role="group" aria-label={`承認 ${a.approval_id}`}>
          <p>
            <strong>{TOOL_LABEL[a.tool] ?? a.tool}</strong>（{REASON_LABEL[a.reason] ?? a.reason}）
          </p>
          <ul className="paths">
            {a.paths.map((p) => (
              <li key={p.real_path}>
                {p.path}
                {p.real_path.toLowerCase() !== p.path.toLowerCase() && !p.inside_workspace && <span className="real">→ 実際の場所: {p.real_path}</span>}
              </li>
            ))}
          </ul>
          {a.tainted && <p className="warn small">Web の情報を読んだ後の操作です。内容をよく確認してください。</p>}
          {!a.tainted && a.reason !== "delete" && a.reason !== "move" && (
            <label className="check small">
              <input type="checkbox" checked={Boolean(remember[a.approval_id])} onChange={(e) => setRemember((r) => ({ ...r, [a.approval_id]: e.target.checked }))} />
              この作業中は同じ操作を自動で承認する
            </label>
          )}
          <div className="row">
            {/* 既定のフォーカスは「却下」（UI 仕様 §6） */}
            <button autoFocus onClick={() => void decide(a.approval_id, "reject", false)}>
              却下
            </button>
            <button className="primary" onClick={() => void decide(a.approval_id, "approve", Boolean(remember[a.approval_id]))}>
              承認
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function Questions({ view }: { view: ConversationView }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Record<string, boolean>>({});
  const open = view.questions.filter((q) => !sent[q.question_id]);
  if (open.length === 0) return null;
  const answer = async (id: string, text: string, secret: boolean) => {
    setSent((s) => ({ ...s, [id]: true }));
    await api.runtime.send(cmd("question.answer", { question_id: id, answer: text, ...(secret ? { secret: true } : {}) }));
  };
  return (
    <section className="card" aria-label="質問">
      <h2>AIからの質問</h2>
      {open.map((q) => (
        <div key={q.question_id} className="question" data-testid="question">
          <p>{q.question}</p>
          {q.choices ? (
            <div className="row wrap">
              {q.choices.map((c) => (
                <button key={c} onClick={() => void answer(q.question_id, c, false)}>
                  {c}
                </button>
              ))}
            </div>
          ) : (
            <div className="row">
              <input
                type={q.secret ? "password" : "text"}
                value={answers[q.question_id] ?? ""}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.question_id]: e.target.value }))}
                aria-label="回答"
              />
              <button className="primary" onClick={() => void answer(q.question_id, answers[q.question_id] ?? "", q.secret)}>
                回答
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

function Changes({ view }: { view: ConversationView }) {
  const [msg, setMsg] = useState<string | null>(null);
  if (view.artifacts.length === 0) return null;
  const undo = async () => {
    if (!view.runId || !confirm("この作業で変更したファイルを、変更前の状態に戻します。よろしいですか？")) return;
    const r = (await api.runtime.send(cmd("undo.run", { run_id: view.runId }))) as { restored: string[]; warnings: string[] };
    setMsg(`${r.restored.length} 件を元に戻しました${r.warnings.length ? `（${r.warnings.join(" / ")}）` : ""}`);
  };
  return (
    <section className="card" aria-label="変更したファイル">
      <h2>変更したファイル（{view.artifacts.length}）</h2>
      <ul className="changes">
        {view.artifacts.map((a) => (
          <li key={a.path} data-testid="artifact">
            <span className={`op ${a.op}`}>{a.op === "create" ? "新規" : a.op === "delete" ? "削除" : a.op === "copy" ? "コピー" : "変更"}</span>
            <button className="link file" onClick={() => void api.shell.openPath(a.path)}>
              {a.path.split(/[\\/]/).pop()}
            </button>
            {a.renamed_from && <span className="small warn">（使用中のため別名で保存）</span>}
          </li>
        ))}
      </ul>
      <button onClick={undo}>この作業を元に戻す</button>
      {msg && <p className="small" role="status">{msg}</p>}
    </section>
  );
}
