# E2E テスト手順（Playwright） — agency-agents

> 「実装後プロトコル」（`CLAUDE.md`）の一部。**実装したら毎回**、変更に対応する
> エンドツーエンドの動作を Playwright で確認し、結果を `ref/LOG.md` に記録する。

---

## 0. ターゲット設定（何を起動・テストするか）

**E2E の対象は `projects/<app>/` 配下の各開発プロジェクト。**
実際のアプリ開発はすべて `projects/` の中で行い、そこを変更したら、その
アプリを起動して Playwright で E2E する（→「A. アプリがある場合」）。

- **起動方法・URL の取得元**: `projects/<app>/e2e.config.json`
  （`start` / `url` / `readyPath` / `flows` / `playwrightTest`）。
  詳細は `projects/README.md` を参照。
- このファイルが無い場合は、`projects/<app>/package.json` の `dev`/`start`
  スクリプトや Playwright 設定から推測して起動・テストする。

> **判定**: 変更が `projects/<app>/` 内 → 「A」。それ以外（`agency-agents` の
> カタログ本体＝エージェント定義 Markdown など、起動アプリ無し）→ 「B」。

---

## A. アプリがある場合の手順（`projects/<app>/`）

0. **設定読込**: `projects/<app>/e2e.config.json` を読み、`start` / `url` /
   `readyPath` / `flows` / `playwrightTest` を取得する（無ければ `package.json`
   から推測）。
1. **起動**: `start` コマンドでアプリ／サーバを**バックグラウンド起動**する。
   `url` + `readyPath` が応答する（＝起動完了）まで Monitor 等で待つ。
2. **ブラウザ操作**: 次のいずれかで E2E を実行する。
   - **Playwright MCP ツール**（`mcp__playwright__browser_navigate`,
     `browser_click`, `browser_type`, `browser_snapshot`,
     `browser_take_screenshot` など）で、変更した画面の主要フローを操作。
   - あるいは既存の **Playwright テストスイート**があれば `npx playwright test`
     （関連スペックのみ: `npx playwright test tests/xxx.spec.ts`）。
   - ローカルアプリの検証には **`webapp-testing` スキル**も利用可。
3. **検証**: 期待する要素・文言・遷移・ネットワーク応答を確認。エラーは
   `browser_console_messages` / `browser_network_requests` で確認。
4. **証跡**: スクリーンショットを scratchpad に保存（`login_ok.png` 等）。
5. **記録**: `ref/LOG.md` の E2E 欄に「合格/不合格・対象URL/画面・確認点」を記入。
6. **後片付け**: 起動したサーバ／ブラウザを停止する。

## B. アプリが無い場合（現状の agency-agents）

「起動できる UI が無い」ため、ブラウザ E2E は **`N/A`**（理由を明記）とし、
代わりに**リポジトリの整合性チェックをテストとして実行**する:

```bash
# Git Bash で（存在するものを実行）
scripts/lint-agents.sh
scripts/check-divisions.sh
scripts/check-tools.sh
```

- 変更が特定エージェント/部門なら、そのファイルの frontmatter・リンク・
  部門整合性が壊れていないことを確認する。
- 結果を `ref/LOG.md` の「E2Eテスト: N/A（アプリ無し）」＋「結果/確認」に記録。

---

## 判定フロー（毎回）

```
実装した？
 └─ いいえ → E2E 不要（ログも通常不要）
 └─ はい
     └─ 起動可能なアプリ/UI がある？（上のターゲット設定 or package.json の dev/start）
         ├─ ある  → A. Playwright でブラウザ E2E → 合格/不合格をログ
         └─ ない  → B. N/A としてリポジトリチェック → 結果をログ
```

## 利用可能なツール（参考）

- Playwright MCP: `mcp__playwright__browser_*`
- Chrome 自動化: `mcp__claude-in-chrome__*`（`claude-in-chrome` スキル）
- ローカルアプリ検証スキル: `webapp-testing`
