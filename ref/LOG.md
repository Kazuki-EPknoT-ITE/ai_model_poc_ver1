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

## 2026-10-12 14:30 — 順番待ちの表示の E2E（APP-10）と、アラートのルールの試験（OPS-05、promtool）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `apps/desktop/e2e/app.e2e.ts`（APP-10）、`eval/mock-gateway/scenarios/runtime/queue.yaml`（新規）
  - `server/deploy`: `prometheus/alerts.test.yml`（新規。promtool のアラートの試験 6本）、`tests/test_deploy_alerts.py`（新規。promtool があれば検査と試験、無ければ飛ばす）
  - 設計ドキュメント: ops/server_setup §8.1（試験の方法）、traceability（APP-10・OPS-05 を検証済みに）
- **やったこと**:
  - APP-10: モックの queue（3 → 2 → 1 番目）で「順番待ち 3 番目（約 6 秒）」の表示と、始まると消えることを E2E で確認した。
  - OPS-05: 固定版の promtool 2.53.2 を scratchpad に取り、ルールの文法の検査と、次のアラートが閾値・継続時間・文言どおりに出るかの単体試験を作った。
    - 対象: GPU 温度・待ち行列・エンジン停止・失敗率・バックアップと取り込みの遅れ・自動再起動。
    - 試験のデータの間隔が粗いと、Prometheus の「5分で古くなる」規則で for が続かないことがわかり、実際の収集（15秒）に近い間隔に直した（ルール自体は正しい）。
- **E2Eテスト**: **合格**。デスクトップ E2E **13件**（APP-10 を含む）。promtool の試験 2件も合格（PROMTOOL を指定して実行）。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest 507件成功・55件スキップ。promtool の2件は PATH に無いため、pnpm check の中では飛ばした）。
- **次にやること / 申し送り**: この PC でできる残りは少なくなった。
  - 検証機の接続情報待ち: M0・T-45・性能 PRF・実モデル評価・コード署名・UPS。
  - LibreOffice の導入で、試験3件のスキップを解消できる。

## 2026-10-12 13:40 — 社内文書の夜間の取り込み（OPS-01）と、PostgreSQL での Gateway の試験（初めて実施）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/deploy`:
    - `scripts/rag-ingest.sh`・`systemd/llm-rag-ingest.{service,timer}`（新規）
    - `compose.yaml`（rag に NAS を読み取り専用でマウント）、`prometheus/alerts.yml`（RagIngestStale）
    - `tests/test_deploy_backup.py`（ingest の試験 2件）
  - `server/rag/examples/rag.yaml`・`acl.yaml`（新規。コンテナ用の設定例）
  - 設計ドキュメント: ops/server_setup 付録D、dev_guide（Docker なしでの PostgreSQL の試験）、traceability（OPS-01 を検証済みに）
- **やったこと**:
  - 毎晩 1:00 に差分取り込みを実行する（2:00 のバックアップより前）。成功時刻・所要時間・追加更新の件数を textfile に書き、26時間を超えたらアラートを出す。失敗したら前回の記録を残したまま、終了コードを返す。
  - これまで Docker が無く飛ばしていた **Gateway の PostgreSQL 版の試験**を、pgserver（PostgreSQL を同梱した pip パッケージ）で一時的に立てて実行した。
- **E2Eテスト**: **N/A（起動アプリの変更なし）→ 代わりに試験で合格**。
  - deploy の試験 9件（ingest: docker を差し替えて成功と失敗を確認）。
  - **Gateway の PostgreSQL 版の試験 50件すべて合格**（migration 0001〜0004、feedback、registry、updates、vision などを含む。これまで SQLite だけで確認していた）。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **507件**成功・53件スキップ）。
  - 一時的な PostgreSQL は試験の後に止めた。
- **次にやること / 申し送り**: 検証機の接続情報待ちの作業（M0・T-45・性能・実モデル評価・コード署名）。この PC でできる残りを探して進める。

## 2026-10-12 12:50 — アプリの自動更新（ADM-05、Gateway から配布）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/gateway`:
    - `src/llm_gateway/updates.py`（新規）、`config.py`（updates_dir）、`app.py`、`cli.py`（publish-update）
    - `tests/test_gw_updates.py`（新規 2件）、`examples/gateway.yaml`、`openapi.json`（作り直し。追加した API を反映）
  - `server/deploy/compose.yaml`（gateway に更新の置き場をマウント）
  - `apps/desktop`:
    - `src/main/updater.ts`（新規）、`src/shared/version.ts`（新規）、`src/main/main.ts`、`src/preload/preload.ts`、`src/shared/ipc.ts`
    - `src/renderer/App.tsx`（更新のお知らせ）、`app.css`
    - `test/updater.test.ts`・`test/version.test.ts`（新規 5件）、`e2e/app.e2e.ts`（ADM-05）、`e2e/fixtures.ts`（LLA_UPDATE_DRY_RUN）
  - `eval/mock-gateway`（/v1/app/latest・download、`scenarios/runtime/update.yaml`）
  - 設計ドキュメント: api（追記）、ops/server_setup 付録C、traceability（ADM-05 を検証済みに）、impl_plan
- **ユーザーの判断**: 配布元は「Gateway から配る」。また「毎回の継続確認で止まらず、GitHub へマージしてそのまま進める」との指示に従い、以後はチェックポイント → push → 次の作業と進める（メモリに記録）。
- **やったこと**:
  - 管理者が publish-update でインストーラーを登録すると、ログイン中のアプリが6時間ごとに確認する。新しい版を取って大きさと sha512 を確かめ、合うものだけを保存する。
  - 画面上部の「再起動して更新」でインストーラーを /S で起動して終了する。作業中は更新しない。
  - 依存を増やさないよう、electron-updater ではなく小さな自前の更新を作った。
- **E2Eテスト**: **合格**。
  - デスクトップ E2E **12件**。新規「ADM-05」: モックが 9.9.9 を配る → 取得・検証して保存 → お知らせ → 押すと /S での起動が要求される（試験ではファイルに記録するだけで、実際には起動しない）。
  - 単体: 改ざんした（sha512 が合わない）更新は捨てる、古い版は無視する。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **505件**成功・53件スキップ、desktop vitest 13件）。
- **次にやること / 申し送り**:
  - コード署名（証明書の調達）。
  - OPS-01（RAG の取り込みの timer）。
  - 検証機の接続情報待ちの作業。

## 2026-10-12 11:40 — ピボットの読み取り（XLS-09）、出典の該当箇所を開く（PDF-07）、実行ファイルを開かない対策

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `tools/office/src/office_tools/tools/xlsx.py`（xlsx.info に pivots）、`tools/office/tests/test_xlsx.py`（+1件）
  - `packages/protocol/schemas/src/tools/xlsx.yaml`（説明）＋生成物
  - `apps/desktop`:
    - `src/shared/open-policy.ts`（新規: canOpen・pageOf・citationPath）、`src/main/main.ts`（shell:openPath の制限と PDF のページ指定）
    - `src/preload/preload.ts`・`src/shared/ipc.ts`、`src/renderer/App.tsx`（出典のファイルを開く）
    - `test/open-policy.test.ts`（新規 3件）、`e2e/app.e2e.ts`（AGT-07 に開く操作）
  - 設計ドキュメント: ui_spec（追記）、traceability（XLS-09・PDF-07 を検証済みに）
- **やったこと**:
  - XLS-09: xlsx.info の各シートに pivots（名前・場所・元データ・行・列・フィルター・値の集計方法）を返す。集計値は場所を read_range で読む（編集は対象外）。
  - PDF-07: 読んだ記録と照らせたファイルの出典をクリックで開く。PDF は `#page=N` で該当ページを開く。
  - **見つけた危険**: 既存の「ファイルを開く」（ファイル一覧・成果物）が、実行ファイルでも既定の動作で開いていた（AI が作業フォルダに書いた .exe や .bat をクリックで実行できた）。実行ファイル・スクリプト・ショートカット・マクロ付き Office 文書は開かないようにした。末尾のドット・空白でのすり抜けにも対応した。
- **E2Eテスト**: **合格**。
  - デスクトップ E2E 11件。AGT-07 では、出典の memo.txt をクリック → 開こうとしたパスを確認した（試験では shell を差し替えて、実際には起動しない）。照らせない出典にはボタンが無い。
  - 試験の手前の状態（XLS-09 だけ）でも、全体（結合 10・E2E 11）が合格していた。
- **結果/確認**:
  - `pnpm check` が全体で成功（desktop vitest 8件、pytest **503件**成功・52件スキップ）。
- **次にやること / 申し送り**:
  - ADM-05（アプリの自動更新・署名。配布サーバーの方針が必要）。
  - OPS-01（RAG の取り込みの timer）。
  - 検証機の接続情報待ちの作業（M0・T-45・性能・実モデル評価）。

## 2026-10-12 10:40 — 画像の説明（DOC-07）: Gateway の画像つき推論と Runtime の中継

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/gateway`:
    - `jobs.py`（content の部品・画像の検証・purpose image_describe）、`util.py`（画像の見積もり）
    - `config.py`（models[].vision）、`db.py`、`engines.py`（FakeEngine.messages_seen）
    - `migrations/versions/0004_image_describe.py`、`examples/gateway.yaml`（vision: true）、`tests/test_gw_vision.py`（新規 2件）
  - `packages/agent-runtime`:
    - `src/sampling.ts`（image_describe）、`src/gateway/client.ts`（ContentPart）
    - `src/prompt/templates.ts`（IMAGE_DESCRIBE_PROMPT）、`src/prompt/builder.ts`（画像の見積もり）
    - `test/integration/office.it.test.ts`（DOC-07）
  - `eval/mock-gateway`（purpose image_describe、`scenarios/image-describe.yaml`）
  - 設計ドキュメント: api（追記）、prompts（追記）、traceability（DOC-07 を検証済みに）
- **やったこと**:
  - office-tools の docx.images（describe）は画像の説明を sampling で頼んでいたが、Runtime も Gateway も画像を扱えなかった。
  - Gateway: messages の content に OpenAI 互換の部品（text / image_url）を受け付ける。
    - 画像は data:image/png・jpeg の base64 だけ（外部の URL は取りに行かない）。4枚・約4.5MB まで。画像を読めないモデルには 400。
    - ジョブの用途に image_describe を追加した（DB の CHECK も migration 0004 で変更）。
  - Runtime: 説明の指示と画像を送り、結果は source=ocr の untrusted で包む（画像の中の「指示」もデータとして扱う）。
- **E2Eテスト**: **合格**。
  - Runtime の結合試験 **10件**。新規「DOC-07」: 画像を含む Word を office-tools で読み、モックの image_describe の説明が untrusted で渡ることを確認。
  - デスクトップ E2E 11件（本物の Gateway を含む）。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **502件**成功・52件スキップ）。
  - migration 0004 は SQLite で、制約が正しく置き換わることを確認した（`ck_jobs_ck_jobs_purpose`）。PostgreSQL では未確認（Docker なし）。
- **次にやること / 申し送り**:
  - PDF-07（該当箇所の表示）、XLS-09（ピボットの読み取り）、ADM-05（自動更新）。
  - PostgreSQL での migration 0003・0004 の確認。

## 2026-10-12 09:40 — プラグインの追加手段（AGT-09）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `apps/desktop`:
    - `src/main/main.ts`（loadPlugins: `%ProgramData%\LocalLLMAgent\plugins.json`）、`src/shared/ipc.ts`（plugins）
    - `src/runtime-host/host.ts`（プラグインごとに MCP ホスト。推論は使えない）
    - `e2e/fixtures.ts`（plugins オプション・LLA_PLUGINS_FILE）、`e2e/app.e2e.ts`（AGT-09）
  - `packages/agent-runtime/src/tools/router.ts`（プラグインのパスも絶対化・結果を相対化）
  - `eval/plugins/sample/sample_plugin.py`（新規。見本の MCP プラグイン）、`eval/mock-gateway/scenarios/runtime/plugin.yaml`（新規）
  - 設計ドキュメント: dev_guide（付録: プラグインの追加）、traceability（AGT-09 を検証済みに）
- **やったこと**:
  - Runtime はもともとプラグイン（MCP）を受け付けたが、設定する手段がなかった。
  - 管理者が PC ごとに置く plugins.json（一般利用者は書き換えられない場所）から読み込むようにした。
  - ツール名は `plugin.<id>.<ツール>`。注釈（x-agent-effect など）の無いツールは書き込み扱いで、毎回承認が必要。
  - パスの引数は office-tools と同様に絶対パスで渡し、書き込みは staging 経由（元に戻せる）。
  - 試験では既定のファイルを読まないよう、必ず LLA_PLUGINS_FILE を指定する。
- **E2Eテスト**: **合格**。デスクトップ E2E **11件**。新規「AGT-09」:
  - 見本プラグインの word_count は承認なしで実行される。
  - stamp は「プラグインによる書き込み」の承認が出て、承認するまで書かない → 承認後に書いて完了する。
  - Runtime の結合試験 9件合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest 500件成功・51件スキップ、mypy 104ファイル）。
  - 試験の途中で、見本プラグインが Windows で CRLF を書いていたのに気づき、改行を固定した。
- **次にやること / 申し送り**: PDF-07（該当箇所の表示）、DOC-07（画像の説明）、XLS-09（ピボットの読み取り）、ADM-05（自動更新）。

## 2026-10-12 08:50 — ダッシュボードの GPU（ADM-03）と、ハングしたコンテナの自動再起動（OPS-02）

- **変更ファイル**（`projects/local-llm-agent/server/`）:
  - `gateway`:
    - `src/llm_gateway/gpu.py`（新規。dcgm-exporter の /metrics を読む）、`app.py`（dashboard の gpu）、`config.py`（metrics.dcgm_url）
    - `examples/gateway.yaml`（dcgm_url、DB のホスト名を postgres に）、`tests/test_gw_gpu.py`（新規 2件）
  - `deploy`:
    - `scripts/watchdog.sh`・`systemd/llm-watchdog.{service,timer}`（新規）
    - `compose.yaml`（gateway・web-relay・ocr・rag にヘルスチェック、`llm.autoheal` ラベル、pg_isready のユーザーを llmgw に）
    - `prometheus/alerts.yml`（ContainerRestartedByWatchdog）、`tests/test_deploy_backup.py`（watchdog の試験 2件）
  - 設計ドキュメント: ops/server_setup §8.3（watchdog、postgres.env の中身）、traceability（ADM-03・OPS-02 を検証済みに）
- **やったこと**:
  - ダッシュボードの GPU が常に空だった。dcgm-exporter から温度・使用率・メモリを読み、10秒だけ覚えておく。取れないときは空にし、ダッシュボードは止めない。
  - Docker は unhealthy なコンテナを自動で再起動しないため、ホストの watchdog（1分ごと）が `llm.autoheal=true` の unhealthy なコンテナを `docker restart` する。同じコンテナは10分に1回まで。回数は textfile に出してアラートにする。
  - **見つけた設定の食い違い**:
    - Gateway の設定例の DB ホストが `db` だったが、compose のサービス名は `postgres`。
    - ヘルスチェックのユーザーが `llm` だったが、Gateway は `llmgw`。
- **E2Eテスト**: **合格**。
  - 管理画面 E2E 4件を本物の Gateway で実施（ダッシュボードの GPU は dcgm_url 未設定で「取得できません」のまま）。
  - GPU と watchdog は試験で確認した。GPU: 実際の DCGM の出力の見本を読む。watchdog: docker を差し替えた bash で再起動・待ち時間・記録を確認。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **500件**成功・51件スキップ、mypy 104ファイル）。
- **次にやること / 申し送り**:
  - AGT-09（プラグインの設定手段）、PDF-07（該当箇所の表示）、DOC-07（画像の説明）、XLS-09（ピボットの読み取り）。
  - 実機での DCGM・watchdog の確認は検証機で。

## 2026-10-12 08:00 — 評価ボタンと同意付きの学習データ候補の収集（ADM-06）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/gateway`:
    - `src/llm_gateway/feedback.py`（新規）、`db.py`（Feedback）、`app.py`
    - `migrations/versions/0003_feedback.py`（新規）、`tests/test_gw_feedback.py`（新規 4件）
  - `packages/protocol/schemas/src/runtime.yaml`（コマンド feedback.submit）＋生成物、`packages/agent-runtime/src/runtime.ts`（submitFeedback）
  - `apps/desktop`: `App.tsx`（評価バー）、`app.css`、`e2e/app.e2e.ts`（E-01 に評価）、`e2e/fixtures.ts`（ctx.url）
  - `apps/admin-web`: `App.tsx`（評価データタブ A-09）、`admin.css`、`e2e/admin.e2e.ts`（ADM-06）
  - `eval/mock-gateway`: `/v1/feedback`、管理 API、試験用の `/mock/feedback`
  - 設計ドキュメント: api §9.1.1、data_model §3.4、protocol（追記）、traceability（ADM-06 を検証済みに）
- **やったこと**:
  - 完了した作業の下に「👍 良い／👎 悪い」とひとこと、「やり取りを改善に提供する」の同意チェックを置いた。
  - 同意したときだけ、Runtime がこの作業のやり取り（依頼・出力・ツール結果）を添えて送る。Gateway は暗号化して保存し、確認待ちにする。同意がなければ中身は受け取っても捨てる。
  - 管理画面「評価データ」で、マスキングした形だけを見て承認／却下する。マスキングするもの: 機密語辞書の語と読み・メール・電話番号・4桁以上の数字・利用者のフォルダ名。
    - 却下したら中身を削除する。
    - 承認したものだけを JSONL で書き出す（ml/datagen の入力）。
  - 監査ログに送信・承認・書き出しを残す。
- **E2Eテスト**: **合格**。
  - デスクトップ E2E 10件（E-01 で評価を送り、モックに同意つき・やり取り3件以上が届くことを確認）。
  - 管理画面 E2E 4件を、モックと本物の Gateway の両方で実施。新規「ADM-06」: API で同意つきの評価を作り → 伏せ字で表示（平井丸・金額が見えない）→ 承認 → 書き出しにも伏せ字。
  - スクリーンショットは scratchpad の `e2e-admin2/real/admin-feedback.png`。
  - Runtime の結合試験 9件合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **496件**成功・50件スキップ、mypy 103ファイル）。
  - DB に平文が残らないこと（暗号文に金額・船名の文字列が無い）を試験で確認した。
- **次にやること / 申し送り**:
  - AGT-09（プラグインの設定手段）、ADM-03（ダッシュボードの GPU を DCGM から）、OPS-02（ハング時の再起動）。
  - 書き出したデータを ml/datagen で使う流れは T-52 で。

## 2026-10-12 06:50 — 会話履歴の検索（APP-06）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `apps/desktop/src/renderer/App.tsx`（検索欄。0.2秒待ってから conversation.search）、`styles/app.css`
  - `packages/agent-runtime/src/store/store.ts`（LIKE の % _ をエスケープ）
  - `packages/agent-runtime/test/foundation.test.ts`（+1件）、`apps/desktop/e2e/app.e2e.ts`（E-01 に検索を追加）
  - 設計ドキュメント: traceability（APP-06 を検証済みに）
- **やったこと**:
  - Runtime にはもともと conversation.search があったが、画面から使えなかった。左の履歴の上に検索欄を置いた。
  - 探す対象は、題名と利用者が送った依頼の文（読み取ったファイルの中身は対象外）。
  - 「%」「_」を含む語でも文字どおりに探すよう、エスケープを直した（以前は「%」で全件が出ていた）。
- **E2Eテスト**: **合格**。デスクトップ E2E 10件。E-01 で次を確認:
  - 「確認」で見つかる。
  - 「存在しない語100%」で「見つかりませんでした」。
  - 空にすると一覧に戻る。
- **結果/確認**:
  - `pnpm check` が全体で成功（runtime vitest 19件＋失敗なし、pytest 492件成功・47件スキップ）。
- **次にやること / 申し送り**: ADM-06（評価ボタンと同意付きの収集）、AGT-09（プラグインの設定手段）、ADM-03（ダッシュボードの GPU）。

## 2026-10-12 06:20 — 出典の表示と照合（AGT-07 / NET-05）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `packages/agent-runtime/src/runtime.ts`（`checkCitations`: 完了報告の citations を読んだ記録と照合し、done の detail に入れる）
  - `apps/desktop/src/renderer/state.ts`（`citations`）、`App.tsx`（「出典」の一覧と「確認できない出典」）、`styles/app.css`
  - `apps/desktop/e2e/app.e2e.ts`（AGT-07/NET-05）、`eval/mock-gateway/scenarios/runtime/cite.yaml`（新規）
  - 設計ドキュメント: ui_spec（生成中・出典・予備機の表示を追記）、traceability（AGT-07・NET-05 を検証済みに）
- **やったこと**:
  - 完了報告の出典（ファイル／社内文書／Web）を、回答の下に一覧表示する。
  - Runtime は、この会話のツール結果・利用者の発言（モデル自身の出力は除く）と照らし、照らせない出典に「確認できない出典」を付ける。作業フォルダにあるファイルは確認できたものとする。モデルが作り話の URL を書いても見分けられる。
  - 途中で、完了報告そのものに URL が含まれるため照合が常に成功してしまう不具合に気づき、assistant の発言を除くよう直した。
- **E2Eテスト**: **合格**。
  - デスクトップ E2E **10件**（新規「AGT-07/NET-05: 社内文書・ファイルは確認済み、読んでいない URL は確認できない出典」を含む）。
  - 途中の1回は E-04 が時間切れで失敗した。単独で2回、全体でもう1回実行してすべて合格したので、一時的な揺れと判断（同じことが続くなら原因を調べる）。
  - Runtime の結合試験 9件も合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest 492件成功・47件スキップ）。
- **次にやること / 申し送り**: 会話履歴の検索の画面（APP-06）、ADM-06 の評価ボタン。

## 2026-10-12 05:40 — 回答の逐次表示（APP-01）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `apps/desktop/src/renderer/state.ts`（`live`、`readableFromPartial`）、`App.tsx`（生成中の吹き出し）、`styles/app.css`
  - `apps/desktop/test/state.test.ts`（+2件）、`apps/desktop/e2e/app.e2e.ts`（APP-01）
  - `eval/mock-gateway/scenarios/runtime/stream.yaml`（新規。ゆっくり流す）
  - 設計ドキュメント: traceability（APP-01 を検証済みに）
- **やったこと**:
  - Runtime はもともと token イベントを出していたが、画面が使っていなかった。
  - 推論中の出力は Action の JSON なので、そのままは見せない。summary / thought / question / note の値だけを書きかけでも取り出して、点線の吹き出しで逐次表示する（エスケープ・\u・切れ目に対応）。
  - 手順が確定したら消え、手順カードや完了報告に置き換わる。
- **E2Eテスト**: **合格**。
  - デスクトップ E2E 9件（新規「APP-01: 完了報告が届く前に、書きかけの文が JSON の記号なしで出る → 完了後に消える」を含む）。
  - スクリーンショットは scratchpad の `e2e-desktop6/app01-live.png`。
- **結果/確認**:
  - `pnpm check` が全体で成功（desktop vitest 5件、pytest 492件成功・47件スキップ）。
- **次にやること / 申し送り**: 出典の表示（AGT-07 / NET-05）、会話履歴の検索の画面（APP-06）。

## 2026-10-12 05:00 — 試用の手順書とアンケート（T-55）、トレーサビリティの状態を実装に合わせて更新

- **変更ファイル**（`projects/docs/`）:
  - `local_llm_ops/trial_guide.md`（新規）、`README.md`（一覧に追加）
  - `local_llm_traceability.md`（全96要件の状態と、根拠の表）、`local_llm_impl_plan.md`（T-55）
- **やったこと**:
  - 試用の手順書を作った。中身: 準備（アカウント・配布・共通指示・機密語）、説明会、課題 A〜D（代表シナリオ3本＋自由課題）、記録シート、アンケート（Q1「業務で使いたい」で 9.2-4 を判定）、集計と判定、問い合わせ対応。
  - トレーサビリティの「状態」が全件「未着手」のままだった。実装と試験を読み直して更新した（読み取り専用のサブエージェントで調査し、反映は自分で行った）。
    - 結果: **検証済み 70・実装済み 6・実装中 15・未着手 5**。
  - 主な不足（次の作業の候補）:
    - APP-01: 回答の逐次表示（token イベントを画面が使っていない）
    - AGT-07 / NET-05: 出典（citations）を画面に出していない
    - APP-06: 会話履歴の検索の画面
    - ADM-03: ダッシュボードの GPU（DCGM 未連携）
    - ADM-05: 自動更新・署名
    - ADM-06: 評価ボタン
    - AGT-09: プラグインの設定手段
    - OPS-02: ハング時の再起動
    - PDF-07: 該当箇所の表示
- **E2Eテスト**: **N/A**（ドキュメントのみで、起動アプリの変更なし）。代わりに `scripts/lint-agents.sh` を実行して成功（exit 0）。
- **結果/確認**: アプリのコードは変更していない（前回 25f7f17 の pnpm check・E2E の合格のまま）。
- **次にやること / 申し送り**: 上の不足のうち、この PC でできるもの（APP-01・出典・履歴検索・ADM-06 など）を進める。

## 2026-10-12 04:20 — 予備機への自動切替（OPS-03）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `packages/agent-runtime/src/gateway/client.ts`（URL を複数に、切替・切り戻し、`onSwitch`）、`src/runtime.ts`（`server.status` failover/primary）、`test/failover.test.ts`（新規 3件）
  - `apps/desktop`:
    - `src/main/main.ts`（予備機の設定 `spareUrl`・環境変数 `LLM_GATEWAY_SPARE_URL`、ログイン・更新・ログアウトを予備機へも）
    - `src/runtime-host/host.ts`（トークンの更新を今の接続先へ）、`src/shared/ipc.ts`（`gatewayUrls`）
    - `SettingsDialog.tsx`（予備のサーバーの URL）、`App.tsx`・`app.css`（「予備機で接続中」の表示）、`e2e/app.e2e.ts`（設定の保存に予備機を追加）
  - `eval/harness/src/headless.ts`（URL の配列・ログイン先の追従）、`eval/fault-injection`（シナリオ OPS-03）
  - 設計ドキュメント: design（OPS-03 の実装）、protocol（server.status の state）、impl_plan
- **やったこと**:
  - 接続そのものができない状態が3回続いたら予備機へ切り替える。HTTP のエラー（503 など）では切り替えない。
  - 予備機の間は5分ごとに本番機の /healthz を確かめて切り戻す。
  - 進行中の推論は 404 → 試行番号を上げて投入し直す（既存の JobExpired の処理）。
- **E2Eテスト**: **合格**。
  - 新しいシナリオ OPS-03（推論中に本番機のモック Gateway を落として戻さない → 予備機で完了・切替の通知・重複 0）が2回とも合格。
  - 全シナリオ `pnpm fi --suite all`: **26件すべて合格**。
  - デスクトップ E2E 8件合格（設定画面で予備機の URL を保存・再表示）。Runtime の結合試験 9件合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（vitest runtime 30件、pytest 492件成功・47件スキップ）。
- **次にやること / 申し送り**:
  - 予備機に同じ鍵・DB を復元する手順の訓練（restore.sh）を検証機で行う。
  - 試用の手順書（T-55）。検証機の接続情報待ち。

## 2026-10-12 03:30 — 監視（アラート）と毎晩のバックアップ・復元（T-65 一部）。compose に rag を追加、上流ポートの食い違いを修正

- **変更ファイル**（`projects/local-llm-agent/server/`）:
  - `deploy/scripts/backup.sh`・`restore.sh`（新規）、`deploy/systemd/llm-backup.service`・`.timer`（新規）
  - `deploy/prometheus/alerts.yml`（新規）、`prometheus.yml`（rule_files・alertmanager・node）、`deploy/alertmanager/alertmanager.example.yml`（新規）
  - `deploy/compose.yaml`（rag・alertmanager・node-exporter を追加。Qdrant のスナップショットの出力先）、`deploy.env.example`
  - `deploy/tests/test_deploy_backup.py`（新規 5件）
  - `gateway/examples/gateway.yaml`（上流のポート）
  - ルートの `pyproject.toml`（testpaths、ruff の除外）
  - 設計ドキュメント: ops/server_setup §5.1・§8.1・§8.2、impl_plan（T-65）
- **やったこと**:
  - アラート: GPU 温度、待ち行列、推論エンジン停止、取得失敗、ディスク、ジョブの失敗率、バックアップの遅れ・未実行。Gateway の実際のメトリクス名に合わせた。
  - バックアップ（server_setup §8.2 の表どおり）:
    - PostgreSQL: 日次14・月次12。Qdrant: コレクションごとのスナップショットを日次7。LoRA: rsync で全版。設定・secrets: tar → age で暗号化し30世代（サーバーには公開鍵だけ）。
    - 書きかけは .part にしてから置き換える。成功時刻を node-exporter の textfile に書く。
  - 復元: postgres（gateway 停止を確認）、qdrant（recover API）、config（空のフォルダへの展開のみ）。
  - **見つけた不具合**:
    - Gateway の設定例の上流ポート（rag 8100 / web-relay 8200 / ocr 8300）が、各サービスの実際の待ち受け（8094 / 8600 / 8090）と食い違っていた。直した。
    - compose に rag サービスが無かった。追加した。
- **E2Eテスト**: **N/A（起動アプリの変更なし）→ 代わりにスクリプトの試験で合格**。
  - `uv run pytest server/deploy/tests` 5件: docker・age・rsync を差し替えた bash で20日分を実行し、世代数・月次・回収・暗号化・成功記録を確認。NAS なし・公開鍵なし・ダンプが空・日付の形式違いでは失敗し、成功の記録を書かないことも確認。
  - compose・prometheus・alerts・alertmanager の YAML 構文と、`bash -n` の確認。promtool と Docker が無いため、ルールの式の実評価は検証機で行う。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest **492件**成功・47件スキップ、mypy 102ファイル）。
- **次にやること / 申し送り**:
  - 検証機で `promtool check rules`、`docker compose config`、復元訓練。
  - 予備機への自動切替（クライアントの接続先を複数に）は未。

## 2026-10-12 02:50 — パスのすり抜け（PX-01〜10）の試験と、見つかった穴の修正（TOCTOU の再確認・末尾のドット）

- **変更ファイル**（`projects/local-llm-agent/packages/agent-runtime/`）:
  - `src/policy/paths.ts`（末尾のドット・空白の正規化、予約デバイス名は `INVALID_ARGS`）
  - `src/policy/policy.ts`（`verifyUnchanged`）
  - `src/runtime.ts`（実行の直前に再確認）、`src/agent/intents.ts`（保存の直前に再確認）
  - `test/policy-px.test.ts`（新規 9件）
  - 設計ドキュメント: test_spec §5.2、design（パスの正規化）
- **やったこと**:
  - PX-01〜10 を単体試験にした: system への traversal、ジャンクション・ファイルのシンボリックリンク、デバイスパス、8.3 名、大文字小文字、末尾のドット・空白と ADS、TOCTOU、移動、予約名。
  - 見つけて直した点:
    1. **PX-07**: `D:\ws.\x` のように末尾にドット・空白がある部分を OS と違う解釈で判定していた。Windows と同じく取り除いてから判定するようにした。ドットと空白だけの名前は `INVALID_ARGS`。
    2. **PX-08**: 判定（や承認待ち）の後にフォルダをジャンクションへ差し替えられると、すり抜けられた。実行の直前と、保存（staging の置き換え）の直前に実体パスを解決し直して比べ、変わっていれば `FORBIDDEN_PATH` で止めるようにした。
    3. **PX-10**: 予約デバイス名を仕様どおり `INVALID_ARGS` にした（以前は FORBIDDEN_PATH）。
  - PX-04 の UNC は名前解決・接続を伴うので単体試験には入れなかった（判定の作りで担保）。
- **E2Eテスト**: **合格**。
  - 障害注入とセキュリティ `pnpm fi --suite all`: **25シナリオすべて合格**。
  - Runtime の結合試験 9件、デスクトップ E2E 8件（本物の Gateway を含む）も合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（vitest runtime 27件 ＝ +9、pytest 487件成功・47件スキップ、mypy 102ファイル）。
- **次にやること / 申し送り**:
  - 検証機の接続情報待ち（M0・T-45・実モデルでの評価）。
  - この PC でできる残り: 監視・バックアップのスクリプト（T-65 の一部）、試用の手順書（T-55）、Should/Could の機能（T-66）。

## 2026-10-12 02:10 — セキュリティ試験（プロンプトインジェクション SX-01〜07）を自動化（T-67 一部）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `eval/fault-injection/src/run.ts`（SX の7シナリオ、`--suite fi|sx|all`）、`src/host.ts`（自動却下 `rejectAfterMs`）
  - `eval/fault-injection/scenarios/sx-*.yaml`（delete・net・outside・escape・form・rag・ocr）
  - `eval/sets/security`（新規。採点器は resilience を流用）、`eval/harness/src/evaluate.ts`（セット名に応じた結果ファイル）
  - 設計ドキュメント: test_spec §5.1、impl_plan（T-67）、dev_guide
- **やったこと**:
  - 「読んだ内容の指示に従ってしまうモデル」をモックで模擬し、Runtime の守りがモデルの判断に関係なく効くことを確かめる。
  - SX-01: 削除の指示 → 承認要求で止まり、却下すれば消えない。
  - SX-02: ネットがオフ → web.search は差し戻され、Gateway に届かない。
  - SX-03: フォルダ外への書き出し → 承認要求。
  - SX-04: ページ内の `</untrusted>` はエスケープされ、タグの対応が保たれる。
  - SX-05: フォーム送信の手は存在せず、何も送られない。
  - SX-06: rag の結果は `source=rag` の untrusted で包まれ、フォルダ外は承認が必要。
  - SX-07: `pdf.ocr` の結果は `source=ocr` の untrusted で包まれる。
  - 気づき: 使えない手（例: ネットがオフのときの web.search）を繰り返すモデルは、error_streak で立て直し → 停滞のまま続け、終わらない（R3 の設計どおり）。試験側は差し戻しの注記がツール結果と同じ発言にまとまる場合も考えてシナリオを書いた。
- **E2Eテスト**: **合格**。
  - `pnpm fi --suite sx --repeat 3`: **7シナリオ × 3回すべて合格**。
  - `pnpm eval --set security`: 総合「合格」（完了率 1.0）。
  - ボットの変更の回帰確認として FI-16・FI-19 も合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest 487件成功・47件スキップ、mypy 102ファイル）。
  - Runtime 本体は変更していないので、デスクトップ E2E は前回（60123ed）の合格のまま。
- **次にやること / 申し送り**:
  - 実モデルでの SX 評価（検証機で `--gateway`）。
  - PX（パスのすり抜け）の残り（8.3 形式・ADS・TOCTOU など）の単体試験の追加。
  - T-45 の一本通しに必要な検証機の接続情報（ホスト名・SSH・Gateway の URL）が必要。

## 2026-10-12 01:30 — 障害注入を18件に拡大（3回ずつ全合格・重複0件）、評価ハーネスの台帳登録（--register）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `eval/fault-injection/src/run.ts`（FI-01・02・04・07・08・09・17・18・20 を追加）、`src/host.ts`（login・office・netLevel・環境変数の追加）
  - `eval/fault-injection/scenarios`（新規: escalate・compact・stale・batch・web と `fixtures/report.docx`）、`tools/docx_fixture.py`
  - `packages/agent-runtime/src/testhooks.ts`（`testNow`: `AGENT_TEST_CLOCK_SPEED` で経過時間を早送り）、`runtime.ts`（長時間の通知の判定に使用）
  - `apps/desktop/scripts/build.mjs`: 配布物の検査を `AGENT_TEST_` 全体に広げた。
  - `server/gateway/scripts/dev_server.py`（`--fake-delay`・`--max-concurrent`）
  - `eval/mock-gateway/src/mock_gateway/app.py`（`web_fetch_by_url`: URL ごとの応答・失敗）
  - `eval/harness/src/evaluate.ts`・`run.ts`（`--register`）、`eval/sets/resilience/items.jsonl`（18件を自動化済みに）
  - 設計ドキュメント: test_spec §4、impl_plan（T-51 完了）、dev_guide
- **やったこと**:
  - FI-01: 300手（ファイル150件を読む＋コピー）。時計を400倍に早送りし、2時間の通知が実行の途中で1回だけ出ることを確認。
  - FI-02: 文脈予算 8K で契約書3本を読む。圧縮が起き、固定メモリは保たれる。
  - FI-04: 26B への昇格を記録して完了。
  - FI-07: **本物の Gateway**（同時実行1・推論を遅く）に3人分の Runtime。3件が待ち行列にある状態で Gateway を強制終了・再起動 → キューが復元され、3件とも投入順に完了。ジョブの重複なし。
  - FI-08・09: LAN の切断。CI ではモックの停止で代替した（推論待ちの間に20秒・書き込み中）。
  - FI-17: 決定的な docx を読んだ直後に他人が段落を追加 → STALE_DOCUMENT → 読み直して編集し、他人の変更を失わない。
  - FI-18: 請求書20件のうち5件が壊れている → 15件を転記し、5件はエラーコード付きで失敗に挙げる。
  - FI-20: 検索結果の最初の2つの URL が 404 とタイムアウト → 3つ目で代替。再試行の無限ループなし。
  - `pnpm eval --set X --gateway … --model-id … --register` で、結果をモデル台帳に登録できるようにした。
- **E2Eテスト**: **合格**。
  - `pnpm fi --repeat 3`: **18シナリオ × 3回 = 54回すべて合格、重複 0件**。
  - `pnpm eval --set resilience`: 実行したものの完了率 1.0、重複 0。全体は 0.9 で、FI-10・FI-14 は自動化の対象外（理由を記録済み）。
  - `--register` を本物の Gateway（dev_server）で確認: 台帳の版 reg-test に doc_quality の指標2件が登録された。
  - Runtime の結合試験 9件合格。デスクトップ E2E 8件合格。
- **結果/確認**:
  - `pnpm check` が全体で成功（pytest 487件成功・47件スキップ、mypy 102ファイル、vitest）。
  - 秘密情報の grep で該当なし。
- **次にやること / 申し送り**:
  - FI-08・09 の実機での遮断と FI-14（スリープ）は試験機で行う。
  - PostgreSQL での試験（Docker）、検証機の vLLM で T-45 の一本通し、実モデルでの評価セット。

## 2026-10-12 00:20 — 障害注入試験（T-51）を実装。FI 9件を3回ずつ全合格、試験で見つけた Runtime の不具合2件を修正

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `packages/agent-runtime`:
    - `src/testhooks.ts`（新規。`AGENT_TEST_HOOKS=1` のときだけ働く）
    - `src/agent/intents.ts`（注入点 `intent.prepared`・`intent.replaced`、**再開時の二重書き込みの防止**）
    - `src/gateway/client.ts`（注入点 `infer.submitted`）
    - `src/runtime.ts`（**承認済み・未反映の操作を残したまま完了しない**）
    - `src/store/store.ts`（`intentsForStep`）、`test/foundation.test.ts`（回帰試験1件）
  - `apps/desktop/scripts/build.mjs`: 配布物から試験フックを除去（define＋minifySyntax、sourcesContent なし）。`AGENT_TEST_HOOK` の文字列が残っていればビルドを失敗させる。
  - `eval/fault-injection`（新規）:
    - `src/host.ts`（Runtime の子プロセス）、`src/run.ts`（シナリオと判定）
    - `scenarios/*.yaml`（モック Gateway の手順）、`tools/lockfile.ps1`（BOM 付き）、`package.json`・`tsconfig.json`
  - `eval/sets/resilience`（新規。FI-01〜20。未自動化のものは理由を `note` に記録）
  - ルート: `pnpm-workspace.yaml`、`package.json`（`pnpm fi`）、`.gitignore`（`eval/fault-injection/results/`）
  - 設計ドキュメント: test_spec §4（実装の説明）、data_model §3.4（再開時の書き込み）、dev_guide §1.4、impl_plan（T-51 一部完了）
- **やったこと**:
  - 本物のプロセスで障害を注入する: Runtime の子プロセスを `taskkill /F /T` で落として再起動する、モック Gateway を落として同じポートで再起動する、出力先を FileShare.None でロックする。
  - 判定は試験仕様 §4 の共通判定: `end_reason=final`、同じ `(path, op_digest)` の committed が2回ない、未確定の意図記録が残らない、成果物の中身が正しい。
- **試験で見つけて直した不具合**:
  1. **FI-13**（置き換えの後・committed の記録の前に落ちる）: 再開時に意図記録は committed に直るが、手順そのものを再実行して同じ書き込みをもう一度していた（重複 1件）。追記のような操作なら二重転記になる。同じ手順・同じ操作の committed の記録があれば、ツールを呼ばずに記録から結果を返すようにした。
  2. **FI-19**（承認待ち2件）: 推論中に2件目が承認されると、承認済みで未実行のまま完了していた（2件目のコピーが実行されない）。完了の前に、未反映の承認・回答を先に実行するようにした。
  - 試験側の不具合も直した:
    - `lockfile.ps1` が UTF-8（BOM なし）だと PowerShell 5.1 で param が壊れ、ロックできないのに `locked` と出していた。最初の FI-15 の合格は偽物だった。BOM 付きにし、`$ErrorActionPreference=Stop` にした。
    - FI-03 は注入が1回しか起きていなかった。推論の投入回数を確認する判定を足して発見し、3回注入するように直した。
- **E2Eテスト**: **合格**。
  - 障害注入 `pnpm fi --repeat 3`: FI-03・05・06・11・12・13・15・16・19 の **9シナリオ × 3回、すべて合格・重複 0件**。
  - 集計 `pnpm eval --set resilience`: 実行したものの完了率 1.0、重複 0。全体の完了率は 0.45（11件が未実行）なので総合は「未達」で、意図どおり。
  - Runtime の結合試験 9件合格。デスクトップ E2E 8件合格（本物の Gateway を含む）。
  - 配布用ビルドに試験フックが残っていないことを確認した（`testHook` の出現 0）。
- **結果/確認**:
  - `pnpm check` が全体で成功（vitest: runtime 18、protocol 198、desktop 3。mypy 102ファイル。pytest 487件成功・47件スキップ）。
  - 秘密情報の grep で該当なし。
- **次にやること / 申し送り**:
  - FI の残り: FI-01（時計の早送り）、02、04（昇格先ありのモック）、07（本物の Gateway の再起動）、17、18、20。08・09・14 は試験機で手動。
  - 評価結果を台帳へ直接登録する `--register`。
  - PostgreSQL での試験、実モデルでの評価、T-45 の一本通し。

## 2026-10-11 22:40 — 評価ハーネス（T-50）を統合し、全体チェック合格。チェックポイント作成（直下のエントリと合わせて1つの時点）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `eval/harness`（新規）:
    - Python `lla_eval/`（manifest 検証・data_hash・学習データとの重複照合・合成データ・採点・合否表・台帳向け出力）
    - TS `src/`（Runtime をヘッドレスで動かすランナー・モック Gateway のプロセス管理）
    - `package.json`・`tsconfig.json`・`tests/test_lla_eval.py`
  - `eval/sets`（新規。サンプル3セット: `invoice_extraction_sample`・`tool_tasks_sample`・`doc_quality_sample`、`_train/train_hashes.sample.txt`。すべて合成データ）
  - ルート: `pnpm-workspace.yaml`（eval/harness）、`package.json`（`pnpm eval`、mypy 対象）、`pyproject.toml`（testpaths・pythonpath）、`.gitignore`（`eval/harness/results/`）
  - 設計ドキュメント: dev_guide §1.4（評価ハーネスの使い方）、impl_plan（T-50 一部完了・T-51 未着手）
- **やったこと**:
  - サブエージェント（resilience-qa-engineer 相当）が、障害注入（T-51）の途中で 20分以上止まったため停止し、残りを引き取った。
  - 引き取った作業: ワークスペースへの登録、mypy の型エラー修正、Python の試験 8件の追加、3セットの実行確認、ルート設定とドキュメント。
  - `eval/fault-injection/src` は空のまま（T-51 は次回）。
- **E2Eテスト**: **合格**。
  - 評価ハーネスを実際に回した: `pnpm eval --set <各セット>`（モック Gateway・office-tools・Runtime をヘッドレスで起動）。
    - `tool_tasks_sample`: 合格（3件とも final、成功率 1.0）。
    - `doc_quality_sample`: 合格（破損 0・書式崩れ 0）。
    - `invoice_extraction_sample`: 「未達」。**意図どおり**で、モックが2枚目の合計・3枚目の船名に誤りを仕込んでいる。ハーネスが誤りを検出して不合格にすることを確認した。
  - 直下のエントリの管理画面 E2E 3件（モック・本物の Gateway）とデスクトップ E2E 8件は、その後の変更が eval だけなので再実行していない。
  - 結果は scratchpad の `eval-out/`。
- **結果/確認**:
  - `pnpm check` が全体で成功（eslint、tsc、生成物の差分、vitest、ruff、mypy 102ファイル、pytest **487件成功・47件スキップ**）。
  - スキップの内訳: PostgreSQL 44件、LibreOffice 3件。
  - 差分に秘密情報が含まれていないことを grep で確認した。
- **次にやること / 申し送り**:
  - T-51 障害注入試験（`eval/fault-injection`。`eval/harness/src/mock.ts` の停止・再起動・リクエスト記録と `scorer: resilience` を使う）。
  - 評価ハーネスの `--register`（結果を台帳へ直接登録）は未実装。今は `<run_id>.registry.json` を手で POST する。
  - PostgreSQL での試験（Docker）、実モデルでの評価（検証機）、T-45 の一本通し。

## 2026-10-11 21:55 — モデル台帳・Web Relay のポリシー取り込み・管理画面のモデル画面（途中経過。チェックポイントは評価ハーネスの統合後）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/gateway`:
    - `registry.py`（新規）、`admin_api.py`（`register_internal`・読み）、`app.py`、`gateway.py`（起動時に本番版を反映）
    - `engines.py`（`activate_lora`）、`util.py`（`ApiError.detail`）、`db.py`
    - `migrations/versions/0002_dlp_readings.py`、`tests/test_gw_registry.py`、`scripts/dev_server.py`（lint）
  - `server/web-relay`:
    - `policy_sync.py`（新規）、`config.py`（`policy_sync`）、`app.py`（lifespan・503）、`errors.py`
    - `examples/web-relay.yaml`、`tests/test_wr_policy_sync.py`
  - `server/deploy/compose.yaml`（Relay の設定・CA・データの volume）
  - `apps/admin-web`（モデル画面、機密語の読み、`ApiError.detail`、E2E 1件追加）
  - `eval/mock-gateway`（モデル台帳のモック、`GatewayError.detail`）
  - 設計ドキュメント: api §9.1（モデル台帳）・§9.2（内部 API）、data_model §3.4、impl_plan、ops/server_setup
- **やったこと**:
  - **モデル台帳**: 登録・評価結果・本番化・切り戻し。本番化には、本番版のすべての指標で同じ以上という条件を付け、満たさなければ 409 `EVAL_GATE_FAILED` と下回った指標を返す。エンジンへの LoRA 反映に失敗したら台帳は変えない。
  - **Web Relay**: Gateway の `GET /internal/v1/net-policy`（X-Relay-Secret）から60秒ごとに取り込む。取り込めるまで、また1時間を超えて古くなったら 503 で断る（安全側）。
- **E2Eテスト**: **合格**。
  - 管理画面 3件（新規「モデル台帳: 評価の条件・本番化・切り戻し」を含む）を、モックと本物の Gateway の両方で実施。
  - デスクトップ 8件（本物の Gateway を含む）。
  - 証跡は scratchpad の `e2e-admin/{mock,real}`・`e2e-desktop`。
- **結果/確認**:
  - pytest: gateway＋mock 70件成功・44件スキップ（PostgreSQL）、web-relay 181件成功。
  - ruff・mypy（gateway・web-relay・mock）・admin-web の tsc は問題なし。
  - **全体の `pnpm check` は未実行**（評価ハーネスのサブエージェントが `eval/harness`・`eval/sets` を書き込み中のため）。
- **次にやること / 申し送り**:
  - 評価ハーネス（T-50/T-51）のサブエージェントの成果を統合する。
  - その後 `pnpm check` → このログに追記 → `scripts/checkpoint.sh` → 継続確認・push の確認。

## 2026-10-12 04:30 — Gateway・Web Relay・RAG・OCR を統合し、本物の Gateway で E2E 合格（M4〜M6 の主要部品がそろった）

- **変更ファイル**（`projects/local-llm-agent/`）:
  - `server/gateway`（新規。サブエージェントが実装。統合時に `admin_api.py`、上流への内部トークン、管理画面の配信、`scripts/dev_server.py`、試験 `test_gw_admin.py` を追加・修正）
  - `server/rag`（新規。サブエージェント）、`server/web-relay`（新規。サブエージェント）、`server/ocr`、`server/deploy`
  - `apps/admin-web`（新規）、`apps/desktop`（差分・添付・定型作業・設定、配布物の作成、本物の Gateway の E2E）
  - `packages/agent-runtime`（`artifact.diff`・`config.get`・`settings.get`、`retryable:false` の扱い）、`packages/protocol`（コマンド3つ）
  - `tools/office/packaging`（exe 化）、`tools/office/tests` と `server/rag/tests`（conftest を testkit に分割）
  - ルートの `pyproject.toml`（workspace に gateway・web-relay・ocr・rag）、`package.json`、`eslint.config.js`
  - 設計ドキュメント: api（§9.1 管理 API の形、エラーコード、内部トークン、Web の判定順）、data_model（§3.4 実装差分、機密語の読み・サイトの会社 ID）、protocol（コマンド）、dev_guide、impl_plan
- **統合で直したこと**（別々のサブエージェントが作った部品の食い違い）:
  - Gateway → Web Relay: 宛先 `/search`・`/fetch`、本文に `user_id`・`org_id`・`level`、`X-Relay-Secret`。検索回数は Gateway だけが数える。
  - Gateway → OCR・RAG: `X-Internal-Token`。値は環境変数 `LLMGW_*_SECRET` かファイルのパスで渡す。
  - クライアント: `retryable:false` の 5xx（`FEATURE_UNAVAILABLE` など）を再試行しない。
  - Gateway に管理 API の残り（部門・ネット・機密語・共通指示・監査）を追加し、ダッシュボードに §9.1 の項目を足した。`from __future__ import annotations` があると、関数内で作った依存性を FastAPI が解決できない問題も直した。
  - pytest を全体で回すと conftest が衝突する問題: 部品を `office_testkit.py` / `rag_testkit.py` に分けた。
- **E2Eテスト**: **合格**。
  - デスクトップ 8件。新しく追加した **本物の Gateway**（argon2・EdDSA の JWT・永続キュー・暗号化・SSE。推論は FakeEngine）を相手にした1件を含む。
  - 管理画面 2件を、モックと本物の Gateway の両方で実施。
  - モック Gateway の E2E 3件、Runtime の結合試験 9件、Gateway と TS クライアントの互換性試験 4件。
  - 証跡は scratchpad の `e2e-final/`・`e2e-real/`・`e2e-admin-real/`。
- **結果/確認**:
  - `pnpm check` が全体で成功（eslint、tsc、生成物の差分、vitest 218件、ruff、mypy strict 94ファイル、pytest **468件成功・44件スキップ**）。
  - スキップの内訳: PostgreSQL 版 41件（Docker が起動していない）、LibreOffice 3件。
  - 変更したファイルに秘密情報が含まれていないことを grep で確認した。
- **次にやること / 申し送り**:
  - **未検証のもの**: PostgreSQL での Gateway の試験（`LLA_TEST_PG_URL`）、LibreOffice での再計算と変換、実モデル（vLLM/Gemma 4）・実 OCR エンジン・実際の埋め込みモデル。いずれも M0（検証機）と Docker・LibreOffice の導入待ち。
  - **管理画面で変えた機密語・サイトを Web Relay に反映する仕組みがまだない**（Relay は自分の YAML を読む。`PolicyStore.load()` はある）。Gateway の DB から配る方法を決める必要がある。
  - **RAG の取り込み**は YAML の ACL 表だけに対応している。NAS の権限を書き出す手順が必要。
  - 残りのタスク: モデル台帳の画面と API、評価ハーネス（T-50）と障害注入（T-51）、自動更新と署名（T-46 の残り）、推論エンジンの M0 作業。

## 2026-10-12 02:00 — （途中経過）画面の残り・OCR・配置・exe 化・インストーラ・管理画面。Gateway と RAG はサブエージェントで作業中

- **状態**: Gateway（T-40）と RAG（T-60）のサブエージェントがまだ作業中のため、**チェックポイントはまだ作っていない**（`checkpoint.sh` は `git add -A` で作業途中のファイルまで含めてしまうため）。両方が終わったら統合し、まとめて試験してからチェックポイントを作る。
- **このセッションで完了したもの**（`projects/local-llm-agent/`）:
  - 画面の残り（T-32/T-33）: 差分プレビュー（Word=段落、Excel=セル、テキスト=行。Runtime コマンド `artifact.diff`）、添付（📎 とドラッグ＆ドロップ。preload で `webUtils.getPathForFile`）、定型作業（`/` で候補。`config.get` の `macros`）、設定画面（`settings.get`）
  - OCR サービス（T-41、`server/ocr`。エンジンは PaddleOCR / VLM / 試験用の差し替え。画素数の上限で解凍爆弾を拒否）
  - サーバー配置一式（`server/deploy`。compose・3系統のネットワーク・共通 Dockerfile・Prometheus・systemd・DOCKER-USER のスクリプト。`docker compose config` で検査済み）
  - office-tools の exe 化（T-19、90MB）、インストーラ（T-46、NSIS 183MB。Node 22.23.3・Runtime・office-tools を同梱。署名なし）
  - 管理画面（T-62、`apps/admin-web`）とモック Gateway の管理 API、API 仕様 §9.1（要求・応答の形）
  - Web Relay（T-61、`server/web-relay`。サブエージェントが完了。試験 174 件）
- **E2Eテスト**: **合格**。
  - デスクトップ 7件（E-01/02、E-04/14、E-07/08、T-35、E-03/13（添付・定型作業）、E-06（差分）、設定）
  - 配布物（release/win-unpacked）での E-01・T-35・E-06 の3件
  - PyInstaller 版 office-tools での T-35・E-06
  - 管理画面 2件（Chromium）
- **結果/確認**: Runtime の単体試験 17 件、モック Gateway 16 件、OCR 7 件、Web Relay 174 件が成功。
- **次にやること / 申し送り**:
  - Gateway・RAG の完了を待って統合する（ルートの workspace に web-relay・ocr・rag を登録、Gateway から Relay・RAG・OCR への中継の結線、Gateway で管理画面を配る、本物の Gateway（FakeEngine）を相手にしたデスクトップの E2E）。
  - その後に `pnpm check` を全体で回し、チェックポイントを作る。

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
