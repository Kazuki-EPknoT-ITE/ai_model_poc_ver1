"""RAG・Web Relay・OCR への中継（local_llm_api.md §5〜7）。

Gateway が行うのは認証・ネットの上限・検索回数の判定と中継だけ。禁止サイト・DLP・SSRF の
判定は Web Relay（M6）が行う。上流の URL が未設定なら 503 FEATURE_UNAVAILABLE。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import Response

from .util import ApiError

log = logging.getLogger("llm_gateway.relay")

_PASS_HEADERS = ("content-type",)


def require_upstream(url: str | None, enabled: bool, feature: str) -> str:
    if not url or not enabled:
        raise ApiError(503, "FEATURE_UNAVAILABLE", f"この機能（{feature}）はサーバーで有効になっていません")
    return url.rstrip("/")


async def forward(
    http: httpx.AsyncClient,
    url: str,
    *,
    json_body: Any | None = None,
    content: bytes | None = None,
    content_type: str | None = None,
    headers: dict[str, str],
) -> tuple[Response, Any | None]:
    """上流へ POST し、(応答, JSON 本文 or None) を返す。本文はログに出さない。"""
    h = dict(headers)
    if content_type is not None:
        h["Content-Type"] = content_type
    try:
        if json_body is not None:
            res = await http.post(url, json=json_body, headers=h)
        else:
            res = await http.post(url, content=content, headers=h)
    except httpx.HTTPError as e:
        log.warning("upstream unreachable url=%s err=%s", url, type(e).__name__)
        raise ApiError(
            503, "UPSTREAM_UNAVAILABLE", "上流のサービスに接続できません", retryable=True
        ) from None
    parsed: Any | None = None
    if res.headers.get("content-type", "").startswith("application/json"):
        try:
            parsed = res.json()
        except ValueError:
            parsed = None
    if res.status_code >= 400 and not (isinstance(parsed, dict) and "error" in parsed):
        log.warning("upstream error url=%s status=%d", url, res.status_code)
        raise ApiError(502, "UPSTREAM_ERROR", "上流のサービスでエラーが発生しました", retryable=True)
    out_headers = {k: v for k, v in res.headers.items() if k.lower() in _PASS_HEADERS}
    return Response(content=res.content, status_code=res.status_code, headers=out_headers), parsed
