// Main ⇄ Runtime ホスト（Node の子プロセス）間のメッセージ。Renderer には関係しない。
import type { RuntimeCommand, RuntimeEvent } from "@lla/protocol";

export interface HostInit {
  type: "init";
  /** 先頭が本番機、2番目が予備機（OPS-03。設定で予備機を入れたときだけ） */
  gatewayUrls: string[];
  dbPath: string;
  dbKey: string;
  appDataDir: string;
  accessToken: string;
  refreshToken: string;
  office: { command: string; args: string[]; cwd?: string } | null;
  /** プラグイン（AGT-09）。管理者が置く plugins.json から（main.ts の loadPlugins） */
  plugins: PluginConfig[];
}

export interface PluginConfig {
  /** 英小文字・数字・_。ツール名は plugin.<id>.<ツール> になる */
  id: string;
  command: string;
  args: string[];
  cwd?: string;
}

export type MainToHost =
  | HostInit
  | { type: "command"; id: string; command: RuntimeCommand }
  | { type: "tokens"; accessToken: string; refreshToken: string }
  | { type: "shutdown" };

export type HostToMain =
  | { type: "ready"; warnings: string[] }
  | { type: "event"; event: RuntimeEvent }
  | { type: "reply"; id: string; ok: true; result: unknown }
  | { type: "reply"; id: string; ok: false; error: string }
  | { type: "tokens"; accessToken: string; refreshToken: string }
  | { type: "auth_failed" }
  | { type: "fatal"; error: string };

/** Renderer に preload で公開する API（プロトコル §5.3 の main:*）。 */
export interface AppApi {
  auth: {
    status(): Promise<{ loggedIn: boolean; user?: { display_name: string; role: string }; serverUrl: string }>;
    login(p: { login: string; password: string; remember: boolean }): Promise<{ ok: boolean; error?: string }>;
    logout(): Promise<void>;
  };
  runtime: {
    send(command: RuntimeCommand): Promise<unknown>;
    onEvent(cb: (e: RuntimeEvent) => void): () => void;
    onStatus(cb: (s: { state: "starting" | "ready" | "restarting" | "stopped"; warnings?: string[] }) => void): () => void;
  };
  dialog: { pickFolder(): Promise<string | null> };
  /** ドロップ・選択したファイルの実際のパス（sandbox の Renderer からは取れないため preload で取る） */
  files: { pathOf(file: File): string };
  /** 既定のアプリで開く。実行ファイル・マクロ付きの文書は開かない（open-policy.ts）。PDF は page で該当ページを開く */
  shell: { openPath(path: string, opts?: { page?: number }): Promise<{ ok: boolean; error?: string }> };
  workspace: { list(dir: string): Promise<{ name: string; path: string; dir: boolean }[]> };
  settings: {
    get(): Promise<{ serverUrl: string; spareUrl: string; autoStart: boolean }>;
    set(patch: Partial<{ serverUrl: string; spareUrl: string; autoStart: boolean }>): Promise<void>;
  };
  app: { quit(): Promise<void>; version(): Promise<string> };
  /** 自動更新（ADM-05）。新しい版を取って確かめ終わると onReady が呼ばれる */
  update: {
    status(): Promise<{ version: string; notes: string } | null>;
    onReady(cb: (u: { version: string; notes: string }) => void): () => void;
    install(): Promise<{ ok: boolean; error?: string }>;
  };
}
