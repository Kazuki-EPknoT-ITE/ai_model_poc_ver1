// アプリの自動更新（ADM-05）: Gateway から新しい版を取り、sha512 を確かめてから、利用者の操作でインストールする。
// - 確認: GET /v1/app/latest（ログイン中のトークン）。今の版より新しいときだけ取る。
// - 取得: GET /v1/app/download/<file> を一時ファイルに書き、大きさと sha512 が latest と合えば置き換える（合わなければ捨てる）。
// - インストール: 作業中でないときに利用者が選ぶと、NSIS のインストーラーを /S（無人）で起動してアプリを終える。
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { isNewer } from "../shared/version.js";

export interface UpdateInfo {
  version: string;
  file: string;
  sha512: string;
  size: number;
  notes: string;
}

export interface UpdaterOptions {
  /** 確認する Gateway（先頭から順に。繋がらなければ次） */
  bases: () => string[];
  token: () => string | null;
  dir: string;
  currentVersion: string;
  onReady: (info: UpdateInfo & { path: string }) => void;
  log?: (msg: string) => void;
  /** 試験用: インストーラーを起動せず、起動したことをファイルに記録する */
  dryRun?: boolean;
}

const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.exe$/;

export class Updater {
  ready: (UpdateInfo & { path: string }) | null = null;
  private checking = false;

  constructor(private readonly o: UpdaterOptions) {}

  private async get(path: string): Promise<Response> {
    const token = this.o.token();
    if (!token) throw new Error("ログインしていません");
    let last: unknown = null;
    for (const base of this.o.bases()) {
      try {
        const r = await fetch(`${base}${path}`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(120_000) });
        if (r.ok) return r;
        last = new Error(`HTTP ${r.status}`);
      } catch (e) {
        last = e;
      }
    }
    throw last instanceof Error ? last : new Error("更新を確認できません");
  }

  /** 新しい版があれば取って確かめる。見つかれば onReady を呼ぶ。 */
  async check(): Promise<void> {
    if (this.checking || this.ready) return;
    this.checking = true;
    try {
      const info = (await (await this.get("/v1/app/latest")).json()) as UpdateInfo & { available: boolean };
      if (!info.available || !isNewer(info.version, this.o.currentVersion)) return;
      if (!SAFE_NAME.test(info.file) || !/^[0-9a-f]{128}$/.test(info.sha512)) throw new Error("更新の情報が不正です");
      mkdirSync(this.o.dir, { recursive: true });
      const path = join(this.o.dir, info.file);
      if (!(existsSync(path) && this.verify(path, info))) {
        await this.download(info, path);
      }
      this.ready = { ...info, path };
      this.o.onReady(this.ready);
    } catch (e) {
      this.o.log?.(`update check failed: ${(e as Error).message}`);
    } finally {
      this.checking = false;
    }
  }

  private verify(path: string, info: UpdateInfo): boolean {
    if (statSync(path).size !== info.size) return false;
    return createHash("sha512").update(readFileSync(path)).digest("hex") === info.sha512;
  }

  private async download(info: UpdateInfo, path: string): Promise<void> {
    const part = `${path}.part`;
    const r = await this.get(`/v1/app/download/${encodeURIComponent(info.file)}`);
    if (!r.body) throw new Error("更新ファイルの本文がありません");
    const hash = createHash("sha512");
    const out = createWriteStream(part);
    let size = 0;
    for await (const chunk of r.body as unknown as AsyncIterable<Uint8Array>) {
      size += chunk.length;
      if (size > info.size) break;
      hash.update(chunk);
      if (!out.write(chunk)) await new Promise<void>((res) => out.once("drain", () => res()));
    }
    await new Promise<void>((res, rej) => out.end((e?: Error | null) => (e ? rej(e) : res())));
    if (size !== info.size || hash.digest("hex") !== info.sha512) {
      rmSync(part, { force: true });
      throw new Error("更新ファイルの検証に失敗しました（大きさか sha512 が合いません）");
    }
    rmSync(path, { force: true });
    renameSync(part, path);
  }

  /** インストーラーを無人モードで起動する（アプリは呼び出し側が終える）。 */
  install(): void {
    if (!this.ready) throw new Error("更新はありません");
    if (!this.verify(this.ready.path, this.ready)) throw new Error("更新ファイルが変わっています。もう一度確認してください");
    const args = ["/S"];
    if (this.o.dryRun) {
      writeFileSync(join(this.o.dir, "install-requested.json"), JSON.stringify({ path: this.ready.path, args }));
      return;
    }
    spawn(this.ready.path, args, { detached: true, stdio: "ignore", windowsHide: false }).unref();
  }
}
