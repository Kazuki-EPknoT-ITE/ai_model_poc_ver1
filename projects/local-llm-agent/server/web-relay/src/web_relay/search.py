"""検索プロバイダ（設計書 §6.6: SearXNG を自前で立てる案と商用 API を設定で切り替える）。

プロバイダには DLP を通過した検索語だけを渡す（呼び出し元の `service` が保証する）。
プロバイダの接続先は設定で固定した URL（SearXNG は社内に置くことがあるため SSRF の検査対象外）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from . import errors
from .config import SearchSettings


@dataclass(frozen=True)
class RawResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    async def search(self, query: str, *, max_results: int) -> list[RawResult]: ...


class FakeSearchProvider:
    """試験用。受け取った検索語を記録し、決め打ちの結果を返す。外へは一切接続しない。"""

    def __init__(self, results: list[RawResult] | None = None) -> None:
        self.queries: list[str] = []
        self.results = (
            results
            if results is not None
            else [
                RawResult(title="Example result", url="https://example.com/", snippet="example snippet"),
            ]
        )

    async def search(self, query: str, *, max_results: int) -> list[RawResult]:
        self.queries.append(query)
        return self.results[:max_results]


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


class SearxngProvider:
    """SearXNG の JSON API（`/search?format=json`）。"""

    def __init__(
        self, base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def search(self, query: str, *, max_results: int) -> list[RawResult]:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False, trust_env=False
        ) as client:
            try:
                r = await client.get(
                    f"{self._base}/search", params={"q": query, "format": "json", "safesearch": "1"}
                )
            except httpx.HTTPError as e:
                raise errors.upstream(
                    "SEARCH_UNAVAILABLE", "検索サービスに接続できませんでした", retryable=True
                ) from e
        if r.status_code != 200:
            raise errors.upstream("SEARCH_UNAVAILABLE", "検索サービスがエラーを返しました", retryable=True)
        data = r.json()
        items = data.get("results", []) if isinstance(data, dict) else []
        out: list[RawResult] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(
                RawResult(_as_str(it.get("title")), _as_str(it.get("url")), _as_str(it.get("content")))
            )
            if len(out) >= max_results:
                break
        return out


class BraveProvider:
    """商用 API の例（Brave Search API）。キーは環境変数から読む。"""

    DEFAULT_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        api_key: str,
        timeout_s: float,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._url = base_url or self.DEFAULT_URL
        self._timeout = timeout_s
        self._transport = transport

    async def search(self, query: str, *, max_results: int) -> list[RawResult]:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False, trust_env=False
        ) as client:
            try:
                r = await client.get(
                    self._url,
                    params={"q": query, "count": str(max_results), "safesearch": "moderate"},
                    headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
                )
            except httpx.HTTPError as e:
                raise errors.upstream(
                    "SEARCH_UNAVAILABLE", "検索サービスに接続できませんでした", retryable=True
                ) from e
        if r.status_code != 200:
            raise errors.upstream("SEARCH_UNAVAILABLE", "検索サービスがエラーを返しました", retryable=True)
        data = r.json()
        web = data.get("web", {}) if isinstance(data, dict) else {}
        items = web.get("results", []) if isinstance(web, dict) else []
        out: list[RawResult] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(
                RawResult(_as_str(it.get("title")), _as_str(it.get("url")), _as_str(it.get("description")))
            )
            if len(out) >= max_results:
                break
        return out


def build_provider(settings: SearchSettings) -> SearchProvider:
    if settings.provider == "fake":
        return FakeSearchProvider()
    if settings.provider == "searxng":
        if not settings.base_url:
            raise RuntimeError("search.base_url（SearXNG の URL）が未設定です")
        return SearxngProvider(settings.base_url, settings.timeout_s)
    key = os.environ.get(settings.api_key_env or "", "").strip()
    if not key:
        raise RuntimeError("検索 API のキーが未設定です（search.api_key_env）")
    return BraveProvider(key, settings.timeout_s, settings.base_url)
