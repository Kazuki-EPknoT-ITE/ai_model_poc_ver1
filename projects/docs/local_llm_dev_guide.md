# ローカルLLMデスクトップエージェント 開発ガイド

2026年10月11日 · v1.0（開発環境・コーディング規約・作業の進め方）

実装者（Claude Code と各サブエージェント）が守る開発の前提を定める。リポジトリ全体の運用ルール（`CLAUDE.md`, `ref/PROJECT_RULES.md`）を優先し、本書はこのプロジェクト固有の内容だけを書く。

---

## 1. 開発環境

### 1.1 構成

| 環境 | 役割 | 接続 |
| --- | --- | --- |
| 開発PC（Windows 11、GPU なし） | コードの編集、単体・結合テスト、モック Gateway での E2E、Electron アプリの実行 | — |
| 検証機（ZOTAC RTX 5080） | 推論サーバー（vLLM・Gateway ほか）、学習、夜間の評価と障害注入 | 社内LAN（SSH・HTTPS） |

### 1.2 必要なツールと版

| ツール | 版 | 開発PCの状況（2026-10-11 確認） | 備考 |
| --- | --- | --- | --- |
| Node.js | 20 LTS 以上 | 20.16.0 | `package.json` の `engines` で下限を固定する |
| pnpm | 10.x | 10.33.0 | `packageManager` フィールドで固定する |
| Python | 3.12 | 3.12.5（`py -3.12`） | `python` コマンドは Microsoft Store のスタブなので使わない |
| uv | 0.10 以上 | 0.10.4 | Python の依存管理と仮想環境 |
| Git | 2.45 以上 | 2.45.0 | |
| Docker Desktop | 29.x | 29.1.3 | モック Gateway・PostgreSQL・Qdrant をローカルで動かす |
| LibreOffice | 24.x 以上 | **未導入** | `winget install TheDocumentFoundation.LibreOffice` で入れる（XLS-04 の再計算と変換） |
| Playwright | 最新の安定版 | 未導入 | `pnpm exec playwright install` |
| Electron | 最新の安定版 | — | M1 の時点で版を固定し、ADR-001 に追記する |

- 固定した版の正本は各設定ファイル（`package.json`、`pyproject.toml`、`uv.lock`、`pnpm-lock.yaml`）とする。なお、リポジトリの `.gitignore` は `package-lock.json` と `yarn.lock` を除外しているが、`pnpm-lock.yaml` と `uv.lock` は**コミットする**。
- サーバー側の版は `projects/local-llm-agent/server/deploy/VERSIONS.md` に記録する（`local_llm_ops/server_setup.md`）。

### 1.3 初回セットアップ（開発PC）

```powershell
# 1) LibreOffice（未導入の場合）
winget install TheDocumentFoundation.LibreOffice

# 2) JS 依存
cd projects/local-llm-agent
pnpm install

# 3) Python 依存（office-tools・サーバー・ML・評価を1つの uv workspace で管理）
uv sync --python 3.12

# 4) スキーマから型を生成（TS と pydantic）
pnpm protocol:gen

# 5) ローカルの依存サービス（PostgreSQL・Qdrant・モック Gateway）
docker compose -f server/deploy/compose.dev.yml up -d

# 6) Playwright
pnpm exec playwright install chromium
```

### 1.4 よく使うコマンド（M1 で `package.json` に定義する）

| コマンド | 内容 |
| --- | --- |
| `pnpm dev` | Electron アプリを開発モードで起動する（接続先は `.env` の `LLM_GATEWAY_URL`） |
| `pnpm mock:gateway` | モック Gateway を起動する（シナリオは `eval/mock/scenarios/*.yaml`。`local_llm_test_spec.md`） |
| `pnpm test` | TS の単体テスト（vitest） |
| `uv run pytest` | Python の単体テスト |
| `pnpm test:integration` | Runtime ＋ office-tools ＋ モック Gateway の結合テスト |
| `pnpm test:e2e` | Playwright（`_electron`）での E2E |
| `pnpm lint` / `uv run ruff check` / `uv run mypy` | 静的検査 |
| `pnpm protocol:gen` / `pnpm protocol:check` | 型の生成 / スキーマと生成物の差分検査 |
| `uv run eval --set <name> --target <gateway>` | 評価ハーネス |

### 1.5 環境変数（`projects/.env`）

キーは `projects/.env` に書く（git の管理外）。キー名は `projects/.env.example` に追記する。

| キー | 例 | 用途 |
| --- | --- | --- |
| `LLM_GATEWAY_URL` | `https://llm-dev.local:8443` / `http://127.0.0.1:8787`（モック） | アプリと評価の接続先 |
| `LLM_GATEWAY_FALLBACK_URL` | `https://llm-spare.local:8443` | 予備サーバー（OPS-03） |
| `LLM_DEV_USER` / `LLM_DEV_PASSWORD` | — | 開発用のアカウント（検証機の Gateway） |
| `LLM_CA_CERT` | `C:\certs\local-llm-ca.crt` | 社内CA の証明書（開発時に NODE_EXTRA_CA_CERTS に渡す） |
| `LIBREOFFICE_PATH` | `C:\Program Files\LibreOffice\program\soffice.exe` | office-tools が使う |
| `VERIFY_HOST` / `VERIFY_SSH_USER` | `llm-dev.local` / `llmadmin` | 検証機への SSH（評価・デプロイ用） |
| `HF_TOKEN` | — | モデル取得（検証機でだけ使う） |
| `WEB_SEARCH_API_KEY` | — | 検索 API（Web Relay。確認事項で選定） |

### 1.6 検証機への接続
- SSH は鍵認証だけを使う（`~/.ssh/config` に `Host llm-dev`）。パスワードはリポジトリにも `.env` にも書かない。
- Gateway は社内CA の証明書で TLS を張る。開発PCでは `LLM_CA_CERT` を信頼させてから接続する。
- ファイアウォールで 8443 を開けられない段階では、`ssh -L 8443:localhost:8443 llm-dev` で転送して `https://localhost:8443` に接続する。

---

## 2. リポジトリとブランチ

- 開発は `projects/local-llm-agent/` 配下で行う（基本設計書 §3）。設計ドキュメントは**すべて `projects/docs/`** に置く。
- ブランチは `main` だけを使い、実装のまとまりごとに `scripts/checkpoint.sh` で復元ポイントを作る（`CLAUDE.md` の実装後プロトコル）。
- GitHub（`Kazuki-EPknoT-ITE/ai_model_poc_ver1`）への push は、ユーザーが指示したときだけ行う。認証の注意点は `ref/LOG.md`（2026-10-11 18:05）にある。
- 置いてはいけないもの: 実データ（請求書・契約書など）、モデルの重み、学習データ、機密語辞書、`.env`、証明書の秘密鍵。`.gitignore` に `projects/local-llm-agent/**/data/`、`*.safetensors`、`*.gguf`、`*.pem`、`*.key` を追加する（M1）。

---

## 3. コーディング規約

### 3.1 共通
- 型の正本は `packages/protocol/schemas/*.schema.json`。TS と Python の型は生成物を使い、手で書き写さない。
- ログには本文（依頼・文書の中身・Web の本文・パスワード・機密語）を出力しない。出してよいのは ID・ツール名・コード・件数・時間だけ。
- 時刻は UTC で保存し、表示するときに JST に変換する。
- 外部から来たテキストは、プロンプトに入れる直前に必ず `<untrusted>` で包む（`local_llm_prompts.md` §4）。包む処理は1つの関数にまとめ、それ以外の経路を作らない。
- ポリシー判定（承認・パス・ネット）は `packages/agent-runtime/src/policy/` の外に書かない。

### 3.2 TypeScript
- `strict: true`、ESM、ESLint（typescript-eslint の推奨設定）＋ Prettier。
- `any` は使わない（必要なら `unknown` にして型を絞る）。
- 非同期処理の失敗は、protocol の `ToolError` に変換してから上位へ返す。例外を投げたまま Runtime のループの外へ出さない。
- ファイル名は kebab-case、型は PascalCase、関数と変数は camelCase。

### 3.3 Python
- Python 3.12、ruff（lint と format）、mypy（strict）、pydantic v2。
- MCP サーバーは公式の Python SDK を使う。ツール1つを1モジュールにし、引数と戻り値は生成した pydantic モデルにする。
- `eval` / `exec` / `pickle` は使わない（`data.transform` の式は専用の構文解析器で評価する）。
- 金額は `decimal.Decimal` で扱う。
- サーバーは FastAPI ＋ SQLAlchemy 2.0（async）＋ Alembic。

### 3.4 テスト
- 新しい機能には単体テストを付ける。バグを直したら、再現テストを先に書く。
- テスト用の文書は合成したものだけを使い、`tests/fixtures/` にコミットする（実データは使わない）。
- 試験の種類・ID・合格基準は `local_llm_test_spec.md` に従う。

---

## 4. 作業の進め方（実装者向け）

1. 着手する前に、`local_llm_impl_plan.md` で対象のタスクと前提を確認する。
2. 該当する詳細仕様（protocol / data_model / api / ui_spec / prompts）を読む。**仕様と違う実装が必要になったら、先に仕様を直す**（同じコミットで行う）。
3. 実装したら、テストを実行する。アプリに影響がある場合は Playwright で E2E を行う（`ref/E2E_TESTING.md`、`projects/local-llm-agent/e2e.config.json`）。
4. `local_llm_traceability.md` の該当する要件の「状態」を更新する（未着手 → 実装中 → 実装済み → 検証済み）。
5. `ref/LOG.md` に記録し、`scripts/checkpoint.sh` で復元ポイントを作る。

### 4.1 サブエージェントの使い分け
専門の作業は `.claude/agents/` のサブエージェントに任せる（基本設計書 §12）。

| 作業 | サブエージェント |
| --- | --- |
| スキーマ・ADR・仕様の整合 | llm-agent-architect |
| 推論サーバー・Gateway | llm-inference-engineer |
| Runtime・fs-tools | agent-runtime-engineer |
| Electron・管理画面・E2E | desktop-app-engineer |
| Word/Excel/PDF | office-doc-tools-engineer |
| 学習・モデル台帳 | ml-finetuning-engineer |
| RAG | rag-search-engineer |
| Web Relay・セキュリティレビュー | web-relay-security-engineer |
| 評価・障害注入・合否判定 | resilience-qa-engineer |

### 4.2 完了の定義
- テストが通っている（単体・関連する結合テスト。UI に関わる場合は E2E も）。
- 仕様書・トレーサビリティ表が実装と一致している。
- ログに本文を出していない、`<untrusted>` を経由しない経路がない、ポリシー判定をすり抜ける経路がない、の3点を自分で確認した。
- `ref/LOG.md` に記録し、チェックポイントを作った。
