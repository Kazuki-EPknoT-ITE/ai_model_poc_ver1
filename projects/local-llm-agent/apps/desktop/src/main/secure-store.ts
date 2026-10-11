// safeStorage（Windows では DPAPI）で暗号化して保存する小さなストア（設計書 §4.4）。
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { safeStorage } from "electron";

export class SecureStore {
  constructor(private readonly dir: string) {
    mkdirSync(dir, { recursive: true });
  }

  private file(name: string): string {
    return join(this.dir, `${name}.bin`);
  }

  get(name: string): string | null {
    const f = this.file(name);
    if (!existsSync(f)) return null;
    const buf = readFileSync(f);
    try {
      return safeStorage.isEncryptionAvailable() ? safeStorage.decryptString(buf) : null;
    } catch {
      return null; // 別ユーザー・別PCで作られたもの
    }
  }

  set(name: string, value: string): void {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("この環境では安全な保存（DPAPI）が使えません");
    const f = this.file(name);
    mkdirSync(dirname(f), { recursive: true });
    writeFileSync(f, safeStorage.encryptString(value));
  }

  delete(name: string): void {
    rmSync(this.file(name), { force: true });
  }

  /** Run Store の鍵。初回は乱数で作る。 */
  dbKey(): string {
    const existing = this.get("db-key");
    if (existing) return existing;
    const key = randomBytes(32).toString("hex");
    this.set("db-key", key);
    return key;
  }
}
