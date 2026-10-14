"""Ollama エンジン（開発 PC で手元のモデルを試す用）の単体試験。"""

from __future__ import annotations

import json

import httpx
import pytest

from llm_gateway.config import EngineSettings
from llm_gateway.engines import (
    Delta,
    EngineRequest,
    EngineRequestError,
    EngineUnavailable,
    Final,
    OllamaEngine,
    build_engine,
)

SCHEMA = {"type": "object", "properties": {"type": {"const": "final"}}, "required": ["type"]}
S = EngineSettings(kind="ollama", base_url="http://127.0.0.1:11434", served_model="gemma4:e4b", num_ctx=16384)


def _req(
    messages: list[dict[str, object]] | None = None, rf: dict[str, object] | None = None
) -> EngineRequest:
    return EngineRequest(
        job_id="j1",
        model="local-12b",
        messages=messages or [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        response_format=rf if rf is not None else {"type": "json_schema", "schema": SCHEMA},
        max_tokens=64,
        temperature=0.0,
        purpose="agent",
    )


def _ndjson(*objs: dict[str, object]) -> bytes:
    return "".join(json.dumps(o) + "\n" for o in objs).encode()


async def _collect(eng: OllamaEngine, req: EngineRequest) -> list[object]:
    try:
        return [c async for c in eng.stream(req)]
    finally:
        await eng.aclose()


@pytest.mark.anyio
async def test_ollama_stream_and_body() -> None:
    seen: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            content=_ndjson(
                {"message": {"role": "assistant", "content": '{"type":'}, "done": False},
                {"message": {"role": "assistant", "content": '"final"}'}, "done": False},
                {
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 12,
                    "eval_count": 5,
                },
            ),
        )

    chunks = await _collect(OllamaEngine(S, transport=httpx.MockTransport(handler)), _req())
    assert [c.text for c in chunks if isinstance(c, Delta)] == ['{"type":', '"final"}']
    final = chunks[-1]
    assert isinstance(final, Final)
    assert (final.output, final.input_tokens, final.output_tokens, final.finish_reason) == (
        '{"type":"final"}',
        12,
        5,
        "stop",
    )
    body = seen[0]
    assert body["model"] == "gemma4:e4b" and body["stream"] is True and body["think"] is False
    assert body["format"] == SCHEMA
    assert body["options"] == {"temperature": 0.0, "num_predict": 64, "num_ctx": 16384}


@pytest.mark.anyio
async def test_ollama_images_and_length() -> None:
    seen: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(
            200, content=_ndjson({"message": {"content": "x"}, "done": True, "done_reason": "length"})
        )

    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "この画像は？"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ],
    }
    chunks = await _collect(
        OllamaEngine(S, transport=httpx.MockTransport(handler)), _req([msg], rf={"type": "json_object"})
    )
    assert seen[0]["messages"] == [{"role": "user", "content": "この画像は？", "images": ["QUJD"]}]
    assert seen[0]["format"] == "json"
    final = chunks[-1]
    assert isinstance(final, Final) and final.finish_reason == "length"


@pytest.mark.anyio
async def test_ollama_errors_and_health() -> None:
    async def run(handler: object) -> None:
        await _collect(OllamaEngine(S, transport=httpx.MockTransport(handler)), _req())  # type: ignore[arg-type]

    with pytest.raises(EngineUnavailable):
        await run(lambda r: httpx.Response(503))
    with pytest.raises(EngineRequestError):  # モデルが入っていない
        await run(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(EngineUnavailable):
        await run(lambda r: httpx.Response(200, content=_ndjson({"error": "runner crashed"})))

    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(EngineUnavailable):
        await run(refuse)
    eng = OllamaEngine(
        S,
        transport=httpx.MockTransport(lambda r: httpx.Response(200 if r.url.path == "/api/version" else 404)),
    )
    assert await eng.health()
    with pytest.raises(EngineRequestError, match="LoRA"):
        await eng.activate_lora("x", "/p")
    await eng.activate_lora(None, None)
    await eng.aclose()


def test_large_max_items_are_removed_for_llama_grammar() -> None:
    # llama.cpp の文法変換は大きな maxItems で失敗する（HTTP 400 failed to parse grammar）
    schema = {
        "oneOf": [
            {"type": "array", "maxItems": 5000, "minItems": 1, "items": {"type": "string", "maxLength": 200}},
            {"type": "array", "maxItems": 20},
        ]
    }
    body = OllamaEngine(S).build_body(_req(rf={"type": "json_schema", "schema": schema}))
    assert body["format"] == {
        "oneOf": [
            {"type": "array", "minItems": 1, "items": {"type": "string", "maxLength": 200}},
            {"type": "array", "maxItems": 20},
        ]
    }


def test_build_engine_and_settings() -> None:
    assert isinstance(build_engine(S), OllamaEngine)
    with pytest.raises(ValueError, match="served_model"):
        OllamaEngine(EngineSettings(kind="ollama", base_url="http://x"))
    with pytest.raises(ValueError):
        EngineSettings(kind="ollama", num_ctx=100)
