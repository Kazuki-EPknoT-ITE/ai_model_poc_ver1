import type { Artifact, ErrorCode, ErrorKind, ToolResult, UntrustedBlock } from "@lla/protocol";
import { estimateTokens } from "../util.js";

export interface ToolContext {
  workspace: string;
  conversationId: string;
  runId: string;
  stepId: string;
  signal?: AbortSignal;
  /** password_ref（pw-N）→ 実際のパスワード。メモリ上だけで渡す（PDF-06） */
  secrets?: Record<string, string>;
}

export type ToolHandler = (args: Record<string, unknown>, ctx: ToolContext) => Promise<ToolResult>;

const KIND: Record<string, ErrorKind> = {
  FILE_LOCKED: "transient",
  NET_UNREACHABLE: "transient",
  SERVER_BUSY: "transient",
  TIMEOUT: "transient",
  TOOL_CRASHED: "transient",
  FORBIDDEN_PATH: "policy",
  NET_DISABLED: "policy",
  DLP_BLOCKED: "policy",
  DOMAIN_BLOCKED: "policy",
  QUOTA_EXCEEDED: "policy",
  APPROVAL_REJECTED: "policy",
  APPROVAL_REQUIRED: "deferred",
  PASSWORD_REQUIRED: "deferred",
};

export function errorKind(code: ErrorCode): ErrorKind {
  return KIND[code] ?? "permanent";
}

export function okResult(
  data: Record<string, unknown>,
  extra: { untrusted?: UntrustedBlock[]; artifacts?: Artifact[]; warnings?: string[]; continuation?: string } = {},
): ToolResult {
  const text = JSON.stringify(data) + (extra.untrusted ?? []).map((u) => u.text).join("");
  return { ok: true, data, ...extra, tokens_estimate: estimateTokens(text) };
}

export function errResult(code: ErrorCode, message: string, detail?: Record<string, unknown>, retryAfterMs?: number): ToolResult {
  return {
    ok: false,
    error: {
      code,
      kind: errorKind(code),
      message,
      ...(detail ? { detail } : {}),
      ...(retryAfterMs !== undefined ? { retry_after_ms: retryAfterMs } : {}),
    },
    tokens_estimate: estimateTokens(message),
  };
}

export class ToolFailure extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
  }
}
