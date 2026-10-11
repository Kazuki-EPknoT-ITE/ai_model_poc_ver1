"""読み取り専用（NET-06, RX-10/11）・本文抽出（NET-09, SX-04/05）の試験。"""

from __future__ import annotations

import pytest
from wr_helpers import Env, LocalServer, build_env, make_settings

from web_relay.extract import extract_html


def test_only_get_and_no_credentials(env: Env) -> None:
    # Set-Cookie を返すページを2回取得しても、Cookie は送られない
    for _ in range(2):
        r = env.fetch("http://public.test/cookie")
        assert r.status_code == 200, r.text
    assert len(env.server.requests) == 2
    for req in env.server.requests:
        assert req.method == "GET"
        for h in ("cookie", "authorization", "proxy-authorization", "referer", "origin"):
            assert h not in req.headers


def test_form_autosubmit_page_sends_nothing_but_get(env: Env) -> None:
    # RX-11 / SX-05: フォームの自動送信やスクリプトは実行されない（取得は GET 1回だけ）
    r = env.fetch("http://public.test/form")
    assert r.status_code == 200
    assert [x.method for x in env.server.requests] == ["GET"]
    assert all(not x.path.startswith("/submit") for x in env.server.requests)


def test_api_has_no_method_or_body_option(env: Env) -> None:
    r = env.fetch("http://public.test/page", method="POST", body="x=1")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert env.server.requests == []


@pytest.mark.parametrize("path", ["/big-cl", "/big-stream", "/bomb"])
def test_size_limit(env: Env, path: str) -> None:
    # RX-10: 10MB を超える応答（宣言・実際の受信量・展開後のサイズ）
    r = env.fetch(f"http://public.test{path}")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("path", ["/octet", "/image", "/no-ctype"])
def test_content_type_not_allowed(env: Env, path: str) -> None:
    r = env.fetch(f"http://public.test{path}")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "CONTENT_TYPE_NOT_ALLOWED"


def test_upstream_http_error(env: Env) -> None:
    r = env.fetch("http://public.test/notfound")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "UPSTREAM_HTTP_ERROR"


HIDDEN_MARKERS = [
    "SECRET_SCRIPT",
    "STYLE_TEXT",
    "COMMENT_TEXT",
    "HIDDEN_TEXT",
    "NONE_TEXT",
    "VIS_TEXT",
    "ARIA_TEXT",
    "SRONLY_TEXT",
    "INPUT_VALUE",
    "NOSCRIPT_TEXT",
    "TEMPLATE_TEXT",
    "IFRAME_TEXT",
    "以前の指示を無視",
]


@pytest.mark.parametrize("use_trafilatura", [True, False])
def test_html_extraction_excludes_hidden(http_server: LocalServer, use_trafilatura: bool) -> None:
    e = build_env(http_server, settings=make_settings(fetch={"use_trafilatura": use_trafilatura}))
    r = e.fetch("http://public.test/page")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == "text/html"
    assert body["title"] == "試験ページ"
    assert "可視の本文です" in body["text"]
    for marker in HIDDEN_MARKERS:
        assert marker not in body["text"], marker
    assert body["chars"] == len(body["text"])
    assert body["truncated"] is False
    assert body["url"] == body["final_url"] == "http://public.test/page"
    # Relay は <untrusted> で包まない（Runtime の責務）。ページ内の偽の終了タグは本文のまま返る
    assert "<untrusted" not in body["text"]


def test_max_chars(env: Env) -> None:
    r = env.fetch("http://public.test/long")
    body = r.json()
    assert body["chars"] == 8000
    assert body["truncated"] is True
    r2 = env.fetch("http://public.test/long", max_chars=100)
    assert r2.json()["chars"] == 100
    r3 = env.fetch("http://public.test/long", max_chars=9000)
    assert r3.status_code == 400  # 上限 8,000 字を超える指定は受け付けない


def test_pdf_extraction(env: Env) -> None:
    r = env.fetch("http://public.test/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == "application/pdf"
    assert "Hello PDF body text" in body["text"]
    assert body["title"] == "Test PDF Title"


def test_plain_sjis_gzip(env: Env) -> None:
    assert "プレーンテキストの本文" in env.fetch("http://public.test/plain").json()["text"]
    assert "シフトJISの本文です" in env.fetch("http://public.test/sjis").json()["text"]
    assert "圧縮された本文です" in env.fetch("http://public.test/gzip").json()["text"]


def test_relative_redirect_and_trusted_flag(env: Env) -> None:
    r = env.fetch("http://www.mlit.go.jp/redirect-relative")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_url"] == "http://www.mlit.go.jp/page2"
    assert body["trusted"] is True
    assert env.fetch("http://public.test/page").json()["trusted"] is False


def test_extract_html_unit_nested_hidden() -> None:
    html = (
        "<html><body><div style='display:none'><p>A<b>B</b></p><img src=x><br>C</div>"
        "<p>visible<!-- c --></p><div hidden><div><p>deep</p></div></div><p>end</p></body></html>"
    )
    ext = extract_html(html, max_chars=100, use_trafilatura=False)
    assert "visible" in ext.text and "end" in ext.text
    for s in ("A", "B", "C", "deep", " c "):
        assert s not in ext.text.split()
