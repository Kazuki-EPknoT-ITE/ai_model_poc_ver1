// ポリシーエンジン（設計書 §5.3、プロトコル §4）。承認の要否はモデルではなくここで決める。
import { dirname } from "node:path";
import type { ToolCatalogEntry } from "@lla/protocol";
import { pointerGetAll } from "../util.js";
import { PathError, forbiddenRoots, isForbidden, isInside, pathKey, resolveRealPath } from "./paths.js";

export type NetLevel = "off" | "search" | "browse";
export type ApprovalReason = "outside_workspace" | "delete" | "move" | "plugin_write" | "tainted_outside";

/** 書き込み先になる引数（それ以外の path_args は読み取り元）。Runtime 内部の表。 */
export const WRITE_TARGETS: Record<string, string[]> = {
  "fs.mkdir": ["/path"],
  "fs.copy": ["/dst"],
  "fs.move": ["/dst"],
  "fs.delete": ["/path"],
  "docx.create": ["/path"],
  "docx.edit": ["/path"],
  "docx.comments": ["/path"],
  "xlsx.write_cells": ["/path"],
  "xlsx.write_table": ["/path"],
  "xlsx.insert_rows": ["/path"],
  "xlsx.delete_rows": ["/path"],
  "xlsx.insert_cols": ["/path"],
  "xlsx.delete_cols": ["/path"],
  "xlsx.add_sheet": ["/path"],
  "xlsx.rename_sheet": ["/path"],
  "xlsx.create": ["/path"],
  "xlsx.recalc": ["/path"],
  "xlsx.chart": ["/path"],
  "csv.write": ["/path"],
  "data.transform": ["/output/xlsx", "/output/csv"],
  "convert.to_docx": ["/dst"],
  "convert.to_xlsx": ["/dst"],
  "convert.to_pdf": ["/dst"],
  "batch.run": ["/output/xlsx", "/output/csv"],
};

export interface ResolvedPath {
  pointer: string[];
  path: string;
  real_path: string;
  inside_workspace: boolean;
  write: boolean;
}

export type Decision =
  | { kind: "allow"; paths: ResolvedPath[] }
  | { kind: "deny"; code: "FORBIDDEN_PATH" | "NET_DISABLED" | "INVALID_ARGS"; message: string }
  | { kind: "approve"; reason: ApprovalReason; paths: ResolvedPath[] };

export interface RememberRule {
  reason: ApprovalReason;
  dirKey: string;
  tool: string;
}

export interface PolicyContext {
  workspace: string;
  tainted: boolean;
  netLevel: NetLevel;
  appDataDir: string;
  remember: RememberRule[];
}

const NET_NEEDED: Record<string, NetLevel> = { "web.search": "search", "web.fetch": "browse" };
const LEVEL: Record<NetLevel, number> = { off: 0, search: 1, browse: 2 };

export function resolvePaths(tool: ToolCatalogEntry, args: unknown, workspace: string): ResolvedPath[] {
  const writes = new Set(WRITE_TARGETS[tool.name] ?? []);
  const out: ResolvedPath[] = [];
  for (const pointer of tool.path_args) {
    for (const hit of pointerGetAll(args, pointer)) {
      if (typeof hit.value !== "string") continue;
      const real = resolveRealPath(hit.value, workspace);
      out.push({
        pointer: hit.path,
        path: hit.value,
        real_path: real,
        inside_workspace: isInside(real, workspace),
        write: writes.has(pointer) || (tool.effect === "write" && !WRITE_TARGETS[tool.name]),
      });
    }
  }
  return out;
}

export function decide(tool: ToolCatalogEntry, args: unknown, ctx: PolicyContext): Decision {
  if (tool.effect === "net") {
    const need = NET_NEEDED[tool.name] ?? "browse";
    if (LEVEL[ctx.netLevel] < LEVEL[need]) {
      return { kind: "deny", code: "NET_DISABLED", message: "ネットアクセスは許可されていません" };
    }
    return { kind: "allow", paths: [] };
  }
  let paths: ResolvedPath[];
  try {
    paths = resolvePaths(tool, args, ctx.workspace);
  } catch (e) {
    if (e instanceof PathError) return { kind: "deny", code: e.code, message: e.message };
    throw e;
  }
  const roots = forbiddenRoots(ctx.appDataDir);
  for (const p of paths) {
    if (isForbidden(p.real_path, roots)) {
      return { kind: "deny", code: "FORBIDDEN_PATH", message: `このフォルダは操作できません: ${p.path}` };
    }
  }
  const isPlugin = tool.name.startsWith("plugin.");
  if (tool.name === "fs.delete") return { kind: "approve", reason: "delete", paths };
  if (tool.name === "fs.move") return { kind: "approve", reason: "move", paths };
  if (isPlugin && tool.effect !== "read" && tool.effect !== "none") {
    return remembered(tool, paths, "plugin_write", ctx) ? { kind: "allow", paths } : { kind: "approve", reason: "plugin_write", paths };
  }
  const outside = paths.filter((p) => !p.inside_workspace);
  if (outside.length === 0) return { kind: "allow", paths };
  const reason: ApprovalReason = ctx.tainted ? "tainted_outside" : "outside_workspace";
  if (remembered(tool, outside, reason, ctx)) return { kind: "allow", paths };
  return { kind: "approve", reason, paths };
}

/** 「この run の間は同じフォルダへの同じ操作を承認する」（プロトコル §4）。汚染中・削除は対象外。 */
function remembered(tool: ToolCatalogEntry, paths: ResolvedPath[], reason: ApprovalReason, ctx: PolicyContext): boolean {
  if (ctx.tainted || reason === "delete" || reason === "move" || reason === "tainted_outside") return false;
  return paths.every((p) =>
    ctx.remember.some(
      (r) => r.reason === reason && r.tool === tool.name && pathKey(dirname(p.real_path)).startsWith(r.dirKey),
    ),
  );
}

export function rememberRuleFor(tool: string, reason: ApprovalReason, paths: ResolvedPath[]): RememberRule[] {
  return paths.filter((p) => !p.inside_workspace).map((p) => ({ reason, tool, dirKey: pathKey(dirname(p.real_path)) }));
}

export { isInside };
