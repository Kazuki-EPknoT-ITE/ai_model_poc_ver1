"""社内文書検索の内部 HTTP API（Gateway から内部ネットワークで呼ぶ。local_llm_api.md §6）。

- `POST /v1/rag/search`: Gateway が利用者の acl_groups を付けて呼ぶ。共有シークレット（X-Internal-Token）で
  Gateway 以外を拒否する。認証は本文を読む前に行う。
- `GET /healthz`: 生存確認（認証なし。件数などは返さない）。
- ログには件数と時間だけを出す（検索語・本文・パスは出さない）。検証エラーの応答にも入力値を含めない。
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llm_rag.model import parse_time
from llm_rag.search import Searcher

log = logging.getLogger("llm_rag")
MAX_BODY = 64 * 1024


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path_prefix: str | None = Field(default=None, max_length=1024)
    doc_type: str | None = Field(default=None, max_length=100)
    updated_after: str | None = Field(default=None, max_length=40)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)
    filters: Filters | None = None
    acl_groups: list[str] = Field(max_length=500)
    """利用者の部門＋個人のグループ。Gateway がサーバー側で付ける（SEC-07）。"""


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "retryable": retryable}}, status_code=status
    )


def create_app(searcher: Searcher, *, secret: str) -> FastAPI:
    if not secret:
        raise ValueError("secret is required")
    app = FastAPI(title="llm-rag", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    @app.post("/v1/rag/search")
    async def search(request: Request) -> JSONResponse:
        t0 = time.monotonic()
        rid = request.headers.get("x-request-id", "")[:100]
        token = request.headers.get("x-internal-token")
        if not token or not hmac.compare_digest(token.encode(), secret.encode()):
            log.warning("rag.search rejected reason=unauthenticated rid=%s", rid)
            return _error(401, "UNAUTHENTICATED", "内部トークンが正しくありません")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return _error(400, "INVALID_REQUEST", "リクエストが大きすぎます")
        try:
            req = SearchRequest.model_validate(json.loads(raw))
            updated_after = (
                parse_time(req.filters.updated_after) if req.filters and req.filters.updated_after else None
            )
        except ValidationError as e:
            fields = sorted({".".join(str(x) for x in err["loc"]) for err in e.errors()})
            log.info("rag.search invalid fields=%s rid=%s", ",".join(fields), rid)
            return _error(400, "INVALID_REQUEST", f"リクエストの形式が不正です: {', '.join(fields)}")
        except ValueError:
            return _error(400, "INVALID_REQUEST", "JSON または日時の形式が不正です")
        f = req.filters or Filters()
        try:
            hits = await run_in_threadpool(
                searcher.search,
                req.query,
                req.acl_groups,
                top_k=req.top_k,
                path_prefix=f.path_prefix,
                doc_type=f.doc_type,
                updated_after=updated_after,
            )
        except Exception as e:  # 索引（Qdrant）に接続できないなど
            log.error("rag.search failed err=%s rid=%s", type(e).__name__, rid)
            return _error(503, "ENGINE_UNAVAILABLE", "検索サービスが一時的に使えません", retryable=True)
        log.info(
            "rag.search ok hits=%d took_ms=%d rid=%s", len(hits), int((time.monotonic() - t0) * 1000), rid
        )
        body = {
            "hits": [
                {"ref": h.ref, "path": h.path, "location": h.location, "score": h.score, "excerpt": h.excerpt}
                for h in hits
            ]
        }
        headers = {"X-Request-Id": rid} if rid else None
        return JSONResponse(body, headers=headers)

    return app
