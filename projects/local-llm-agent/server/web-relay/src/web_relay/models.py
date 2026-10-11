"""内部 API（Gateway → Relay）のリクエスト・レスポンス。

利用者向け API（`/v1/web/search`, `/v1/web/fetch`。api §7）の本体に、Gateway が判定済みの
`user_id`・`org_id`・`level`（利用者の設定と部門・全社の上限のうち最も小さいもの）を加えた形。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NetLevel = Literal["off", "search", "browse"]


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=64)
    level: NetLevel
    request_id: str | None = Field(default=None, max_length=128)


class SearchRequest(_Req):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=20)
    site: str | None = Field(default=None, max_length=253)
    # Gateway が net_policies から決めた月の上限。省略時は Relay の既定値。null は無制限
    monthly_search_limit: int | None = Field(default=None, ge=0)


class SearchResult(BaseModel):
    rank: int
    title: str
    url: str
    snippet: str
    trusted: bool


class SearchResponse(BaseModel):
    results: list[SearchResult]
    remaining_quota: int | None  # 無制限なら null


class FetchRequest(_Req):
    url: str = Field(min_length=1, max_length=2048)
    max_chars: int = Field(default=8000, ge=1, le=8000)


class FetchResponse(BaseModel):
    url: str
    final_url: str
    title: str
    content_type: str
    text: str
    chars: int
    truncated: bool
    trusted: bool
