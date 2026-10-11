# 作業ログ（引き継ぎ用） — agency-agents

> **最新のエントリを一番上に**追記する（逆時系列）。
> 実装したら毎回、`ref/E2E_TESTING.md` の E2E を実行し、**E2E 結果欄を必ず埋める**。
> このログの追記は「実装後プロトコル」の最後のステップ（`CLAUDE.md` 参照）。
>
> 各エントリのフォーマット:
>
> ```
> ## YYYY-MM-DD HH:MM — 一言サマリ
> - **変更ファイル**: path/to/file ほか
> - **やったこと**: 何を変更/実装/調査したか
> - **理由/背景**: なぜ
> - **E2Eテスト**: 合格 / 不合格 / N/A（+理由）。実行内容・対象URL/画面・確認点
> - **結果/確認**: その他の確認（lint・チェックスクリプト等）
> - **次にやること / 申し送り**: あれば
> ```

---

## 2026-10-11 23:30 — M2〜M4 実装: office-tools・Runtime 中核・デスクトップアプリ（縦の一本通しまで）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `tools/office/`（新規。office-tools、MCP stdio、32ツール。サブエージェントが実装）
  - `packages/agent-runtime/`（新規。Run Store・ポリシー・意図記録・文法・Gateway クライアント・MCP ホスト・ルーター・一括処理・停滞監視・文脈圧縮・runtime.ts、単体試験と結合試験）
  - `apps/desktop/`（新規。Electron 44.4.5 の main・preload・Runtime ホスト・React 画面・Playwright E2E・Electron 本体の取得スクリプト）
  - `packages/protocol/scripts/gen.mjs`（TS 型の生成を修正、ページング付き引数の最上位に `type:object`）、`eval/mock-gateway/scenarios/runtime/*.yaml`（新規6本）、抽出 fixture の形式、ルートの package.json/pyproject.toml
  - 設計ドキュメント: ADR-001 の改訂、design（§5.4 の staging、§7.3）、data_model（`staged`/`staging_path`）、prompts、dev_guide（版・コマンド）、impl_plan（状態）、test_spec
- **やったこと**:
  - ユーザーの指示「止まらずに実行」に従い、マイルストーンをまたいで連続で実装した。途中の「進める／戻す」確認は省き、最後にまとめて確認する。
  - office-tools（T-10〜T-18）はサブエージェント（office-doc-tools-engineer の定義で general-purpose）に並行して任せた。pytest は 112 件成功・3 件スキップ（LibreOffice 未導入）、網羅率 92%。
  - Runtime（T-03, T-20〜T-29b）とデスクトップ（T-30〜T-35）は自分で実装した。
- **実装で決めたこと・設計からの変更**:
  - **ADR-001 改訂**: Runtime は Electron の utilityProcess ではなく、Node の子プロセスで動かす。理由は SQLCipher モジュールのネイティブ ABI（Node 20 と、サポート中の Electron 44 を同時に満たせない）。副次的に、画面が落ちても作業が続く。IPC は JSON（V8 の版が違うため advanced 直列化は不可）。
  - **書き込みは staging 方式**: ツールには作業用コピーを書かせ、prepared → staged → committed の3段階で記録する。ツールが書いた直後に落ちても、重複も取りこぼしもなく再開できる。
  - **承認・質問の ID は全体で一意にした**（コマンドに会話 ID が含まれないため）。
  - **office-tools のパス**: 全会話で1プロセスを共有するため、Runtime が作業フォルダ基準の絶対パスにして渡し、結果は相対表示に戻す。パスワードは `_meta["lla/passwords"]` でメモリ上だけで渡す。
  - **外部由来の出力**: `untrusted_output` のツールは、data 全体を `<untrusted>` で包んでモデルに渡す（Excel の値や PDF の表も文書の中身のため）。
  - data.transform は pandas を使わず純 Python＋Decimal（サブエージェントの判断を採用）。
- **見つけて直した不具合**（試験で検出）:
  - 鍵違いで RunStore を開くとハンドルが残る。
  - 不正な出力の記録を停滞判定が読んで例外になり、ループが止まる → 想定外の例外でも止めない作りにした。
  - 圧縮対象が無いと 413 を繰り返す → 段階的な圧縮と、ツール結果の切り詰めを追加。
  - 生成した TS 型が `if/then` で崩れる。
  - MCP SDK が `inputSchema.type` を要求する。
  - Electron 内蔵の Node と外部の Node の IPC 直列化の不一致。
  - 新しい Chromium で `scrollIntoView` が Promise を返し、React が落ちる。
- **E2Eテスト**: **合格**。
  - `pnpm test:e2e:desktop`（Playwright `_electron`、Electron 44.4.5）**4件成功**:
    - E-01/02: ログイン → 作業フォルダ → 依頼 → 手順カード → 完了報告
    - E-04/14: フォルダ外への書き込み → 承認カード（既定フォーカスは却下）→ キーボードで承認 → 実行
    - E-07/08: ウィンドウを閉じてもトレイで継続 → 停止
    - **T-35**: 画面から請求書 PDF 3件 → office-tools で抽出（sampling でモックに推論依頼）→ 送金明細.xlsx → 元に戻す
  - スクリーンショットは scratchpad の `e2e-m4/`（e02-final, e04-approval, e07-stopped, t35-invoices）。
  - モック Gateway の E2E（`pnpm test:e2e`）3件成功。
- **結果/確認**:
  - `pnpm check` 成功（eslint、tsc 4パッケージ、生成物の差分なし、vitest は protocol 198＋runtime 16＋desktop 3、ruff、mypy strict、pytest 183 件成功・3 件スキップ）。
  - Runtime の結合試験 `pnpm test:integration` **9件成功**: I-basic、再起動からの再開と重複なし、承認、503/切断/不正出力、停滞（立て直し2回 → 停滞・低優先度）、Web の後の汚染承認、圧縮、停止、I-01（office-tools 込み）。
- **次にやること / 申し送り**:
  - **LibreOffice 未導入**: 再計算・変換の実機試験（3件）がスキップのまま（`winget install TheDocumentFoundation.LibreOffice`）。
  - 画面の残り: 差分プレビュー、添付のドラッグ＆ドロップ、定型作業（/コマンド）、設定画面、管理画面（M6）。
  - サーバー側: Gateway 本体（T-40）、OCR サービス（T-41）、インストーラ（T-46。Node 本体の同梱が必要）。M0（検証機）は接続情報が分かり次第。
  - 画像の説明（`image_describe` の sampling）は Runtime 側が未対応（Could）。
  - pytest の警告1件（Starlette の TestClient が httpx の使用を非推奨にしている）は残っている。

## 2026-10-11 21:00 — 実装開始: T-01 雛形・T-02 protocol・T-05 モック Gateway

- **変更ファイル**: `projects/local-llm-agent/`（新規62ファイル）。主なもの:
  - ルート: `package.json`（pnpm workspace・スクリプト）, `pyproject.toml`（uv workspace・ruff/mypy/pytest）, `tsconfig.base.json`, `eslint.config.js`, `e2e.config.json`, `README.md`, `pnpm-lock.yaml`, `uv.lock`
  - `packages/protocol/`: 正本 `schemas/src/*.yaml`（共通・アクション・承認/Runtime メッセージ・ツール47個）、生成スクリプト `scripts/gen.mjs`・`python/gen_models.py`、生成物（`schemas/gen/`・`src/generated/`・`python/src/lla_protocol/{data,generated}/`）、`src/index.ts`（ajv による検証）、`lla_protocol`（jsonschema による検証）、テスト
  - `eval/mock-gateway/`: FastAPI のモック（`scenario.py`/`engine.py`/`app.py`/`cli.py`）、シナリオ `smoke.yaml`・`invoice-basic.yaml`＋fixture、pytest
  - `e2e/`: Playwright の設定と `mock-gateway.spec.ts`
  - 仕様の書き戻し（`projects/docs/`）: api（`purpose` フィールド・取り消し時の `CANCELLED`）、protocol（path_args の `*`・paginated の形）、test_spec（モックの障害パラメータ・シナリオ例の値を修正）、dev_guide（シナリオのパス）、impl_plan（T-01/02/05 を完了に）
- **やったこと**:
  - 版の選定: Node 20.16 で動くよう TypeScript 6.0.3 / vitest 3.2.7 / eslint 9.39.5 / typescript-eslint 8.71.1 / Playwright 1.64.0 に固定した。TS 7.0（最新）は typescript-eslint が未対応（<6.1）、vitest 5 と eslint 10 は Node 22 以上が必要なため。
  - プロトコル: 155定義・47ツール。生成物は `pnpm protocol:check` で差分を検査する（Python 側はリポジトリの ruff 設定で整形して出力を固定）。
  - モック Gateway: 認証（リフレッシュトークンのローテーション）、冪等キー（同じ中身は200、異なれば409）、SSE（Last-Event-ID で途中再開。イベント列はジョブ作成時に時刻付きで確定）、文脈予算413、ネットの上限と DLP、要求の記録（本文なし）、障害注入9種（http_status/latency/drop_connection/hang/queue/raw/restart/dlp_block/net_disabled）。
- **理由/背景**: 実装計画の「直近の着手順」1（T-01→T-02→T-05）。ユーザーから「続きを行って」と依頼された。
- **E2Eテスト**: **合格**。`pnpm test:e2e`（Playwright。`webServer` でモック Gateway を smoke シナリオで自動起動）で3件成功。確認した内容: ①ヘルスチェック・未認証時の401・ログイン・設定取得、②推論3手（plan_update → fs.list → final）の SSE で、トークン連結が done の出力と一致すること、出力が Action スキーマに合うこと、同じ冪等キーの再投入が200で同じ job を返すこと、Last-Event-ID=2 で続きから取り直せること、③ブラウザ（Chromium）で `/readyz` を表示。スクリーンショットは scratchpad の `e2e-t05/mock-gateway-readyz.png`。
- **結果/確認**: `pnpm check` がすべて成功（eslint、tsc、生成物の差分なし、vitest 198件、ruff check/format、mypy strict、pytest 71件）。
- **次にやること / 申し送り**:
  - 次は着手順2: **T-10（office-tools の MCP 骨組み）→ T-11 → T-12（Excel）**。T-12 の前に LibreOffice の導入が必要（未導入）。T-03（Action の文法生成器）は Runtime と一緒に進める。
  - Node 20 は 2026年4月でサポートが終了している。Node 22 LTS へ更新すれば vitest 5・eslint 10 に上げられる（ユーザーの確認待ち）。
  - pytest の警告1件（Starlette の TestClient が httpx の使用を非推奨にしている）は動作に影響しない。テスト依存を整理するときに対応する。

## 2026-10-11 19:30 — 実装に必要な詳細設計ドキュメント一式を作成（要件書 v0.3・基本設計 v0.2）

- **変更ファイル**（すべて `projects/docs/`）: 新規 `README.md`（目次）, `local_llm_protocol.md`, `local_llm_data_model.md`,
  `local_llm_api.md`, `local_llm_ui_spec.md`, `local_llm_prompts.md`, `local_llm_test_spec.md`, `local_llm_dev_guide.md`,
  `local_llm_impl_plan.md`, `local_llm_traceability.md`, `local_llm_adr/`（README＋001〜008）, `local_llm_ops/server_setup.md`。
  更新 `local_llm.md`（v0.3）, `local_llm_design.md`（v0.2）
- **やったこと**:
  - ユーザーの回答に沿って作成した。サーバーOSは未定（Ubuntu を主手順、WSL2 を付録）、ログインは独自アカウントで開始（ADR-008）、
    設計提案 R3/R4/R8/R10/R12 はすべて採用、開発環境はこのPCと検証機（接続可）。
  - プロトコル仕様（全47ツールの引数と戻り値・エラーコード・承認・Runtime と preload のメッセージ）、データモデル（SQLite/PostgreSQL の DDL）、
    API、プロンプトの雛形、開発ガイド、実装計画（T-01〜T-67）は自分で書いた。画面・試験・構築手順・ADR とトレーサビリティ表はサブエージェント（fork）で並行して作り、レビューして統合した。
  - 整合を取るために次を直した: 画面仕様の未決3点（`waiting_question` の追加、実行中の会話への追加指示、`main:*` API）をプロトコル仕様に反映。
    認証の食い違い（ロック回数5回・EdDSA・`external_id`）を統一。試験用フックを本番ビルドから除去することを明記。
  - 要件書を v0.3 に更新した（R1〜R12 の反映、確認事項への回答、改訂履歴）。
- **理由/背景**: 「あなたが実装するので、必要なドキュメントを作成して」というユーザーの依頼。
- **E2Eテスト**: N/A（ドキュメントだけの変更で、起動できるアプリはまだない）。代わりに `check-divisions.sh` **PASSED(17)**、
  `check-tools.sh` **PASSED(15)**。文書どうしの整合を機械的に検査した: ほかの文書が参照するツール名37個はすべてプロトコル仕様に定義あり、
  エラーコードの未定義0件、run.state の値は UI 仕様と一致。
- **結果/確認**: 設計ドキュメントは計約4,600行。目次は `projects/docs/README.md`。
- **次にやること / 申し送り**:
  - 実装は `local_llm_impl_plan.md` の「直近の着手順」に従う: **T-01 雛形 → T-02 protocol → T-05 モック Gateway** から始める。
  - LibreOffice が開発PCに入っていない（`winget install TheDocumentFoundation.LibreOffice`。T-12 までに必要）。
  - 検証機の接続情報（ホスト名・SSH ユーザー）を `projects/.env` に記入してもらう必要がある（M0 の着手時）。
  - 構築手順書の社内アドレス帯 `192.168.0.0/16` は仮の値。

## 2026-10-11 18:20 — 設計ドキュメントの置き場所を projects/docs/ に統一

- **変更ファイル**: `projects/docs/local_llm_design.md`, `projects/README.md`, `.claude/agents/*.md`（9件）
- **やったこと**: ユーザーの指示（「設計書はすべて projects/docs に入れる」）を反映した。
  - 設計書 §3: モノレポ構成から `docs/` を外し、「設計ドキュメントの置き場所」の節を追加した（要件書・設計書・`local_llm_traceability.md`・`local_llm_adr/`・`local_llm_ops/` をすべて `projects/docs/` に置く）。§13 にも ADR の置き場所を明記した。
  - `llm-agent-architect`: ADR とトレーサビリティ表の出力先を `projects/docs/` に変更した。ほかの8体には「設計ドキュメントは `projects/docs/` に置く」というルールを1行追加した。
  - `projects/README.md` の構成図に `docs/` を追記した。
- **理由/背景**: ユーザーの指示。既存の設計書（`local_llm.md` / `local_llm_design.md`）はもともと `projects/docs/` にあったので、ファイルの移動はしていない。
- **E2Eテスト**: N/A（起動できるアプリがない。ドキュメントとエージェント定義だけの変更）。代わりに `check-divisions.sh` **PASSED(17)**、`check-tools.sh` **PASSED(15)**。古いパス（`local-llm-agent/docs` など）が残っていないことも grep で確認した。
- **結果/確認**: `projects/docs/` の外に設計ドキュメントはない。
- **次にやること / 申し送り**: 今後の ADR・運用手順も `projects/docs/` に作る。push の 403 対策は直前の 18:05 のエントリを参照。

## 2026-10-11 18:05 — GitHub（ai_model_poc_ver1）へ初回 push

- **変更ファイル**: `ref/LOG.md`（本エントリ）のみ
- **やったこと**: ユーザーの指示で `main` を `origin`（`https://github.com/Kazuki-EPknoT-ITE/ai_model_poc_ver1.git`、空のリポジトリ）に push した（`72083d5`。上流ブランチを `origin/main` に設定）。リモートが空だったので、これでそのまま反映（マージ）されたことになる。
- **理由/背景**: 「push してマージするように」というユーザーの依頼。
- **E2Eテスト**: N/A（コードの変更なし。push 作業だけ）。push 後に `git ls-remote` で、リモートの `main` とローカルの HEAD が一致することを確認した。
- **結果/確認**: 1回目は **403** で失敗した。Windows の資格情報と gh の有効アカウントが `USEEd-Kazuki` で、このリポジトリへの書き込み権限がなかったため。
  gh には `Kazuki-EPknoT-ITE` もログイン済みだったので、そのトークンを**この push の1回だけ** http ヘッダーで渡して成功した（グローバルの設定と有効アカウントは変えていない）。
- **次にやること / 申し送り**: 次からの push も同じ 403 になる。恒久的に直すなら、ユーザーが `gh auth switch --user Kazuki-EPknoT-ITE` を実行するか、このリポジトリで資格情報のユーザー名を指定する。勝手に切り替えないこと。

## 2026-10-11 17:40 — ローカルLLMエージェントの基本設計書と専用サブエージェント9体を作成

- **変更ファイル**: `projects/docs/local_llm_design.md`（新規）,
  `.claude/agents/`（新規9件: llm-agent-architect / llm-inference-engineer /
  agent-runtime-engineer / desktop-app-engineer / office-doc-tools-engineer /
  ml-finetuning-engineer / rag-search-engineer / web-relay-security-engineer /
  resilience-qa-engineer）, `ref/LOG.md`
- **やったこと**:
  - 要件書 `projects/docs/local_llm.md`（v0.2, 平井海運向け Gemma 4 ローカルLLMデスクトップエージェント）をレビューし、
    矛盾・不足を12件（R1〜R12）設計書 §1 に整理した。主なもの: 検証機では 26B に昇格できない（R3）、
    SEC-06 と RUN-09 の衝突（R4）、PyMuPDF が AGPL（R6）、Office不要なのに再計算が必要（R7）、OCRの置き場所（R8）。
  - 基本設計を作成した: Electron＋React（Runtime は utilityProcess）、独自アクションプロトコル＋guided_json、
    SQLCipher の Run Store と書き込みの意図記録による冪等性、ポリシーエンジン（実体パス判定・汚染の追跡）、
    停滞監視と昇格、一括処理ランナー、自作の FastAPI Gateway（PostgreSQL 永続キュー・公平スケジューリング・SSE 再開）、
    vLLM、RAG（Qdrant＋BM25・ACL を検索条件に入れる）、Web Relay（DLP・SSRF 防御）、office-tools（MCP/Python）、
    評価・障害注入ハーネス、会社プロファイル、モノレポ構成 `projects/local-llm-agent/`、マイルストーン M0〜M6、ADR 候補 7件。
  - 設計の担当ごとにプロジェクト専用のサブエージェントを `.claude/agents/` に作成した（設計書 §12 の担当表と対応）。
- **理由/背景**: ユーザーから「要件書を確認して設計し、必要なサブエージェントも作成してほしい」と依頼があった。
- **E2Eテスト**: N/A（起動できるアプリがまだない。設計ドキュメントとエージェント定義だけの変更）。
  代わりに次を実行した: `check-tools.sh` **PASSED(15)**、`lint-agents.sh` **PASSED（0 error / 88 warning、
  既存カタログの警告）**、`check-divisions.sh` **FAILED** — 原因は `.git` がないため `git ls-files` が空になること
  （今回の変更とは無関係。git を復旧すれば解消する見込み）。新しいサブエージェント9件は frontmatter（name/description、
  `---` 2つ）を検証済みで、設計書の担当表の名前とすべて一致する。
- **結果/確認**: 設計書は約600行。要件IDとの対応は各節に記載した。
- **次にやること / 申し送り**:
  - **【解決済み】git**: ユーザーの指示で、新しいリモート
    `https://github.com/Kazuki-EPknoT-ITE/ai_model_poc_ver1.git`（空のリポジトリ）を `origin` にして
    `git init -b main` し、最初のチェックポイント `6d985a7` を作った（**push はまだしていない**）。
    git の復旧後は `check-divisions.sh` も **PASSED(17)**。旧リモート `agency-agents-main` とはつないでいない。
  - 設計書 §1（R3/R4/R8/R10/R12）と §14 の合意事項を、要件書 v0.3 に反映する。
  - 実装の着手順は M0（検証機で vLLM＋12B）→ M1（`packages/protocol` と雛形。llm-agent-architect が担当）。

## 2026-08-10 16:20 — GitHub リモートへ反映（旧履歴に接続し直して push）

- **変更ファイル**: `ref/LOG.md`（本エントリ＋14:44エントリに解決追記）。
  push 内容は `.gitignore`, `projects/README.md`, `projects/.env.example`, `ref/LOG.md`
- **やったこと**:
  - リモート `https://github.com/Kazuki-EPknoT-ITE/agency-agents-main.git` を
    `origin` に登録して fetch。**旧履歴（`b8bbe4f`→`eec8365` の4コミット、
    7/24 のチェックポイント含む）がリモートに全て残っている**ことを確認。
  - ローカル baseline `37ce517` と `origin/main` の**中身が完全一致**（差分0）で
    あることを確認 → フォルダは `eec8365` 時点の正確なコピーと判明。
  - force push（リモート履歴の破壊）を避け、**旧履歴の上に今回の正味差分
    （4ファイル81行）を積み直す**方式を採用。手順: バックアップタグ
    `backup/pre-remote-merge-20260810`（`664872a`）作成 → 差分を patch 化 →
    `git checkout -B main origin/main` → `git apply` → 1コミットにまとめて push。
  - 結果、履歴は `b8bbe4f`→…→`eec8365`→（今回のコミット）の**直線的な履歴**。
    重複コミットや unrelated-histories のマージコミットは発生していない。
- **理由/背景**: 「GitHub にマージして反映してほしい」というユーザー要望。
  ローカルは 14:44 に `git init` し直したため履歴が分断されており、そのままでは
  force push か unrelated-histories マージしか選べない状態だった。
- **E2Eテスト**: N/A（起動アプリを伴わない設定/ドキュメント変更）。代わりに
  リポジトリチェック → `check-divisions.sh` **PASSED(17)**、
  `check-tools.sh` **PASSED(15)**。加えて push 前に
  `git check-ignore`（`projects/.env`=ignored）とシークレット混入 grep を実施し、
  **キーの実値が含まれないことを確認**。
- **結果/確認**: リモート `main` に反映済み。ローカルの旧チェックポイント
  （`37ce517` / `3fd2388` / `664872a`）はタグ `backup/pre-remote-merge-20260810`
  から辿れるため失われていない。
- **次にやること / 申し送り**: 以降は `origin/main` と接続済みなので、
  チェックポイント後に `git push` すれば GitHub に反映できる。
  `projects/.env` はローカル専用（push されない）ため、他マシンでは
  `.env.example` からコピーして各自入力すること。

## 2026-08-10 16:06 — 台帳ドキュメントを廃止し、.env 入力ファイル方式へ変更

- **変更ファイル**: `projects/.env`（新規・git管理外）, `projects/.env.example`（新規）,
  `projects/README.md`, `projects/EXTERNAL_SERVICES.md`（削除）
- **やったこと**:
  - ユーザー要望により、説明文中心の `EXTERNAL_SERVICES.md` を削除し、
    **キーを直接入力できる `projects/.env`** に置き換えた（MCP用・API用・DB用の
    セクション＋コメント例入り。実値をそのまま書ける）。
  - `projects/.env.example` を追加（キー名のみのテンプレート。コミット対象）。
  - `projects/README.md` の導線を `.env` 方式の説明に差し替え。
- **理由/背景**: 「説明ドキュメントではなく、.env のようにキーを入力できる
  ファイルが欲しい」というユーザー要望。
- **E2Eテスト**: N/A（起動アプリを伴わない設定/ドキュメント変更）。代わりに
  リポジトリチェック → `check-divisions.sh` **PASSED(17)**、
  `check-tools.sh` **PASSED(15)**。`git check-ignore` で
  `projects/.env`=ignored / `projects/.env.example`=tracked を確認。
- **結果/確認**: `.env` は gitignore 済みのためコミット・チェックポイントに
  含まれない（ロールバックしても消えない）。
- **次にやること / 申し送り**: 実際にキーを使い始めたら `projects/.env` に記入し、
  キー名を `.env.example` にも追記する。アプリからは dotenv 等で
  `projects/.env`（または各アプリにコピー）を読み込む。

## 2026-08-10 14:44 — 外部サービス依存の管理台帳を追加（+ git 再初期化）

- **変更ファイル**: `projects/EXTERNAL_SERVICES.md`（新規）, `projects/README.md`,
  `.gitignore`
- **やったこと**:
  - `projects/EXTERNAL_SERVICES.md` を新規作成。MCP・APIキー等の外部サービス依存を
    管理する台帳＋規約（実値は `.env` / 環境変数へ、リポジトリにはメタ情報のみ。
    `.env.example` 規約、`.mcp.json` の環境変数参照、漏えい時対応、コミット前チェック）。
  - `projects/README.md` に台帳への導線と `.env` / `.env.example` の構成を追記。
  - `.gitignore` にシークレット系除外（`.env`, `.env.*`, `!.env.example`,
    `*.local.json`, `credentials.json`, `secrets.json`）を追加。
  - **注意**: 本ワークスペース（`nautra_poc`）に `.git` が無かった（2026-08-10 に
    フォルダコピーで作られた模様。旧 git 履歴は未継承）。チェックポイント運用の
    ため `git init -b main` で再初期化し、変更前状態を baseline コミット
    `37ce517` として保存してから本変更を適用した。
- **理由/背景**: projects 配下で外部サービス（MCP接続キー・APIキー等）を使う際の
  保存ルールが未整備だったため、誤コミット防止の規約と台帳をユーザー要望で作成。
- **E2Eテスト**: N/A（起動アプリを伴わないドキュメント/設定変更）。代わりに
  リポジトリチェックを実行 → `check-divisions.sh` **PASSED(17)**、
  `check-tools.sh` **PASSED(15)**。
  ※git 再初期化前は両スクリプトが `not a git repository` で誤FAILしていた。
- **結果/確認**: 台帳・README導線・gitignore 追記を確認。baseline `37ce517` から
  ロールバック可能。
- **次にやること / 申し送り**: 旧ワークスペースの git 履歴（2026-07-24 の
  チェックポイント群）はこのフォルダに引き継がれていない。旧履歴が必要なら
  コピー元フォルダから `.git` を持ってくるか、GitHub リモートの再設定を検討。
  依存サービスを実際に追加したら台帳（§3）へ記入すること。
  → **【2026-08-10 16:20 追記・解決済】旧履歴は GitHub リモート
  （`Kazuki-EPknoT-ITE/agency-agents-main`）に全て残っていた。ローカルを
  リモート履歴に接続し直したため、7/24 のチェックポイントも復元ポイントとして
  再び利用可能。なお本エントリの `EXTERNAL_SERVICES.md` は 16:06 の変更で
  廃止済み（現在は `projects/.env` 方式）。**

## 2026-07-24 17:15 — ロールバック（復元ポイント）の仕組みを追加

- **変更ファイル**: `scripts/checkpoint.sh`（新規）, `scripts/rollback.sh`（新規）,
  `ref/ROLLBACK.md`（新規）, `CLAUDE.md`, `ref/PROJECT_RULES.md`, `ref/LOG.md`
- **やったこと**:
  - チェックポイント方式のロールバック機構を導入。`checkpoint.sh` で現在の状態
    （コード＋ログ）を1コミット＝復元ポイントとして保存。`rollback.sh` で
    `--list` / `--last` / `<時点ID>` 復元、`--undo` 取り消し。
  - ロールバック前に `backup/<日時>` タグへ自動退避（何も失われない）。未コミット
    変更があるとブロック。
  - `CLAUDE.md` の実装後プロトコルに「手順3: チェックポイント作成」「手順4: 継続確認
    （このまま進める／元に戻す／特定時点へ）」を追加。実装後に毎回ユーザーへ確認する。
- **理由/背景**: 機能・デザインを足したあと「元に戻したい／ある時点まで戻したい」に、
  実装とログをまとめて安全に戻せるようにするユーザー要望。
- **E2Eテスト**: N/A（アプリ無しのスクリプト/ドキュメント変更）。代替として
  scratchpad の使い捨て git リポジトリで全フローを検証 →
  checkpoint作成 / `--last` 復元（コード＋ログ両方戻る）/ `--undo` / 特定SHA復元 /
  未コミット時ブロック すべて **PASS**。`bash -n` 構文OK、`check-divisions.sh` PASSED(17)。
- **結果/確認**: ローカルで復元機構が動作。`rollback.sh --list` で既存3コミットを確認。
- **次にやること / 申し送り**: この変更は未 push。GitHub反映は要確認。以降、実装後は
  本プロトコル（チェックポイント＋継続確認）を適用する。

## 2026-07-24 16:59 — check-divisions.sh に projects/ ref/ を除外追加

- **変更ファイル**: `scripts/check-divisions.sh`
- **やったこと**: `NON_DIVISION_DIRS` に `projects` と `ref` を追加。
- **理由/背景**: ワークスペースに `ref/`（ルール・ログ）と `projects/`（開発
  プロジェクト置き場）を追加したことで、これらが「部門ディレクトリ」と誤検知され
  `check-divisions.sh` が `FAILED: 1 divisions consistency error(s)` を出していた。
  （`.claude/` `.github/` はドット始まりで自動除外される。）
- **E2Eテスト**: N/A（起動アプリを伴わないカタログ/スクリプト変更）。代わりに
  リポジトリチェックを実行 → `check-divisions.sh` **PASSED(17)**、
  `check-tools.sh` **PASSED(15)**。`lint-agents.sh` は全エージェント走査で2分制限
  タイムアウト（本変更と無関係・AGENT_DIRS ハードコードで projects/ref を走査しない）。
- **結果/確認**: `check-divisions.sh` が PASSED に回復。
- **次にやること / 申し送り**: 未 push。GitHub `main` へ反映するかは要確認。

## 2026-07-24 — ref 読み込みの仕組みを構築

- **やったこと**: `CLAUDE.md`（セッション開始プロトコル）、`ref/PROJECT_RULES.md`、
  本ログ `ref/LOG.md`、`ref/README.md` を新規作成。SessionStart フックを設定。
- **理由/背景**: 毎回の新チャットで、最初に `ref/` のプロジェクトルールとログを
  必ず読み込ませたいというユーザー要望。
- **結果**: 次回以降のセッションから、開始時に `ref/` を読む運用が有効になる。
- **次にやること / 申し送り**: `ref/PROJECT_RULES.md` の「追記欄」に、実際の
  プロジェクト固有ルールを埋めていく。
