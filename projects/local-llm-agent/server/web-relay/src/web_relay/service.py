"""検索・取得の判定と実行の流れ（設計書 §6.6, api §7）。

判定の順序: ネットの段階（level。Gateway の判定を Relay でも再確認）→ DLP（NET-07）→
社内アドレス・禁止サイト・SSRF（NET-08）→ 利用回数（NET-13）→ 実行 → 監査ログ（NET-11）。

DLP を SSRF より先に判定するのは、拒否した URL を監査ログに残すときに機密語が混ざらないようにするため
（DLP に該当した依頼は検索語・URL を記録しない）。拒否の結果は同じ（どちらも送信しない）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from . import errors
from .audit import AuditEvent, AuditSink
from .config import Settings
from .extract import Extracted, clean_text, decode_bytes, extract_html, extract_pdf, extract_text_plain
from .fetcher import Fetcher
from .models import FetchRequest, FetchResponse, SearchRequest, SearchResponse, SearchResult
from .netguard import Target, UrlGuard, normalize_site_pattern
from .policy import OrgPolicy, PolicyStore
from .search import RawResult, SearchProvider
from .usage import UsageStore

log = logging.getLogger("web_relay")

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _level_allows(level: str, op: Literal["search", "fetch"]) -> bool:
    if op == "search":
        return level in ("search", "browse")
    return level == "browse"


class RelayService:
    def __init__(
        self,
        settings: Settings,
        *,
        policy: PolicyStore,
        guard: UrlGuard,
        fetcher: Fetcher,
        provider: SearchProvider,
        usage: UsageStore,
        audit: AuditSink,
        clock: Clock = _utcnow,
    ) -> None:
        self._s = settings
        self._policy = policy
        self._guard = guard
        self._fetcher = fetcher
        self._provider = provider
        self._usage = usage
        self._audit = audit
        self._clock = clock

    # ------------------------------------------------------------------ 共通

    def _month(self) -> str:
        local = self._clock() + timedelta(hours=self._s.search.quota_utc_offset_hours)
        return local.strftime("%Y-%m")

    def _record(
        self,
        *,
        action: str,
        req: SearchRequest | FetchRequest,
        target: str | None,
        result: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        d: dict[str, object] = dict(detail or {})
        if req.request_id:
            d["request_id"] = req.request_id
        if target is None:
            d["redacted"] = True
        event = AuditEvent(
            at=self._clock().isoformat(),
            user_id=req.user_id,
            org_id=req.org_id,
            action=action,
            target=target,
            result=result,
            detail=d,
        )
        try:
            self._audit.write(event)
        except Exception:  # noqa: BLE001 - 監査ログの失敗で利用者の作業は止めないが、運用者には知らせる
            log.exception("audit write failed action=%s result=%s", action, result)
        # アプリのログには本文・検索語・URL を出さない（dev_guide §3.1）
        log.info("%s user=%s org=%s result=%s", action, req.user_id, req.org_id, result)

    @staticmethod
    def _result_of(e: errors.RelayError) -> str:
        kind = "blocked" if e.status in (403, 429) else "error"
        return f"{kind}:{e.code}"

    def _checked_site(self, site: str, pol: OrgPolicy) -> str:
        host = normalize_site_pattern(site)
        if host.startswith("*."):
            host = host[2:]
        if (
            not host
            or any(c in host for c in "/:@?#[] \\")
            or pol.sites.is_internal(host)
            or pol.sites.is_blocked(host)
        ):
            raise errors.domain_blocked()
        return host

    # ------------------------------------------------------------------ 検索

    async def search(self, req: SearchRequest) -> SearchResponse:
        pol = self._policy.get(req.org_id)
        dlp_hit = pol.dlp.query_blocked(req.query) or (req.site is not None and pol.dlp.url_blocked(req.site))
        logged = None if dlp_hit else req.query + (f" site:{req.site}" if req.site else "")
        try:
            if not _level_allows(req.level, "search"):
                raise errors.net_disabled()
            if dlp_hit:
                raise errors.dlp_blocked()
            site = self._checked_site(req.site, pol) if req.site else None
            limit = (
                req.monthly_search_limit
                if "monthly_search_limit" in req.model_fields_set
                else self._s.search.default_monthly_limit
            )
            remaining = self._usage.try_consume(req.user_id, self._month(), "search", limit)
            if remaining is None:
                raise errors.quota_exceeded()
            n = min(req.max_results, self._s.search.max_results)
            q = req.query + (f" site:{site}" if site else "")
            raw = await self._provider.search(q, max_results=n)
            results = self._shape_results(raw, pol, n)
        except errors.RelayError as e:
            self._record(action="web.search", req=req, target=logged, result=self._result_of(e))
            raise
        self._record(
            action="web.search", req=req, target=logged, result="ok", detail={"results": len(results)}
        )
        return SearchResponse(results=results, remaining_quota=None if remaining < 0 else remaining)

    def _shape_results(self, raw: list[RawResult], pol: OrgPolicy, n: int) -> list[SearchResult]:
        s = self._s.search
        shaped: list[tuple[bool, str, str, str]] = []
        seen: set[str] = set()
        for r in raw:
            try:
                target = self._guard.check_static(r.url, pol.sites)
            except errors.RelayError:
                continue  # 社内アドレス・禁止サイト・http(s) 以外の結果は返さない
            if target.url in seen:
                continue
            seen.add(target.url)
            title = clean_text(r.title).replace("\n", " ")[: s.title_max_chars]
            snippet = clean_text(r.snippet).replace("\n", " ")[: s.snippet_max_chars]
            shaped.append((pol.sites.is_trusted(target.host), r.url, title, snippet))
        # 信頼サイトを先に（それ以外の順序は検索エンジンのまま）
        shaped.sort(key=lambda t: not t[0])
        return [
            SearchResult(rank=i + 1, title=title, url=url, snippet=snippet, trusted=trusted)
            for i, (trusted, url, title, snippet) in enumerate(shaped[:n])
        ]

    # ------------------------------------------------------------------ 取得

    async def fetch(self, req: FetchRequest) -> FetchResponse:
        pol = self._policy.get(req.org_id)
        dlp_hit = pol.dlp.url_blocked(req.url)
        logged = None if dlp_hit else req.url
        detail: dict[str, object] = {}

        async def check(url: str) -> Target:
            # リダイレクト先にも毎回 DLP・SSRF・禁止サイトの判定をかける
            if pol.dlp.url_blocked(url):
                raise errors.dlp_blocked()
            return self._guard.check_static(url, pol.sites)

        try:
            if not _level_allows(req.level, "fetch"):
                raise errors.net_disabled()
            if dlp_hit:
                raise errors.dlp_blocked()
            self._guard.check_static(req.url, pol.sites)  # DNS の前に静的な判定で落とす
            self._usage.try_consume(req.user_id, self._month(), "fetch", None)
            res = await self._fetcher.fetch(req.url, check)
            detail.update({"redirects": res.redirects, "content_type": res.mime})
            if res.final_url != req.url:
                detail["final_url"] = res.final_url
            max_chars = min(req.max_chars, self._s.fetch.max_chars)
            ext = await self._extract(res.mime, res.charset, res.body, max_chars)
            detail["chars"] = ext.chars
            final_host = self._guard.check_static(res.final_url, pol.sites).host
        except errors.RelayError as e:
            self._record(action="web.fetch", req=req, target=logged, result=self._result_of(e), detail=detail)
            raise
        self._record(action="web.fetch", req=req, target=logged, result="ok", detail=detail)
        return FetchResponse(
            url=req.url,
            final_url=res.final_url,
            title=ext.title,
            content_type=res.mime,
            text=ext.text,
            chars=ext.chars,
            truncated=ext.truncated,
            trusted=pol.sites.is_trusted(final_host),
        )

    async def _extract(self, mime: str, charset: str | None, body: bytes, max_chars: int) -> Extracted:
        f = self._s.fetch
        try:
            if mime == "application/pdf":
                return await asyncio.to_thread(
                    extract_pdf, body, max_chars=max_chars, max_pages=f.max_pdf_pages
                )
            if mime == "text/plain":
                return extract_text_plain(decode_bytes(body, charset, sniff_html=False), max_chars=max_chars)
            raw = decode_bytes(body, charset, sniff_html=True)
            return await asyncio.to_thread(
                extract_html, raw, max_chars=max_chars, use_trafilatura=f.use_trafilatura
            )
        except errors.RelayError:
            raise
        except Exception as e:  # noqa: BLE001 - 壊れた PDF など。中身はログに出さない
            log.warning("extract failed mime=%s err=%s", mime, type(e).__name__)
            raise errors.upstream("EXTRACT_FAILED", "本文を取り出せませんでした") from e
