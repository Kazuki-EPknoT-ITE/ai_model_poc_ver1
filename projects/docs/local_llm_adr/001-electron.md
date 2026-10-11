# ADR-001: クライアントは Electron＋React、Runtime は utilityProcess で動かす

- 状態: 承認済み（2026-10-11）

## 背景
要件書 4.2 ではクライアントの候補を「Tauri または Electron＋React」としていた。エージェント実行部は TypeScript で書き、画面を閉じても動き続け（RUN-07）、スリープを抑止し、通知を出し、自動更新できる必要がある。

## 決定
- Electron（最新の安定版。版は `package.json` で固定する）＋ React＋Vite を採用する。
- エージェント実行部（Agent Runtime）は Electron の `utilityProcess` で動かし、Main とは `MessagePort` でつなぐ。Renderer は表示だけを担当する。
- `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`。
- スリープ抑止は `powerSaveBlocker`、鍵の保護は `safeStorage`（DPAPI）、更新は electron-updater を使う。

## 検討した代替案
- **Tauri 2＋Node SEA の別同梱**: 画面は軽くなるが、Runtime のために Node を別に同梱する必要があり、トレイ・スリープ抑止・自動更新・DPAPI を Rust 側で個別に実装することになる。軽さの利点が小さく、実装の手間が増える。
- **Runtime を Main プロセス内で動かす**: 構成は簡単だが、Runtime がクラッシュすると UI も巻き込まれる。

## 影響・リスク
- インストーラが 100MB 程度になる（クライアント要件「空き2GB」の範囲内）。
- Playwright の `_electron.launch` でそのまま E2E できる。
- Electron の更新への追従（セキュリティ修正）が運用作業として必要になる。

## 関連要件ID
APP-01〜11, RUN-07, RUN-08, ADM-05, SEC-02
