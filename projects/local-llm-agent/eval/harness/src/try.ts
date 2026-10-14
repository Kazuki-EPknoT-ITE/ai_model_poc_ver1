// 1件の依頼を実際のモデル（Gateway 経由）で動かし、手順と答えを表示する（開発 PC での動作確認用）。
//   pnpm --filter @lla/eval-harness try -- --gateway http://127.0.0.1:8787 --login dev --password dev-password-1 [--workspace <フォルダ>] [--max-steps 30] "依頼"
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { RuntimeEvent } from "@lla/protocol";
import { sleep } from "./env.js";
import { createHeadless } from "./headless.js";

const argv = process.argv.slice(2).filter((a) => a !== "--");
const o = { gateway: "http://127.0.0.1:8787", login: "dev", password: "dev-password-1", workspace: "", maxSteps: 30, text: "" };
for (let i = 0; i < argv.length; i++) {
  const a = argv[i]!;
  if (a === "--gateway") o.gateway = argv[++i]!;
  else if (a === "--login") o.login = argv[++i]!;
  else if (a === "--password") o.password = argv[++i]!;
  else if (a === "--workspace") o.workspace = argv[++i]!;
  else if (a === "--max-steps") o.maxSteps = Number(argv[++i]);
  else o.text = a;
}
const dir = mkdtempSync(join(tmpdir(), "lla-try-"));
const ws = o.workspace || join(dir, "ws");
mkdirSync(ws, { recursive: true });
mkdirSync(join(dir, "app"), { recursive: true });
let n = 0;
const emit = (e: RuntimeEvent): void => {
  if (e.type !== "step") return;
  const p = e.payload as { kind?: string; status?: string; tool?: string; result_preview?: string };
  if (p.status === "running") return;
  n++;
  const err = p.status === "error" ? ` ${String(p.result_preview ?? "").slice(0, 120)}` : "";
  console.log(`#${n} ${p.kind}${p.tool ? ` ${p.tool}` : ""} ${p.status}${err}`);
};
const h = createHeadless({ gatewayUrl: o.gateway, login: o.login, password: o.password, appDataDir: join(dir, "app"), dbKey: "try", office: true, emit });
const cmd = (type: string, payload: Record<string, unknown>) => ({ v: 1 as const, id: `t${Date.now()}${Math.random()}`, type: type as never, payload });
const t0 = Date.now();
await h.runtime.start();
const { run_id } = (await h.runtime.handle(cmd("run.start", { conversation_id: "try", text: o.text, attachments: [], workspace: ws, model: "auto" }))) as { run_id: string };
while (!h.store.getRun(run_id)?.ended_at && n < o.maxSteps) await sleep(200);
const run = h.store.getRun(run_id);
if (!run?.ended_at) await h.runtime.handle(cmd("run.stop", { run_id }));
const fin = run?.final_action ? (JSON.parse(run.final_action) as { summary?: string }) : null;
console.log(`END ${run?.end_reason ?? `打ち切り（${o.maxSteps}手）`} ${((Date.now() - t0) / 1000).toFixed(1)}秒`);
console.log(`答え: ${fin?.summary ?? "（なし）"}`);
await h.close();
process.exit(0);
