# projects/docs — 設計ドキュメント置き場

設計ドキュメントは**すべてこのフォルダ**に置く（アプリのフォルダには置かない）。

## ローカルLLMデスクトップエージェント（`projects/local-llm-agent/`）

読む順番の目安: 要件書 → 基本設計書 → 開発ガイド → 実装計画 → 担当部分の詳細仕様。

| 文書 | 版 | 内容 |
| --- | --- | --- |
| [local_llm.md](local_llm.md) | v0.3 | 要件定義書 |
| [local_llm_design.md](local_llm_design.md) | v0.2 | 基本設計書（構成・方針・レビュー指摘 R1〜R12） |
| [local_llm_dev_guide.md](local_llm_dev_guide.md) | v1.0 | 開発環境・コーディング規約・作業の進め方 |
| [local_llm_impl_plan.md](local_llm_impl_plan.md) | v1.0 | 実装計画（タスクの分解と着手順） |
| [local_llm_protocol.md](local_llm_protocol.md) | v1.0-draft | アクション・ツール定義・エラーの分類・Runtime のメッセージ |
| [local_llm_data_model.md](local_llm_data_model.md) | v1.0-draft | SQLite（Run Store）・PostgreSQL・Qdrant |
| [local_llm_api.md](local_llm_api.md) | v1.0-draft | Gateway の HTTP API |
| [local_llm_ui_spec.md](local_llm_ui_spec.md) | v1.0-draft | 画面・状態の表示・文言 |
| [local_llm_prompts.md](local_llm_prompts.md) | v1.0-draft | プロンプトの雛形 |
| [local_llm_test_spec.md](local_llm_test_spec.md) | v1.0-draft | 試験仕様（モック Gateway・評価セット・障害注入・セキュリティ・性能） |
| [local_llm_traceability.md](local_llm_traceability.md) | — | 要件ID → 設計・仕様・実装・試験の対応表 |
| [local_llm_adr/](local_llm_adr/README.md) | — | 設計判断の記録（ADR 001〜008） |
| [local_llm_ops/server_setup.md](local_llm_ops/server_setup.md) | v1.0-draft | 推論サーバーの構築手順（Ubuntu 主・WSL2 付録） |

- `-draft` の文書は、実装中に見つかった問題を反映して更新する。仕様と違う実装が必要になったら、先に仕様を直す（`local_llm_dev_guide.md` §4）。
- 実装上の正本は、スキーマ（`packages/protocol`）・マイグレーション・OpenAPI。文書とずれたら同じコミットで両方を直す。
