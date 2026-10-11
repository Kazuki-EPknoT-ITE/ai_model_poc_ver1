"""内部 API（/v1/rag/search, /healthz）の認証・形式と、ログに本文・検索語が出ないこと。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rag_testkit import IndexFactory, make_settings, write_docx, write_text

from llm_rag.app import create_app
from llm_rag.ingest import run_ingest
from llm_rag.search import Searcher

SECRET = "test-secret-123"  # noqa: S105
HIDDEN_WORD = "極秘合言葉ゼブラ"
FILE_NAME = "機密ファイル名カモメ.txt"


@pytest.fixture
def client(tmp_path: Path, make_index: IndexFactory) -> TestClient:
    docs = tmp_path / "docs"
    write_text(docs / "公開" / FILE_NAME, f"{HIDDEN_WORD}の本文。傭船料の支払期日は月末。\n")
    write_docx(docs / "公開" / "規程.docx", [("h1", "第5条 支払期日"), ("p", "支払は月末とする。")])
    write_text(docs / "役員" / "報酬.txt", "役員報酬の支払期日。\n")
    s = make_settings(tmp_path, [(str(docs / "公開"), ["all"]), (str(docs / "役員"), ["executive"])])
    idx = make_index(s)
    run_ingest(idx)
    return TestClient(create_app(Searcher(idx), secret=SECRET))


def _post(c: TestClient, body: object, token: str | None = SECRET, **headers: str) -> object:
    h = dict(headers)
    if token is not None:
        h["X-Internal-Token"] = token
    return c.post("/v1/rag/search", json=body, headers=h)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("token", [None, "", "wrong", SECRET + "x"])
def test_rejects_without_valid_internal_token(client: TestClient, token: str | None) -> None:
    r = _post(client, {"query": "支払期日", "acl_groups": ["all"]}, token)
    assert r.status_code == 401  # type: ignore[attr-defined]
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"  # type: ignore[attr-defined]


def test_auth_is_checked_before_body(client: TestClient) -> None:
    r = client.post("/v1/rag/search", content=b"{not json", headers={"X-Internal-Token": "bad"})
    assert r.status_code == 401


def test_search_shape(client: TestClient) -> None:
    r = client.post(
        "/v1/rag/search",
        json={"query": "支払期日", "top_k": 5, "filters": {"doc_type": "docx"}, "acl_groups": ["all"]},
        headers={"X-Internal-Token": SECRET, "X-Request-Id": "req-1"},
    )
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "req-1"
    hits = r.json()["hits"]
    assert len(hits) == 1
    h = hits[0]
    assert set(h) == {"ref", "path", "location", "score", "excerpt"}
    assert h["ref"] == "r1" and h["path"].endswith("規程.docx") and h["location"] == "第5条 支払期日"
    assert isinstance(h["score"], float)


def test_acl_groups_are_required_and_applied(client: TestClient) -> None:
    r = client.post("/v1/rag/search", json={"query": "役員報酬"}, headers={"X-Internal-Token": SECRET})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = client.post(
        "/v1/rag/search",
        json={"query": "役員報酬", "acl_groups": ["all"], "top_k": 10},
        headers={"X-Internal-Token": SECRET},
    )
    assert all("役員" not in h["path"] for h in r.json()["hits"])
    r = client.post(
        "/v1/rag/search", json={"query": "役員報酬", "acl_groups": []}, headers={"X-Internal-Token": SECRET}
    )
    assert r.status_code == 200 and r.json() == {"hits": []}


@pytest.mark.parametrize(
    "body",
    [
        {"query": "", "acl_groups": ["all"]},
        {"query": "あ" * 201, "acl_groups": ["all"]},
        {"query": "x", "acl_groups": ["all"], "top_k": 0},
        {"query": "x", "acl_groups": ["all"], "top_k": 11},
        {"query": "x", "acl_groups": ["all"], "filters": {"unknown": 1}},
        {"query": "x", "acl_groups": ["all"], "filters": {"updated_after": "昨日"}},
        {"query": "x", "acl_groups": ["all"], "extra": True},
        ["not", "an", "object"],
    ],
)
def test_invalid_requests_do_not_echo_input(client: TestClient, body: object) -> None:
    r = client.post("/v1/rag/search", json=body, headers={"X-Internal-Token": SECRET})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert "あああ" not in r.text and "昨日" not in r.text


def test_updated_after_filter_via_api(client: TestClient) -> None:
    r = client.post(
        "/v1/rag/search",
        json={"query": "支払期日", "acl_groups": ["all"], "filters": {"updated_after": "2999-01-01"}},
        headers={"X-Internal-Token": SECRET},
    )
    assert r.status_code == 200 and r.json() == {"hits": []}


def test_logs_have_no_query_or_document_text(
    tmp_path: Path, make_index: IndexFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    docs = tmp_path / "docs"
    write_text(docs / FILE_NAME, f"{HIDDEN_WORD}の本文。傭船料の支払期日は月末。\n")
    write_text(docs / "壊れ.docx", "broken")
    idx = make_index(make_settings(tmp_path, [(str(docs), ["all"])]))
    run_ingest(idx)
    c = TestClient(create_app(Searcher(idx), secret=SECRET))
    query = f"{HIDDEN_WORD} 支払期日"
    ok = c.post(
        "/v1/rag/search", json={"query": query, "acl_groups": ["all"]}, headers={"X-Internal-Token": SECRET}
    )
    assert ok.status_code == 200 and HIDDEN_WORD in ok.text  # 応答には抜粋が入る
    c.post(
        "/v1/rag/search",
        json={"query": query * 50, "acl_groups": ["all"]},
        headers={"X-Internal-Token": SECRET},
    )
    c.post("/v1/rag/search", json={"query": query, "acl_groups": ["all"]}, headers={"X-Internal-Token": "x"})

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "rag.search" in text and "ingest done" in text  # 件数・時間は出る
    for secret in (HIDDEN_WORD, "支払期日", "傭船料", FILE_NAME, "壊れ.docx", str(docs), SECRET):
        assert secret not in text, secret
