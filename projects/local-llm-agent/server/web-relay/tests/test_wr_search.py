"""検索（結果の絞り込み・上限）と利用回数（NET-13）、検索プロバイダの単体試験。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from wr_helpers import Env, LocalServer, build_env

from web_relay.search import BraveProvider, RawResult, SearxngProvider
from web_relay.usage import SqliteUsageStore


def test_search_ok_and_quota_reported(env: Env) -> None:
    r = env.search("国際海事機関 2026年 規則改正", monthly_search_limit=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["remaining_quota"] == 9
    assert body["results"][0]["rank"] == 1
    assert env.provider.queries == ["国際海事機関 2026年 規則改正"]


def test_dlp_blocked_query_is_never_sent(env: Env) -> None:
    for q in ("平井丸 入渠 費用", "ﾋﾗｲﾏﾙ", "Ｈｉｒａｉ　Ｍａｒｕ", "普通 1234567", "¥12,345,678", "1234万円"):
        r = env.search(q)
        assert r.status_code == 403, q
        err = r.json()["error"]
        assert err["code"] == "DLP_BLOCKED"
        assert "言い換え" in err["message"]
        assert "平井丸" not in r.text and "hirai" not in r.text.lower()
    assert env.provider.queries == []  # 外へ送信0件
    assert env.usage.count("u1", _month_now(), "search") == 0  # 止めた検索は回数に数えない


def _month_now() -> str:
    from datetime import timedelta

    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m")


def test_site_param(env: Env) -> None:
    r = env.search("規則改正", site="imo.org")
    assert r.status_code == 200
    assert env.provider.queries == ["規則改正 site:imo.org"]
    for bad in ("intranet.hirai.local", "x.blocked.test", "localhost", "a/b"):
        r = env.search("規則改正", site=bad)
        assert r.status_code == 403, bad
        assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    r = env.search("規則改正", site="hirai-maru.com")
    assert r.json()["error"]["code"] == "DLP_BLOCKED"


def test_results_are_filtered_trimmed_and_trusted_first(http_server: LocalServer) -> None:
    results = [
        RawResult("一般サイト", "https://example.com/a", "s" * 1000),
        RawResult("社内", "http://intranet.hirai.local/x", "x"),
        RawResult("ファイル", "file:///etc/passwd", "x"),
        RawResult("禁止", "https://www.blocked.test/", "x"),
        RawResult("ループバック", "http://127.0.0.1/", "x"),
        RawResult("T" * 500, "https://www.mlit.go.jp/b", "官公庁\x00の<b>結果</b>"),
    ]
    e = build_env(http_server, results=results)
    body = e.search("規則改正", max_results=10).json()
    urls = [x["url"] for x in body["results"]]
    assert urls == ["https://www.mlit.go.jp/b", "https://example.com/a"]
    assert body["results"][0]["trusted"] is True
    assert [x["rank"] for x in body["results"]] == [1, 2]
    assert len(body["results"][0]["title"]) == 200
    assert len(body["results"][1]["snippet"]) == 400
    assert "\x00" not in body["results"][0]["snippet"]


def test_level_checks(env: Env) -> None:
    r = env.search("規則改正", level="off")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NET_DISABLED"
    assert env.search("規則改正", level="browse").status_code == 200
    r = env.fetch("http://public.test/page", level="search")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NET_DISABLED"
    r = env.fetch("http://public.test/page", level="off")
    assert r.json()["error"]["code"] == "NET_DISABLED"
    assert env.server.requests == []
    assert env.provider.queries == ["規則改正"]


def test_quota_exceeded_and_month_rollover(http_server: LocalServer) -> None:
    now = [datetime(2026, 10, 31, 14, 0, tzinfo=UTC)]  # JST 10/31 23:00
    e = build_env(http_server, clock=lambda: now[0])
    assert e.search("a1", monthly_search_limit=2).json()["remaining_quota"] == 1
    assert e.search("a2", monthly_search_limit=2).json()["remaining_quota"] == 0
    r = e.search("a3", monthly_search_limit=2)
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "QUOTA_EXCEEDED" and err["retryable"] is False
    assert "ネットを使わずに" in err["message"]
    assert e.provider.queries == ["a1", "a2"]
    # 別の利用者は数えない
    r_other = e.post("/search", {"user_id": "u2", "org_id": "hirai", "level": "search", "query": "b"})
    assert r_other.status_code == 200
    # JST で月が変わると数え直す
    now[0] = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)  # JST 11/01 00:00
    assert e.search("a4", monthly_search_limit=2).status_code == 200


def test_default_and_unlimited_quota(env: Env) -> None:
    assert env.search("x").json()["remaining_quota"] == 99  # 既定の上限 100
    assert env.search("y", monthly_search_limit=None).json()["remaining_quota"] is None


def test_sqlite_usage_store_persists(tmp_path: Path) -> None:
    db = tmp_path / "usage.sqlite3"
    s1 = SqliteUsageStore(db)
    assert s1.try_consume("u", "2026-10", "search", 2) == 1
    assert s1.try_consume("u", "2026-10", "search", 2) == 0
    s1.close()
    s2 = SqliteUsageStore(db)
    assert s2.try_consume("u", "2026-10", "search", 2) is None
    assert s2.count("u", "2026-10", "search") == 2
    assert s2.try_consume("u", "2026-11", "search", None) == -1
    s2.close()


def test_searxng_provider_parses_results() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        data = {"results": [{"title": "t1", "url": "https://a.example/", "content": "c1"}, {"title": "t2"}]}
        return httpx.Response(200, content=json.dumps(data).encode())

    p = SearxngProvider("http://searx.internal:8080", 5.0, transport=httpx.MockTransport(handler))
    out = asyncio.run(p.search("規則", max_results=1))
    assert out == [RawResult("t1", "https://a.example/", "c1")]
    assert seen[0].url.params["q"] == "規則" and seen[0].url.params["format"] == "json"
    assert "cookie" not in seen[0].headers


def test_brave_provider_parses_results() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Subscription-Token"] == "k"
        data = {"web": {"results": [{"title": "t", "url": "https://b.example/", "description": "d"}]}}
        return httpx.Response(200, content=json.dumps(data).encode())

    p = BraveProvider("k", 5.0, transport=httpx.MockTransport(handler))
    assert asyncio.run(p.search("q", max_results=5)) == [RawResult("t", "https://b.example/", "d")]


def test_provider_failure_is_reported(http_server: LocalServer) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    p = SearxngProvider("http://searx", 5.0, transport=httpx.MockTransport(handler))
    e = build_env(http_server)
    e.comps.service._provider = p  # noqa: SLF001
    r = e.search("規則")
    assert r.status_code == 502
    assert r.json()["error"] == {
        "code": "SEARCH_UNAVAILABLE",
        "message": "検索サービスがエラーを返しました",
        "retryable": True,
    }
