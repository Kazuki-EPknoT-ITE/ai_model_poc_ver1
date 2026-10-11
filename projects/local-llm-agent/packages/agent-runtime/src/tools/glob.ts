// 最小限の glob（Node 20 には fs.glob が無いため）。"*" "?" "**" と、区切り文字 "/" "\" に対応する。
import { readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";

export function globToRegExp(glob: string): RegExp {
  const g = glob.replace(/\\/g, "/").replace(/^\.\//, "");
  let re = "";
  for (let i = 0; i < g.length; i++) {
    const c = g[i]!;
    if (c === "*") {
      if (g[i + 1] === "*") {
        const slash = g[i + 2] === "/";
        re += slash ? "(?:.*/)?" : ".*";
        i += slash ? 2 : 1;
      } else re += "[^/]*";
    } else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`, "i");
}

export interface WalkEntry {
  rel: string; // "/" 区切りの相対パス
  abs: string;
  dir: boolean;
}

/** root 以下を深さ優先で列挙する（隠しファイルとステージング用の一時ファイルは除く）。 */
export async function walk(root: string, maxDepth: number, limit = 20000, includeHidden = false): Promise<WalkEntry[]> {
  const out: WalkEntry[] = [];
  const rec = async (dir: string, depth: number) => {
    if (out.length >= limit) return;
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const e of entries) {
      if (out.length >= limit) return;
      if (e.name.startsWith(".~lla-")) continue;
      if (!includeHidden && e.name.startsWith(".")) continue;
      const abs = join(dir, e.name);
      const rel = relative(root, abs).split(sep).join("/");
      out.push({ rel, abs, dir: e.isDirectory() });
      if (e.isDirectory() && depth < maxDepth) await rec(abs, depth + 1);
    }
  };
  await rec(root, 1);
  return out;
}

/** glob に一致するファイル（ディレクトリは除く）。 */
export async function globFiles(root: string, glob: string): Promise<WalkEntry[]> {
  const re = globToRegExp(glob);
  const depth = glob.includes("**") ? 20 : glob.replace(/\\/g, "/").split("/").length;
  return (await walk(root, depth)).filter((e) => !e.dir && re.test(e.rel));
}
