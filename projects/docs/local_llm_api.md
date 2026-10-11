# ローカルLLMデスクトップエージェント サーバーAPI仕様

2026年10月11日 · v1.0-draft（基本設計書 §6.3〜6.6 の詳細化）

Gateway（FastAPI）が提供する HTTP API を定める。OpenAPI 3.1 の定義は FastAPI から自動生成し、`server/gateway/openapi.json` としてコミットする。クライアントの型（`packages/protocol`）は OpenAPI から生成する。本書はその設計仕様にあたる。

---

## 1. 共通

| 項目 | 内容 |
| --- | --- |
| ベースURL | `https://<server>:8443`（社内CAで発行した証明書。ポートは設定で変更可） |
| 認証 | `Authorization: Bearer <access_token>`（JWT、EdDSA（Ed25519）署名。秘密鍵はサーバーの秘密情報に置く。有効期限15分。ADR-008） |
| 形式 | リクエスト・レスポンスとも JSON（UTF-8）。SSE は `text/event-stream` |
| バージョン | パスに `/v1`。プロトコル版は `/v1/config` で通知する |
| トレース | すべてのレスポンスに `X-Request-Id` を付ける。クライアントから送られてきた場合はその値を引き継ぐ |
| 時刻 | ISO 8601（UTC） |

### 1.1 エラー形式

```json
{ "error": { "code": "QUEUE_LIMIT", "message": "同時実行数の上限に達しています", "retryable": true, "retry_after_ms": 3000 } }
```

| HTTP | code | retryable | 説明 |
| --- | --- | --- | --- |
| 400 | `INVALID_REQUEST` | × | スキーマ違反 |
| 401 | `UNAUTHENTICATED` / `TOKEN_EXPIRED` | ×（refresh する） | |
| 403 | `FORBIDDEN` / `NET_DISABLED` / `DLP_BLOCKED` / `DOMAIN_BLOCKED` | × | 権限・ポリシー |
| 404 | `NOT_FOUND` | × | ジョブがない（結果の保持期限切れを含む） |
| 409 | `IDEMPOTENCY_CONFLICT` | × | 同じキーで中身の異なる依頼が来た |
| 413 | `CONTEXT_TOO_LARGE` | × | 文脈予算の超過（クライアントは圧縮してから再投入する） |
| 426 | `CLIENT_TOO_OLD` | × | プロトコルのメジャー版が合わない |
| 429 | `QUEUE_LIMIT` / `QUOTA_EXCEEDED` | ○ / × | 同時実行数の上限 / 検索回数の上限 |
| 503 | `ENGINE_UNAVAILABLE` / `MAINTENANCE` | ○ | 推論エンジンの停止・再起動中 |

クライアントは `retryable: true` と 5xx・接続断を transient として扱い、無制限に再試行する（protocol §2.4）。

---

## 2. 認証（ADR-008）

| メソッド | パス | リクエスト | レスポンス |
| --- | --- | --- | --- |
| POST | `/v1/auth/login` | `{ login, password, device_name }` | `{ access_token, refresh_token, expires_in: 900, user: { id, display_name, role, department } }` |
| POST | `/v1/auth/refresh` | `{ refresh_token }` | 同上。refresh_token はローテーションする（使った古いトークンは失効させる） |
| POST | `/v1/auth/logout` | `{ refresh_token }` | 204 |
| POST | `/v1/auth/password` | `{ current, new }` | 204 |

- パスワードは argon2id（m=64MiB, t=3, p=1）でハッシュ化する。5回失敗したら15分ロックする。
- 失効済みの refresh_token が使われた場合は、トークンの盗用とみなし、そのセッション系列をすべて失効させる。
- 将来の OIDC（Entra ID）は `/v1/auth/oidc/start` と `/v1/auth/oidc/callback` を追加する（ADR-008）。

---

## 3. 設定

### GET `/v1/config`

```json
{
  "protocol_version": "1.0",
  "server_version": "0.1.0",
  "org": { "id": "hirai", "name": "平井海運" },
  "models": [
    { "id": "local-12b", "label": "高速", "available": true,  "context_budget": 32768 },
    { "id": "local-26b", "label": "高精度", "available": false, "context_budget": 65536 }
  ],
  "escalation_target": null,                 // 昇格先がない検証機では null（R3）
  "net": { "level_cap": "search", "monthly_search_limit": 300, "used_this_month": 12 },
  "instructions": { "version": 4, "body": "..." },      // AGT-10
  "invoice_schemas": ["invoice.v1"],
  "limits": { "max_concurrent": 2, "max_tool_result_tokens": 6000 },
  "features": { "rag": false, "ocr": true, "web": true }
}
```

クライアントは起動時と、15分ごとにこれを取得する。

---

## 4. 推論ジョブ

### POST `/v1/steps`
ヘッダー `Idempotency-Key: <run_id>:<step_id>:<attempt>`（必須）

```json
{
  "model": "local-12b",                  // auto はクライアント側で解決してから送る
  "priority_hint": "interactive",        // interactive | normal | low
  "messages": [ { "role": "system", "content": "..." }, { "role": "user", "content": "..." } ],
  "response_format": { "type": "json_schema", "schema": { ... Action oneOf ... } },
  "max_tokens": 2048,
  "temperature": 0.2,
  "stream": true,
  "purpose": "agent"                     // agent | invoice_extract | compact（集計・モックの照合用。既定 agent）
}
```

レスポンス `202 Accepted`:
```json
{ "job_id": "01J...", "state": "queued", "position": 3, "eta_seconds": 12 }
```

- 同じ `Idempotency-Key` で同じ中身（本文の sha256 で比較）なら、既存のジョブを返す（200）。中身が違えば 409。
- `messages` の合計トークン数が、そのモデルの `context_budget` を超えたら 413。
- 構造化出力（`response_format`）は vLLM の guided decoding に渡す。Gateway はスキーマのサイズ（64KB まで）だけを検証する。

### GET `/v1/steps/{job_id}/events`（SSE）
ヘッダー `Last-Event-ID: <seq>`（省略可。指定するとその次から送る）

```
id: 1
event: queue
data: {"position":3,"eta_seconds":12}

id: 2
event: started
data: {"model":"local-12b"}

id: 3
event: token
data: {"delta":"{\"type\":\"tool_call\","}

id: 57
event: done
data: {"output":"{...Action JSON...}","usage":{"input_tokens":3120,"output_tokens":180},"finish_reason":"stop"}

event: error
data: {"code":"ENGINE_UNAVAILABLE","retryable":true}
```

- 15秒ごとに `: ping` のコメントを送る。
- 接続が切れた場合、クライアントは `Last-Event-ID` を付けて再接続する。完了から10分を過ぎて結果が消えていたら 404 になるので、クライアントは attempt を1増やして投入し直す。
- エンジンの再起動などで `running` のジョブが `queued` に戻ったら、`event: requeued` を送り、トークンは最初から送り直す（クライアントは表示中の逐次テキストを捨てる）。

### GET `/v1/steps/{job_id}`
SSE を使わない場合のポーリング用。`{ state, position?, output?, usage?, error? }`

### DELETE `/v1/steps/{job_id}`
取り消すと、SSE には `event: error` / `data: {"code":"CANCELLED","retryable":false}` を送って閉じる。
取り消し（RUN-11 の停止）。実行中ならエンジンに中止を伝える。204。

### スケジューリング（RUN-10）
- 利用者ごとのキューを `priority` 順に並べ、利用者間ではラウンドロビンで1ジョブずつ取り出す。
- 同時に実行するのは最大 `max_num_seqs`（本番機では5）ジョブ。利用者ごとの上限は `users.max_concurrent`。
- `priority_hint=interactive`（入力4Kトークン以下、または依頼の初手）は優先する。`low`（停滞中）は、ほかに待ちがないときだけ実行する。
- `eta_seconds` は、直近50ジョブの平均処理時間 × 前に並んでいる件数 ÷ 並列数 で求める。

---

## 5. 画像読取（OCR。R8, ADR-006）

### POST `/v1/vision/ocr`
`multipart/form-data`: `image`（PNG、1ページ、最大20MB）、`engine`（`auto|ocr|vlm`）、`lang`（`ja`）

```json
{ "text": "...", "lines": [{ "text": "請求書", "bbox": [x0,y0,x1,y1], "confidence": 0.98 }], "engine_used": "ocr", "confidence": 0.93 }
```

画像はメモリ上で処理し、ディスクには書かない。監査ログに残すのはサイズと所要時間だけ。

---

## 6. 社内文書検索（AGT-07）

### POST `/v1/rag/search`
```json
{ "query": "傭船料の支払期日", "top_k": 5, "filters": { "path_prefix": "\\\\nas\\契約" } }
```
```json
{ "hits": [ { "ref": "r1", "path": "...", "location": "p.12 / 第5条", "score": 0.82, "excerpt": "..." } ] }
```
利用者の `acl_groups`（部門＋個人）を、検索条件としてサーバー側で必ず付ける（SEC-07）。

---

## 7. Web アクセス（5.5, Web Relay）

### POST `/v1/web/search`
```json
{ "query": "国際海事機関 2026年 規則改正", "max_results": 5, "site": "imo.org" }
```
```json
{ "results": [ { "rank": 1, "title": "...", "url": "https://...", "snippet": "..." } ], "remaining_quota": 287 }
```

### POST `/v1/web/fetch`
```json
{ "url": "https://www.mlit.go.jp/...", "max_chars": 8000 }
```
```json
{ "url": "...", "final_url": "...", "title": "...", "content_type": "text/html", "text": "...", "chars": 7999, "truncated": true }
```

- 判定の順序: 認証 → 利用者の設定・部門・全社の上限（いちばん小さいもの。NET-02）→ 禁止サイト・社内アドレス（NET-08）→ DLP（NET-07）→ 実行 → 監査ログ（NET-11）。
- クライアントは自分のネット設定を `X-Net-Level: off|search|browse` で送る。サーバーは上限との小さいほうを採用する。クライアントがオフにしていれば、サーバー側でも拒否する（二重の確認）。
- DLP で検知した場合は `403 DLP_BLOCKED`。どの語に該当したかは返さない（辞書が漏れるのを防ぐため）。

---

## 8. 監査ログの受け取り（SEC-05）

### POST `/v1/audit`
```json
{ "records": [ { "id": "01J...", "at": "...", "action": "file.write", "target": "C:\\...\\送金明細.xlsx", "result": "ok", "detail": { "tool": "xlsx.write_cells", "run_id": "..." } } ] }
```
`id` で重複を除く（クライアントは送れなかった分を再送するため）。204。

---

## 9. 管理 API（admin のみ。ADM-02〜05）

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/admin/v1/dashboard` | 件数・応答時間の分位点・待ち件数・GPU 温度と使用率・エラー率（ADM-03） |
| GET/POST/PATCH | `/admin/v1/users`, `/admin/v1/users/{id}` | 利用者の作成・無効化・パスワード再設定・部門・同時実行数・ネットの上限 |
| GET/POST/PATCH | `/admin/v1/departments` | 部門と `acl_groups` |
| GET/PUT | `/admin/v1/net/policies`, `/admin/v1/net/sites` | ネットの上限・禁止サイトと信頼サイト。全社停止は `PUT /admin/v1/net/policies {scope:"org", level_cap:"off"}` |
| GET/POST/DELETE | `/admin/v1/dlp/terms` | 機密語辞書（一覧では語を一部伏せて表示し、CSV で一括登録できる） |
| GET/POST | `/admin/v1/instructions` | 共通指示の配信（AGT-10） |
| GET | `/admin/v1/audit?from&to&user&action` | 監査ログの検索（CSV 出力可） |
| GET/POST | `/admin/v1/models`, `/admin/v1/models/{id}/promote`, `/rollback` | モデル台帳の参照・本番化・切り戻し（7.4） |
| POST | `/admin/v1/runs/{user_id}/pause` | 長時間実行中の作業の一時停止を要求する（確認事項。クライアントは次の推論投入のときに受け取る） |

---

## 10. 死活監視・メトリクス

| パス | 認証 | 内容 |
| --- | --- | --- |
| GET `/healthz` | なし | プロセスの生存確認 |
| GET `/readyz` | なし | DB・エンジン・各サービスの状態（`{"engine":{"local-12b":"ok"},"db":"ok"}`） |
| GET `/metrics` | 内部ネットワークのみ | Prometheus 形式（`llmgw_queue_depth`, `llmgw_job_seconds_bucket`, `llmgw_jobs_total{state}` ほか） |

クライアントは `/readyz` の結果を `server.status` イベントに変換して表示する（OPS-02）。予備サーバーの URL が設定されていれば、本番機が30秒以上応答しないときに切り替える（OPS-03）。
