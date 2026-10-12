// モック Gateway（eval/mock-gateway）のプロセス管理。停止・同じポートでの再起動（障害注入）に対応する。
import type { ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { join } from "node:path";
import { PROJECT, freePort, killTree, spawnLines, waitHttp, waitUntil } from "./env.js";

export interface MockRequest {
  ts: number;
  method: string;
  path: string;
  idempotency_key: string | null;
  last_event_id: string | null;
  status: number;
  incarnation: number;
}

export class MockGatewayProc {
  port = 0;
  private proc: ChildProcess | null = null;
  private incarnation = 0;
  private readonly records: string[] = [];
  readonly log: string[] = [];

  constructor(
    readonly scenarioPath: string,
    readonly recordDir: string,
    private readonly opts: { pingIntervalS?: number; port?: number; /** 推論の入力と出力の組を書き出す（ml/datagen） */ samples?: string } = {},
  ) {}

  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }
  get running(): boolean {
    return this.proc !== null;
  }

  async start(): Promise<void> {
    if (this.proc) return;
    if (!this.port) this.port = this.opts.port ?? (await freePort());
    mkdirSync(this.recordDir, { recursive: true });
    this.incarnation++;
    const record = join(this.recordDir, `requests-${this.incarnation}.jsonl`);
    this.records.push(record);
    this.proc = spawnLines(
      "uv",
      [
        "run",
        "--project",
        PROJECT,
        "mock-gateway",
        "--scenario",
        this.scenarioPath,
        "--port",
        String(this.port),
        "--ping-interval",
        String(this.opts.pingIntervalS ?? 0.5),
        "--record",
        record,
        ...(this.opts.samples ? ["--record-samples", this.opts.samples] : []),
      ],
      { onLine: (l) => this.log.push(l) },
    );
    const proc = this.proc;
    proc.on("exit", () => {
      if (this.proc === proc) this.proc = null;
    });
    await waitHttp(`${this.url}/healthz`, 90_000);
  }

  /** プロセスごと止める（応答なし・再起動の模擬）。ポートが空くまで待つ。 */
  async stop(): Promise<void> {
    const p = this.proc;
    this.proc = null;
    if (!p) return;
    await killTree(p.pid);
    await waitUntil(() => portClosed(this.port), 30_000, "mock port closed", 100);
  }

  async restart(downMs = 0): Promise<void> {
    await this.stop();
    if (downMs > 0) await new Promise((r) => setTimeout(r, downMs));
    await this.start();
  }

  /** これまでの全プロセスが受け取ったリクエスト（再起動をまたいで連結）。 */
  requests(): MockRequest[] {
    const out: MockRequest[] = [];
    this.records.forEach((file, i) => {
      if (!existsSync(file)) return;
      for (const line of readFileSync(file, "utf8").split("\n")) {
        if (!line.trim()) continue;
        try {
          out.push({ ...(JSON.parse(line) as Omit<MockRequest, "incarnation">), incarnation: i + 1 });
        } catch {
          /* 書きかけの行 */
        }
      }
    });
    return out;
  }
}

function portClosed(port: number): Promise<boolean> {
  return new Promise((res) => {
    const s = createConnection({ port, host: "127.0.0.1" });
    s.once("connect", () => {
      s.destroy();
      res(false);
    });
    s.once("error", () => res(true));
  });
}
