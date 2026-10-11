// MCP ツールホスト（office-tools とプラグイン。AGT-09）。
// サーバーからの sampling 要求（OCR・帳票抽出の推論）は Runtime が Gateway に中継する。
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport, getDefaultEnvironment } from "@modelcontextprotocol/sdk/client/stdio.js";
import { CreateMessageRequestSchema, type CreateMessageRequest, type CreateMessageResult } from "@modelcontextprotocol/sdk/types.js";
import { getTool, validateDef, type ToolCatalogEntry, type ToolResult } from "@lla/protocol";
import { errResult } from "./types.js";

export interface McpServerConfig {
  id: string; // "office" | "plugin.<id>"
  command: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string;
}

export interface SamplingRequest {
  purpose: string;
  messages: CreateMessageRequest["params"]["messages"];
  systemPrompt?: string;
  maxTokens: number;
  responseSchema?: unknown;
}
export type SamplingHandler = (req: SamplingRequest) => Promise<{ text: string; model: string }>;

const ANN = (o: Record<string, unknown> | undefined, k: string) => o?.[`x-agent-${k}`];

export class McpToolHost {
  private client: Client | null = null;
  private connecting: Promise<Client> | null = null;
  private specs = new Map<string, ToolCatalogEntry>();
  private crashes = 0;

  constructor(
    private readonly cfg: McpServerConfig,
    private readonly sampling: SamplingHandler,
  ) {}

  get id(): string {
    return this.cfg.id;
  }

  private async connect(): Promise<Client> {
    if (this.client) return this.client;
    if (this.connecting) return this.connecting;
    this.connecting = (async () => {
      const client = new Client({ name: "local-llm-agent", version: "0.1.0" }, { capabilities: { sampling: {} } });
      client.setRequestHandler(CreateMessageRequestSchema, async (req): Promise<CreateMessageResult> => {
        const meta = (req.params.metadata ?? {}) as Record<string, unknown>;
        const out = await this.sampling({
          purpose: String(meta.purpose ?? "unknown"),
          messages: req.params.messages,
          ...(req.params.systemPrompt ? { systemPrompt: req.params.systemPrompt } : {}),
          maxTokens: req.params.maxTokens,
          ...(meta.response_schema ? { responseSchema: meta.response_schema } : {}),
        });
        return { role: "assistant", content: { type: "text", text: out.text }, model: out.model };
      });
      const transport = new StdioClientTransport({
        command: this.cfg.command,
        args: this.cfg.args ?? [],
        env: { ...getDefaultEnvironment(), ...(this.cfg.env ?? {}) },
        ...(this.cfg.cwd ? { cwd: this.cfg.cwd } : {}),
        stderr: "pipe",
      });
      transport.onclose = () => {
        this.client = null;
      };
      await client.connect(transport);
      const listed = await client.listTools();
      this.specs.clear();
      for (const t of listed.tools) {
        const known = getTool(t.name);
        const ann = { ...((t as { _meta?: Record<string, unknown> })._meta ?? {}), ...((t.annotations ?? {}) as Record<string, unknown>) };
        if (known && this.cfg.id === "office") {
          this.specs.set(t.name, known);
          continue;
        }
        const name = this.cfg.id === "office" ? t.name : `${this.cfg.id}.${t.name}`;
        const effect = (ANN(ann, "effect") as ToolCatalogEntry["effect"] | undefined) ?? "write"; // 注釈が無ければ安全側
        this.specs.set(name, {
          name: name as ToolCatalogEntry["name"],
          description: (t.description ?? "").slice(0, 120),
          args_schema: t.inputSchema as Record<string, unknown>,
          result_schema: { type: "object" },
          effect,
          path_args: (ANN(ann, "path-args") as string[] | undefined) ?? [],
          idempotent: Boolean(ANN(ann, "idempotent")),
          untrusted_output: ANN(ann, "untrusted-output") !== false,
          timeout_ms: 120_000,
          priority: "Could",
          paginated: Boolean(ANN(ann, "paginated")),
          examples: [],
        });
      }
      this.client = client;
      return client;
    })();
    try {
      return await this.connecting;
    } finally {
      this.connecting = null;
    }
  }

  async tools(): Promise<ToolCatalogEntry[]> {
    await this.connect();
    return [...this.specs.values()];
  }

  async call(
    name: string,
    args: Record<string, unknown>,
    timeoutMs: number,
    signal?: AbortSignal,
    secrets?: Record<string, string>,
  ): Promise<ToolResult> {
    let client: Client;
    try {
      client = await this.connect();
    } catch (e) {
      return errResult("TOOL_CRASHED", `ツールを起動できません: ${(e as Error).message}`);
    }
    const remote = this.cfg.id === "office" ? name : name.slice(this.cfg.id.length + 1);
    try {
      const ref = typeof args.password_ref === "string" ? args.password_ref : null;
      const meta = ref && secrets?.[ref] ? { _meta: { "lla/passwords": { [ref]: secrets[ref] } } } : {};
      const res = await client.callTool({ name: remote, arguments: args, ...meta }, undefined, {
        timeout: timeoutMs,
        ...(signal ? { signal } : {}),
      });
      this.crashes = 0;
      const structured = (res as { structuredContent?: unknown }).structuredContent;
      let envelope: unknown = structured;
      if (!envelope) {
        const text = (res.content as { type: string; text?: string }[] | undefined)?.find((c) => c.type === "text")?.text;
        envelope = text ? safeJson(text) : null;
      }
      if (envelope && validateDef("ToolResult", envelope).ok) return envelope as ToolResult;
      if (this.cfg.id !== "office" && !res.isError) {
        // プラグインは封筒を返さないことがある。外部由来のテキストとして包む
        const text = (res.content as { type: string; text?: string }[] | undefined)?.map((c) => c.text ?? "").join("\n") ?? "";
        return { ok: true, data: { text_ref: "u1" }, untrusted: [{ source: "file", ref: name, text }], tokens_estimate: Math.ceil(text.length / 1.5) };
      }
      return errResult("TOOL_FAILED", "ツールの応答の形式が不正です");
    } catch (e) {
      const msg = (e as Error).message ?? String(e);
      if (/timed? ?out/i.test(msg)) return errResult("TIMEOUT", "ツールの応答がタイムアウトしました");
      this.client = null;
      this.crashes++;
      return errResult(this.crashes >= 3 ? "TOOL_FAILED" : "TOOL_CRASHED", `ツールが異常終了しました: ${msg}`);
    }
  }

  async close(): Promise<void> {
    await this.client?.close();
    this.client = null;
  }
}

function safeJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}
