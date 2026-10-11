import { useCallback, useEffect, useState } from "react";
import { api, ApiError, download, login, logout, NET_LABEL, resume, type NetLevel, type User } from "./api.js";

// 日時は保存は UTC、表示は日本時間（開発ガイド §3.1）
const fmt = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

type Tab = "dashboard" | "users" | "net" | "dlp" | "instructions" | "audit";
const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "ダッシュボード" },
  { id: "users", label: "利用者" },
  { id: "net", label: "ネットアクセス" },
  { id: "dlp", label: "機密語辞書" },
  { id: "instructions", label: "共通指示" },
  { id: "audit", label: "監査ログ" },
];

export function App() {
  const [user, setUser] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [tab, setTab] = useState<Tab>("dashboard");
  useEffect(() => {
    void resume()
      .then((ok) => ok && setUser("管理者"))
      .finally(() => setChecking(false));
  }, []);
  if (checking) return <div className="splash">読み込み中…</div>;
  if (!user) return <Login onDone={setUser} />;
  return (
    <div className="admin">
      <header className="admin-top">
        <strong>ローカルAIアシスタント 管理画面</strong>
        <nav className="tabs" role="tablist" aria-label="管理メニュー">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <span className="muted small">{user}</span>
        <button
          className="link"
          onClick={() => {
            logout();
            setUser(null);
          }}
        >
          ログアウト
        </button>
      </header>
      <main className="admin-main" role="tabpanel">
        {tab === "dashboard" && <Dashboard />}
        {tab === "users" && <Users />}
        {tab === "net" && <Net />}
        {tab === "dlp" && <Dlp />}
        {tab === "instructions" && <Instructions />}
        {tab === "audit" && <Audit />}
      </main>
    </div>
  );
}

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    api<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [path]);
  useEffect(reloadEffect(reload), [reload]);
  return { data, error, reload };
}
// 効果関数から値を返さないための小さな包み（React の後片付けとして解釈させない）
const reloadEffect = (f: () => void) => () => {
  f();
};

function Message({ error, ok }: { error?: string | null; ok?: string | null }) {
  if (error) return <p className="error" role="alert">{error}</p>;
  if (ok) return <p className="ok-msg" role="status">{ok}</p>;
  return null;
}

function Login({ onDone }: { onDone: (name: string) => void }) {
  const [id, setId] = useState("");
  const [pw, setPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="login">
      <form
        className="login-card"
        aria-label="管理者ログイン"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const u = await login(id, pw);
            onDone(u.display_name);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <h1>管理画面</h1>
        <label>
          ログインID
          <input autoFocus value={id} onChange={(e) => setId(e.target.value)} autoComplete="username" />
        </label>
        <label>
          パスワード
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" />
        </label>
        <Message error={error} />
        <button className="primary" type="submit" disabled={!id || !pw}>
          ログイン
        </button>
      </form>
    </div>
  );
}

interface Dash {
  jobs_today: number;
  jobs_running: number;
  queue_depth: number;
  errors_today: number;
  users_active_today: number;
  latency_ms: { p50: number; p95: number };
  gpu: { name: string; temp_c: number; util_pct: number; mem_used_mb: number; mem_total_mb: number }[];
}

function Dashboard() {
  const { data, error, reload } = useLoad<Dash>("/dashboard");
  useEffect(() => {
    const t = setInterval(reload, 15_000);
    return () => clearInterval(t);
  }, [reload]);
  return (
    <section aria-label="ダッシュボード">
      <Message error={error} />
      {data && (
        <>
          <div className="tiles">
            <Tile label="今日の依頼" value={data.jobs_today} />
            <Tile label="実行中" value={data.jobs_running} />
            <Tile label="順番待ち" value={data.queue_depth} warn={data.queue_depth > 5} />
            <Tile label="今日のエラー" value={data.errors_today} warn={data.errors_today > 0} />
            <Tile label="今日の利用者" value={data.users_active_today} />
            <Tile label="応答時間 p50 / p95" value={`${(data.latency_ms.p50 / 1000).toFixed(1)} / ${(data.latency_ms.p95 / 1000).toFixed(1)} 秒`} />
          </div>
          <h2>GPU</h2>
          {data.gpu.length === 0 && <p className="muted">GPU の情報を取得できません</p>}
          <table className="grid">
            <tbody>
              {data.gpu.map((g) => (
                <tr key={g.name}>
                  <td>{g.name}</td>
                  <td className={g.temp_c >= 80 ? "warn" : ""}>{g.temp_c}℃</td>
                  <td>使用率 {g.util_pct}%</td>
                  <td>
                    メモリ {(g.mem_used_mb / 1024).toFixed(1)} / {(g.mem_total_mb / 1024).toFixed(1)} GB
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Tile({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className={`tile ${warn ? "warn" : ""}`} data-testid="tile">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Users() {
  const { data, error, reload } = useLoad<{ users: User[] }>("/users");
  const [form, setForm] = useState({ login: "", display_name: "", password: "", role: "user" });
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const patch = async (u: User, body: Partial<User> & { password?: string }, ok: string) => {
    try {
      await api(`/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg({ ok });
      reload();
    } catch (e) {
      setMsg({ error: (e as Error).message });
    }
  };
  return (
    <section aria-label="利用者">
      <Message error={error ?? msg.error} ok={msg.ok} />
      <table className="grid" aria-label="利用者の一覧">
        <thead>
          <tr>
            <th>ログインID</th>
            <th>表示名</th>
            <th>権限</th>
            <th>同時実行</th>
            <th>ネットの上限</th>
            <th>状態</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.users.map((u) => (
            <tr key={u.id} data-testid={`user-${u.login}`}>
              <td className="mono">{u.login}</td>
              <td>{u.display_name}</td>
              <td>{u.role === "admin" ? "管理者" : "利用者"}</td>
              <td>
                <input
                  type="number"
                  min={1}
                  max={10}
                  defaultValue={u.max_concurrent}
                  aria-label={`${u.login} の同時実行数`}
                  onBlur={(e) => Number(e.target.value) !== u.max_concurrent && void patch(u, { max_concurrent: Number(e.target.value) }, "同時実行数を変更しました")}
                />
              </td>
              <td>
                <select
                  value={u.net_level_cap}
                  aria-label={`${u.login} のネットの上限`}
                  onChange={(e) => void patch(u, { net_level_cap: e.target.value as NetLevel }, "ネットの上限を変更しました")}
                >
                  {(Object.keys(NET_LABEL) as NetLevel[]).map((l) => (
                    <option key={l} value={l}>
                      {NET_LABEL[l]}
                    </option>
                  ))}
                </select>
              </td>
              <td>{u.disabled ? <span className="badge err">無効</span> : <span className="badge">有効</span>}</td>
              <td className="row">
                <button onClick={() => void patch(u, { disabled: !u.disabled }, u.disabled ? "有効にしました" : "無効にしました")}>{u.disabled ? "有効にする" : "無効にする"}</button>
                <button
                  onClick={() => {
                    const pw = prompt(`${u.login} の新しいパスワード（12文字以上）`);
                    if (pw && pw.length >= 12) void patch(u, { password: pw }, "パスワードを再設定しました");
                    else if (pw) setMsg({ error: "パスワードは12文字以上にしてください" });
                  }}
                >
                  パスワード再設定
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>利用者を追加</h2>
      <form
        className="inline-form"
        aria-label="利用者を追加"
        onSubmit={async (e) => {
          e.preventDefault();
          if (form.password.length < 12) return setMsg({ error: "パスワードは12文字以上にしてください" });
          try {
            await api("/users", { method: "POST", body: JSON.stringify(form) });
            setMsg({ ok: `${form.login} を追加しました` });
            setForm({ login: "", display_name: "", password: "", role: "user" });
            reload();
          } catch (err) {
            setMsg({ error: (err as Error).message });
          }
        }}
      >
        <input placeholder="ログインID" aria-label="ログインID" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} />
        <input placeholder="表示名" aria-label="表示名" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <input type="password" placeholder="初期パスワード（12文字以上）" aria-label="初期パスワード" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select aria-label="権限" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="user">利用者</option>
          <option value="admin">管理者</option>
        </select>
        <button className="primary" type="submit" disabled={!form.login || !form.password}>
          追加
        </button>
      </form>
    </section>
  );
}

function Net() {
  const pol = useLoad<{ policies: { scope: string; level_cap: NetLevel; monthly_search_limit: number | null }[] }>("/net/policies");
  const sites = useLoad<{ sites: { pattern: string; kind: string }[] }>("/net/sites");
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [pattern, setPattern] = useState("");
  const [kind, setKind] = useState("block");
  const org = pol.data?.policies.find((p) => p.scope === "org");
  const setOrg = async (level: NetLevel, limit: number | null) => {
    try {
      await api("/net/policies", { method: "PUT", body: JSON.stringify({ scope: "org", level_cap: level, monthly_search_limit: limit }) });
      setMsg({ ok: level === "off" ? "全社のネットアクセスを停止しました" : "全社の上限を変更しました" });
      pol.reload();
    } catch (e) {
      setMsg({ error: (e as Error).message });
    }
  };
  const saveSites = async (next: { pattern: string; kind: string }[]) => {
    try {
      await api("/net/sites", { method: "PUT", body: JSON.stringify({ sites: next }) });
      setMsg({ ok: "サイトの一覧を保存しました" });
      sites.reload();
    } catch (e) {
      setMsg({ error: (e as Error).message });
    }
  };
  return (
    <section aria-label="ネットアクセス">
      <Message error={pol.error ?? sites.error ?? msg.error} ok={msg.ok} />
      <h2>全社の上限</h2>
      {org && (
        <div className="inline-form">
          <select aria-label="全社の上限" value={org.level_cap} onChange={(e) => void setOrg(e.target.value as NetLevel, org.monthly_search_limit)}>
            {(Object.keys(NET_LABEL) as NetLevel[]).map((l) => (
              <option key={l} value={l}>
                {NET_LABEL[l]}
              </option>
            ))}
          </select>
          <label className="small">
            月あたりの検索回数の上限（1人）
            <input
              type="number"
              min={0}
              defaultValue={org.monthly_search_limit ?? ""}
              aria-label="検索回数の上限"
              onBlur={(e) => void setOrg(org.level_cap, e.target.value === "" ? null : Number(e.target.value))}
            />
          </label>
          <button className="danger" onClick={() => confirm("全社のネットアクセスを止めます。よろしいですか？") && void setOrg("off", org.monthly_search_limit)}>
            全社で停止
          </button>
        </div>
      )}
      <h2>禁止サイト・信頼サイト</h2>
      <table className="grid" aria-label="サイトの一覧">
        <tbody>
          {sites.data?.sites.map((s, i) => (
            <tr key={`${s.pattern}-${i}`}>
              <td className="mono">{s.pattern}</td>
              <td>{s.kind === "block" ? <span className="badge err">禁止</span> : <span className="badge">信頼</span>}</td>
              <td>
                <button className="link" onClick={() => void saveSites(sites.data!.sites.filter((_, j) => j !== i))}>
                  削除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!/^(\*\.)?[A-Za-z0-9.-]+$/.test(pattern)) return setMsg({ error: "ドメインの形式で入力してください（例: *.example.com）" });
          void saveSites([...(sites.data?.sites ?? []), { pattern, kind }]);
          setPattern("");
        }}
      >
        <input placeholder="*.example.com" aria-label="サイト" value={pattern} onChange={(e) => setPattern(e.target.value)} />
        <select aria-label="種類" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="block">禁止</option>
          <option value="trusted">信頼</option>
        </select>
        <button type="submit" disabled={!pattern}>
          追加
        </button>
      </form>
    </section>
  );
}

function Dlp() {
  const { data, error, reload } = useLoad<{ terms: { id: string; masked: string; category: string; updated_at: string }[] }>("/dlp/terms");
  const [text, setText] = useState("");
  const [category, setCategory] = useState("client");
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const add = async (terms: { term: string; category: string }[]) => {
    try {
      const r = await api<{ added: number; skipped: number }>("/dlp/terms", { method: "POST", body: JSON.stringify({ terms }) });
      setMsg({ ok: `${r.added} 件を登録しました${r.skipped ? `（重複 ${r.skipped} 件は飛ばしました）` : ""}` });
      reload();
    } catch (e) {
      setMsg({ error: (e as Error).message });
    }
  };
  return (
    <section aria-label="機密語辞書">
      <p className="muted small">Web 検索や閲覧の URL にこれらの語が含まれると、外部へ送信しません（NET-07）。一覧では語の一部を伏せています。</p>
      <Message error={error ?? msg.error} ok={msg.ok} />
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          // 1行1語。「語,分類」の CSV 形式も受け付ける
          const terms = text
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter(Boolean)
            .map((l) => {
              const [term, cat] = l.split(",").map((x) => x.trim());
              return { term: term!, category: cat || category };
            });
          void add(terms);
          setText("");
        }}
      >
        <textarea rows={4} placeholder={"1行に1語（「語,分類」の CSV も可）"} aria-label="機密語" value={text} onChange={(e) => setText(e.target.value)} />
        <select aria-label="分類" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="client">取引先</option>
          <option value="vessel">船名</option>
          <option value="project">案件</option>
          <option value="other">その他</option>
        </select>
        <button className="primary" type="submit" disabled={!text.trim()}>
          登録
        </button>
      </form>
      <table className="grid" aria-label="機密語の一覧">
        <tbody>
          {data?.terms.map((t) => (
            <tr key={t.id} data-testid="dlp-term">
              <td className="mono">{t.masked}</td>
              <td>{t.category}</td>
              <td className="muted small">{fmt(t.updated_at)}</td>
              <td>
                <button
                  className="link"
                  onClick={async () => {
                    await api(`/dlp/terms/${t.id}`, { method: "DELETE" });
                    reload();
                  }}
                >
                  削除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Instructions() {
  const { data, error, reload } = useLoad<{ current: { version: number; body: string; published_at: string } | null; history: { version: number; published_at: string }[] }>(
    "/instructions",
  );
  const [body, setBody] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const text = body ?? data?.current?.body ?? "";
  return (
    <section aria-label="共通指示">
      <p className="muted small">全利用者の AI に渡す社内ルール・文体の指示です（AGT-10）。1,500 トークン（約 2,250 字）まで。</p>
      <Message error={error ?? msg.error} ok={msg.ok} />
      <textarea className="big" aria-label="共通指示" value={text} onChange={(e) => setBody(e.target.value)} />
      <div className="row">
        <span className={`small ${text.length > 2250 ? "error" : "muted"}`}>{text.length} 字</span>
        <button
          className="primary"
          onClick={async () => {
            try {
              const r = await api<{ version: number }>("/instructions", { method: "POST", body: JSON.stringify({ body: text }) });
              setMsg({ ok: `第 ${r.version} 版を配信しました` });
              setBody(null);
              reload();
            } catch (e) {
              setMsg({ error: (e as Error).message });
            }
          }}
        >
          配信
        </button>
      </div>
      {data && data.history.length > 0 && (
        <p className="small muted">
          配信履歴: {data.history.map((h) => `第${h.version}版（${fmt(h.published_at)}）`).join(" / ")}
        </p>
      )}
    </section>
  );
}

function Audit() {
  const [user, setUser] = useState("");
  const [action, setAction] = useState("");
  const q = `?user=${encodeURIComponent(user)}&action=${encodeURIComponent(action)}&limit=500`;
  const { data, error, reload } = useLoad<{ events: { at: string; user: string; source: string; action: string; target: string; result: string }[] }>(`/audit${q}`);
  return (
    <section aria-label="監査ログ">
      <div className="inline-form">
        <input placeholder="利用者" aria-label="利用者で絞り込む" value={user} onChange={(e) => setUser(e.target.value)} />
        <input placeholder="操作（例 web.search）" aria-label="操作で絞り込む" value={action} onChange={(e) => setAction(e.target.value)} />
        <button onClick={reload}>検索</button>
        <button onClick={() => void download(`/audit${q}&format=csv`, "audit.csv")}>CSV で保存</button>
      </div>
      <Message error={error} />
      <table className="grid" aria-label="監査ログの一覧">
        <thead>
          <tr>
            <th>日時</th>
            <th>利用者</th>
            <th>区分</th>
            <th>操作</th>
            <th>対象</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {data?.events.map((e, i) => (
            <tr key={i} data-testid="audit-row">
              <td className="small">{fmt(e.at)}</td>
              <td>{e.user}</td>
              <td>{e.source}</td>
              <td className="mono">{e.action}</td>
              <td className="mono small">{e.target}</td>
              <td>{e.result}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
