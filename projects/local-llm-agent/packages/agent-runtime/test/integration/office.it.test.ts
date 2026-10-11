// 結合試験 I-01（試験仕様 §2.3）: Runtime ＋ office-tools（実プロセス）＋ モック Gateway
// 請求書3件 → batch.run(invoice.extract) → 送金明細.xlsx → final
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { McpToolHost } from "../../src/tools/mcp.js";
import { createSamplingHandler } from "../../src/sampling.js";
import { PROJECT, cmd, dump, makeRig, waitFor, type Rig } from "./harness.js";

let rig: Rig | null = null;
afterEach(async () => {
  await rig?.close();
  rig = null;
});

function readXlsx(path: string): unknown[][] {
  const out = execFileSync(
    "uv",
    ["run", "--project", PROJECT, "python", "-c", "import json,sys,openpyxl;ws=openpyxl.load_workbook(sys.argv[1]).active;print(json.dumps([[c.value for c in r] for r in ws.iter_rows()], default=str))", path],
    { encoding: "utf8" },
  );
  return JSON.parse(out) as unknown[][];
}

describe("runtime + office-tools", () => {
  it("I-01: invoices → batch extract → Excel, with checks and audit", async () => {
    rig = await makeRig("../invoice-basic.yaml", {}, (gateway) => [
      new McpToolHost(
        { id: "office", command: "uv", args: ["run", "--project", PROJECT, "office-tools"], cwd: PROJECT },
        createSamplingHandler(gateway, () => "local-12b"),
      ),
    ]);
    execFileSync("uv", ["run", "--project", PROJECT, "python", join(import.meta.dirname, "make_invoices.py"), join(rig.ws, "請求書"), "3"]);
    const { run_id } = (await rig.runtime.handle(
      cmd("run.start", { conversation_id: "cv1", text: "請求書フォルダのPDFを読んで、送金明細をExcelにまとめて", attachments: [], workspace: rig.ws, model: "auto" }),
    )) as { run_id: string };
    await waitFor(() => rig!.store.getRun(run_id)?.ended_at, 120_000, "run end").catch((e: Error) => {
      throw new Error(`${e.message}
${dump(rig!, run_id)}`);
    });
    const run = rig.store.getRun(run_id)!;
    expect(JSON.parse(run.final_action!).summary).toContain("3件");

    const out = join(rig.ws, "送金明細.xlsx");
    expect(existsSync(out)).toBe(true);
    const rows = readXlsx(out);
    expect(rows[0]).toEqual(["請求日", "請求元", "通貨", "金額"]);
    expect(rows.slice(1).map((r) => [r[1], r[2], Number(r[3])])).toEqual([
      ["Example Shipping Co. 1", "USD", 1000],
      ["Example Shipping Co. 2", "USD", 2000],
      ["Example Shipping Co. 3", "USD", 3000],
    ]);
    // 一括処理の進捗が流れ、出力は1回の意図記録で書かれている
    expect(rig.events.filter((e) => e.type === "batch_progress").length).toBe(3);
    const intents = rig.store.intentsForRun(run_id).filter((i) => i.status === "committed");
    expect(intents.map((i) => i.path.split(/[\\/]/).pop())).toEqual(["送金明細.xlsx"]);
    // 監査ログ（送信待ち）にファイル操作が残っている。本文は入っていない
    const audit = rig.store.unsentAudit().map((a) => JSON.parse(a.record) as { action: string; target: string });
    expect(audit.some((a) => a.action === "file.create" && a.target.endsWith("送金明細.xlsx"))).toBe(true);
  }, 180_000);
});
