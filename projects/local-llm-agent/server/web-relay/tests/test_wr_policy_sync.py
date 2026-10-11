"""Gateway からのポリシー取り込み（/internal/v1/net-policy）と、取り込めないときに断ること。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from wr_helpers import SECRET, LocalServer, build_env, make_settings

from web_relay.app import create_app
from web_relay.config import PolicySyncSettings
from web_relay.policy_sync import PolicySync, merge

REMOTE: dict[str, Any] = {
    "sites": [{"pattern": "*.remote-block.test", "kind": "block"}],
    "orgs": {
        "hirai": {"dlp_terms": [{"term": "極秘計画", "category": "project", "readings": ["ごくひけいかく"]}]}
    },
    "version": "v1",
}


class FakeGateway:
    def __init__(self) -> None:
        self.body: dict[str, Any] = REMOTE
        self.status = 200
        self.calls: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.headers.get("X-Relay-Secret") != SECRET:
            return httpx.Response(401)
        return httpx.Response(self.status, json=self.body)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_merge_keeps_local_and_adds_remote() -> None:
    base = make_settings().policy
    from web_relay.policy_sync import NetPolicyIn

    merged = merge(base, NetPolicyIn.model_validate(REMOTE))
    assert [s.pattern for s in merged.sites][-1] == "*.remote-block.test"
    terms = [t.term for t in merged.orgs["hirai"].dlp_terms]
    assert terms[0] == "平井丸" and terms[-1] == "極秘計画"
    assert merged.orgs["hirai"].sites[0].pattern == "evil-org.test"  # 設定ファイル側の org のサイトも残る
    assert merged.internal_domains == base.internal_domains
    assert base.orgs["hirai"].dlp_terms[-1].term == "三井商事"  # 元の設定は書き換えない


def _app(http_server: LocalServer, gw: FakeGateway, clock: Clock) -> tuple[TestClient, PolicySync]:
    settings = make_settings(policy_sync={"gateway_url": "http://gateway.internal", "max_stale_s": 600})
    env = build_env(http_server, settings=settings)
    sync = PolicySync(
        settings.policy,
        settings.policy_sync,
        env.comps.policy,
        SECRET,
        transport=httpx.MockTransport(gw),
        clock=clock,
    )
    env.comps.sync = sync
    return TestClient(create_app(settings, SECRET, env.comps)), sync


def _search(client: TestClient, query: str) -> httpx.Response:
    body = {"user_id": "u1", "org_id": "hirai", "level": "search", "query": query}
    return client.post("/search", json=body, headers={"X-Relay-Secret": SECRET})


def test_fail_closed_until_first_sync_and_when_stale(http_server: LocalServer) -> None:
    gw, clock = FakeGateway(), Clock()
    client, sync = _app(http_server, gw, clock)
    gw.status = 500
    assert asyncio.run(sync.sync_once()) is False
    r = _search(client, "天気")
    assert r.status_code == 503
    assert r.json()["error"] == {
        "code": "UPSTREAM_UNAVAILABLE",
        "message": "ネットのポリシーを読み込めていないため、今は使えません",
        "retryable": True,
    }

    gw.status = 200
    assert asyncio.run(sync.sync_once()) is True
    assert _search(client, "天気").status_code == 200
    # 取り込んだ機密語（読みも）で止まる。設定ファイルの語も引き続き効く
    for q in ("極秘計画 進捗", "ごくひけいかく", "平井丸"):
        assert _search(client, q).json()["error"]["code"] == "DLP_BLOCKED", q
    fetch = client.post(
        "/fetch",
        json={"user_id": "u1", "org_id": "hirai", "level": "browse", "url": "http://a.remote-block.test/"},
        headers={"X-Relay-Secret": SECRET},
    )
    assert fetch.json()["error"]["code"] == "DOMAIN_BLOCKED"

    # Gateway が落ちても、しばらくは前回の内容で続け、古くなりすぎたら断る
    gw.status = 503
    clock.t += 300
    assert asyncio.run(sync.sync_once()) is False
    assert _search(client, "天気").status_code == 200
    clock.t += 301
    assert _search(client, "天気").status_code == 503


def test_version_change_reloads(http_server: LocalServer) -> None:
    gw, clock = FakeGateway(), Clock()
    client, sync = _app(http_server, gw, clock)
    assert asyncio.run(sync.sync_once())
    assert _search(client, "新造船").status_code == 200
    gw.body = {**REMOTE, "orgs": {"hirai": {"dlp_terms": [{"term": "新造船"}]}}, "version": "v2"}
    assert asyncio.run(sync.sync_once())
    assert sync.version == "v2"
    assert _search(client, "新造船").json()["error"]["code"] == "DLP_BLOCKED"
    assert _search(client, "極秘計画").status_code == 200  # 消された語は効かなくなる


def test_wrong_secret_is_not_ready(http_server: LocalServer) -> None:
    settings = make_settings()
    env = build_env(http_server, settings=settings)
    sync = PolicySync(
        settings.policy,
        PolicySyncSettings(gateway_url="http://gateway.internal"),
        env.comps.policy,
        "wrong-secret-0123456789",
        transport=httpx.MockTransport(FakeGateway()),
    )
    assert asyncio.run(sync.sync_once()) is False
    assert not sync.ready()


def test_lifespan_runs_sync(http_server: LocalServer) -> None:
    gw = FakeGateway()
    client, sync = _app(http_server, gw, Clock())
    with client:  # 起動時に取り込みが始まる
        for _ in range(100):
            if sync.ready():
                break
            asyncio.run(asyncio.sleep(0.02))
        assert sync.ready() and gw.calls
        assert _search(client, "天気").status_code == 200


@pytest.mark.parametrize("bad", [{"sites": [{"pattern": "x", "kind": "nope"}]}, ["not", "an", "object"]])
def test_bad_payload_keeps_previous(http_server: LocalServer, bad: Any) -> None:
    gw, clock = FakeGateway(), Clock()
    client, sync = _app(http_server, gw, clock)
    assert asyncio.run(sync.sync_once())
    gw.body = bad
    assert asyncio.run(sync.sync_once()) is False
    assert sync.version == "v1"
    assert _search(client, "極秘計画").json()["error"]["code"] == "DLP_BLOCKED"
