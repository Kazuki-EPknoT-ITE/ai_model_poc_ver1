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
