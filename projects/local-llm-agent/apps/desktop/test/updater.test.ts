// 自動更新（ADM-05）: sha512・大きさが合わない更新は捨て、合うものだけを用意する
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Updater } from "../src/main/updater.js";

let server: Server | null = null;
afterEach(() => server?.close());

async function serve(info: Record<string, unknown>, body: string): Promise<string> {
  server = createServer((req, res) => {
    if (req.headers.authorization !== "Bearer t") {
      res.writeHead(401).end();
      return;
    }
    if (req.url === "/v1/app/latest") res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(info));
    else if (req.url === `/v1/app/download/${info.file}`) res.writeHead(200).end(body);
    else res.writeHead(404).end();
  });
  await new Promise<void>((r) => server!.listen(0, "127.0.0.1", r));
  return `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
}

const infoFor = (body: string, version = "0.2.0") => ({
  available: true,
  version,
  file: `LocalLLMAgent-Setup-${version}.exe`,
  sha512: createHash("sha512").update(body).digest("hex"),
  size: Buffer.byteLength(body),
  notes: "",
});

function make(base: string, dir: string, ready: unknown[]) {
  return new Updater({ bases: () => ["http://127.0.0.1:9", base], token: () => "t", dir, currentVersion: "0.1.0", onReady: (i) => ready.push(i), dryRun: true });
}

describe("Updater", () => {
  it("downloads a newer version, verifies it, and records the install request", async () => {
    const base = await serve(infoFor("good installer"), "good installer");
    const dir = mkdtempSync(join(tmpdir(), "lla-upd-"));
    const ready: unknown[] = [];
    const u = make(base, dir, ready);
    await u.check();
    expect(ready).toHaveLength(1);
    expect(readFileSync(join(dir, "LocalLLMAgent-Setup-0.2.0.exe"), "utf8")).toBe("good installer");
    u.install();
    expect(JSON.parse(readFileSync(join(dir, "install-requested.json"), "utf8")).args).toEqual(["/S"]);
  });

  it("discards a download whose sha512 does not match", async () => {
    const base = await serve(infoFor("good installer"), "evil installer"); // 同じ大きさで中身が違う
    const dir = mkdtempSync(join(tmpdir(), "lla-upd-"));
    const ready: unknown[] = [];
    const u = make(base, dir, ready);
    await u.check();
    expect(ready).toHaveLength(0);
    expect(readdirSync(dir)).toEqual([]);
    expect(() => u.install()).toThrow();
  });

  it("ignores versions that are not newer", async () => {
    const base = await serve(infoFor("x", "0.1.0"), "x");
    const dir = mkdtempSync(join(tmpdir(), "lla-upd-"));
    const ready: unknown[] = [];
    await make(base, dir, ready).check();
    expect(ready).toHaveLength(0);
    expect(existsSync(join(dir, "LocalLLMAgent-Setup-0.1.0.exe"))).toBe(false);
  });
});
