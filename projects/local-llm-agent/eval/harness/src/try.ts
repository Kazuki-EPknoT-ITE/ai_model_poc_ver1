// 1件の依頼を実際のモデル（Gateway 経由）で動かし、手順と答えを表示する（開発 PC での動作確認用）。
//   pnpm --filter @lla/eval-harness try -- --gateway http://127.0.0.1:8787 --login dev --password dev-password-1 [--workspace <フォルダ>] [--max-steps 30] [--args] "依頼"
// --args を付けると、各手のツールの引数（先頭300文字）も表示する
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { RuntimeEvent } from "@lla/protocol";
import { sleep } from "./env.js";
import { createHeadless } from "./headless.js";

const argv = process.argv.slice(2).filter((a) => a !== "--");
const o = { gateway: "http://127.0.0.1:8787", login: "dev", password: "dev-password-1", workspace: "", maxSteps: 30, text: "", args: false };
for (let i = 0; i < argv.length; i++) {
  const a = argv[i]!;
  if (a === "--gateway") o.gateway = argv[++i]!;
  else if (a === "--login") o.login = argv[++i]!;
  else if (a === "--password") o.password = argv[++i]!;
  else if (a === "--workspace") o.workspace = argv[++i]!;
  else if (a === "--max-steps") o.maxSteps = Number(argv[++i]);
  else if (a === "--args") o.args = true;
  else o.text = a;
}
const dir = mkdtempSync(join(tmpdir(), "lla-try-"));
const ws = o.workspace || join(dir, "ws");
mkdirSync(ws, { recursive: true });
mkdirSync(join(dir, "app"), { recursive: true });
let n = 0;
let waiting = null as { state: string; since: number } | null;
const emit = (e: RuntimeEvent): void => {
  if (e.type === "run.state") {
    const st = (e.payload as { state: string }).state;
    waiting = st === "waiting_approval" || st === "waiting_question" ? { state: st, since: Date.now() } : null;
  }
  if (e.type !== "step") return;
  const p = e.payload as { kind?: string; status?: string; tool?: string; result_preview?: string; args_preview?: string };
  if (p.status === "running") {
    if (o.args && p.args_preview) console.log(`   引数 ${p.args_preview.slice(0, 300)}`);
    return;
  }
  n++;
  const err = p.status === "error" ? ` ${String(p.result_preview ?? "").slice(0, 120)}` : "";
  console.log(`#${n} ${p.kind}${p.tool ? ` ${p.tool}` : ""} ${p.status}${err}`);
};
const h = createHeadless({ gatewayUrl: o.gateway, login: o.login, password: o.password, appDataDir: join(dir, "app"), dbKey: "try", office: true, emit });
const cmd = (type: string, payload: Record<string, unknown>) => ({ v: 1 as const, id: `t${Date.now()}${Math.random()}`, type: type as never, payload });
const t0 = Date.now();
await h.runtime.start();
const { run_id } = (await h.runtime.handle(cmd("run.start", { conversation_id: "try", text: o.text, attachments: [], workspace: ws, model: "auto" }))) as { run_id: string };
// 承認・質問の待ちが15秒続いたら、利用者の判断が要るところで止まったとして終える。全体は5分まで
let stopped = "";
while (!h.store.getRun(run_id)?.ended_at && n < o.maxSteps) {
  if (waiting && Date.now() - waiting.since > 15_000) {
    stopped = waiting.state === "waiting_approval" ? "承認待ち" : "質問待ち";
    break;
  }
  if (Date.now() - t0 > 300_000) {
    stopped = "時間切れ（5分）";
    break;
  }
  await sleep(200);
}
const run = h.store.getRun(run_id);
if (!run?.ended_at) await h.runtime.handle(cmd("run.stop", { run_id }));
const fin = run?.final_action ? (JSON.parse(run.final_action) as { summary?: string }) : null;
console.log(`END ${run?.end_reason ?? (stopped || `打ち切り（${o.maxSteps}手）`)} ${((Date.now() - t0) / 1000).toFixed(1)}秒`);
console.log(`答え: ${fin?.summary ?? "（なし）"}`);
await h.close();
process.exit(0);
