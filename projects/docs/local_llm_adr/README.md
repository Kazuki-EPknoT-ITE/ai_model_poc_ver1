# ADR（設計判断の記録） — ローカルLLMデスクトップエージェント

設計上の重要な判断を1件1ファイルで記録する。基本設計書 `../local_llm_design.md` の §13 が候補の一覧にあたる。

## 一覧

| ADR | タイトル | 状態 | 関連要件 |
| --- | --- | --- | --- |
| [001](001-electron.md) | クライアントは Electron＋React、Runtime は Node の子プロセスで動かす（改訂） | 承認済み | APP-*, RUN-07 |
| [002](002-action-protocol-guided-json.md) | 独自のアクションプロトコルを guided_json で強制する | 承認済み | RUN-04, AGT-05 |
| [003](003-custom-gateway.md) | Gateway は FastAPI＋PostgreSQL で自作する | 承認済み | RUN-09, RUN-10 |
| [004](004-pdf-libraries.md) | PDF は pypdfium2＋pdfplumber を使い、PyMuPDF は使わない | 承認済み | PDF-01〜07 |
| [005](005-libreoffice-recalc.md) | 再計算と変換のため LibreOffice をクライアントに同梱する | 承認済み | XLS-04, DOC-08/09 |
| [006](006-ocr-on-server.md) | OCR はサーバーの GPU で処理する | 承認済み（R8） | PDF-03 |
| [007](007-encrypted-queue-payload.md) | キューに依頼本文を暗号化して完了まで置く | 承認済み（R4） | RUN-09, SEC-06 |
| [008](008-local-accounts-auth.md) | 認証は独自アカウントから始め、OIDC を後から追加できるようにする | 承認済み | ADM-01, SEC-02 |

## 書式

```
# ADR-NNN: タイトル
- 状態: 提案 / 承認済み（日付）/ 廃止（置き換え先）
## 背景
## 決定
## 検討した代替案
## 影響・リスク
## 関連要件ID
```

- 承認済みの ADR は書き換えない。方針を変えるときは新しい ADR を起こし、古い方の状態を「廃止（ADR-xxx に置き換え）」にする。
