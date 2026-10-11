// ツールの振り分け（プロトコル §2.1）。ポリシー判定・書き込みの意図記録は呼び出し側（ループ）が行う。
import { getTool, toolCatalog, validateToolArgs, type ErrorCode, type ToolCatalogEntry, type ToolResult } from "@lla/protocol";
import { GatewayError, TransientError, type GatewayClient } from "../gateway/client.js";
import type { NetLevel } from "../policy/policy.js";
import type { RunStore } from "../store/store.js";
import { isAbsolute, join, sep } from "node:path";
import { pointerGetAll, pointerSet } from "../util.js";
import { fsCopy, fsList, fsMkdir, fsReadText, fsSearch, fsStat } from "./fs-tools.js";
import type { McpToolHost } from "./mcp.js";
import { ToolFailure, errResult, okResult, type ToolContext, type ToolHandler } from "./types.js";

const NATIVE: Record<string, ToolHandler> = {
  "fs.list": fsList,
  "fs.stat": fsStat,
  "fs.read_text": fsReadText,
  "fs.search": fsSearch,
  "fs.mkdir": fsMkdir,
  "fs.copy": fsCopy,
};
/** WriteCoordinator が実体を担う（ルーターでは何もしない） */
const COORDINATED = new Set(["fs.delete", "fs.move"]);
const OFFICE_NS = /^(docx|xlsx|csv|pdf|data|convert|invoice)\./;

function absolutizePaths(spec: ToolCatalogEntry, args: Record<string, unknown>, workspace: string): Record<string, unknown> {
  const out = structuredClone(args);
  for (const pointer of spec.path_args) {
    for (const hit of pointerGetAll(out, pointer)) {
      if (typeof hit.value === "string" && !isAbsolute(hit.value)) pointerSet(out, hit.path, join(workspace, hit.value));
    }
  }
  return out;
}

function relativizePaths(r: ToolResult, workspace: string): ToolResult {
  const prefix = JSON.stringify(workspace.endsWith(sep) ? workspace : workspace + sep).slice(1, -1);
  const json = JSON.stringify(r);
  if (!json.includes(prefix)) return r;
  return JSON.parse(json.split(prefix).join("")) as ToolResult;
}

export interface EnabledContext {
  netLevel: NetLevel;
  features: { rag: boolean; web: boolean; ocr: boolean };
}

export class ToolRouter {
  private pluginSpecs: ToolCatalogEntry[] = [];
  private officeNames = new Set<string>();

  constructor(
    private readonly store: RunStore,
    private readonly gateway: GatewayClient,
    private readonly office: McpToolHost | null,
    private readonly plugins: McpToolHost[] = [],
  ) {}

  /** 起動時に MCP サーバーのツール一覧を取り込む。失敗したサーバーのツールは無効のまま。 */
  async init(): Promise<string[]> {
    const warnings: string[] = [];
    if (this.office) {
      try {
        this.officeNames = new Set((await this.office.tools()).map((t) => t.name));
      } catch (e) {
        warnings.push(`文書ツールを起動できません: ${(e as Error).message}`);
      }
    }
    for (const p of this.plugins) {
      try {
        this.pluginSpecs.push(...(await p.tools()));
      } catch (e) {
        warnings.push(`プラグイン ${p.id} を起動できません: ${(e as Error).message}`);
      }
    }
    return warnings;
  }

  spec(name: string): ToolCatalogEntry | undefined {
    return getTool(name) ?? this.pluginSpecs.find((t) => t.name === name);
  }

  /** その会話で使えるツール（文法とプロンプトの TOOLS に使う。プロトコル §1.3）。 */
  enabled(ctx: EnabledContext): ToolCatalogEntry[] {
    const out = toolCatalog.filter((t) => {
      if (t.name === "web.search") return ctx.features.web && ctx.netLevel !== "off";
      if (t.name === "web.fetch") return ctx.features.web && ctx.netLevel === "browse";
      if (t.name === "rag.search") return ctx.features.rag;
      if (t.name === "pdf.ocr") return ctx.features.ocr && this.officeNames.has(t.name);
      if (OFFICE_NS.test(t.name)) return this.officeNames.has(t.name);
      return true;
    });
    return [...out, ...this.pluginSpecs];
  }

  /** 引数の検証と実行。例外は ToolResult に変換する。 */
  async call(name: string, args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
    const spec = this.spec(name);
    if (!spec) return errResult("UNKNOWN_TOOL", `${name} は使えません`);
    if (getTool(name)) {
      const v = validateToolArgs(name, args);
      if (!v.ok) {
        const first = v.errors[0];
        return errResult("INVALID_ARGS", `引数が不正です: ${first?.instancePath || "/"} ${first?.message ?? ""}`.trim());
      }
    }
    try {
      if (NATIVE[name]) return await NATIVE[name]!(args, ctx);
      if (COORDINATED.has(name)) return okResult({});
      if (name.startsWith("memory.")) return this.memory(name, args, ctx);
      if (name === "rag.search" || name.startsWith("web.")) return await this.remote(name, args, ctx);
      if (OFFICE_NS.test(name) && this.office) {
        // office-tools は全会話で1プロセスを共有するので、パスは作業フォルダ基準の絶対パスで渡し、結果は相対に戻す
        const r = await this.office.call(name, absolutizePaths(spec, args, ctx.workspace), spec.timeout_ms, ctx.signal, ctx.secrets);
        return relativizePaths(r, ctx.workspace);
      }
      const plugin = this.plugins.find((p) => name.startsWith(`${p.id}.`));
      if (plugin) return await plugin.call(name, args, spec.timeout_ms, ctx.signal);
      return errResult("UNKNOWN_TOOL", `${name} は使えません`);
    } catch (e) {
      if (e instanceof ToolFailure) return errResult(e.code, e.message);
      if (e instanceof TransientError) return errResult("NET_UNREACHABLE", e.message, undefined, e.retryAfterMs);
      return errResult("TOOL_FAILED", `この操作は失敗しました。理由: ${(e as Error).message}`);
    }
  }

  private memory(name: string, args: Record<string, unknown>, ctx: ToolContext): ToolResult {
    if (name === "memory.note") {
      const items = this.store.listMemory(ctx.conversationId);
      const total = items.reduce((n, i) => n + i.value.length, 0) + String(args.value).length;
      if (items.length >= 50 && !items.some((i) => i.key === args.key)) return errResult("INVALID_ARGS", "記録できるのは50件までです。memory.forget で整理してください");
      if (total > 8000) return errResult("INVALID_ARGS", "記録の合計が8,000字を超えます。memory.forget で整理してください");
      this.store.setMemory(ctx.conversationId, String(args.key), String(args.value));
    } else {
      this.store.deleteMemory(ctx.conversationId, String(args.key));
    }
    return okResult({ keys: this.store.listMemory(ctx.conversationId).map((m) => m.key) });
  }

  private async remote(name: string, args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
    try {
      if (name === "rag.search") {
        const r = await this.gateway.post<{ hits: { ref: string; path: string; location?: string; score?: number; excerpt?: string }[] }>(
          "/v1/rag/search",
          args,
          ctx.signal,
        );
        return okResult(
          { hits: r.hits.map(({ excerpt: _e, ...h }) => h) },
          { untrusted: r.hits.map((h) => ({ source: "rag" as const, ref: `${h.path}#${h.location ?? ""}`, text: h.excerpt ?? "" })) },
        );
      }
      if (name === "web.search") {
        const r = await this.gateway.post<{ results: { rank: number; title: string; url: string; snippet: string }[] }>(
          "/v1/web/search",
          args,
          ctx.signal,
        );
        return okResult(
          { results: r.results.map((x) => ({ rank: x.rank, url: x.url, title_ref: `w${x.rank}`, snippet_ref: `w${x.rank}` })) },
          { untrusted: r.results.map((x) => ({ source: "web" as const, ref: x.url, text: `${x.title}\n${x.snippet}` })) },
        );
      }
      const r = await this.gateway.post<{ url: string; final_url: string; title: string; content_type: string; text: string; chars: number; truncated: boolean }>(
        "/v1/web/fetch",
        args,
        ctx.signal,
      );
      return okResult(
        { url: r.url, final_url: r.final_url, title_ref: "u1", content_type: r.content_type, chars: r.chars, truncated: r.truncated },
        { untrusted: [{ source: "web", ref: r.final_url, text: `${r.title}\n${r.text}` }] },
      );
    } catch (e) {
      if (e instanceof GatewayError) {
        const known: ErrorCode[] = ["NET_DISABLED", "DLP_BLOCKED", "DOMAIN_BLOCKED", "QUOTA_EXCEEDED"];
        const code = (known as string[]).includes(e.code) ? (e.code as ErrorCode) : "TOOL_FAILED";
        const msg: Record<string, string> = {
          DLP_BLOCKED: "検索語やURLに社外秘の語が含まれます。一般的な言葉に言い換えて検索してください",
          NET_DISABLED: "ネットアクセスは許可されていません",
          DOMAIN_BLOCKED: "このサイトは閲覧できません",
          QUOTA_EXCEEDED: "今月の検索上限に達しました。ネットなしで続けてください",
        };
        return errResult(code, msg[code] ?? e.message);
      }
      throw e;
    }
  }

  async close(): Promise<void> {
    await this.office?.close();
    for (const p of this.plugins) await p.close();
  }
}
