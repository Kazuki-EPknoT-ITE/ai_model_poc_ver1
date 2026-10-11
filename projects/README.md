# projects/ — 開発プロジェクト置き場

実際のアプリ開発は、このフォルダ配下に **プロジェクトごとのサブフォルダ**を作って行う。

```
projects/
  .env                 ← 外部サービスのキー（MCP・APIキー等）をここに入力（コミットされない）
  .env.example         ← キー名一覧のテンプレート（コミットされる。値は書かない）
  <your-app>/          ← 1アプリ = 1フォルダ
    e2e.config.json    ← 起動方法とE2E設定（下記）※あると自動E2Eが確実になる
    ...(アプリのコード)
```

外部 API・MCP サーバなどのキーは **`projects/.env` に `KEY=値` で記入**する。
このファイルは `.gitignore` 済みでコミット・チェックポイントに含まれない
（＝実値を書いてよい。ロールバックしても消えない）。キーを追加したら
`.env.example` にキー名だけ追記しておく。

ここで行われた開発が、**実装後プロトコル（`CLAUDE.md` / `ref/E2E_TESTING.md`）の
E2E テスト対象**になる。`projects/<app>/` 内を変更すると、Stop フックが検知して
「そのアプリを Playwright で起動して E2E せよ」と促す。

## 各プロジェクトの E2E 設定（`projects/<app>/e2e.config.json`）

E2E を確実に自動化するため、各プロジェクト直下にこのファイルを置くことを推奨:

```json
{
  "name": "your-app",
  "start": "npm run dev",
  "url": "http://localhost:3000",
  "readyPath": "/",
  "flows": [
    { "name": "smoke", "steps": "トップ→ログイン→ダッシュボード表示を確認" }
  ],
  "playwrightTest": "npx playwright test"
}
```

| キー | 意味 |
|---|---|
| `start` | アプリ/サーバの起動コマンド（バックグラウンド起動する） |
| `url` | ブラウザで開くベースURL |
| `readyPath` | 起動完了を確認するパス（ここが 200 になるまで待つ） |
| `flows` | E2Eで確認する主要フロー |
| `playwrightTest` | Playwright スイートがあればそのコマンド（無ければ MCP ツールで手動E2E） |

このファイルが無い場合は、`package.json` の `dev`/`start` スクリプトや
Playwright 設定から推測して起動・テストする（`ref/E2E_TESTING.md` 参照）。

## 新規プロジェクトの始め方（例）

1. `projects/<app>/` を作成し、アプリを実装する。
2. `projects/<app>/e2e.config.json` を用意する。
3. 実装したら Playwright で E2E → 結果を `ref/LOG.md` に記録（自動で促される）。
