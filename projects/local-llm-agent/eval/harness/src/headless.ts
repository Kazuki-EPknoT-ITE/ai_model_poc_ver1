// Runtime をヘッドレスで組み立てる（Electron なし）。評価ハーネス（同じプロセス）と
// 障害注入試験の子プロセス（eval/fault-injection/src/host.ts）の両方から使う。
import { join } from "node:path";
import type { RuntimeEvent } from "@lla/protocol";
import {
  AgentRuntime,
  GatewayClient,
  McpToolHost,
  RunStore,
  ToolRouter,
  createSamplingHandler,
  type RuntimeOptions,
  type TokenProvider,
} from "@lla/agent-runtime";
import { PROJECT } from "./env.js";

export interface HeadlessOptions {
  gatewayUrl: string;
  login: string;
  password: string;
  appDataDir: string;
  dbKey: string;
  /** office-tools（MCP、uv run office-tools）を起動するか */
  office: boolean;
  emit: (ev: RuntimeEvent) => void;
  runtime?: Partial<Omit<RuntimeOptions, "store" | "gateway" | "router" | "appDataDir" | "emit">>;
  deviceName?: string;
  /** office-tools の抽出推論（sampling）に使うモデル ID */
  samplingModel?: string;
}

export interface Headless {
  runtime: AgentRuntime;
  store: RunStore;
  gateway: GatewayClient;
  router: ToolRouter;
  office: McpToolHost | null;
  close(): Promise<void>;
}

/** ログイン（401 のときはもう一度ログインする。Gateway の再起動でトークンが消えた場合も回復する）。 */
export function loginProvider(url: string, login: string, password: string, deviceName = "eval-harness"): TokenProvider {
  let token: string | null = null;
  const doLogin = async () => {
    const r = await fetch(`${url}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password, device_name: deviceName }),
    });
    if (!r.ok) throw new Error(`ログインできません: HTTP ${r.status}`);
    token = ((await r.json()) as { access_token: string }).access_token;
    return token;
  };
  return { accessToken: async () => token ?? doLogin(), refresh: doLogin };
}

export function officeHost(gateway: GatewayClient, model: () => string): McpToolHost {
  return new McpToolHost({ id: "office", command: "uv", args: ["run", "--project", PROJECT, "office-tools"], cwd: PROJECT }, createSamplingHandler(gateway, model));
}

export function createHeadless(o: HeadlessOptions): Headless {
  const store = RunStore.open(join(o.appDataDir, "runs.db"), o.dbKey);
  const gateway = new GatewayClient(o.gatewayUrl, loginProvider(o.gatewayUrl, o.login, o.password, o.deviceName), {
    netLevel: () => store.getSetting("net_level", "off"),
  });
  const office = o.office ? officeHost(gateway, () => o.samplingModel ?? "local-12b") : null;
  const router = new ToolRouter(store, gateway, office);
  const runtime = new AgentRuntime({ store, gateway, router, appDataDir: o.appDataDir, emit: o.emit, ...(o.runtime ?? {}) });
  return {
    runtime,
    store,
    gateway,
    router,
    office,
    close: async () => {
      await runtime.shutdown();
      store.close();
    },
  };
}
