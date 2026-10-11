// 評価ハーネスと障害注入試験の共通部品: パス・子プロセス・待ち合わせ。
import { execFile, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const HARNESS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const EVAL_DIR = resolve(HARNESS_DIR, "..");
export const PROJECT = resolve(EVAL_DIR, "..");
export const SETS_DIR = join(EVAL_DIR, "sets");
export const IS_WINDOWS = process.platform === "win32";

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** vitest に同梱の vite-node（TypeScript をそのまま動かす）。子プロセスで Runtime を動かすのに使う。 */
export function viteNodeBin(): string {
  const req = createRequire(join(PROJECT, "package.json"));
  const vitest = req.resolve("vitest/package.json");
  const bin = join(dirname(createRequire(vitest).resolve("vite-node/package.json")), "vite-node.mjs");
  if (!existsSync(bin)) throw new Error(`vite-node が見つかりません: ${bin}`);
  return bin;
}

export async function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const s = createServer();
    s.on("error", rej);
    s.listen(0, "127.0.0.1", () => {
      const port = (s.address() as { port: number }).port;
      s.close(() => res(port));
    });
  });
}

export interface ExecResult {
  code: number;
  stdout: string;
  stderr: string;
}

export function exec(cmd: string, args: string[], opts: { cwd?: string; env?: NodeJS.ProcessEnv; timeoutMs?: number } = {}): Promise<ExecResult> {
  return new Promise((res) => {
    execFile(
      cmd,
      args,
      { cwd: opts.cwd ?? PROJECT, env: opts.env ?? process.env, timeout: opts.timeoutMs ?? 600_000, maxBuffer: 256 * 1024 * 1024, windowsHide: true, encoding: "utf8" },
      (err, stdout, stderr) => {
        const code = err ? (typeof (err as { code?: unknown }).code === "number" ? ((err as { code: number }).code) : 1) : 0;
        res({ code, stdout, stderr });
      },
    );
  });
}

/** `uv run python ...`（eval/harness を PYTHONPATH に入れる）。 */
export function uvPython(args: string[], timeoutMs = 600_000): Promise<ExecResult> {
  const env = { ...process.env, PYTHONPATH: HARNESS_DIR, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  return exec("uv", ["run", "--project", PROJECT, "python", ...args], { env, timeoutMs });
}

/** `python -m lla_eval <args>` を実行し、標準出力の最後の JSON 行を返す。 */
export async function llaEval<T>(args: string[], opts: { allowFail?: boolean; echoStderr?: boolean } = {}): Promise<T> {
  const r = await uvPython(["-m", "lla_eval", ...args]);
  if (opts.echoStderr && r.stderr.trim()) process.stderr.write(r.stderr.replace(/^.*warning: .*$/gim, "").trimStart());
  const line = r.stdout.trim().split(/\r?\n/).filter((l) => l.startsWith("{") || l.startsWith("[")).pop();
  if (!line) throw new Error(`lla_eval ${args[0]} が結果を返しません (code=${r.code})\n${r.stderr}`);
  const parsed = JSON.parse(line) as T & { error?: string[] };
  if (parsed && typeof parsed === "object" && "error" in parsed && Array.isArray(parsed.error) && !opts.allowFail) {
    throw new Error(`lla_eval ${args[0]}: ${parsed.error.join(" / ")}`);
  }
  return parsed;
}

/** プロセスを子孫ごと強制終了する（Windows は taskkill /F /T、FI の「強制終了」）。 */
export async function killTree(pid: number | undefined): Promise<void> {
  if (!pid) return;
  if (IS_WINDOWS) {
    await exec("taskkill", ["/F", "/T", "/PID", String(pid)], { timeoutMs: 30_000 });
  } else {
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        /* 既に終了 */
      }
    }
  }
}

export interface ProcInfo {
  pid: number;
  ppid: number;
  name: string;
  cmd: string;
}

/** 子孫プロセスの一覧（Windows: CIM）。office-tools だけを落とすときに使う。 */
export async function descendants(root: number): Promise<ProcInfo[]> {
  if (!IS_WINDOWS) return [];
  const r = await exec(
    "powershell",
    ["-NoProfile", "-NonInteractive", "-Command", "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"],
    { timeoutMs: 60_000 },
  );
  const all = (JSON.parse(r.stdout || "[]") as { ProcessId: number; ParentProcessId: number; Name: string; CommandLine: string | null }[]).map((p) => ({
    pid: p.ProcessId,
    ppid: p.ParentProcessId,
    name: p.Name,
    cmd: p.CommandLine ?? "",
  }));
  const out: ProcInfo[] = [];
  const queue = [root];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const p of all) {
      if (p.ppid === cur && p.pid !== cur && !out.some((o) => o.pid === p.pid)) {
        out.push(p);
        queue.push(p.pid);
      }
    }
  }
  return out;
}

export async function waitUntil<T>(fn: () => T | undefined | false | null | Promise<T | undefined | false | null>, timeoutMs: number, label: string, intervalMs = 50): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > deadline) throw new Error(`timeout waiting for ${label} (${timeoutMs}ms)`);
    await sleep(intervalMs);
  }
}

export async function waitHttp(url: string, timeoutMs = 60_000): Promise<void> {
  await waitUntil(
    async () => {
      try {
        return (await fetch(url, { signal: AbortSignal.timeout(2000) })).ok;
      } catch {
        return false;
      }
    },
    timeoutMs,
    `HTTP ${url}`,
    200,
  );
}

/** 子プロセスの起動（標準出力を行ごとに受け取る）。 */
export function spawnLines(cmd: string, args: string[], opts: { cwd?: string; env?: NodeJS.ProcessEnv; onLine?: (line: string, stream: "out" | "err") => void }) {
  const proc = spawn(cmd, args, { cwd: opts.cwd ?? PROJECT, env: opts.env ?? process.env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  const feed = (stream: "out" | "err") => {
    let buf = "";
    return (chunk: Buffer) => {
      buf += chunk.toString("utf8");
      let i: number;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).replace(/\r$/, "");
        buf = buf.slice(i + 1);
        opts.onLine?.(line, stream);
      }
    };
  };
  proc.stdout.on("data", feed("out"));
  proc.stderr.on("data", feed("err"));
  return proc;
}
