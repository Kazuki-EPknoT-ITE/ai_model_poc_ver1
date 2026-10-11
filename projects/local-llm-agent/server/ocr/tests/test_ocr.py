"""画像読取サービスの試験（API 仕様 §5、ADR-006）。"""

from __future__ import annotations

import io
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from llm_ocr.app import create_app
from llm_ocr.engines import FakeEngine, VlmEngine

SECRET = "internal-secret"  # noqa: S105 - 試験用
H = {"X-Internal-Token": SECRET}


def png(w: int = 200, h: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def client(**engines: FakeEngine) -> TestClient:
    e = engines or {"fake": FakeEngine()}
    return TestClient(create_app(dict(e), default=next(iter(e)), secret=SECRET))


def test_reads_image_and_returns_lines_with_confidence() -> None:
    c = client()
    r = c.post(
        "/v1/vision/ocr",
        files={"image": ("p.png", png(), "image/png")},
        data={"engine": "auto", "lang": "ja"},
        headers=H,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["engine_used"] == "fake"
    assert body["text"].startswith("請求書")
    assert body["confidence"] == pytest.approx(0.95)
    assert body["lines"][0]["bbox"][2] == 200  # 受け取った画像の幅


def test_requires_internal_token() -> None:
    c = client()
    assert c.post("/v1/vision/ocr", files={"image": ("p.png", png(), "image/png")}).status_code == 401
    bad = {"X-Internal-Token": "wrong"}
    assert (
        c.post("/v1/vision/ocr", files={"image": ("p.png", png(), "image/png")}, headers=bad).status_code
        == 401
    )


def test_rejects_non_images_unknown_engines_and_huge_images() -> None:
    c = client()
    r = c.post("/v1/vision/ocr", files={"image": ("p.png", b"not an image", "image/png")}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_IMAGE"
    r = c.post(
        "/v1/vision/ocr", files={"image": ("p.png", png(), "image/png")}, data={"engine": "paddle"}, headers=H
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    # 画素数の上限（解凍爆弾対策）: 小さいファイルでも画素数が多すぎれば拒否する
    r = c.post("/v1/vision/ocr", files={"image": ("p.png", png(9000, 9000), "image/png")}, headers=H)
    assert r.status_code == 400


def test_engine_failure_is_retryable_503() -> None:
    c = client(fake=FakeEngine(fail=True))
    r = c.post("/v1/vision/ocr", files={"image": ("p.png", png(), "image/png")}, headers=H)
    assert r.status_code == 503
    assert r.json()["error"] == {
        "code": "ENGINE_UNAVAILABLE",
        "message": "（試験）エンジンの失敗",
        "retryable": True,
    }


def test_logs_do_not_contain_recognized_text(caplog: pytest.LogCaptureFixture) -> None:
    c = client(fake=FakeEngine(text="口座 1234567 平井丸"))
    with caplog.at_level(logging.DEBUG, logger="llm_ocr"):
        c.post("/v1/vision/ocr", files={"image": ("p.png", png(), "image/png")}, headers=H)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "1234567" not in joined and "平井丸" not in joined
    assert "size=200x100" in joined


@pytest.mark.anyio
async def test_vlm_engine_sends_image_to_openai_compatible_api() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "請求書\nTotal 1,000"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        eng = VlmEngine("http://vllm:8000/", "gemma-4-12b-it", client=http)
        r = await eng.read(Image.new("RGB", (10, 10)), "ja")
    assert seen["url"] == "http://vllm:8000/v1/chat/completions"
    content = seen["body"]["messages"][0]["content"]  # type: ignore[index]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert r.text == "請求書\nTotal 1,000" and r.confidence is None and len(r.lines) == 2


@pytest.mark.anyio
async def test_vlm_engine_errors_are_engine_errors() -> None:
    from llm_ocr.engines import EngineError

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as http:
        with pytest.raises(EngineError):
            await VlmEngine("http://vllm:8000", "m", client=http).read(Image.new("RGB", (10, 10)), "ja")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
