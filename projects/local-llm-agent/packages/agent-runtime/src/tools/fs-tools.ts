// fs-tools（プロトコル §3.1）。ポリシー判定と書き込みの意図記録は呼び出し側（WriteCoordinator）が行う。
// ここに渡されるパスは、ポリシー判定済みの実体パス、または書き込み時のステージングパス。
import { copyFile, mkdir, readFile, stat } from "node:fs/promises";
import { isAbsolute, join, relative, sep } from "node:path";
import type { ToolResult, UntrustedBlock } from "@lla/protocol";
import { fileHash } from "../util.js";
import { globToRegExp, walk } from "./glob.js";
import { ToolFailure, okResult, type ToolContext } from "./types.js";

const abs = (p: string, ctx: ToolContext) => (isAbsolute(p) ? p : join(ctx.workspace, p));
const relOut = (p: string, ctx: ToolContext) => {
  const r = relative(ctx.workspace, p);
  return r.startsWith("..") || isAbsolute(r) ? p : r.split(sep).join("/");
};

async function statOrFail(p: string) {
  try {
    return await stat(p);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") throw new ToolFailure("NOT_FOUND", `${p} は存在しません。fs.list で確認してください`);
    throw e;
  }
}

export async function fsList(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const root = abs(String(args.path ?? "."), ctx);
  const s = await statOrFail(root);
  if (!s.isDirectory()) throw new ToolFailure("INVALID_ARGS", "path はフォルダを指定してください");
  const depth = Number(args.depth ?? 1);
  const glob = args.glob ? globToRegExp(String(args.glob)) : null;
  const entries = (await walk(root, glob && String(args.glob).includes("**") ? 5 : depth, 20000, Boolean(args.include_hidden)))
    .filter((e) => !glob || e.dir === false)
    .filter((e) => !glob || glob.test(e.rel));
  const rows = [];
  for (const e of entries.slice(0, 500)) {
    const st = await stat(e.abs).catch(() => null);
    rows.push({ path: relOut(e.abs, ctx), type: e.dir ? "dir" : "file", size: st?.size ?? 0, mtime: st?.mtime.toISOString() ?? "" });
  }
  const warnings = entries.length > 500 ? [`${entries.length}件中500件だけを返しました。glob で絞り込んでください`] : undefined;
  return okResult({ entries: rows }, warnings ? { warnings } : {});
}

export async function fsStat(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const p = abs(String(args.path), ctx);
  const s = await statOrFail(p);
  const data: Record<string, unknown> = {
    path: relOut(p, ctx),
    type: s.isDirectory() ? "dir" : "file",
    size: s.size,
    mtime: s.mtime.toISOString(),
    locked: false,
  };
  if (s.isFile()) {
    data.hash = await fileHash(p);
    data.locked = await isLocked(p);
  }
  return okResult(data);
}

/** 他のアプリが書き込みロックしているか（Windows で r+ を開けなければロック中とみなす）。 */
async function isLocked(p: string): Promise<boolean> {
  const { open } = await import("node:fs/promises");
  try {
    const fh = await open(p, "r+");
    await fh.close();
    return false;
  } catch (e) {
    return ["EBUSY", "EPERM", "EACCES"].includes((e as NodeJS.ErrnoException).code ?? "");
  }
}

export async function fsReadText(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const p = abs(String(args.path), ctx);
  await statOrFail(p);
  const buf = await readFile(p);
  const enc = String(args.encoding ?? "auto");
  let text: string;
  let used = "utf-8";
  if (enc === "cp932" || (enc === "auto" && !isUtf8(buf))) {
    text = new TextDecoder("shift_jis").decode(buf);
    used = "cp932";
  } else text = new TextDecoder("utf-8").decode(buf).replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/);
  const offset = Number(args.offset_lines ?? 0);
  const max = Number(args.max_lines ?? 400);
  const slice = lines.slice(offset, offset + max);
  return okResult(
    { text_ref: "u1", encoding: used, total_lines: lines.length },
    {
      untrusted: [{ source: "file", ref: `${relOut(p, ctx)}#L${offset + 1}-L${offset + slice.length}`, text: slice.join("\n") }],
      ...(offset + max < lines.length ? { warnings: [`続きは offset_lines=${offset + max} で読めます`] } : {}),
    },
  );
}

function isUtf8(buf: Buffer): boolean {
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(buf);
    return true;
  } catch {
    return false;
  }
}

export async function fsSearch(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const root = abs(String(args.path ?? "."), ctx);
  const nameRe = args.name_glob ? globToRegExp(String(args.name_glob).includes("/") ? String(args.name_glob) : `**/${String(args.name_glob)}`) : null;
  const content = args.content ? String(args.content) : null;
  const max = Number(args.max_results ?? 50);
  const hits: { path: string; line?: number; snippet_ref?: string }[] = [];
  const untrusted: UntrustedBlock[] = [];
  for (const e of await walk(root, 10)) {
    if (hits.length >= max) break;
    if (e.dir || (nameRe && !nameRe.test(e.rel))) continue;
    if (!content) {
      hits.push({ path: relOut(e.abs, ctx) });
      continue;
    }
    // 本文検索はテキスト系だけ（Office/PDF の本文検索は office-tools の索引で行う。T-60 以降）
    if (!/\.(txt|md|csv|json|log|xml|html?)$/i.test(e.rel)) continue;
    const text = await readFile(e.abs, "utf8").catch(() => "");
    text.split(/\r?\n/).forEach((line, i) => {
      if (hits.length < max && line.includes(content)) {
        const ref = `s${hits.length + 1}`;
        hits.push({ path: relOut(e.abs, ctx), line: i + 1, snippet_ref: ref });
        untrusted.push({ source: "file", ref: `${relOut(e.abs, ctx)}#L${i + 1}`, text: line.slice(0, 300) });
      }
    });
  }
  return okResult({ hits }, untrusted.length ? { untrusted } : {});
}

export async function fsMkdir(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const p = abs(String(args.path), ctx);
  await mkdir(p, { recursive: true });
  return okResult({ path: relOut(p, ctx) });
}

/** fs.copy の実体（dst はステージングパスが渡される）。 */
export async function fsCopy(args: Record<string, unknown>, ctx: ToolContext): Promise<ToolResult> {
  const src = abs(String(args.src), ctx);
  const dst = abs(String(args.dst), ctx);
  const s = await statOrFail(src);
  if (!s.isFile()) throw new ToolFailure("INVALID_ARGS", "src はファイルを指定してください");
  await copyFile(src, dst);
  return okResult({ artifact: { path: dst, op: "copy" } }, { artifacts: [{ path: dst, op: "copy", post_hash: (await fileHash(dst))! }] });
}
