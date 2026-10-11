// 管理 API のクライアント（local_llm_api.md §9.1）。アクセストークンはメモリだけに持つ。
let access: string | null = null;
const REFRESH_KEY = "lla-admin-refresh"; // タブを閉じれば消える sessionStorage に置く

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail?: Record<string, unknown>,
  ) {
    super(message);
  }
}

async function parse<T>(r: Response): Promise<T> {
  if (r.status === 204) return undefined as T;
  const body = (await r.json().catch(() => ({}))) as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } };
  if (!r.ok) throw new ApiError(r.status, body.error?.code ?? `HTTP_${r.status}`, body.error?.message ?? `エラー（HTTP ${r.status}）`, body.error?.detail);
  return body as T;
}

export async function login(loginId: string, password: string): Promise<{ display_name: string; role: string }> {
  const r = await fetch("/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginId, password, device_name: "admin-web" }),
  });
  const b = await parse<{ access_token: string; refresh_token: string; user: { display_name: string; role: string } }>(r);
  if (b.user.role !== "admin") {
    throw new ApiError(403, "FORBIDDEN", "管理者のアカウントでログインしてください");
  }
  access = b.access_token;
  sessionStorage.setItem(REFRESH_KEY, b.refresh_token);
  return b.user;
}

async function refresh(): Promise<boolean> {
  const rt = sessionStorage.getItem(REFRESH_KEY);
  if (!rt) return false;
  const r = await fetch("/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) });
  if (!r.ok) {
    sessionStorage.removeItem(REFRESH_KEY);
    return false;
  }
  const b = (await r.json()) as { access_token: string; refresh_token: string };
  access = b.access_token;
  sessionStorage.setItem(REFRESH_KEY, b.refresh_token);
  return true;
}

export async function resume(): Promise<boolean> {
  return access !== null || refresh();
}

export function logout(): void {
  access = null;
  sessionStorage.removeItem(REFRESH_KEY);
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (access) headers.set("Authorization", `Bearer ${access}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const r = await fetch(`/admin/v1${path}`, { ...init, headers });
  if (r.status === 401 && retry && (await refresh())) return api<T>(path, init, false);
  return parse<T>(r);
}

export async function download(path: string, filename: string): Promise<void> {
  const r = await fetch(`/admin/v1${path}`, { headers: { Authorization: `Bearer ${access ?? ""}` } });
  if (!r.ok) throw new ApiError(r.status, "DOWNLOAD_FAILED", "ダウンロードできませんでした");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export interface EvalRow {
  eval_set: string;
  metric: string;
  value: number;
}

export interface ModelRow {
  id: string;
  slot: string;
  base_model: string;
  quant: string;
  lora: string | null;
  status: "candidate" | "production" | "retired";
  created_at: string;
  promoted_at: string | null;
  evals: EvalRow[];
}

export type NetLevel = "off" | "search" | "browse";
export const NET_LABEL: Record<NetLevel, string> = { off: "オフ", search: "検索のみ", browse: "検索＋閲覧" };

export interface User {
  id: string;
  login: string;
  display_name: string;
  role: "user" | "admin";
  department_id: string | null;
  disabled: boolean;
  max_concurrent: number;
  net_level_cap: NetLevel;
  created_at: string;
  last_login_at: string | null;
}
