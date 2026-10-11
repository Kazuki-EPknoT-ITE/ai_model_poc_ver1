import { createHash, randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";

const ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** ULID（時刻順に並ぶ26文字の ID）。データモデル仕様 §1。 */
export function ulid(now: number = Date.now()): string {
  let t = now;
  let time = "";
  for (let i = 0; i < 10; i++) {
    time = ULID_ALPHABET[t % 32] + time;
    t = Math.floor(t / 32);
  }
  const rnd = randomBytes(16);
  let rand = "";
  for (let i = 0; i < 16; i++) rand += ULID_ALPHABET[rnd[i]! % 32];
  return time + rand;
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function sha256(data: string | Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

/** ファイルの sha256。存在しなければ null。 */
export async function fileHash(path: string): Promise<string | null> {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    const s = createReadStream(path);
    s.on("error", (e: NodeJS.ErrnoException) => (e.code === "ENOENT" ? resolve(null) : reject(e)));
    s.on("data", (c) => h.update(c));
    s.on("end", () => resolve(h.digest("hex")));
  });
}

/** キーを並べ替えた JSON（ダイジェスト計算用）。 */
export function stableStringify(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(",")}]`;
  const o = v as Record<string, unknown>;
  return `{${Object.keys(o)
    .sort()
    .filter((k) => o[k] !== undefined)
    .map((k) => `${JSON.stringify(k)}:${stableStringify(o[k])}`)
    .join(",")}}`;
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

/** 文字数からの概算トークン数（protocol §2.5）。 */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 1.5);
}

/** RFC 6901 の JSON Pointer。"*" は配列の全要素（protocol §2.2 の path_args）。 */
export function pointerGetAll(obj: unknown, pointer: string): { path: string[]; value: unknown }[] {
  const parts = pointer.split("/").slice(1).map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
  const out: { path: string[]; value: unknown }[] = [];
  const walk = (node: unknown, i: number, acc: string[]) => {
    if (i === parts.length) {
      if (node !== undefined) out.push({ path: acc, value: node });
      return;
    }
    const key = parts[i]!;
    if (node === null || typeof node !== "object") return;
    if (key === "*") {
      if (Array.isArray(node)) node.forEach((child, idx) => walk(child, i + 1, [...acc, String(idx)]));
      return;
    }
    walk((node as Record<string, unknown>)[key], i + 1, [...acc, key]);
  };
  walk(obj, 0, []);
  return out;
}

export function pointerSet(obj: unknown, path: string[], value: unknown): void {
  let node = obj as Record<string, unknown>;
  for (const key of path.slice(0, -1)) node = node[key] as Record<string, unknown>;
  node[path[path.length - 1]!] = value;
}
