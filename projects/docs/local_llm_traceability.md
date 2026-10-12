# ローカルLLMデスクトップエージェント トレーサビリティ表

2026年10月11日 · v0.1

要件書 `local_llm.md` の全要件IDについて、設計・詳細仕様・実装モジュール・テスト・マイルストーンの対応を管理する。実装やテストが進んだら「状態」を更新する（未着手 → 実装中 → 実装済み → 検証済み）。

**凡例**
- 詳細仕様の略称: `protocol` = `local_llm_protocol.md`、`data_model` = `local_llm_data_model.md`、`api` = `local_llm_api.md`、`ui` = `local_llm_ui_spec.md`、`prompts` = `local_llm_prompts.md`、`test` = `local_llm_test_spec.md`、`ops` = `local_llm_ops/server_setup.md`。プロトコル仕様だけは節番号まで書く。
- 実装モジュールは `projects/local-llm-agent/` からの相対パス（設計書 §3）。
- テスト種別: `unit`（単体）、`integ`（結合）、`e2e`（Playwright/Electron）、`fault`（障害注入）、`eval:<評価セット>`（9.1 の評価セット）、`perf`（性能）、`sec`（攻撃シナリオ）。テストIDは試験仕様で決める。
- 優先度: Must / Should / Could（要件書のとおり）。非機能要件（PRF/SEC/OPS）は「NFR」と書く。

## 5.1 会話・画面（APP）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| APP-01 | Must | §4.1, §6.3 | protocol §5.2(token), api, ui | apps/desktop, packages/agent-runtime, server/gateway | e2e, integ | M4 | 検証済み |
| APP-02 | Must | §4.1 | protocol §5.1(run.start), ui | apps/desktop | e2e | M4 | 検証済み |
| APP-03 | Must | §4.1 | protocol §3.1, ui, data_model | apps/desktop, packages/fs-tools | e2e | M4 | 検証済み |
| APP-04 | Must | §4.1 | protocol §5.2(step), ui | apps/desktop | e2e | M4 | 検証済み |
| APP-05 | Should | §4.1 | protocol §1(PlanUpdate), §5.2(plan), ui | apps/desktop | e2e | M4 | 検証済み |
| APP-06 | Must | §4.1, §4.4 | protocol §5.1(conversation.*), data_model, ui | apps/desktop, packages/agent-runtime | unit, e2e | M4 | 検証済み |
| APP-07 | Must | §4.1, §5.4 | protocol §5.1(run.stop), ui | apps/desktop, packages/agent-runtime | e2e, integ | M4 | 検証済み |
| APP-08 | Should | §4.1, §6.3 | protocol §5.1(run.start.model), api, ui | apps/desktop, server/gateway | e2e, integ | M4 | 検証済み |
| APP-09 | Should | §4.1, §9 | ui, prompts | apps/desktop, profiles/_template | e2e | M6 | 検証済み |
| APP-10 | Should | §4.1, §6.3 | protocol §5.2(queue), api, ui | apps/desktop, server/gateway | integ, e2e | M4 | 実装済み |
| APP-11 | Must | §4.1 | ui | apps/desktop | e2e | M4 | 検証済み |

## 5.2 エージェント（AGT）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AGT-01 | Must | §5.1, §5.2 | protocol §1, prompts, data_model | packages/agent-runtime | unit, eval:ツール操作 | M3 | 検証済み |
| AGT-02 | Must | §5.3 | protocol §2.2, §4, data_model | packages/agent-runtime | unit, sec, eval:安全性 | M3 | 検証済み |
| AGT-03 | Must | §5.4 | protocol §2.3(Artifact), §5.1(undo.run), data_model, ui | packages/agent-runtime, apps/desktop | unit, e2e | M3 | 検証済み |
| AGT-04 | Must | §5.1, §5.6 | protocol §1.1, prompts, data_model | packages/agent-runtime | unit, eval:長時間作業 | M3 | 検証済み |
| AGT-05 | Must | §5.2 | protocol §1.2, §1.3, §2.4 | packages/agent-runtime, server/gateway | unit, eval:ツール操作 | M3 | 検証済み |
| AGT-06 | Must | §5.7 | protocol §2.5, §3.7, prompts | packages/agent-runtime | unit, eval:ツール操作 | M3 | 検証済み |
| AGT-07 | Should | §6.4 | protocol §3.8(rag.search), api, prompts | server/rag, packages/agent-runtime | integ, eval:社内文書検索 | M6 | 検証済み |
| AGT-08 | Must | §5.3 | protocol §2.1 | packages/agent-runtime | unit, sec | M3 | 検証済み |
| AGT-09 | Should | §5.2, §7.1 | protocol §2.1, §2.2 | packages/agent-runtime | integ | M6 | 実装中 |
| AGT-10 | Should | §6.3, §9 | api(/v1/config), prompts | server/gateway, profiles/_template | integ | M6 | 検証済み |

## 5.3 利用者・管理（ADM）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADM-01 | Must | §6.3, §10 | api, data_model, ui | server/gateway, apps/desktop | integ, sec, e2e | M4 | 検証済み |
| ADM-02 | Should | §6.4, §10 | api, data_model, ui | server/gateway, server/rag, apps/admin-web | integ, sec | M6 | 検証済み |
| ADM-03 | Should | §6.7 | api, ui, ops | apps/admin-web, server/deploy | e2e | M6 | 実装中 |
| ADM-04 | Should | §6.3 | api, data_model | server/gateway | integ | M4 | 検証済み |
| ADM-05 | Should | §4.3 | ops | apps/desktop | integ | M6 | 実装中 |
| ADM-06 | Should | §8.3 | api, data_model, ui | server/gateway, apps/desktop, ml/datagen | integ | M6 | 未着手 |

## 5.4 継続実行（RUN）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RUN-01 | Must | §5.1, §4.4 | data_model | packages/agent-runtime | unit, fault | M3 | 検証済み |
| RUN-02 | Must | §5.4 | protocol §2.3(Artifact), §2.7, data_model | packages/agent-runtime, tools/office | unit, fault, eval:継続実行 | M3 | 検証済み |
| RUN-03 | Must | §5.9 | protocol §2.4, §5.2(retry), api | packages/agent-runtime | unit, fault | M3 | 検証済み |
| RUN-04 | Must | §5.2, §6.2 | protocol §1, §1.3, prompts | server/gateway, server/deploy, packages/protocol | integ, eval:ツール操作 | M0/M3 | 検証済み |
| RUN-05 | Must | §5.6 | prompts, data_model | packages/agent-runtime, server/gateway | unit, eval:長時間作業 | M3 | 検証済み |
| RUN-06 | Must | §5.8 | protocol §3.6, data_model | packages/agent-runtime, tools/office | unit, eval:長時間作業 | M3 | 検証済み |
| RUN-07 | Must | §4.3 | ui | apps/desktop | e2e, fault | M4 | 検証済み |
| RUN-08 | Must | §5.3, §4.3 | protocol §4, §5.2(approval.request, notify), ui | packages/agent-runtime, apps/desktop | unit, e2e | M3/M4 | 検証済み |
| RUN-09 | Must | §6.3 | api, data_model | server/gateway | integ, fault | M4 | 検証済み |
| RUN-10 | Should | §6.3 | api, data_model | server/gateway | integ, perf | M4 | 検証済み |
| RUN-11 | Must | §5.1 | protocol §5.1(run.pause/resume/stop), ui | packages/agent-runtime, apps/desktop | unit, e2e | M3/M4 | 検証済み |

## 5.5 ネットアクセス（NET）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NET-01 | Must | §4.1, §6.6 | protocol §5.1(settings.update), ui | apps/desktop, server/gateway | e2e, sec | M6 | 検証済み |
| NET-02 | Must | §6.6 | api, data_model, ui | server/gateway, apps/admin-web | integ, sec | M6 | 検証済み |
| NET-03 | Must | §5.3 | protocol §3.8, §1.3 | packages/agent-runtime | unit | M6 | 検証済み |
| NET-04 | Must | §4.1 | protocol §5.2(net.activity), ui | apps/desktop | e2e | M6 | 検証済み |
| NET-05 | Must | §6.6 | protocol §1(Final.citations), prompts | packages/agent-runtime | eval:ネットアクセス | M6 | 検証済み |
| NET-06 | Must | §6.6 | api | server/web-relay | sec | M6 | 検証済み |
| NET-07 | Must | §6.6 | protocol §2.4(DLP_BLOCKED), api | server/web-relay | sec, eval:ネットアクセス | M6 | 検証済み |
| NET-08 | Must | §6.6 | protocol §2.4(DOMAIN_BLOCKED), api, data_model | server/web-relay | sec | M6 | 検証済み |
| NET-09 | Must | §5.5, §6.6 | protocol §2.3(UntrustedBlock), prompts | server/web-relay, packages/agent-runtime | sec, eval:ネットアクセス | M6 | 検証済み |
| NET-10 | Must | §5.5 | protocol §4(tainted), data_model | packages/agent-runtime | unit, sec | M6 | 検証済み |
| NET-11 | Must | §6.6 | api, data_model | server/web-relay, server/gateway | integ | M6 | 検証済み |
| NET-12 | Must | §6.6 | ops | server/deploy | sec | M0/M6 | 実装済み |
| NET-13 | Should | §6.6 | protocol §2.4(QUOTA_EXCEEDED), api, data_model | server/web-relay, server/gateway | integ | M6 | 検証済み |

## 6.1 Word（DOC）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DOC-01 | Must | §7.2 | protocol §3.2(docx.read, docx.outline) | tools/office | unit, eval:文書品質 | M2 | 検証済み |
| DOC-02 | Must | §7.2 | protocol §3.2(docx.create) | tools/office | unit, eval:文書品質 | M2 | 検証済み |
| DOC-03 | Must | §7.2 | protocol §3.2(docx.edit) | tools/office | unit, eval:文書品質 | M2 | 検証済み |
| DOC-04 | Should | §7.2 | protocol §3.2(track_changes) | tools/office | unit | M2 | 検証済み |
| DOC-05 | Should | §7.2 | protocol §3.2(docx.diff) | tools/office | unit | M6 | 検証済み |
| DOC-06 | Could | §7.2 | protocol §3.2(docx.comments) | tools/office | unit | M6 | 検証済み |
| DOC-07 | Could | §7.2 | protocol §3.2(docx.images) | tools/office, server/ocr | unit | M6 | 実装中 |
| DOC-08 | Should | §7.2 | protocol §3.5(convert.to_docx) | tools/office | unit | M2 | 検証済み |
| DOC-09 | Should | §7.2 | protocol §3.5(convert.to_pdf) | tools/office | unit | M2 | 検証済み |

## 6.2 Excel（XLS）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| XLS-01 | Must | §7.3 | protocol §3.3(xlsx.info, xlsx.read_range) | tools/office | unit | M2 | 検証済み |
| XLS-02 | Must | §7.3 | protocol §3.3(write_cells, insert/delete) | tools/office | unit, eval:文書品質 | M2 | 検証済み |
| XLS-03 | Must | §7.3 | protocol §3.3(xlsx.create) | tools/office | unit, eval:文書品質 | M2 | 検証済み |
| XLS-04 | Must | §7.3 | protocol §3.3(xlsx.recalc, RecalcResult) | tools/office | unit | M2 | 検証済み |
| XLS-05 | Must | §7.3 | protocol §3.4(data.transform) | tools/office | unit, eval:ツール操作 | M2 | 検証済み |
| XLS-06 | Should | §7.3 | protocol §3.3(xlsx.chart) | tools/office | unit | M6 | 検証済み |
| XLS-07 | Must | §7.3 | protocol §3.3(csv.read, csv.write) | tools/office | unit | M2 | 検証済み |
| XLS-08 | Should | §7.3 | protocol §3.3, §3.5(convert.to_xlsx) | tools/office | unit | M2 | 検証済み |
| XLS-09 | Could | §7.3 | protocol §3.3(has_pivot) | tools/office | unit | M6 | 実装中 |

## 6.3 PDF（PDF）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PDF-01 | Must | §7.4 | protocol §3.5(pdf.info, pdf.read_pages) | tools/office | unit, perf | M2 | 検証済み |
| PDF-02 | Must | §7.4 | protocol §3.5(pdf.extract_tables) | tools/office | unit | M2 | 検証済み |
| PDF-03 | Must | §6.5, §7.4 | protocol §3.5(pdf.ocr), api(/v1/vision/ocr) | tools/office, server/ocr | integ, eval:帳票抽出 | M2/M5 | 実装中 |
| PDF-04 | Must | §7.4 | protocol §3.5(invoice.extract), §3.6, prompts | tools/office, packages/agent-runtime | eval:帳票抽出 | M2/M5 | 検証済み |
| PDF-05 | Should | §7.4 | prompts | packages/agent-runtime | eval:ツール操作 | M6 | 実装済み |
| PDF-06 | Should | §7.4 | protocol §3.5(password_ref), §2.4(PASSWORD_REQUIRED) | tools/office, packages/agent-runtime | unit | M6 | 検証済み |
| PDF-07 | Should | §7.4 | protocol §3.5(page, bbox), ui | tools/office, apps/desktop | unit, e2e | M6 | 実装中 |

## 8.1 性能（PRF）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PRF-01 | NFR | §6.2, §6.3 | api, ops, test | server/deploy, server/gateway | perf | M0/M5 | 実装中 |
| PRF-02 | NFR | §6.2 | ops, test | server/deploy | perf | M0/M5 | 未着手 |
| PRF-03 | NFR | §6.2 | ops, test | server/deploy | perf | M5 | 未着手 |
| PRF-04 | NFR | §5.7, §6.2 | api(/v1/config), ops | server/gateway, server/deploy | perf | M0/M5 | 実装中 |
| PRF-05 | NFR | §7.4 | protocol §3.5, test | tools/office | perf | M2 | 検証済み |
| PRF-06 | NFR | §11 | protocol §3.6, test | packages/agent-runtime, tools/office, server/ocr | perf, eval:帳票抽出 | M5 | 実装中 |

## 8.2 セキュリティ（SEC）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | NFR | §6.6, §10 | ops | server/deploy | sec | M0/M6 | 実装済み |
| SEC-02 | NFR | §10 | api, ops | server/gateway, apps/desktop | sec, integ | M4 | 検証済み |
| SEC-03 | NFR | §5.3 | protocol §2.6, §4 | packages/agent-runtime, packages/fs-tools | unit, sec, eval:安全性 | M3 | 検証済み |
| SEC-04 | NFR | §5.5 | protocol §2.3(UntrustedBlock), prompts | packages/agent-runtime | sec, eval:安全性 | M3 | 検証済み |
| SEC-05 | NFR | §4.4, §10 | api(/v1/audit), data_model | packages/agent-runtime, server/gateway | integ | M4 | 検証済み |
| SEC-06 | NFR | §6.3 | api, data_model | server/gateway | integ, sec | M4 | 検証済み |
| SEC-07 | NFR | §6.4 | api, data_model | server/rag | sec, eval:社内文書検索 | M6 | 検証済み |
| SEC-08 | NFR | §6.3 | api | server/gateway | integ | M6 | 未着手 |

## 8.3 可用性・運用保守（OPS）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-01 | NFR | §6.4, §6.7 | ops | server/deploy, server/rag | integ | M6 | 実装中 |
| OPS-02 | NFR | §2.1, §6.3 | api, ops, ui | server/deploy, server/gateway, apps/desktop | fault | M4 | 実装済み |
| OPS-03 | NFR | §6.7 | api, ops | apps/desktop, server/deploy | fault | M6 | 検証済み |
| OPS-04 | NFR | §6.7 | ops | server/deploy | integ | M6 | 検証済み |
| OPS-05 | NFR | §6.7 | ops | server/deploy | integ | M6 | 実装済み |
| OPS-06 | NFR | §6.7 | ops | （機器・設備） | — | M6 | 未着手 |

## 状態の根拠（2026-10-12 の監査）

実装とテストを読み直して状態を更新した（検証済み 70件、実装済み 6件、実装中 15件、未着手 5件）。「要検証機」は検証機・実モデル・実機が必要なもの。

| ID | 状態 | 根拠（実装 / 試験） | 不足（あれば） |
|---|---|---|---|
| APP-01 | 検証済み | 推論中の出力から読める部分（summary/thought）を逐次表示 / desktop E2E「APP-01」、state.test | なし（2026-10-12 対応） |
| APP-02 | 検証済み | Composer.tsx の onDrop と webUtils / E2E E-03/E-13（添付） | E2E はファイル選択で試験しており、D&D 操作そのものは未試験 |
| APP-03 | 検証済み | App.tsx のファイルツリー / E2E E-01/02（ツリーの内容を確認） | なし |
| APP-04 | 検証済み | App.tsx の step-card（details） / E2E E-01/02 | なし |
| APP-05 | 検証済み | App.tsx の計画パネルと plan_update / E2E E-01/02「計画と承認」 | なし |
| APP-06 | 検証済み | 会話の保存（SQLCipher）と再開、履歴の検索欄 / foundation「conversation search」、desktop E2E「E-01/E-02」 | なし（2026-10-12 対応） |
| APP-07 | 検証済み | run.stop と変更一覧 / IT「RUN-11」、E2E E-07/08 | なし |
| APP-08 | 検証済み | モデル選択（自動/高速/高精度） / E2E「settings」、FI-04（昇格） | なし |
| APP-09 | 検証済み | config.get の macros と「/」候補 / E2E E-03/E-13 | なし |
| APP-10 | 実装済み | App.tsx の queue（順番・目安秒） | 表示の自動試験なし |
| APP-11 | 検証済み | 日本語 UI（lang=ja） / 全 E2E が日本語ラベルで操作 | なし（英語切替は将来） |
| AGT-01 | 検証済み | runtime.ts のループ / IT「I-basic」、I-01 | なし |
| AGT-02 | 検証済み | policy.ts / foundation「policy」、E2E E-04/E-14 | なし |
| AGT-03 | 検証済み | intents.ts のバックアップと undo.run / foundation「WriteCoordinator」、T-35 | なし |
| AGT-04 | 検証済み | stall.ts と昇格 / FI-01（300手）、FI-04、FI-05 | なし |
| AGT-05 | 検証済み | grammar.ts と再生成 / IT「AGT-05/RUN-03」、FI-03 | なし |
| AGT-06 | 検証済み | 続き読み（continuation）と圧縮 / IT「I-07」、FI-02、test_paginate | なし |
| AGT-07 | 検証済み | rag.search の結果を出典として完了報告に表示（読んだ記録と照合） / desktop E2E「AGT-07/NET-05」、SX-06 | 実モデルでの出典の正確さは要検証機 |
| AGT-08 | 検証済み | ツールカタログにシェルなし / foundation「grammar (T-03)」 | なし |
| AGT-09 | 実装中 | router.ts は plugins を受け付ける（mcp.ts） | host.ts が office しか渡さず、追加の設定手段がない。試験なし |
| AGT-10 | 検証済み | /admin/v1/instructions と builder.ts / test_instructions_versions、admin E2E | なし |
| ADM-01 | 検証済み | auth.py（argon2・EdDSA） / test_gw_auth、real-gateway E2E、admin E2E | 社内アカウント連携は未対応（確認事項） |
| ADM-02 | 検証済み | Gateway が部門・利用者の acl_groups を付与 / test_relay_rag_and_ocr、test_acl | 役職単位の制限と NAS 権限の書き出しがない |
| ADM-03 | 実装中 | /admin/v1/dashboard と管理画面 / test_departments_and_dashboard_shape、admin E2E | GPU 負荷は空のまま返している（DCGM 未連携） |
| ADM-04 | 検証済み | users.max_concurrent / test_per_user_and_global_limits | なし |
| ADM-05 | 実装中 | モデル更新は台帳の promote・rollback / test_gw_registry、admin E2E | アプリの自動更新と署名が未実装 |
| ADM-06 | 未着手 | なし | 評価ボタンと同意付きの収集が未実装 |
| RUN-01 | 検証済み | RunStore（手順・意図記録・バックアップの場所） / foundation、IT「RUN-02」 | なし |
| RUN-02 | 検証済み | resumeOpen と FI-13 の修正 / IT「RUN-02」、FI-11、FI-12、FI-13 | なし |
| RUN-03 | 検証済み | retry.ts（無制限の backoff）と retry 表示 / IT「AGT-05/RUN-03」、FI-06、FI-08、FI-15 | なし |
| RUN-04 | 検証済み | grammar.ts と vLLM の guided / test_vllm_guided_modes、grammar 試験 | 実モデルでの確認は要検証機 |
| RUN-05 | 検証済み | stall.ts / IT「RUN-05/R3」、FI-04、FI-05 | なし |
| RUN-06 | 検証済み | batch.ts / FI-18、IT「I-01」 | なし |
| RUN-07 | 検証済み | Runtime を別プロセスで常駐、powerSaveBlocker / E2E E-07/08 | スリープ抑止は実機で未試験（FI-14、要試験機） |
| RUN-08 | 検証済み | 承認の後回しと notify / IT「I-08」、FI-19 | Windows 通知の表示は E2E で無効にしており未試験 |
| RUN-09 | 検証済み | 永続キュー（jobs.py） / test_restart_requeues、FI-07 | PostgreSQL 版の試験はスキップ（Docker なし） |
| RUN-10 | 検証済み | スケジューラの手順単位ラウンドロビン / test_round_robin、test_interactive_first | なし |
| RUN-11 | 検証済み | run.pause・resume・stop / IT「RUN-11」 | 一時停止・再開の自動試験なし |
| NET-01 | 検証済み | 設定・ヘッダーの3段階（既定はオフ） / foundation「denies net tools」、SX-02 | 画面での切替は E2E 未試験 |
| NET-02 | 検証済み | net/policies と全社停止 / test_net_policies_sites_and_org_stop、admin E2E | なし |
| NET-03 | 検証済み | ネットの許可中は承認なしで検索・閲覧 / FI-20、IT「I-09」 | なし |
| NET-04 | 検証済み | net.activity と「ネット接続中」表示 / IT「I-09」（検索語を記録） | 「ネット接続中」の表示は E2E 未試験 |
| NET-05 | 検証済み | 完了報告の citations（web）を表示し、読んでいない URL は「確認できない出典」 / desktop E2E「AGT-07/NET-05」 | 実モデルでの出典の正確さは要検証機 |
| NET-06 | 検証済み | fetcher は GET のみ / test_only_get_and_no_credentials、SX-05 | なし |
| NET-07 | 検証済み | dlp.py（Aho-Corasick・正規化） / test_wr_dlp、test_dlp_blocked_query | なし |
| NET-08 | 検証済み | netguard.py と sites / test_wr_ssrf、test_results_trusted_first | なし |
| NET-09 | 検証済み | extract.py、max_chars、untrusted / test_max_chars、test_html_extraction、SX-04 | なし |
| NET-10 | 検証済み | 会話の tainted 承認 / IT「I-09/R10」 | なし |
| NET-11 | 検証済み | Relay の監査（メタ情報のみ） / test_audit_records_only_metadata | なし |
| NET-12 | 実装済み | compose（internal・egress の網）と firewall.sh、SSRF 対策 / test_wr_ssrf | 実機での遮断は未検証（要検証機） |
| NET-13 | 検証済み | usage.py の月次上限 / test_quota_exceeded_and_month_rollover、test_relay_search_quota | なし |
| DOC-01 | 検証済み | docx/model.py（見出し・表・ヘッダー/フッター） / test_read_blocks、test_outline | なし |
| DOC-02 | 検証済み | docx.create / test_create_with_template | なし |
| DOC-03 | 検証済み | docx.edit / test_replace_in_block_keeps_other_xml、test_table_ops、FI-17 | なし |
| DOC-04 | 検証済み | 変更履歴の出力 / test_track_changes | なし |
| DOC-05 | 検証済み | docx.diff / test_diff | なし |
| DOC-06 | 検証済み | docx.comments / test_comments | なし |
| DOC-07 | 実装中 | docx.images / test_images_extract_and_describe | Runtime の sampling が画像の説明に未対応 |
| DOC-08 | 検証済み | convert.to_docx（LibreOffice） / test_convert_pipeline_with_fake_soffice | 本物の LibreOffice での試験はスキップ（未導入） |
| DOC-09 | 検証済み | convert.to_pdf / test_convert_pipeline_with_fake_soffice | 本物の LibreOffice での試験はスキップ |
| XLS-01 | 検証済み | xlsx.info・read_range（結合セル・続き読み） / test_info、test_read_range_continuation | なし |
| XLS-02 | 検証済み | write_cells と行・列の挿入・削除 / test_write_cells_preserves_formatting、test_insert_and_delete_rows | なし |
| XLS-03 | 検証済み | xlsx.create / test_create | なし |
| XLS-04 | 検証済み | recalc.py とエラー値の検知 / test_recalc_with_fake_engine | 本物の LibreOffice での試験はスキップ |
| XLS-05 | 検証済み | data.transform（Decimal） / test_transform_*、test_xlsx_100k_rows | なし |
| XLS-06 | 検証済み | xlsx.chart / test_chart | なし |
| XLS-07 | 検証済み | csv.read・write（文字コードの自動判定） / test_csv_read_shift_jis、test_detect_encoding | なし |
| XLS-08 | 検証済み | VBA を保持、.xls を変換 / test_xlsm_keeps_vba、偽 soffice の変換試験 | .xls の実変換は LibreOffice 待ち |
| XLS-09 | 実装中 | xlsx.info の has_pivot（検出のみ） | ピボットの内容の読み取りと試験がない |
| PDF-01 | 検証済み | pdf.read_pages / test_read_pages_two_columns_reading_order | なし |
| PDF-02 | 検証済み | pdf.extract_tables と write_table / test_extract_tables、T-35 | なし |
| PDF-03 | 実装中 | pdf.ocr、server/ocr（PaddleOCR・VLM） / test_ocr、test_ocr_uses_sampling、SX-07 | エンジンの比較・採用判断と実 OCR 精度（要検証機） |
| PDF-04 | 検証済み | invoice.extract / test_invoice、IT「I-01」、FI-18、T-35 | 実モデルでの精度は要検証機 |
| PDF-05 | 実装済み | 汎用ツールとモデルで対応（専用ツールなし） | 英文契約書の評価セットなし。要検証機 |
| PDF-06 | 検証済み | 秘密入力の質問と _meta で渡すパスワード / test_password_protected | なし |
| PDF-07 | 実装中 | 抽出結果にページ番号 / test_read_pages_untrusted | 画面で該当箇所を表示する機能がない |
| PRF-01 | 実装中 | 全体の同時実行上限の設定 / test_per_user_and_global_limits | 5件同時の性能測定（要検証機） |
| PRF-02 | 未着手 | なし | 最初の文字までの時間の測定（要検証機） |
| PRF-03 | 未着手 | なし | 生成速度の測定（要検証機） |
| PRF-04 | 実装中 | context_budget、413、圧縮 / test_context_too_large_413、FI-02 | 32K/64K の実測（要検証機） |
| PRF-05 | 検証済み | pdf.read_pages / test_prf05_text_pdf_100_pages_within_60s | 本番機での再測定 |
| PRF-06 | 実装中 | 請求書の抽出→転記の流れ / T-35、I-01（モック） | 実モデルで 10 枚 5 分以内の測定（要検証機） |
| SEC-01 | 実装済み | compose の網分離と firewall.sh | 実機での通信制限の検証（要検証機） |
| SEC-02 | 検証済み | TLS の設定（cli.py）と利用者ごとの JWT / test_gw_auth | TLS 接続の自動試験なし |
| SEC-03 | 検証済み | policy.ts・paths.ts / policy-px（PX-01〜10）、foundation「policy」 | なし |
| SEC-04 | 検証済み | untrusted で包む、削除は常に承認 / SX-01〜07、test_untrusted_close_tag | 実モデルでの SX は要検証機 |
| SEC-05 | 検証済み | /v1/audit と audit.py / test_audit_dedupe_by_id、I-01、admin E2E | 保存期間は確認事項 |
| SEC-06 | 検証済み | 暗号化キューと10分削除 / test_no_plaintext_in_db_and_logs、test_results_deleted_ten_minutes | 同意分の学習データ収集（ADM-06）が未実装 |
| SEC-07 | 検証済み | rag の acl.py（ACL はサーバー側で付与） / test_acl、test_relay_rag_and_ocr | なし |
| SEC-08 | 未着手 | なし（将来の機能） | クラウド AI への送信制御は未実装 |
| OPS-01 | 実装中 | 夜間のバックアップ timer。RAG の取り込みは timer 前提（compose のコメント） | 24時間運用は要否確認。取り込みの timer 定義がない |
| OPS-02 | 実装済み | compose の restart、エンジン異常時の再投入、再接続の表示 / test_requeued_when_engine_crashes、FI-06 | ハング時に再起動しない（healthcheck と連動していない）。要検証機 |
| OPS-03 | 検証済み | client.ts の予備機への切替・切り戻し / failover.test.ts、FI シナリオ OPS-03 | 予備機への復元訓練（要検証機） |
| OPS-04 | 検証済み | backup.sh・restore.sh・timer / test_deploy_backup（5件） | 復元訓練は検証機で |
| OPS-05 | 実装済み | alerts.yml と alertmanager（GPU 温度・待ち件数） | promtool での式の検証と通知の試験なし（要検証機） |
| OPS-06 | 未着手 | なし（ハードウェアの推奨事項） | UPS の導入と接続（要検証機） |
