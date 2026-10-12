// Runtime ホスト: Electron とは別の Node プロセスで Agent Runtime を動かす。
// 画面（Electron）を閉じても・落ちても作業は続き、Main は再起動時に再接続する（RUN-07）。
import { AgentRuntime, GatewayClient, McpToolHost, RunStore, ToolRouter, createSamplingHandler, type TokenProvider } from "@lla/agent-runtime";
import type { HostInit, HostToMain, MainToHost } from "../shared/ipc.js";

const send = (m: HostToMain) => process.send?.(m);

let runtime: AgentRuntime | null = null;
let store: RunStore | null = null;
let tokens = { access: "", refresh: "" };

function tokenProvider(gatewayUrl: () => string): TokenProvider {
  let refreshing: Promise<string> | null = null;
  return {
    accessToken: async () => tokens.access,
    refresh: () => {
      refreshing ??= (async () => {
        try {
          const r = await fetch(`${gatewayUrl()}/v1/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: tokens.refresh }),
          });
          if (!r.ok) {
            send({ type: "auth_failed" });
            throw new Error("ログインの有効期限が切れました");
          }
          const body = (await r.json()) as { access_token: string; refresh_token: string };
          tokens = { access: body.access_token, refresh: body.refresh_token };
          send({ type: "tokens", accessToken: tokens.access, refreshToken: tokens.refresh });
          return tokens.access;
        } finally {
          refreshing = null;
        }
      })();
      return refreshing;
    },
  };
}

async function init(m: HostInit): Promise<void> {
  tokens = { access: m.accessToken, refresh: m.refreshToken };
  store = RunStore.open(m.dbPath, m.dbKey);
  const s = store;
  // 予備機があれば、本番機に繋がらないときに切り替える（OPS-03）。トークンの更新も今の接続先に送る
  let client: GatewayClient | null = null;
  const gateway = new GatewayClient(m.gatewayUrls, tokenProvider(() => client?.currentUrl ?? m.gatewayUrls[0]!), {
    netLevel: () => s.getSetting("net_level", "off"),
  });
  client = gateway;
  let model = "local-12b";
  const office = m.office
    ? new McpToolHost({ id: "office", ...m.office }, createSamplingHandler(gateway, () => model))
    : null;
  runtime = new AgentRuntime({
    store: s,
    gateway,
    router: new ToolRouter(s, gateway, office),
    appDataDir: m.appDataDir,
    emit: (event) => {
      if (event.type === "run.state") model = s.getRun(String((event.payload as { run_id: string }).run_id))?.model_current ?? model;
      send({ type: "event", event });
    },
  });
  const { warnings } = await runtime.start();
  send({ type: "ready", warnings });
}

process.on("message", (m: MainToHost) => {
  switch (m.type) {
    case "init":
      init(m).catch((e: Error) => send({ type: "fatal", error: e.message }));
      break;
    case "command":
      if (!runtime) {
        send({ type: "reply", id: m.id, ok: false, error: "Runtime が初期化されていません" });
        break;
      }
      runtime
        .handle(m.command)
        .then((result) => send({ type: "reply", id: m.id, ok: true, result: JSON.parse(JSON.stringify(result ?? null)) as unknown }))
        .catch((e: Error) => send({ type: "reply", id: m.id, ok: false, error: e.message }));
      break;
    case "tokens":
      tokens = { access: m.accessToken, refresh: m.refreshToken };
      break;
    case "shutdown":
      void (async () => {
        await runtime?.shutdown();
        store?.close();
        process.exit(0);
      })();
      break;
  }
});

// 親（Electron）が落ちても、実行中の作業は最後まで続ける。終わったら抜ける（再起動時は Run Store から再開）
process.on("disconnect", () => {
  const check = setInterval(() => {
    if (!store || store.activeRuns().length === 0) {
      clearInterval(check);
      void runtime?.shutdown().finally(() => process.exit(0));
    }
  }, 5000);
});
