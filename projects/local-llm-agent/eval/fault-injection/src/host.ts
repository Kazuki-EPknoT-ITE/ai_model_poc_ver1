// 障害注入試験（T-51）の子プロセス: Runtime をヘッドレスで動かし、イベントを標準出力に1行ずつ出す。
// 親（run.ts）はこのプロセスを強制終了・再起動して、アプリの異常終了を模擬する。
//
// 設定は環境変数 FI_HOST_CONFIG（JSON）で受け取る。出力:
//   @@event <RuntimeEvent>   … Runtime のイベント
//   @@run <run_id>           … 依頼を始めた
//   @@done <run_id>          … 実行中の作業がなくなった（このあと終了する）
// 試験フック（AGENT_TEST_HOOKS=1）の出力は標準エラーに出る（packages/agent-runtime/src/testhooks.ts）。
import type { RuntimeEvent } from "@lla/protocol";
import { createHeadless } from "../../harness/src/headless.js";

export interface HostConfig {
  gatewayUrl: string;
  appDataDir: string;
  /** start: 依頼を始める / resume: Run Store に残った作業を再開するだけ */
  mode: "start" | "resume";
  workspace: string;
  request: string;
  /** 承認要求を自動で承認するまでの時間（ミリ秒）。省略時は承認しない */
  approveAfterMs?: number;
  lockWaitMs?: number;
  retryBaseMs?: number;
  /** 本物の Gateway（dev_server.py）ではパスワードが違う */
  login?: string;
  password?: string;
  /** office-tools（MCP）を起動するか（FI-17 など Office 文書を扱うシナリオ） */
  office?: boolean;
  /** 利用者のネット設定（off / search / browse） */
  netLevel?: string;
}

const cfg = JSON.parse(process.env.FI_HOST_CONFIG ?? "{}") as HostConfig;
const out = (line: string) => process.stdout.write(`${line}\n`);
let seq = 0;
const cmd = (type: string, payload: Record<string, unknown>) => ({ v: 1 as const, id: `fi${++seq}`, type: type as never, payload });

async function main(): Promise<void> {
  const pending: Promise<unknown>[] = [];
  const h = createHeadless({
    gatewayUrl: cfg.gatewayUrl,
    login: cfg.login ?? "dev",
    password: cfg.password ?? "dev",
    appDataDir: cfg.appDataDir,
    dbKey: "fi",
    office: cfg.office ?? false,
    deviceName: "fault-injection",
    runtime: { retryBaseMs: cfg.retryBaseMs ?? 200, lockWaitMs: cfg.lockWaitMs ?? 5_000 },
    emit: (e: RuntimeEvent) => {
      out(`@@event ${JSON.stringify(e)}`);
      if (e.type === "approval.request" && cfg.approveAfterMs !== undefined) {
        const id = (e.payload as { approval_id: string }).approval_id;
        // 自動承認ボット（試験仕様 §4「人手なし」）
        pending.push(
          new Promise((r) => setTimeout(r, cfg.approveAfterMs)).then(() => h.runtime.handle(cmd("approval.decide", { approval_id: id, decision: "approve" }))),
        );
      }
    },
  });
  await h.runtime.start();
  if (cfg.netLevel) await h.runtime.handle(cmd("settings.update", { net_level: cfg.netLevel }));
  let runId: string | null = null;
  if (cfg.mode === "start") {
    const r = (await h.runtime.handle(
      cmd("run.start", { conversation_id: "fi", text: cfg.request, attachments: [], workspace: cfg.workspace, model: "auto" }),
    )) as { run_id: string };
    runId = r.run_id;
    out(`@@run ${runId}`);
  }
  // 実行中の作業がなくなるまで待つ
  for (;;) {
    const active = h.store.activeRuns();
    if (active.length === 0) break;
    runId ??= active[0]!.id;
    await new Promise((r) => setTimeout(r, 100));
  }
  await Promise.allSettled(pending);
  out(`@@done ${runId ?? ""}`);
  await h.close();
}

main().then(
  () => process.exit(0),
  (e: unknown) => {
    process.stderr.write(`host failed: ${(e as Error).stack ?? String(e)}\n`);
    process.exit(3);
  },
);
