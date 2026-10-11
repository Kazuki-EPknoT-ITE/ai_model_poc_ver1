// Gateway クライアント（local_llm_api.md）。一時的な失敗は TransientError として上位の再試行に任せる。
import type { NetLevel } from "../policy/policy.js";
import { sleep } from "../util.js";
import { testHook } from "../testhooks.js";

export interface TokenProvider {
  accessToken(): Promise<string>;
  /** 401 のときに呼ぶ。新しいアクセストークンを返す。 */
  refresh(): Promise<string>;
}

export interface ModelInfo {
  id: string;
  label: string;
  available: boolean;
  context_budget: number;
}
export interface GatewayConfig {
  protocol_version: string;
  server_version: string;
  org: { id: string; name: string };
  models: ModelInfo[];
  escalation_target: string | null;
  net: { level_cap: NetLevel; monthly_search_limit?: number; used_this_month?: number };
  instructions: { version: number; body: string };
  invoice_schemas: string[];
  limits: { max_concurrent: number; max_tool_result_tokens: number };
  features: { rag: boolean; ocr: boolean; web: boolean };
  /** 定型作業（APP-09）。会社プロファイルから配る */
  macros?: { name: string; description: string; prompt: string }[];
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}
export interface InferRequest {
  model: string;
  messages: ChatMessage[];
  response_format?: { type: "json_schema"; schema: unknown };
  max_tokens?: number;
  temperature?: number;
  priority_hint?: "interactive" | "normal" | "low";
  purpose?: "agent" | "invoice_extract" | "compact";
}
export interface InferResult {
  output: string;
  usage: { input_tokens: number; output_tokens: number };
}

/** 再試行すれば直る失敗（RUN-03）。 */
export class TransientError extends Error {
  constructor(
    readonly code: "NET_UNREACHABLE" | "SERVER_BUSY" | "TIMEOUT",
    message: string,
    readonly retryAfterMs?: number,
  ) {
    super(message);
  }
}
/** 再試行しても直らない失敗。 */
export class GatewayError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
/** 応答の保持期限切れ（404）。attempt を上げて投入し直す。 */
export class JobExpiredError extends Error {}
export class CancelledError extends Error {}

export interface InferCallbacks {
  onQueue?: (position: number, etaSeconds: number) => void;
  onToken?: (delta: string) => void;
  onReset?: () => void;
  signal?: AbortSignal;
}

type FetchFn = typeof fetch;

export class GatewayClient {
  constructor(
    private readonly baseUrl: string,
    private readonly tokens: TokenProvider,
    private readonly opts: { fetch?: FetchFn; netLevel?: () => NetLevel; requestTimeoutMs?: number } = {},
  ) {}

  private get f(): FetchFn {
    return this.opts.fetch ?? fetch;
  }

  private async request(path: string, init: RequestInit & { retryAuth?: boolean } = {}): Promise<Response> {
    const token = await this.tokens.accessToken();
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    if (this.opts.netLevel) headers.set("X-Net-Level", this.opts.netLevel());
    let res: Response;
    try {
      res = await this.f(this.baseUrl + path, { ...init, headers });
    } catch (e) {
      if (init.signal?.aborted) throw new CancelledError("中止しました");
      throw new TransientError("NET_UNREACHABLE", `サーバーに接続できません: ${(e as Error).message}`);
    }
    if (res.status === 401 && init.retryAuth !== false) {
      await this.tokens.refresh();
      return this.request(path, { ...init, retryAuth: false });
    }
    return res;
  }

  private async raise(res: Response): Promise<never> {
    let body: { error?: { code?: string; message?: string; retryable?: boolean; retry_after_ms?: number } } = {};
    try {
      body = (await res.json()) as typeof body;
    } catch {
      /* 本文が JSON でない */
    }
    const err = body.error ?? {};
    // サーバーが retryable: false を明示したもの（機能が無効・上限超過など）は、5xx でも再試行しない
    if (err.retryable === false) throw new GatewayError(res.status, err.code ?? `HTTP_${res.status}`, err.message ?? `HTTP ${res.status}`);
    if (res.status >= 500 || res.status === 429 || err.retryable) {
      throw new TransientError(res.status === 429 ? "SERVER_BUSY" : "SERVER_BUSY", err.message ?? `HTTP ${res.status}`, err.retry_after_ms);
    }
    throw new GatewayError(res.status, err.code ?? `HTTP_${res.status}`, err.message ?? `HTTP ${res.status}`);
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.request(path, init);
    if (!res.ok) await this.raise(res);
    return (await res.json()) as T;
  }

  config(): Promise<GatewayConfig> {
    return this.json<GatewayConfig>("/v1/config");
  }

  post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return this.json<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
      ...(signal ? { signal } : {}),
    });
  }

  async ocr(png: Uint8Array, engine: string = "auto"): Promise<{ text: string; confidence: number; engine_used: string }> {
    const form = new FormData();
    form.set("image", new Blob([new Uint8Array(png)], { type: "image/png" }), "page.png");
    form.set("engine", engine);
    form.set("lang", "ja");
    return this.json("/v1/vision/ocr", { method: "POST", body: form });
  }

  async cancel(jobId: string): Promise<void> {
    await this.request(`/v1/steps/${jobId}`, { method: "DELETE" });
  }

  /**
   * 推論1手。投入 → SSE で受信。SSE が途中で切れたら Last-Event-ID 付きで再接続する。
   * onJob で job_id を受け取り、呼び出し側が Run Store に保存する（再開時の問い合わせ用）。
   */
  async infer(key: string, req: InferRequest, cb: InferCallbacks & { onJob?: (jobId: string) => void } = {}): Promise<InferResult> {
    const res = await this.request("/v1/steps", {
      method: "POST",
      body: JSON.stringify({ stream: true, ...req }),
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      ...(cb.signal ? { signal: cb.signal } : {}),
    });
    if (!res.ok) await this.raise(res);
    const job = (await res.json()) as { job_id: string; position?: number; eta_seconds?: number };
    cb.onJob?.(job.job_id);
    testHook("infer.submitted", { job_id: job.job_id });
    return this.follow(job.job_id, cb);
  }

  async follow(jobId: string, cb: InferCallbacks = {}): Promise<InferResult> {
    let lastId = 0;
    let quickDrops = 0;
    for (;;) {
      const headers: Record<string, string> = lastId ? { "Last-Event-ID": String(lastId) } : {};
      const res = await this.request(`/v1/steps/${jobId}/events`, { headers, ...(cb.signal ? { signal: cb.signal } : {}) });
      if (res.status === 404) throw new JobExpiredError("応答の保持期限が切れました");
      if (!res.ok) await this.raise(res);
      if (!res.body) throw new TransientError("NET_UNREACHABLE", "応答の本文がありません");
      const before = lastId;
      let result: InferResult | null = null;
      try {
        for await (const ev of readSse(res.body)) {
          lastId = ev.id ?? lastId;
          const data = ev.data ? (JSON.parse(ev.data) as Record<string, unknown>) : {};
          switch (ev.event) {
            case "queue":
              cb.onQueue?.(Number(data.position ?? 0), Number(data.eta_seconds ?? 0));
              break;
            case "token":
              cb.onToken?.(String(data.delta ?? ""));
              break;
            case "requeued":
              cb.onReset?.();
              break;
            case "done":
              result = { output: String(data.output ?? ""), usage: data.usage as InferResult["usage"] };
              break;
            case "error":
              if (data.code === "CANCELLED") throw new CancelledError("取り消されました");
              if (data.retryable) throw new TransientError("SERVER_BUSY", String(data.code ?? "error"));
              throw new GatewayError(500, String(data.code ?? "ENGINE_ERROR"), "推論でエラーが発生しました");
          }
          if (result) return result;
        }
      } catch (e) {
        if (e instanceof CancelledError || e instanceof GatewayError || e instanceof TransientError) throw e;
        if (cb.signal?.aborted) throw new CancelledError("中止しました");
        // 読み取り中の切断は再接続で続ける
      }
      // done を受け取らずに閉じた → 続きから再接続
      quickDrops = lastId === before ? quickDrops + 1 : 0;
      if (quickDrops >= 5) throw new TransientError("NET_UNREACHABLE", "応答の受信が途切れています");
      await sleep(Math.min(200 * 2 ** quickDrops, 5000), cb.signal);
    }
  }
}

export interface SseEvent {
  id?: number;
  event: string;
  data: string;
}

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + (buf[idx] === "\r" ? 4 : 2));
      const ev: SseEvent = { event: "message", data: "" };
      let hasField = false;
      for (const line of block.split(/\r?\n/)) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        hasField = true;
        if (field === "id") ev.id = Number(value);
        else if (field === "event") ev.event = value;
        else if (field === "data") ev.data = ev.data ? `${ev.data}\n${value}` : value;
      }
      if (hasField) yield ev;
    }
  }
}
