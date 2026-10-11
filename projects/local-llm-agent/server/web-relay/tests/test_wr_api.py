"""内部 API の認証（共有シークレット）・監査ログ（NET-11）・入力検証の試験。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

import pytest
from wr_helpers import DLP_TERMS_TEXT, SECRET, Env, make_settings

from web_relay.app import create_app
from web_relay.audit import AuditEvent, JsonlAuditSink
from web_relay.config import ServerSettings, load_secret, load_settings


@pytest.mark.parametrize("secret", [None, "", "wrong-secret-0123456789", SECRET + "x", SECRET.upper()])
def test_secret_required(env: Env, secret: str | None) -> None:
    for path, body in (
        ("/search", {"user_id": "u1", "org_id": "hirai", "level": "search", "query": "x"}),
        ("/fetch", {"user_id": "u1", "org_id": "hirai", "level": "browse", "url": "http://public.test/page"}),
    ):
        r = env.post(path, body, secret=secret)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert env.provider.queries == []
    assert env.server.requests == []
    assert env.audit.events == []


def test_healthz_and_no_docs(env: Env) -> None:
    assert env.client.get("/healthz").json()["status"] == "ok"
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert env.client.get(path, headers={"X-Relay-Secret": SECRET}).status_code == 404


def test_request_id_is_echoed(env: Env) -> None:
    r = env.client.post(
        "/search",
        json={"user_id": "u1", "org_id": "hirai", "level": "search", "query": "x"},
        headers={"X-Relay-Secret": SECRET, "X-Request-Id": "req-123"},
    )
    assert r.headers["X-Request-Id"] == "req-123"


def test_short_secret_rejected() -> None:
    with pytest.raises(ValueError):
        create_app(make_settings(), "short")


def test_load_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WEB_RELAY_SECRET", "")
    with pytest.raises(RuntimeError):
        load_secret(ServerSettings())
    monkeypatch.setenv("WEB_RELAY_SECRET", SECRET)
    assert load_secret(ServerSettings()) == SECRET
    f = tmp_path / "secret"
    f.write_text(SECRET + "\n", encoding="utf-8")
    assert load_secret(ServerSettings(secret_file=str(f))) == SECRET


def test_load_settings_with_policy_file(tmp_path: Path) -> None:
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        "orgs:\n  hirai:\n    dlp_terms:\n      - {term: 平井丸, readings: [ひらいまる]}\n", encoding="utf-8"
    )
    cfg = tmp_path / "relay.yaml"
    cfg.write_text(f"policy_file: {pol.as_posix()}\nsearch:\n  provider: fake\n", encoding="utf-8")
    s = load_settings(cfg)
    assert s.policy.orgs["hirai"].dlp_terms[0].readings == ["ひらいまる"]


def _all_audit_text(env: Env) -> str:
    return json.dumps([asdict(e) for e in env.audit.events], ensure_ascii=False)


def test_audit_records_only_metadata(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    env.search("国際海事機関 規則改正")
    env.search("平井丸 入渠 費用")
    env.search("ﾋﾗｲﾏﾙ")
    env.fetch("http://public.test/page")
    env.fetch("https://example.com/?q=%E5%B9%B3%E4%BA%95%E4%B8%B8")
    env.fetch("http://127.0.0.1/?q=平井丸")  # SSRF でも DLP でも拒否される → 記録しない
    env.fetch("http://10.0.0.1/admin")

    ev = env.audit.events
    assert [(e.action, e.result) for e in ev] == [
        ("web.search", "ok"),
        ("web.search", "blocked:DLP_BLOCKED"),
        ("web.search", "blocked:DLP_BLOCKED"),
        ("web.fetch", "ok"),
        ("web.fetch", "blocked:DLP_BLOCKED"),
        ("web.fetch", "blocked:DLP_BLOCKED"),
        ("web.fetch", "blocked:DOMAIN_BLOCKED"),
    ]
    assert ev[0].target == "国際海事機関 規則改正"
    assert ev[0].user_id == "u1" and ev[0].org_id == "hirai" and ev[0].source == "web_relay"
    assert ev[0].at.endswith("+00:00")
    assert ev[1].target is None and ev[1].detail.get("redacted") is True
    assert ev[3].target == "http://public.test/page"
    assert ev[6].target == "http://10.0.0.1/admin"

    text = _all_audit_text(env) + "\n" + caplog.text
    # 辞書の語（読み・表記ゆれを含む）とページ本文が監査ログ・アプリのログに出ていない
    for term in [*DLP_TERMS_TEXT, "ﾋﾗｲﾏﾙ", "%E5%B9%B3", "入渠"]:
        assert term not in text, term
    for body_text in ("可視の本文です", "海事規則の改正について", "SECRET_SCRIPT"):
        assert body_text not in text
    # アプリのログには検索語・URL も出さない
    assert "国際海事機関" not in caplog.text
    assert "public.test" not in caplog.text


def test_jsonl_audit_sink(tmp_path: Path) -> None:
    p = tmp_path / "a" / "audit.jsonl"
    sink = JsonlAuditSink(p)
    sink.write(
        AuditEvent(
            at="2026-10-11T00:00:00+00:00",
            user_id="u",
            org_id="o",
            action="web.search",
            target="q",
            result="ok",
        )
    )
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["target"] == "q" and rows[0]["source"] == "web_relay"


def test_validation_error_does_not_echo_input(env: Env) -> None:
    r = env.search("平井丸" * 200)  # 500 文字を超える
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert "平井丸" not in r.text
    r = env.post("/search", {"user_id": "u1", "org_id": "hirai", "level": "admin", "query": "x"})
    assert r.status_code == 400
    r = env.post("/search", {"user_id": "u1", "org_id": "hirai", "level": "search", "query": "x", "extra": 1})
    assert r.status_code == 400


def test_unknown_org_still_applies_regex_rules(env: Env) -> None:
    r = env.search("普通 1234567", org="other-org")
    assert r.json()["error"]["code"] == "DLP_BLOCKED"
    assert env.search("平井丸", org="other-org").status_code == 200  # 辞書は org ごと
