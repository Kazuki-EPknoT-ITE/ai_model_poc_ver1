import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RuntimeCommand, RuntimeEvent } from "@lla/protocol";
import { canOpen, citationPath, pageOf } from "../shared/open-policy.js";
import { applyEvent, emptyConversation, groupTurns, readableFromPartial, REASON_LABEL, STATE_LABEL, TOOL_LABEL, type ConversationView, type RunView, type Turn, type UnresolvedView } from "./state.js";
import { Composer, type Macro } from "./components/Composer.js";
import { DiffDialog, type ArtifactDiff } from "./components/DiffDialog.js";
import { SettingsDialog } from "./components/SettingsDialog.js";
import { RunActivity } from "./components/RunActivity.js";
import { Markdown } from "./components/Markdown.js";
import { Icon, Spinner } from "./components/Icon.js";

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

/** 空の会話に出す例の依頼（押すと入力欄に入る） */
const EXAMPLES = ["メモをCSVにまとめる", "Excelの単価を直す", "Wordの文書を作る", "389×125は？"];

const isEnded = (state: string | null | undefined) => state === "done" || state === "stopped";

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
        <span className="brand-mark large" aria-hidden="true">
          <Icon name="mark" size={22} />
        </span>
        <h1>ローカルAIアシスタント</h1>
        <p className="muted small login-server">社内サーバー: {serverUrl}</p>
        <label className="field">
          ログインID
          <input autoFocus value={login} onChange={(e) => setLogin(e.target.value)} name="login" autoComplete="username" />
        </label>
        <label className="field">
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
        <button type="submit" className="primary wide" disabled={busy || !login || !password}>
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
  const [spareActive, setSpareActive] = useState<string | null>(null);
  // 窓が狭いときはサイドバーを畳んだ状態で始める
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 900);
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  // 自動更新（ADM-05）
  const [update, setUpdate] = useState<{ version: string; notes: string } | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  useEffect(() => {
    void api.update.status().then((u) => u && setUpdate(u));
    return api.update.onReady(setUpdate);
  }, []);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [netLevel, setNetLevel] = useState<"off" | "search" | "browse">("off");
  const [model, setModel] = useState<"auto" | "fast" | "accurate">("auto");
  const [macros, setMacros] = useState<Macro[]>([]);
  const [netCap, setNetCap] = useState<"off" | "search" | "browse">("off");
  const [showSettings, setShowSettings] = useState(false);
  const currentRef = useRef<string | null>(null);
  currentRef.current = current;

  const loadConfig = useCallback(async () => {
    const c = (await api.runtime.send(cmd("config.get", {}))) as { macros: Macro[]; net_level_cap: "off" | "search" | "browse" };
    setMacros(c.macros);
    setNetCap(c.net_level_cap);
    const s = (await api.runtime.send(cmd("settings.get", {}))) as { net_level: "off" | "search" | "browse"; model_default: "auto" | "fast" | "accurate" };
    setNetLevel(s.net_level);
    setModel(s.model_default);
  }, []);

  const loadConvs = useCallback(async () => {
    const list = (await api.runtime.send(cmd("conversation.list", {}))) as ConvRow[];
    setConvs(list);
  }, []);

  // 会話の履歴の検索（APP-06）: 題名と、自分が送った依頼の文から探す
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ConvRow[] | null>(null);
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setFound(null);
      return;
    }
    const t = setTimeout(() => {
      void api.runtime.send(cmd("conversation.search", { q })).then((r) => setFound(r as ConvRow[]));
    }, 200);
    return () => clearTimeout(t);
  }, [query]);
  const shown = found ?? convs;

  const loadConversation = useCallback(async (id: string) => {
    const r = (await api.runtime.send(cmd("conversation.get", { conversation_id: id }))) as { conversation?: ConvRow; messages: MessageRow[] };
    setMessages(r.messages.filter((m) => m.role === "user"));
    if (r.conversation) setWorkspace(r.conversation.workspace);
    const evs = (await api.runtime.send(cmd("events.since", { conversation_id: id, seq: 0 }))) as RuntimeEvent[];
    setViews((v) => ({ ...v, [id]: evs.reduce(applyEvent, emptyConversation()) }));
  }, []);

  useEffect(() => {
    const offEvent = api.runtime.onEvent((e) => {
      // 予備機への切替・切り戻し（OPS-03）
      if (e.type === "server.status") {
        const p = e.payload as { state?: string; url?: string };
        if (p.state === "failover") setSpareActive(p.url ?? "予備機");
        else if (p.state === "primary") setSpareActive(null);
      }
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
        void loadConfig().catch(() => undefined);
        if (currentRef.current) void loadConversation(currentRef.current);
      }
    });
    void loadConvs()
      .then(() => {
        setHostState("ready");
        return loadConfig();
      })
      .catch(() => undefined);
    return () => {
      offEvent();
      offStatus();
    };
  }, [loadConvs, loadConversation, loadConfig]);

  const view = current ? (views[current] ?? emptyConversation()) : emptyConversation();
  const running = view.state !== null && !isEnded(view.state);
  const title = (current && convs.find((c) => c.id === current)?.title) || "新しい会話";

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

  const send = async (text: string, attachments: string[] = []) => {
    const id = current ?? `cv-${Date.now().toString(36)}`;
    if (!current) setCurrent(id);
    setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", content: text, run_id: null }]);
    await api.runtime.send(cmd("run.start", { conversation_id: id, text, attachments, workspace, model }));
    void loadConvs();
  };

  const serverLabel = hostState === "ready" ? "サーバー接続" : hostState === "restarting" ? "再起動中" : hostState === "stopped" ? "停止" : "起動中";

  return (
    <div className={`shell ${sidebarOpen ? "" : "sidebar-closed"}`}>
      {sidebarOpen && (
        <aside className="sidebar" aria-label="会話とファイル">
          <div className="sidebar-head">
            <span className="brand">
              <span className="brand-mark" aria-hidden="true">
                <Icon name="mark" size={16} />
              </span>
              ローカルAIアシスタント
            </span>
            <button className="icon-btn" onClick={() => setSidebarOpen(false)} aria-label="サイドバーを閉じる" title="サイドバーを閉じる">
              <Icon name="sidebar" size={18} />
            </button>
          </div>
          <button className="new-conv" onClick={newConversation}>
            <Icon name="plus" size={16} />
            新しい会話
          </button>
          <div className="search-box">
            <Icon name="search" size={15} />
            <input type="search" className="conv-search" placeholder="会話を検索" aria-label="会話を検索" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <nav className="conv-list" aria-label="会話の履歴">
            <h2 className="side-heading">最近の会話</h2>
            {shown.map((c) => {
              const st = views[c.id]?.state;
              return (
                <button key={c.id} className={`conv ${c.id === current ? "active" : ""}`} onClick={() => openConversation(c.id)} title={c.workspace} aria-current={c.id === current ? "true" : undefined}>
                  <span className="conv-title">{c.title}</span>
                  {st && !isEnded(st) && (
                    <span className="badge run">
                      <Spinner size={11} />
                      実行中
                    </span>
                  )}
                </button>
              );
            })}
            {found !== null && found.length === 0 && <p className="muted small side-note">見つかりませんでした</p>}
            {found === null && convs.length === 0 && <p className="muted small side-note">まだ会話はありません</p>}
          </nav>
          {workspace && <FileTree root={workspace} refreshKey={view.artifacts.length} />}
          <div className="user-row">
            <span className="avatar" aria-hidden="true">
              {userName.slice(0, 1)}
            </span>
            <span className="user-name">{userName}</span>
            <button className="icon-btn" onClick={() => setShowSettings(true)} aria-label="設定" title="設定">
              <Icon name="settings" size={17} />
            </button>
            <button
              className="icon-btn"
              aria-label="ログアウト"
              title="ログアウト"
              onClick={async () => {
                if (running && !confirm("実行中の作業があります。ログアウトすると作業は止まります。よろしいですか？")) return;
                await api.auth.logout();
                onLogout();
              }}
            >
              <Icon name="logout" size={17} />
            </button>
          </div>
        </aside>
      )}

      <main className="main">
        <header className="topbar">
          {!sidebarOpen && (
            <>
              <button className="icon-btn" onClick={() => setSidebarOpen(true)} aria-label="サイドバーを開く" title="サイドバーを開く">
                <Icon name="sidebar" size={18} />
              </button>
              <button className="icon-btn" onClick={newConversation} aria-label="新しい会話" title="新しい会話">
                <Icon name="plus" size={18} />
              </button>
            </>
          )}
          <span className="topbar-title">{title}</span>
          <span className="spacer" />
          <span className={`net-badge ${netLevel}`} data-testid="net-badge">
            <Icon name="globe" size={13} />
            {netLevel === "off" ? "ネット: オフ" : "ネット接続中"}
          </span>
          <span className={`server ${hostState}`} data-testid="server-status">
            <span className="server-dot" aria-hidden="true" />
            {serverLabel}
          </span>
          {spareActive && (
            <span className="server spare" data-testid="spare-status" title={spareActive}>
              予備機で接続中
            </span>
          )}
        </header>
        {update && (
          <div className="banner info" role="status" data-testid="update-bar">
            <span className="banner-text">
              新しい版 {update.version} があります{update.notes ? `（${update.notes}）` : ""}。
            </span>
            <button
              className="primary small-btn"
              onClick={async () => {
                const r = await api.update.install();
                setUpdateError(r.ok ? null : (r.error ?? "更新できませんでした"));
              }}
            >
              再起動して更新
            </button>
            {updateError && <span className="error small">{updateError}</span>}
          </div>
        )}
        {warnings.length > 0 && (
          <div className="banner warn" role="status">
            <Icon name="alert" size={15} />
            <span className="banner-text">{warnings.join(" / ")}</span>
            <button className="link" onClick={() => setWarnings([])}>
              閉じる
            </button>
          </div>
        )}
        {(hostState === "restarting" || hostState === "stopped") && (
          <div className="banner err" role="status">
            <Icon name="alert" size={15} />
            <span className="banner-text">{hostState === "restarting" ? "作業の実行部を再起動しています。終わると自動で続きを表示します。" : "作業の実行部が止まっています。アプリを開き直してください。"}</span>
          </div>
        )}
        <Timeline
          view={view}
          messages={messages}
          workspace={workspace}
          userName={userName}
          onExample={(text) => setSeed((s) => ({ text, n: (s?.n ?? 0) + 1 }))}
        />
        <div className="composer-wrap">
          <div className="col">
            <Composer
              macros={macros}
              disabled={!workspace || hostState !== "ready"}
              placeholder={workspace ? "依頼を入力してください" : "先に作業フォルダを選んでください"}
              running={running}
              paused={view.state === "paused"}
              workspace={workspace}
              folderLocked={Boolean(current && messages.length)}
              model={model}
              netLevel={netLevel}
              seed={seed}
              onPickFolder={() => void pickFolder()}
              onModel={setModel}
              onNet={(n) => void changeNet(n)}
              onSend={send}
              onPause={() => view.runId && void api.runtime.send(cmd(view.state === "paused" ? "run.resume" : "run.pause", { run_id: view.runId }))}
              onStop={() => view.runId && void api.runtime.send(cmd("run.stop", { run_id: view.runId }))}
            />
            <p className="composer-hint">Ctrl+Enter で送信 ・ / で定型作業 ・ ファイルはドラッグして添付できます</p>
          </div>
        </div>
      </main>

      {showSettings && (
        <SettingsDialog
          send={(type, payload) => api.runtime.send(cmd(type, payload))}
          netCap={netCap}
          onSaved={(st) => {
            setNetLevel(st.net_level);
            setModel(st.model_default);
          }}
          onClose={() => setShowSettings(false)}
        />
      )}
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
      <h2 className="side-heading" title={root}>
        ファイル
      </h2>
      <ul>
        {entries.map((e) => (
          <li key={e.path}>
            <button className="file-item" onDoubleClick={() => !e.dir && void api.shell.openPath(e.path)} title="ダブルクリックで開く">
              <Icon name={e.dir ? "folder" : "file"} size={14} />
              <span className="file-name">{e.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Timeline({ view, messages, workspace, userName, onExample }: { view: ConversationView; messages: MessageRow[]; workspace: string | null; userName: string; onExample: (text: string) => void }) {
  const end = useRef<HTMLDivElement>(null);
  const turns = useMemo(() => groupTurns(messages, view), [messages, view]);
  // 新しい Chromium では scrollIntoView が Promise を返すので、戻り値を effect の後片付けとして返さない
  useEffect(() => {
    void end.current?.scrollIntoView({ block: "end" });
  }, [view.steps.length, messages.length, view.finalSummary, view.live?.raw.length, view.approvals.length, view.questions.length]);
  const lastRunTurn = [...turns].reverse().find((t) => t.runId)?.runId ?? null;
  return (
    <section className="timeline" aria-label="会話">
      <div className="col">
        {turns.length === 0 && (
          <div className="empty">
            <h1 className="greeting">{userName ? `${userName}さん、` : ""}何をお手伝いしましょうか？</h1>
            <p className="muted">作業フォルダを選んで、してほしいことを書いてください。ファイルの読み書きは、選んだフォルダの中で行います。</p>
            <ul className="examples" aria-label="使い方の例">
              {EXAMPLES.map((x) => (
                <li key={x}>
                  <button className="example" onClick={() => onExample(x)}>
                    {x}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {turns.map((t) => (
          <TurnView key={t.key} turn={t} view={view} workspace={workspace} latest={t.runId !== null && t.runId === lastRunTurn} />
        ))}
        {view.state === "done" && view.runId && <FeedbackBar key={view.runId} runId={view.runId} />}
        <p className="sr-only" role="status" aria-live="polite">
          {view.state ? (STATE_LABEL[view.state] ?? "") : ""}
        </p>
        <div ref={end} />
      </div>
    </section>
  );
}

/** 1回分のやり取り: 依頼 → 作業の経過（1行） → 承認・質問 → 答え → 変更したファイル・出典 */
function TurnView({ turn, view, workspace, latest }: { turn: Turn; view: ConversationView; workspace: string | null; latest: boolean }) {
  const run: RunView | undefined = turn.runId ? view.runs.find((r) => r.runId === turn.runId) : undefined;
  const steps = useMemo(() => view.steps.filter((s) => (s.runId ? s.runId === turn.runId : latest)), [view.steps, turn.runId, latest]);
  const net = view.net.filter((n) => (n.runId ? n.runId === turn.runId : latest));
  const artifacts = view.artifacts.filter((a) => a.run_id === turn.runId);
  const live = latest && view.live ? readableFromPartial(view.live.raw) : "";
  return (
    <article className="turn">
      {turn.messages.map((m) => (
        <div key={m.id} className="bubble user">
          {m.content}
        </div>
      ))}
      {run && <RunActivity state={run.state} steps={steps} plan={latest ? view.plan : []} latest={latest} queue={view.queue} retry={view.retry} batch={view.batch} />}
      {net.length > 0 && (
        <ul className="net-lines" aria-label="ネットの利用">
          {net.map((n, i) => (
            <li key={`net${i}`} className="net-line" data-testid="net-activity">
              <Icon name="globe" size={13} />
              <span className="net-text">{n.kind === "search" ? `検索: ${n.query}` : `閲覧: ${n.url}`}</span>
              {n.blocked && <span className="badge err">送信しませんでした</span>}
            </li>
          ))}
        </ul>
      )}
      {latest && <Approvals view={view} />}
      {latest && <Questions view={view} />}
      {live && (
        <div className="answer live" data-testid="live-output" aria-live="polite">
          {live}
        </div>
      )}
      {run?.finalSummary != null && (
        <div className="answer" {...(latest ? { "data-testid": "final-summary" } : {})}>
          <Markdown text={run.finalSummary} />
        </div>
      )}
      {run && run.unresolved.length > 0 && <Unresolved items={run.unresolved} />}
      {run && run.failures.length > 0 && <Failures items={run.failures} />}
      {run && run.needsReview.length > 0 && <NeedsReview items={run.needsReview} workspace={workspace} />}
      {run && run.outputs.length > 0 && <Outputs items={run.outputs} artifacts={artifacts} workspace={workspace} />}
      {artifacts.length > 0 && turn.runId && <Changes artifacts={artifacts} runId={turn.runId} canUndo={latest} />}
      {run?.finalSummary != null && run.citations.length > 0 && <Citations items={run.citations} workspace={workspace} />}
    </article>
  );
}

/** 失敗したまま終わった書き込み。答えの文が「できました」でも、手順を開かずに分かるように答えの下に出す */
function Unresolved({ items }: { items: UnresolvedView[] }) {
  return (
    <section className="notice-card unresolved" aria-label="失敗したままの操作" data-testid="unresolved">
      <h3 className="notice-title">
        <Icon name="alert" size={15} />
        注意: 次の操作は失敗したままです
      </h3>
      <ul className="unresolved-list">
        {items.map((u, i) => (
          <li key={i} title={`${u.error_code}: ${u.message}`}>
            <strong>{TOOL_LABEL[u.tool] ?? u.tool}</strong>
            <span className="unresolved-msg">{u.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 完了報告の「できなかったこと」（final の failures） */
function Failures({ items }: { items: RunView["failures"] }) {
  return (
    <section className="notice-card report failures" aria-label="できなかったこと" data-testid="failures">
      <h3 className="notice-title">
        <Icon name="alert" size={15} />
        できなかったこと（{items.length}件）
      </h3>
      <ul className="report-list">
        {items.map((f, i) => (
          <li key={i}>
            <strong className="report-item">{f.item}</strong>
            <span className="report-reason">{f.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 開いてよいファイルなら、開くための絶対パスを返す（open-policy の決まりに従う） */
function openablePath(path: string, workspace: string | null): string | null {
  const abs = citationPath(path, workspace);
  return abs && canOpen(abs) ? abs : null;
}

/** 完了報告の「確認してください」（final の needs_review）。該当のファイルを開ける */
function NeedsReview({ items, workspace }: { items: RunView["needsReview"]; workspace: string | null }) {
  return (
    <section className="notice-card report review" aria-label="確認してください" data-testid="needs-review">
      <h3 className="notice-title">
        <Icon name="clock" size={15} />
        確認してください（{items.length}件）
      </h3>
      <ul className="report-list">
        {items.map((r, i) => {
          const abs = openablePath(r.path, workspace);
          return (
            <li key={i} data-testid="review-item">
              <span className="report-path">
                <span className="mono">{r.path}</span>
                {r.location && <span className="muted small">（{r.location}）</span>}
                {abs && (
                  <button className="ghost-btn small-btn" onClick={() => void api.shell.openPath(abs, pageOf(r.location) ? { page: pageOf(r.location)! } : undefined)} aria-label={`${r.path} を開く`}>
                    <Icon name="open" size={13} />
                    開く
                  </button>
                )}
              </span>
              <span className="report-reason">{r.reason}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const pathKey = (p: string, workspace: string | null) => (citationPath(p, workspace) ?? p).replace(/\//g, "\\").toLowerCase();

/** 完了報告の成果物（final の outputs）。変更したファイルの一覧に既に出ているものは出さない */
function Outputs({ items, artifacts, workspace }: { items: RunView["outputs"]; artifacts: ConversationView["artifacts"]; workspace: string | null }) {
  const shown = new Set(artifacts.map((a) => pathKey(a.path, workspace)));
  const rest = items.filter((o) => !shown.has(pathKey(o.path, workspace)));
  if (rest.length === 0) return null;
  return (
    <section className="files" aria-label="作ったもの" data-testid="outputs">
      <h3 className="sub-heading">作ったもの（{rest.length}）</h3>
      <ul className="changes">
        {rest.map((o, i) => {
          const abs = openablePath(o.path, workspace);
          return (
            <li key={i} className="file-chip">
              <Icon name="file" size={16} />
              {abs ? (
                <button className="link file" onClick={() => void api.shell.openPath(abs)} title={abs}>
                  {o.path.split(/[\\/]/).pop()}
                </button>
              ) : (
                <span className="file mono">{o.path}</span>
              )}
              {o.description && <span className="muted small">{o.description}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 評価ボタン（ADM-06）。同意したときだけ、この作業のやり取りを送る（管理者が確認し、伏せ字にしてから使う） */
function FeedbackBar({ runId }: { runId: string }) {
  const [rating, setRating] = useState<"good" | "bad" | null>(null);
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  if (state === "sent") {
    return (
      <p className="feedback-sent small muted" role="status">
        評価を送りました。ありがとうございます。
      </p>
    );
  }
  const submit = async () => {
    if (!rating) return;
    setState("sending");
    try {
      await api.runtime.send(cmd("feedback.submit", { run_id: runId, rating, consent, ...(comment.trim() ? { comment: comment.trim() } : {}) }));
      setState("sent");
    } catch {
      setState("error");
    }
  };
  return (
    <section className="feedback" aria-label="この作業の評価">
      <span className="small muted">この作業はいかがでしたか？</span>
      <button className={`ghost-btn ${rating === "good" ? "active" : ""}`} aria-pressed={rating === "good"} onClick={() => setRating("good")}>
        <Icon name="thumbUp" size={14} />
        良い
      </button>
      <button className={`ghost-btn ${rating === "bad" ? "active" : ""}`} aria-pressed={rating === "bad"} onClick={() => setRating("bad")}>
        <Icon name="thumbDown" size={14} />
        悪い
      </button>
      {rating && (
        <div className="feedback-more">
          <input aria-label="ひとこと（任意）" placeholder="ひとこと（任意）" value={comment} maxLength={2000} onChange={(e) => setComment(e.target.value)} />
          <label className="check small">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            このやり取りを AI の改善に提供する（管理者が確認し、社名・金額などを伏せてから使います）
          </label>
          <button className="primary small-btn" disabled={state === "sending"} onClick={() => void submit()}>
            送る
          </button>
          {state === "error" && (
            <span className="error small" role="alert">
              送れませんでした。時間をおいてもう一度お試しください
            </span>
          )}
        </div>
      )}
    </section>
  );
}

const CITE_KIND: Record<string, string> = { file: "ファイル", rag: "社内文書", web: "Web" };

/** 出典の一覧（AGT-07, NET-05）。読んだ記録と照らせない出典は目立たせる */
function Citations({ items, workspace }: { items: ConversationView["citations"]; workspace: string | null }) {
  return (
    <section className="citations" aria-label="出典" data-testid="citations">
      <h3 className="sub-heading">出典</h3>
      <ul>
        {items.map((c, i) => (
          <li key={`${c.ref}-${i}`} className={c.verified ? "" : "unverified"} data-testid="citation">
            <span className="badge">{CITE_KIND[c.kind] ?? c.kind}</span>{" "}
            {c.kind === "file" && c.verified && citationPath(c.ref, workspace) ? (
              // 該当箇所を開く（PDF-07）。PDF はページを指定して開く
              <button
                className="link mono"
                title="開く"
                onClick={() => void api.shell.openPath(citationPath(c.ref, workspace)!, pageOf(c.location) ? { page: pageOf(c.location)! } : undefined)}
              >
                {c.ref}
              </button>
            ) : (
              <span className="mono">{c.ref}</span>
            )}
            {c.location && <span className="muted small">（{c.location}）</span>}
            {!c.verified && (
              <span className="badge warn">
                <Icon name="alert" size={12} />
                確認できない出典
              </span>
            )}
          </li>
        ))}
      </ul>
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
    <section className="notice-card approvals" aria-label="承認待ち">
      <h3 className="notice-title">
        <Icon name="alert" size={15} />
        承認が必要です（{pending.length}件）
      </h3>
      {pending.map((a) => (
        <div key={a.approval_id} className="approval" data-testid="approval" role="group" aria-label={`承認 ${a.approval_id}`}>
          <p className="approval-what">
            <strong>{TOOL_LABEL[a.tool] ?? a.tool}</strong>
            <span className="muted">（{REASON_LABEL[a.reason] ?? a.reason}）</span>
          </p>
          <ul className="paths">
            {a.paths.map((p) => (
              <li key={p.real_path}>
                {p.path}
                {p.real_path.toLowerCase() !== p.path.toLowerCase() && !p.inside_workspace && <span className="real">→ 実際の場所: {p.real_path}</span>}
              </li>
            ))}
          </ul>
          {a.tainted && (
            <p className="tainted small">
              <Icon name="alert" size={13} />
              Web の情報を読んだ後の操作です。内容をよく確認してください。
            </p>
          )}
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
    <section className="notice-card" aria-label="質問">
      <h3 className="notice-title">AIからの質問</h3>
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
                className="grow"
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

const OP_LABEL: Record<string, string> = { create: "新規", delete: "削除", copy: "コピー" };

/** この作業で変更したファイル。開く・差分・元に戻す（AGT-03, RUN-07） */
function Changes({ artifacts, runId, canUndo }: { artifacts: ConversationView["artifacts"]; runId: string; canUndo: boolean }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [diffOf, setDiffOf] = useState<{ run_id: string; path: string } | null>(null);
  const loadDiff = useCallback(
    () => api.runtime.send(cmd("artifact.diff", { run_id: diffOf!.run_id, path: diffOf!.path })) as Promise<ArtifactDiff>,
    [diffOf],
  );
  const undo = async () => {
    if (!confirm("この作業で変更したファイルを、変更前の状態に戻します。よろしいですか？")) return;
    const r = (await api.runtime.send(cmd("undo.run", { run_id: runId }))) as { restored: string[]; warnings: string[] };
    setMsg(`${r.restored.length} 件を元に戻しました${r.warnings.length ? `（${r.warnings.join(" / ")}）` : ""}`);
  };
  return (
    <section className="files" aria-label="変更したファイル">
      <h3 className="sub-heading">変更したファイル（{artifacts.length}）</h3>
      <ul className="changes">
        {artifacts.map((a) => (
          <li key={a.path} data-testid="artifact" className="file-chip">
            <Icon name="file" size={16} />
            <button className="link file" onClick={() => void api.shell.openPath(a.path)} title={a.path}>
              {a.path.split(/[\\/]/).pop()}
            </button>
            <span className={`op ${a.op}`}>{OP_LABEL[a.op] ?? "変更"}</span>
            {a.renamed_from && <span className="small warn">（使用中のため別名で保存）</span>}
            <span className="spacer" />
            {a.op !== "copy" && (
              <button className="ghost-btn small-btn" onClick={() => setDiffOf({ run_id: a.run_id, path: a.path })}>
                <Icon name="diff" size={13} />
                差分
              </button>
            )}
          </li>
        ))}
      </ul>
      {canUndo && (
        <button className="ghost-btn small-btn" onClick={() => void undo()}>
          <Icon name="undo" size={13} />
          この作業を元に戻す
        </button>
      )}
      {msg && (
        <p className="small" role="status">
          {msg}
        </p>
      )}
      {diffOf && <DiffDialog path={diffOf.path} load={loadDiff} onClose={() => setDiffOf(null)} />}
    </section>
  );
}
