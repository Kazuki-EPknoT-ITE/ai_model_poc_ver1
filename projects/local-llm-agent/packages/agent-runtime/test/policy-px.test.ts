// パスのすり抜け（試験仕様 §5.2 PX-01〜10）。ポリシーエンジンの判定を直接確かめる。
import { mkdirSync, mkdtempSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getTool, type ToolCatalogEntry } from "@lla/protocol";
import { decide, verifyUnchanged, type PolicyContext } from "../src/policy/policy.js";
import { resolveRealPath } from "../src/policy/paths.js";

let dir: string;
let ws: string;
let appData: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "lla-px-"));
  ws = join(dir, "ws");
  appData = join(dir, "appdata");
  mkdirSync(ws);
  mkdirSync(appData);
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const tool = (name: string): ToolCatalogEntry => getTool(name)!;
const ctx = (): PolicyContext => ({ workspace: ws, tainted: false, netLevel: "off", appDataDir: appData, remember: [] });
const stat = (path: string) => decide(tool("fs.stat"), { path }, ctx());
const isWin = process.platform === "win32";

describe("PX: path bypass", () => {
  it("PX-01: traversal into the system folder is forbidden", () => {
    const win = process.env.SystemRoot ?? "C:\\Windows";
    // 作業フォルダからの相対で Windows フォルダを指す
    const rel = join(...Array(ws.split(/[\\/]/).length).fill(".."), win.replace(/^[A-Za-z]:\\/, ""), "System32", "drivers", "etc", "hosts");
    expect(stat(rel)).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
  });

  it("PX-02/03: links are judged by their real target", () => {
    const outside = join(dir, "outside");
    mkdirSync(outside);
    writeFileSync(join(outside, "x.txt"), "x");
    try {
      symlinkSync(outside, join(ws, "link"), "junction");
    } catch {
      return;
    }
    expect(decide(tool("fs.copy"), { src: "a.txt", dst: "link\\new.txt" }, ctx())).toMatchObject({ kind: "approve", reason: "outside_workspace" });
    // ファイルのシンボリックリンク（作成に権限が要る環境では省略）
    try {
      symlinkSync(join(outside, "x.txt"), join(ws, "file-link.txt"), "file");
    } catch {
      return;
    }
    expect(stat("file-link.txt")).toMatchObject({ kind: "approve", reason: "outside_workspace" });
  });

  it("PX-04: device paths are denied", () => {
    expect(stat("\\\\?\\C:\\Windows\\win.ini")).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(stat("\\\\.\\C:\\Windows\\win.ini")).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(stat("\\\\.\\PhysicalDrive0")).toMatchObject({ kind: "deny" });
  });

  it("PX-05: 8.3 short names resolve to the long name", () => {
    if (!isWin) return;
    let short: string;
    try {
      short = realpathSync.native("C:\\PROGRA~1");
    } catch {
      return; // 8.3 名が無効なボリューム
    }
    expect(short.toLowerCase()).toContain("program files");
    expect(stat("C:\\PROGRA~1\\x.txt")).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
  });

  it("PX-06: case differences stay inside the workspace", () => {
    expect(stat(ws.toUpperCase() + "\\A.XLSX").kind).toBe("allow");
  });

  it("PX-07: trailing dots/spaces are normalized before judging, and ADS is denied", () => {
    if (!isWin) return;
    // Windows は末尾のドット・空白を取り除いて扱う → 作業フォルダの中として判定する
    expect(resolveRealPath(`${ws}.\\x.txt`, ws).toLowerCase()).toBe(join(ws, "x.txt").toLowerCase());
    expect(resolveRealPath("sub. \\x.txt", ws).toLowerCase()).toBe(join(ws, "sub", "x.txt").toLowerCase());
    expect(stat(`${ws}. \\x.txt`).kind).toBe("allow");
    // ドットと空白だけの名前は使えない
    expect(stat("... \\x.txt")).toMatchObject({ kind: "deny", code: "INVALID_ARGS" });
    expect(stat("a.xlsx:evil")).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
    expect(stat("a.xlsx::$DATA")).toMatchObject({ kind: "deny", code: "FORBIDDEN_PATH" });
  });

  it("PX-08: a folder swapped for a junction after the decision is detected (TOCTOU)", () => {
    const outside = join(dir, "outside");
    mkdirSync(outside);
    mkdirSync(join(ws, "sub"));
    const d = decide(tool("fs.copy"), { src: "a.txt", dst: "sub\\b.txt" }, ctx());
    expect(d.kind).toBe("allow");
    if (d.kind !== "allow") return;
    expect(verifyUnchanged(d.paths, ws)).toBeNull();
    // 判定の後で sub をフォルダ外へのジャンクションに差し替える
    renameSync(join(ws, "sub"), join(ws, "sub-old"));
    try {
      symlinkSync(outside, join(ws, "sub"), "junction");
    } catch {
      return;
    }
    expect(verifyUnchanged(d.paths, ws)).toBe("sub\\b.txt");
  });

  it("PX-09: moving out of the workspace needs approval", () => {
    const d = decide(tool("fs.move"), { src: "a.txt", dst: join(dir, "outside", "a.txt") }, ctx());
    expect(d).toMatchObject({ kind: "approve", reason: "move" });
  });

  it("PX-10: reserved device names are invalid", () => {
    for (const name of ["CON", "nul.txt", "sub\\COM1.csv", "LPT9", "aux"]) {
      expect(stat(name), name).toMatchObject({ kind: "deny", code: "INVALID_ARGS" });
    }
    expect(stat("console.txt").kind).toBe("allow"); // 予約名で始まるだけの名前は使える
  });
});
