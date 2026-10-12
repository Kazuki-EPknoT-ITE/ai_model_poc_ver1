"""画像の入力（DOC-07 の画像の説明）: 画像を読めるモデルだけが受け付け、data: URI 以外・枚数超過は断る。"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from llm_gateway.config import EngineSettings, ModelSettings
from llm_gateway.engines import FakeEngine
from llm_gateway.util import estimate_tokens

from .gw_helpers import rig, wait_until

pytestmark = pytest.mark.anyio

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
MODELS = [
    ModelSettings(
        id="local-12b", label="高速", context_budget=4000, vision=True, engine=EngineSettings(kind="fake")
    ),
    ModelSettings(id="text-only", label="文字だけ", context_budget=4000, engine=EngineSettings(kind="fake")),
]


def _image_message(url: str = PNG, n: int = 1) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": "この画像を説明してください"}]
    parts += [{"type": "image_url", "image_url": {"url": url}}] * n
    return [{"role": "user", "content": parts}]


def test_estimate_tokens_counts_images_not_base64() -> None:
    msgs = _image_message(n=2)
    assert estimate_tokens(msgs, 1.5) == int(len("この画像を説明してください") / 1.5) + 600


async def test_images_go_to_vision_models_only(db_url: str) -> None:
    engine = FakeEngine()
    async with rig(db_url, engine=engine, models=MODELS) as r:
        await r.user("alice")
        h = {"Idempotency-Key": "k1"}
        ok = await r.http.post(
            "/v1/steps",
            json={
                "model": "local-12b",
                "messages": _image_message(),
                "purpose": "image_describe",
                "max_tokens": 64,
            },
            headers=r.h("alice", **h),
        )
        assert ok.status_code == 202, ok.text
        await wait_until(lambda: engine.messages_seen)
        content = engine.messages_seen[-1][0]["content"]
        assert content[1] == {"type": "image_url", "image_url": {"url": PNG}}

        async def post(model: str, messages: list[dict[str, Any]], key: str) -> Any:
            return await r.http.post(
                "/v1/steps",
                json={"model": model, "messages": messages, "max_tokens": 64},
                headers=r.h("alice", **{"Idempotency-Key": key}),
            )

        no_vision = await post("text-only", _image_message(), "k2")
        assert no_vision.status_code == 400 and "画像を読めません" in no_vision.json()["error"]["message"]
        assert no_vision.json()["error"]["retryable"] is False
        remote = await post("local-12b", _image_message("https://example.com/a.png"), "k3")
        assert remote.status_code == 400  # 外部の URL は取りに行かない
        svg = await post("local-12b", _image_message("data:image/svg+xml;base64,PHN2Zz4="), "k4")
        assert svg.status_code == 400
        many = await post("local-12b", _image_message(n=5), "k5")
        assert many.status_code == 400 and "4 枚まで" in many.json()["error"]["message"]
        # 文字だけの部品の配列は、画像を読めないモデルでも使える
        text_parts = await post(
            "text-only", [{"role": "user", "content": [{"type": "text", "text": "こんにちは"}]}], "k6"
        )
        assert text_parts.status_code == 202
