# ADR-001: クライアントは Electron＋React、Runtime は Node の子プロセスで動かす

- 状態: 承認済み（2026-10-11）。同日、実装時に Runtime の実行場所を utilityProcess から Node の子プロセスに改訂（下記「改訂」）

## 背景
要件書 4.2 ではクライアントの候補を「Tauri または Electron＋React」としていた。エージェント実行部は TypeScript で書き、画面を閉じても動き続け（RUN-07）、スリープを抑止し、通知を出し、自動更新できる必要がある。

## 決定
- Electron（44.4.5 に固定。`apps/desktop/package.json`）＋ React 18 ＋ Vite 6 を採用する。
- エージェント実行部（Agent Runtime）は **Electron とは別の Node プロセス（Runtime ホスト）**で動かす。Main が `child_process.fork`（`execPath` に Node を指定、JSON 直列化の IPC）で起動・監視し、異常終了したら間隔を空けて再起動する。Renderer は表示だけを担当する。
- `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`。Renderer の CSP は `default-src 'self'; style-src 'self'`。
- スリープ抑止は `powerSaveBlocker`、鍵とリフレッシュトークンの保護は `safeStorage`（DPAPI）、更新は electron-updater を使う。

## 改訂（2026-10-11、T-30 実装時）
当初は Runtime を Electron の `utilityProcess` で動かす予定だった。実装してみると、Run Store に使う SQLCipher 対応のネイティブモジュール（better-sqlite3-multiple-ciphers）が次の制約を持っていた。
- Node 20（開発機）向けのビルド済みバイナリは 12.9.0 まで。12.9.0 が対応する Electron の ABI は 41 系（サポート終了）まで。
- 新しい版（12.10 以降）は Electron 44 に対応するが、Node 20 向けのバイナリが無い。手元でのビルドには Visual Studio Build Tools が必要。

そこで Runtime を Electron の ABI から切り離し、通常の Node で動かすことにした。利点:
- ネイティブモジュールは Node の ABI だけを相手にすればよい（開発は Node 20、製品は同梱する Node LTS）。
- Electron（画面・Main）が異常終了しても、Runtime の作業は続く（親が消えたら、実行中の作業が終わるまで動いてから終了する）。RUN-07 にとってもむしろ好ましい。
- `serialization: "advanced"`（V8 structured clone）は Electron 内蔵の Node と外部の Node で V8 の版が違うため使えない。IPC は JSON にする。

## 検討した代替案
- **Tauri 2＋Node SEA の別同梱**: 画面は軽くなるが、トレイ・スリープ抑止・自動更新・DPAPI を Rust 側で個別に実装することになる。
- **Runtime を utilityProcess で動かす**（当初案）: 上記のネイティブモジュールの ABI の制約で、サポート中の Electron と Node 20 を同時に満たせない。
- **Runtime を Main プロセス内で動かす**: Runtime がクラッシュすると UI も巻き込まれる。

## 影響・リスク
- 製品には Node 本体（約 80MB）を同梱する必要がある（T-46 のインストーラ作成で対応）。開発時は `LLA_NODE_PATH`（既定は PATH 上の `node`）。
- Electron 44 の npm パッケージのインストールスクリプトは Node 22 以上が必要。Node 20 の開発機では `apps/desktop/scripts/fetch-electron.py`（公式リリースを SHASUMS256 で検証して展開）で代替する。Node 22 LTS に上げればこの手順は不要になる。
- Playwright の `_electron.launch` でそのまま E2E できる（`apps/desktop/e2e`）。
- Electron の更新への追従（セキュリティ修正）が運用作業として必要になる。

## 関連要件ID
APP-01〜11, RUN-07, RUN-08, ADM-05, SEC-02
