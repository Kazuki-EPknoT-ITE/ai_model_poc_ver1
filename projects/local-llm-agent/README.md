# local-llm-agent

平井海運向けローカルLLMデスクトップエージェントの実装（モノレポ）。
設計ドキュメントは **`projects/docs/`**（目次: `projects/docs/README.md`）。開発の前提は
`projects/docs/local_llm_dev_guide.md`、作業の順番は `projects/docs/local_llm_impl_plan.md`。

## 構成（現状）

| パス | 内容 | 状態 |
| --- | --- | --- |
| `packages/protocol/` | プロトコルの正本（`schemas/src/*.yaml`）と生成物（JSON Schema・ツール一覧・TS 型・pydantic） | T-02 完了 |
| `packages/protocol/python/` | Python から使う `lla_protocol`（検証・ツール一覧・モデル） | T-02 完了 |
| `eval/mock-gateway/` | シナリオ駆動のモック Gateway（`local_llm_test_spec.md` §2） | T-05 完了 |
| `e2e/` | Playwright の E2E（現在はモック Gateway。M4 でデスクトップアプリに拡張） | — |

## よく使うコマンド

```powershell
pnpm install; uv sync --all-packages --all-groups   # 依存の導入
pnpm protocol:gen        # schemas/src/*.yaml を変えたら必ず実行（生成物もコミットする）
pnpm check               # lint・型・生成物の差分・TS/Python のテストをまとめて実行
pnpm mock:gateway        # モック Gateway を http://127.0.0.1:8787 で起動（smoke シナリオ）
pnpm test:e2e            # Playwright の E2E（モック Gateway を自動で起動する）
```

`schemas/gen/`・`src/generated/`・`python/src/lla_protocol/{data,generated}/` は生成物なので、手で編集しない。
