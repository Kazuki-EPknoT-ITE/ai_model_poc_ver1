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
| APP-01 | Must | §4.1, §6.3 | protocol §5.2(token), api, ui | apps/desktop, packages/agent-runtime, server/gateway | e2e, integ | M4 | 未着手 |
| APP-02 | Must | §4.1 | protocol §5.1(run.start), ui | apps/desktop | e2e | M4 | 未着手 |
| APP-03 | Must | §4.1 | protocol §3.1, ui, data_model | apps/desktop, packages/fs-tools | e2e | M4 | 未着手 |
| APP-04 | Must | §4.1 | protocol §5.2(step), ui | apps/desktop | e2e | M4 | 未着手 |
| APP-05 | Should | §4.1 | protocol §1(PlanUpdate), §5.2(plan), ui | apps/desktop | e2e | M4 | 未着手 |
| APP-06 | Must | §4.1, §4.4 | protocol §5.1(conversation.*), data_model, ui | apps/desktop, packages/agent-runtime | unit, e2e | M4 | 未着手 |
| APP-07 | Must | §4.1, §5.4 | protocol §5.1(run.stop), ui | apps/desktop, packages/agent-runtime | e2e, integ | M4 | 未着手 |
| APP-08 | Should | §4.1, §6.3 | protocol §5.1(run.start.model), api, ui | apps/desktop, server/gateway | e2e, integ | M4 | 未着手 |
| APP-09 | Should | §4.1, §9 | ui, prompts | apps/desktop, profiles/_template | e2e | M6 | 未着手 |
| APP-10 | Should | §4.1, §6.3 | protocol §5.2(queue), api, ui | apps/desktop, server/gateway | integ, e2e | M4 | 未着手 |
| APP-11 | Must | §4.1 | ui | apps/desktop | e2e | M4 | 未着手 |

## 5.2 エージェント（AGT）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AGT-01 | Must | §5.1, §5.2 | protocol §1, prompts, data_model | packages/agent-runtime | unit, eval:ツール操作 | M3 | 未着手 |
| AGT-02 | Must | §5.3 | protocol §2.2, §4, data_model | packages/agent-runtime | unit, sec, eval:安全性 | M3 | 未着手 |
| AGT-03 | Must | §5.4 | protocol §2.3(Artifact), §5.1(undo.run), data_model, ui | packages/agent-runtime, apps/desktop | unit, e2e | M3 | 未着手 |
| AGT-04 | Must | §5.1, §5.6 | protocol §1.1, prompts, data_model | packages/agent-runtime | unit, eval:長時間作業 | M3 | 未着手 |
| AGT-05 | Must | §5.2 | protocol §1.2, §1.3, §2.4 | packages/agent-runtime, server/gateway | unit, eval:ツール操作 | M3 | 未着手 |
| AGT-06 | Must | §5.7 | protocol §2.5, §3.7, prompts | packages/agent-runtime | unit, eval:ツール操作 | M3 | 未着手 |
| AGT-07 | Should | §6.4 | protocol §3.8(rag.search), api, prompts | server/rag, packages/agent-runtime | integ, eval:社内文書検索 | M6 | 未着手 |
| AGT-08 | Must | §5.3 | protocol §2.1 | packages/agent-runtime | unit, sec | M3 | 未着手 |
| AGT-09 | Should | §5.2, §7.1 | protocol §2.1, §2.2 | packages/agent-runtime | integ | M6 | 未着手 |
| AGT-10 | Should | §6.3, §9 | api(/v1/config), prompts | server/gateway, profiles/_template | integ | M6 | 未着手 |

## 5.3 利用者・管理（ADM）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADM-01 | Must | §6.3, §10 | api, data_model, ui | server/gateway, apps/desktop | integ, sec, e2e | M4 | 未着手 |
| ADM-02 | Should | §6.4, §10 | api, data_model, ui | server/gateway, server/rag, apps/admin-web | integ, sec | M6 | 未着手 |
| ADM-03 | Should | §6.7 | api, ui, ops | apps/admin-web, server/deploy | e2e | M6 | 未着手 |
| ADM-04 | Should | §6.3 | api, data_model | server/gateway | integ | M4 | 未着手 |
| ADM-05 | Should | §4.3 | ops | apps/desktop | integ | M6 | 未着手 |
| ADM-06 | Should | §8.3 | api, data_model, ui | server/gateway, apps/desktop, ml/datagen | integ | M6 | 未着手 |

## 5.4 継続実行（RUN）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RUN-01 | Must | §5.1, §4.4 | data_model | packages/agent-runtime | unit, fault | M3 | 未着手 |
| RUN-02 | Must | §5.4 | protocol §2.3(Artifact), §2.7, data_model | packages/agent-runtime, tools/office | unit, fault, eval:継続実行 | M3 | 未着手 |
| RUN-03 | Must | §5.9 | protocol §2.4, §5.2(retry), api | packages/agent-runtime | unit, fault | M3 | 未着手 |
| RUN-04 | Must | §5.2, §6.2 | protocol §1, §1.3, prompts | server/gateway, server/deploy, packages/protocol | integ, eval:ツール操作 | M0/M3 | 未着手 |
| RUN-05 | Must | §5.6 | prompts, data_model | packages/agent-runtime, server/gateway | unit, eval:長時間作業 | M3 | 未着手 |
| RUN-06 | Must | §5.8 | protocol §3.6, data_model | packages/agent-runtime, tools/office | unit, eval:長時間作業 | M3 | 未着手 |
| RUN-07 | Must | §4.3 | ui | apps/desktop | e2e, fault | M4 | 未着手 |
| RUN-08 | Must | §5.3, §4.3 | protocol §4, §5.2(approval.request, notify), ui | packages/agent-runtime, apps/desktop | unit, e2e | M3/M4 | 未着手 |
| RUN-09 | Must | §6.3 | api, data_model | server/gateway | integ, fault | M4 | 未着手 |
| RUN-10 | Should | §6.3 | api, data_model | server/gateway | integ, perf | M4 | 未着手 |
| RUN-11 | Must | §5.1 | protocol §5.1(run.pause/resume/stop), ui | packages/agent-runtime, apps/desktop | unit, e2e | M3/M4 | 未着手 |

## 5.5 ネットアクセス（NET）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NET-01 | Must | §4.1, §6.6 | protocol §5.1(settings.update), ui | apps/desktop, server/gateway | e2e, sec | M6 | 未着手 |
| NET-02 | Must | §6.6 | api, data_model, ui | server/gateway, apps/admin-web | integ, sec | M6 | 未着手 |
| NET-03 | Must | §5.3 | protocol §3.8, §1.3 | packages/agent-runtime | unit | M6 | 未着手 |
| NET-04 | Must | §4.1 | protocol §5.2(net.activity), ui | apps/desktop | e2e | M6 | 未着手 |
| NET-05 | Must | §6.6 | protocol §1(Final.citations), prompts | packages/agent-runtime | eval:ネットアクセス | M6 | 未着手 |
| NET-06 | Must | §6.6 | api | server/web-relay | sec | M6 | 未着手 |
| NET-07 | Must | §6.6 | protocol §2.4(DLP_BLOCKED), api | server/web-relay | sec, eval:ネットアクセス | M6 | 未着手 |
| NET-08 | Must | §6.6 | protocol §2.4(DOMAIN_BLOCKED), api, data_model | server/web-relay | sec | M6 | 未着手 |
| NET-09 | Must | §5.5, §6.6 | protocol §2.3(UntrustedBlock), prompts | server/web-relay, packages/agent-runtime | sec, eval:ネットアクセス | M6 | 未着手 |
| NET-10 | Must | §5.5 | protocol §4(tainted), data_model | packages/agent-runtime | unit, sec | M6 | 未着手 |
| NET-11 | Must | §6.6 | api, data_model | server/web-relay, server/gateway | integ | M6 | 未着手 |
| NET-12 | Must | §6.6 | ops | server/deploy | sec | M0/M6 | 未着手 |
| NET-13 | Should | §6.6 | protocol §2.4(QUOTA_EXCEEDED), api, data_model | server/web-relay, server/gateway | integ | M6 | 未着手 |

## 6.1 Word（DOC）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DOC-01 | Must | §7.2 | protocol §3.2(docx.read, docx.outline) | tools/office | unit, eval:文書品質 | M2 | 未着手 |
| DOC-02 | Must | §7.2 | protocol §3.2(docx.create) | tools/office | unit, eval:文書品質 | M2 | 未着手 |
| DOC-03 | Must | §7.2 | protocol §3.2(docx.edit) | tools/office | unit, eval:文書品質 | M2 | 未着手 |
| DOC-04 | Should | §7.2 | protocol §3.2(track_changes) | tools/office | unit | M2 | 未着手 |
| DOC-05 | Should | §7.2 | protocol §3.2(docx.diff) | tools/office | unit | M6 | 未着手 |
| DOC-06 | Could | §7.2 | protocol §3.2(docx.comments) | tools/office | unit | M6 | 未着手 |
| DOC-07 | Could | §7.2 | protocol §3.2(docx.images) | tools/office, server/ocr | unit | M6 | 未着手 |
| DOC-08 | Should | §7.2 | protocol §3.5(convert.to_docx) | tools/office | unit | M2 | 未着手 |
| DOC-09 | Should | §7.2 | protocol §3.5(convert.to_pdf) | tools/office | unit | M2 | 未着手 |

## 6.2 Excel（XLS）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| XLS-01 | Must | §7.3 | protocol §3.3(xlsx.info, xlsx.read_range) | tools/office | unit | M2 | 未着手 |
| XLS-02 | Must | §7.3 | protocol §3.3(write_cells, insert/delete) | tools/office | unit, eval:文書品質 | M2 | 未着手 |
| XLS-03 | Must | §7.3 | protocol §3.3(xlsx.create) | tools/office | unit, eval:文書品質 | M2 | 未着手 |
| XLS-04 | Must | §7.3 | protocol §3.3(xlsx.recalc, RecalcResult) | tools/office | unit | M2 | 未着手 |
| XLS-05 | Must | §7.3 | protocol §3.4(data.transform) | tools/office | unit, eval:ツール操作 | M2 | 未着手 |
| XLS-06 | Should | §7.3 | protocol §3.3(xlsx.chart) | tools/office | unit | M6 | 未着手 |
| XLS-07 | Must | §7.3 | protocol §3.3(csv.read, csv.write) | tools/office | unit | M2 | 未着手 |
| XLS-08 | Should | §7.3 | protocol §3.3, §3.5(convert.to_xlsx) | tools/office | unit | M2 | 未着手 |
| XLS-09 | Could | §7.3 | protocol §3.3(has_pivot) | tools/office | unit | M6 | 未着手 |

## 6.3 PDF（PDF）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PDF-01 | Must | §7.4 | protocol §3.5(pdf.info, pdf.read_pages) | tools/office | unit, perf | M2 | 未着手 |
| PDF-02 | Must | §7.4 | protocol §3.5(pdf.extract_tables) | tools/office | unit | M2 | 未着手 |
| PDF-03 | Must | §6.5, §7.4 | protocol §3.5(pdf.ocr), api(/v1/vision/ocr) | tools/office, server/ocr | integ, eval:帳票抽出 | M2/M5 | 未着手 |
| PDF-04 | Must | §7.4 | protocol §3.5(invoice.extract), §3.6, prompts | tools/office, packages/agent-runtime | eval:帳票抽出 | M2/M5 | 未着手 |
| PDF-05 | Should | §7.4 | prompts | packages/agent-runtime | eval:ツール操作 | M6 | 未着手 |
| PDF-06 | Should | §7.4 | protocol §3.5(password_ref), §2.4(PASSWORD_REQUIRED) | tools/office, packages/agent-runtime | unit | M6 | 未着手 |
| PDF-07 | Should | §7.4 | protocol §3.5(page, bbox), ui | tools/office, apps/desktop | unit, e2e | M6 | 未着手 |

## 8.1 性能（PRF）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PRF-01 | NFR | §6.2, §6.3 | api, ops, test | server/deploy, server/gateway | perf | M0/M5 | 未着手 |
| PRF-02 | NFR | §6.2 | ops, test | server/deploy | perf | M0/M5 | 未着手 |
| PRF-03 | NFR | §6.2 | ops, test | server/deploy | perf | M5 | 未着手 |
| PRF-04 | NFR | §5.7, §6.2 | api(/v1/config), ops | server/gateway, server/deploy | perf | M0/M5 | 未着手 |
| PRF-05 | NFR | §7.4 | protocol §3.5, test | tools/office | perf | M2 | 未着手 |
| PRF-06 | NFR | §11 | protocol §3.6, test | packages/agent-runtime, tools/office, server/ocr | perf, eval:帳票抽出 | M5 | 未着手 |

## 8.2 セキュリティ（SEC）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | NFR | §6.6, §10 | ops | server/deploy | sec | M0/M6 | 未着手 |
| SEC-02 | NFR | §10 | api, ops | server/gateway, apps/desktop | sec, integ | M4 | 未着手 |
| SEC-03 | NFR | §5.3 | protocol §2.6, §4 | packages/agent-runtime, packages/fs-tools | unit, sec, eval:安全性 | M3 | 未着手 |
| SEC-04 | NFR | §5.5 | protocol §2.3(UntrustedBlock), prompts | packages/agent-runtime | sec, eval:安全性 | M3 | 未着手 |
| SEC-05 | NFR | §4.4, §10 | api(/v1/audit), data_model | packages/agent-runtime, server/gateway | integ | M4 | 未着手 |
| SEC-06 | NFR | §6.3 | api, data_model | server/gateway | integ, sec | M4 | 未着手 |
| SEC-07 | NFR | §6.4 | api, data_model | server/rag | sec, eval:社内文書検索 | M6 | 未着手 |
| SEC-08 | NFR | §6.3 | api | server/gateway | integ | M6 | 未着手 |

## 8.3 可用性・運用保守（OPS）

| 要件ID | 優先度 | 設計書の節 | 詳細仕様 | 実装モジュール | テスト | M | 状態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-01 | NFR | §6.4, §6.7 | ops | server/deploy, server/rag | integ | M6 | 未着手 |
| OPS-02 | NFR | §2.1, §6.3 | api, ops, ui | server/deploy, server/gateway, apps/desktop | fault | M4 | 未着手 |
| OPS-03 | NFR | §6.7 | api, ops | apps/desktop, server/deploy | fault | M6 | 未着手 |
| OPS-04 | NFR | §6.7 | ops | server/deploy | integ | M6 | 未着手 |
| OPS-05 | NFR | §6.7 | ops | server/deploy | integ | M6 | 未着手 |
| OPS-06 | NFR | §6.7 | ops | （機器・設備） | — | M6 | 未着手 |
