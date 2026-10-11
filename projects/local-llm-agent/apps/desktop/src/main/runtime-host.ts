// Runtime ホスト（Node の子プロセス）の起動・監視・再起動（RUN-07）。
// Electron の ABI に縛られないよう、Runtime は Electron ではなく Node で動かす（ADR-001 改訂）。
import { fork, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import type { RuntimeCommand, RuntimeEvent } from "@lla/protocol";
import type { HostInit, HostToMain, MainToHost } from "../shared/ipc.js";

export interface HostOptions {
  script: string;
  nodePath: string;
  init: () => HostInit;
}

type HostEvents = {
  event: [ev: RuntimeEvent];
  status: [s: { state: "starting" | "ready" | "restarting" | "stopped"; warnings?: string[] }];
  tokens: [t: { accessToken: string; refreshToken: string }];
  auth_failed: [];
};

export class RuntimeHost extends EventEmitter<HostEvents> {
  private child: ChildProcess | null = null;
  private pending = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private seq = 0;
  private restarts = 0;
  private stopping = false;
  private ready: Promise<void> | null = null;

  constructor(private readonly opts: HostOptions) {
    super();
  }

  start(): Promise<void> {
    this.stopping = false;
    this.emit("status", { state: this.restarts ? "restarting" : "starting" });
    const child = fork(this.opts.script, [], {
      execPath: this.opts.nodePath,
      stdio: ["ignore", "inherit", "inherit", "ipc"],
      // Electron 内蔵の Node と外部の Node は V8 の版が違うため、advanced（structured clone）ではなく JSON で送る
      serialization: "json",
      // GUI の Electron から node.exe を起動するとコンソール窓が開くため隠す（spawn に渡され有効になる）
      ...({ windowsHide: true } as object),
    });
    this.child = child;
    this.ready = new Promise((resolve, reject) => {
      const onMsg = (m: HostToMain) => {
        if (m.type === "ready") {
          this.restarts = 0;
          this.emit("status", { state: "ready", warnings: m.warnings });
          resolve();
        } else if (m.type === "fatal") reject(new Error(m.error));
      };
      child.on("message", onMsg);
      child.once("exit", () => reject(new Error("Runtime の起動に失敗しました")));
    });
    child.on("message", (m: HostToMain) => this.onMessage(m));
    child.on("exit", (code) => {
      this.child = null;
      for (const p of this.pending.values()) p.reject(new Error("Runtime が停止しました"));
      this.pending.clear();
      if (this.stopping) {
        this.emit("status", { state: "stopped" });
        return;
      }
      // 異常終了したら間隔を空けて再起動する。再起動後は Run Store から自動で再開する（RUN-02）
      this.restarts++;
      const delay = Math.min(30_000, 500 * 2 ** Math.min(this.restarts, 6));
      this.emit("status", { state: "restarting" });
      setTimeout(() => void this.start().catch(() => undefined), delay);
      void code;
    });
    this.send(this.opts.init());
    return this.ready;
  }

  private send(m: MainToHost): void {
    this.child?.send(m);
  }

  private onMessage(m: HostToMain): void {
    switch (m.type) {
      case "event":
        this.emit("event", m.event);
        break;
      case "reply": {
        const p = this.pending.get(m.id);
        if (!p) break;
        this.pending.delete(m.id);
        if (m.ok) p.resolve(m.result);
        else p.reject(new Error(m.error));
        break;
      }
      case "tokens":
        this.emit("tokens", { accessToken: m.accessToken, refreshToken: m.refreshToken });
        break;
      case "auth_failed":
        this.emit("auth_failed");
        break;
    }
  }

  async command(command: RuntimeCommand): Promise<unknown> {
    await this.ready;
    if (!this.child) throw new Error("Runtime が起動していません");
    const id = `m${++this.seq}`;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.send({ type: "command", id, command });
    });
  }

  updateTokens(accessToken: string, refreshToken: string): void {
    this.send({ type: "tokens", accessToken, refreshToken });
  }

  get running(): boolean {
    return this.child !== null;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    const child = this.child;
    if (!child) return;
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        child.kill();
        resolve();
      }, 5000);
      child.once("exit", () => {
        clearTimeout(t);
        resolve();
      });
      this.send({ type: "shutdown" });
    });
  }
}
