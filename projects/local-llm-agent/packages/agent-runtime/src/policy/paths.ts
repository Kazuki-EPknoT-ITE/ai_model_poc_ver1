// 実体パスの解決と禁止領域の判定（設計書 §5.3、試験仕様 §5.2）。
import { realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, win32 } from "node:path";
import { homedir } from "node:os";

const IS_WIN = process.platform === "win32";
const RESERVED = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$/i;

export class PathError extends Error {
  constructor(
    message: string,
    readonly code: "FORBIDDEN_PATH" | "INVALID_ARGS",
  ) {
    super(message);
  }
}

/** 比較用のキー（Windows は大文字小文字を区別しない）。 */
export function pathKey(p: string): string {
  const n = IS_WIN ? win32.normalize(p) : resolve(p);
  return IS_WIN ? n.toLowerCase() : n;
}

/**
 * 引数のパスを実体パスにする。存在しない部分は、存在する最も近い祖先を realpath で解決してから連結する。
 * realpathSync.native はジャンクション・シンボリックリンク・8.3 形式の短い名前を最終パスに解決する。
 */
export function resolveRealPath(input: string, workspace: string): string {
  const raw = input.normalize("NFC").replace(/\//g, IS_WIN ? "\\" : "/");
  if (raw.length === 0) throw new PathError("パスが空です", "INVALID_ARGS");
  if (/^\\\\[?.]\\/.test(raw)) throw new PathError("デバイスパスは使えません", "FORBIDDEN_PATH");
  if (/[\0]/.test(raw)) throw new PathError("パスに使えない文字が含まれます", "INVALID_ARGS");
  const abs = isAbsolute(raw) ? stripTrailing(raw) : join(workspace, stripTrailing(raw));
  const normalized = IS_WIN ? win32.normalize(abs) : resolve(abs);
  // NTFS の代替データストリーム（file.txt:stream）を拒否する。ドライブ文字の ":" は除く。
  const withoutDrive = IS_WIN ? normalized.replace(/^[A-Za-z]:/, "") : normalized;
  if (withoutDrive.includes(":")) throw new PathError("代替データストリームは使えません", "FORBIDDEN_PATH");
  for (const seg of normalized.split(/[\\/]/)) {
    if (RESERVED.test(seg)) throw new PathError(`予約されたデバイス名です: ${seg}`, "INVALID_ARGS");
  }
  let existing = normalized;
  const rest: string[] = [];
  for (;;) {
    try {
      const real = realpathSync.native(existing);
      return rest.length ? join(real, ...rest.reverse()) : real;
    } catch {
      const parent = dirname(existing);
      if (parent === existing) return normalized;
      rest.push(basename(existing));
      existing = parent;
    }
  }
}

/**
 * Windows は各部分の末尾のドット・空白を取り除いて扱う（`D:\ws.\x` は `D:\ws\x`）。判定の前に同じ正規化をする（PX-07）。
 * ドットと空白だけの名前（`...` など）は使えない。
 */
function stripTrailing(p: string): string {
  if (!IS_WIN) return p;
  return p
    .split("\\")
    .map((seg, i) => {
      if (seg === "" || seg === "." || seg === ".." || (i === 0 && /^[A-Za-z]:$/.test(seg))) return seg;
      const s = seg.replace(/[. ]+$/, "");
      if (s === "") throw new PathError(`ドットと空白だけの名前は使えません: ${seg}`, "INVALID_ARGS");
      return s;
    })
    .join("\\");
}

export function isInside(real: string, root: string): boolean {
  const rel = relative(pathKey(root), pathKey(real));
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

/** 承認しても操作させない領域（SEC-03）。 */
export function forbiddenRoots(appDataDir: string): string[] {
  const env = process.env;
  const roots = [env.SystemRoot ?? env.windir, env.ProgramFiles, env["ProgramFiles(x86)"], env.ProgramData, appDataDir]
    .filter((x): x is string => Boolean(x))
    .map((p) => safeReal(p));
  return roots;
}

/** 他人のユーザープロファイル（C:\Users\<他人>）なら true。 */
export function isOtherUsersProfile(real: string): boolean {
  if (!IS_WIN) return false;
  const home = safeReal(homedir());
  const usersRoot = dirname(home);
  return isInside(real, usersRoot) && !isInside(real, home) && pathKey(real) !== pathKey(usersRoot);
}

export function isForbidden(real: string, roots: string[]): boolean {
  return roots.some((r) => isInside(real, r)) || isOtherUsersProfile(real);
}

function safeReal(p: string): string {
  try {
    return realpathSync.native(p);
  } catch {
    return resolve(p);
  }
}
