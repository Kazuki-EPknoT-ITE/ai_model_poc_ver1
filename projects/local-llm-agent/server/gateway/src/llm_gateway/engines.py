"""推論エンジンの抽象（InferenceEngine）と実装（vLLM / 開発 PC 用の Ollama / 試験用の FakeEngine）。

エンジンが落ちた・接続できない場合は EngineUnavailable を投げる。スケジューラは
running のジョブを queued に戻し、SSE に requeued を送る（local_llm_api.md §4）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import EngineSettings


@dataclass
class EngineRequest:
    job_id: str
    model: str  # Gateway のモデル ID（local-12b など）
    messages: list[dict[str, Any]]
    response_format: dict[str, Any] | None
    max_tokens: int
    temperature: float
    purpose: str
    stream: bool = True


@dataclass
class Delta:
    text: str


@dataclass
class Final:
    output: str
    input_tokens: int
    output_tokens: int
    finish_reason: str


Chunk = Delta | Final


class EngineUnavailable(Exception):
    """エンジンの停止・再起動・接続断（再投入すれば直る）。"""


class EngineRequestError(Exception):
    """依頼そのものが受け付けられない（再投入しても直らない）。本文はメッセージに入れない。"""


class InferenceEngine(Protocol):
    def stream(self, req: EngineRequest) -> AsyncIterator[Chunk]:
        """Delta を順に返し、最後に Final を1つ返す。"""
        ...

    async def health(self) -> bool: ...

    async def activate_lora(self, name: str | None, path: str | None) -> None:
        """モデル台帳の本番版を反映する（name が None なら基本の設定に戻す）。"""
        ...

    async def aclose(self) -> None: ...


# ---- vLLM（OpenAI 互換 /v1/chat/completions） ----


class VLLMEngine:
    def __init__(self, s: EngineSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not s.base_url:
            raise ValueError("vllm エンジンには base_url が必要です")
        self.s = s
        self.active_model: str | None = None  # モデル台帳で本番化した LoRA の名前
        headers = {}
        if s.api_key_env and (key := os.environ.get(s.api_key_env)):
            headers["Authorization"] = f"Bearer {key}"
        self.client = httpx.AsyncClient(
            base_url=s.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(s.timeout_s, connect=s.connect_timeout_s),
            transport=transport,
        )

    def build_body(self, req: EngineRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.active_model or self.s.served_model or req.model,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        rf = req.response_format
        if rf is not None:
            schema = rf.get("schema") if rf.get("type") == "json_schema" else None
            if schema is None and isinstance(rf.get("json_schema"), dict):
                schema = rf["json_schema"].get("schema")
            if schema is not None:
                if self.s.guided_mode == "guided_json":
                    body["guided_json"] = schema
                elif self.s.guided_mode == "structured_outputs":
                    body["structured_outputs"] = {"json": schema}
                else:
                    body["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": "action", "schema": schema, "strict": True},
                    }
            elif rf.get("type") == "json_object":
                body["response_format"] = {"type": "json_object"}
        return body

    async def activate_lora(self, name: str | None, path: str | None) -> None:
        """vLLM（--enable-lora）に LoRA を読み込ませ、以後の推論をその名前で行う。"""
        if name is None:
            self.active_model = None
            return
        if path:
            res = await self.client.post("/v1/load_lora_adapter", json={"lora_name": name, "lora_path": path})
            # 400 は「読み込み済み」のことがあるので、本文に already が含まれていれば成功とみなす
            if res.status_code >= 400 and not (res.status_code == 400 and "already" in res.text.lower()):
                raise EngineRequestError(f"vLLM load_lora_adapter HTTP {res.status_code}")
        self.active_model = name

    async def stream(self, req: EngineRequest) -> AsyncIterator[Chunk]:
        body = self.build_body(req)
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish = "stop"
        try:
            async with self.client.stream("POST", "/v1/chat/completions", json=body) as res:
                if res.status_code >= 500 or res.status_code in (404, 429):
                    raise EngineUnavailable(f"vLLM HTTP {res.status_code}")
                if res.status_code >= 400:
                    raise EngineRequestError(f"vLLM HTTP {res.status_code}")
                async for line in res.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    for choice in obj.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            parts.append(delta)
                            yield Delta(delta)
                        if choice.get("finish_reason"):
                            finish = str(choice["finish_reason"])
        except httpx.TransportError as e:
            raise EngineUnavailable(type(e).__name__) from None
        output = "".join(parts)
        yield Final(
            output=output,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or max(1, len(output) // 3)),
            finish_reason=finish,
        )

    async def health(self) -> bool:
        try:
            res = await self.client.get("/health", timeout=3.0)
        except httpx.HTTPError:
            return False
        return res.status_code == 200

    async def aclose(self) -> None:
        await self.client.aclose()


# ---- Ollama（/api/chat、開発 PC で手元のモデルを試す用） ----


def _response_schema(rf: dict[str, Any] | None) -> dict[str, Any] | str | None:
    if rf is None:
        return None
    schema = rf.get("schema") if rf.get("type") == "json_schema" else None
    if schema is None and isinstance(rf.get("json_schema"), dict):
        schema = rf["json_schema"].get("schema")
    if isinstance(schema, dict):
        return schema
    return "json" if rf.get("type") == "json_object" else None


# Ollama（llama.cpp）は JSON Schema を文法に変換する。配列の上限（maxItems）が大きいと、繰り返しを
# 展開した文法が大きくなりすぎて「failed to parse grammar」（HTTP 400）になる（Action のスキーマには
# 2000〜100000 の上限がある）。この値を超える上限だけを外す。外した上限は Runtime の形式の検証で確かめ、
# 超えていれば出力の形式エラーとしてやり直す。
OLLAMA_MAX_ITEMS = 100


def _relax_for_llama(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: _relax_for_llama(v)
            for k, v in node.items()
            if not (k == "maxItems" and isinstance(v, int) and v > OLLAMA_MAX_ITEMS)
        }
    if isinstance(node, list):
        return [_relax_for_llama(v) for v in node]
    return node


def _ollama_message(m: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 形式の1メッセージを Ollama の形にする（画像の部品は base64 の images に移す）。"""
    content = m.get("content")
    if not isinstance(content, list):
        return {"role": m.get("role"), "content": content or ""}
    texts: list[str] = []
    images: list[str] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            texts.append(str(p.get("text", "")))
        elif p.get("type") == "image_url":
            url = str((p.get("image_url") or {}).get("url", ""))
            if url.startswith("data:") and "," in url:
                images.append(url.split(",", 1)[1])
    out: dict[str, Any] = {"role": m.get("role"), "content": "\n".join(texts)}
    if images:
        out["images"] = images
    return out


class OllamaEngine:
    """Ollama の /api/chat（NDJSON のストリーム）。構造化出力は format に JSON Schema を渡す。

    本番の推論は vLLM（設計書 §6.2）。これは GPU の検証機が無い開発 PC で、手元のモデルで
    アプリを試すためのもの。LoRA の切り替えは扱わない。
    """

    def __init__(self, s: EngineSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not s.base_url or not s.served_model:
            raise ValueError("ollama エンジンには base_url と served_model（Ollama のモデル名）が必要です")
        self.s = s
        self.client = httpx.AsyncClient(
            base_url=s.base_url.rstrip("/"),
            timeout=httpx.Timeout(s.timeout_s, connect=s.connect_timeout_s),
            transport=transport,
        )

    def build_body(self, req: EngineRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": req.temperature, "num_predict": req.max_tokens}
        if self.s.num_ctx:
            options["num_ctx"] = self.s.num_ctx
        body: dict[str, Any] = {
            "model": self.s.served_model,
            "messages": [_ollama_message(m) for m in req.messages],
            "stream": True,
            "think": self.s.think,
            "options": options,
        }
        fmt = _response_schema(req.response_format)
        if fmt is not None:
            body["format"] = _relax_for_llama(fmt)
        return body

    async def activate_lora(self, name: str | None, path: str | None) -> None:
        if name is not None:
            raise EngineRequestError("ollama エンジンは LoRA の切り替えに対応していません")

    async def stream(self, req: EngineRequest) -> AsyncIterator[Chunk]:
        body = self.build_body(req)
        parts: list[str] = []
        last: dict[str, Any] = {}
        try:
            async with self.client.stream("POST", "/api/chat", json=body) as res:
                if res.status_code >= 500 or res.status_code == 429:
                    raise EngineUnavailable(f"Ollama HTTP {res.status_code}")
                if res.status_code >= 400:
                    # 404 はモデルが入っていない（ollama pull が要る）。再投入しても直らない
                    raise EngineRequestError(f"Ollama HTTP {res.status_code}")
                async for line in res.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        raise EngineUnavailable("Ollama error")
                    text = (obj.get("message") or {}).get("content")
                    if text:
                        parts.append(text)
                        yield Delta(text)
                    if obj.get("done"):
                        last = obj
                        break
        except httpx.TransportError as e:
            raise EngineUnavailable(type(e).__name__) from None
        output = "".join(parts)
        reason = str(last.get("done_reason") or "stop")
        yield Final(
            output=output,
            input_tokens=int(last.get("prompt_eval_count") or 0),
            output_tokens=int(last.get("eval_count") or max(1, len(output) // 3)),
            finish_reason="length" if reason == "length" else "stop",
        )

    async def health(self) -> bool:
        try:
            res = await self.client.get("/api/version", timeout=3.0)
        except httpx.HTTPError:
            return False
        return res.status_code == 200

    async def aclose(self) -> None:
        await self.client.aclose()


# ---- 試験用 ----


@dataclass
class FakeScript:
    """1回の呼び出しの振る舞い（FakeEngine.queue で順に消費する）。"""

    output: str | None = None
    token_delay_s: float | None = None
    fail_after_tokens: int | None = None  # この件数を送ってから EngineUnavailable
    request_error: bool = False
    hang: bool = False  # 取り消されるまで返さない
    gate: asyncio.Event | None = None  # 指定すると、セットされるまで開始しない


@dataclass
class FakeEngine:
    """決め打ちの応答・遅延・失敗注入。呼ばれた順番を calls に残す（公平性の試験用）。"""

    output: str = '{"type":"final","summary":"(fake) done","outputs":[],"failures":[],"needs_review":[]}'
    token_delay_s: float = 0.0
    chunk_chars: int = 12
    responder: Callable[[EngineRequest], str] | None = None
    scripts: list[FakeScript] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # job_id
    messages_seen: list[list[dict[str, Any]]] = field(default_factory=list)  # 受け取った messages（試験用）
    finished: list[str] = field(default_factory=list)
    healthy: bool = True
    active_lora: str | None = None
    lora_activations: list[tuple[str | None, str | None]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0

    @classmethod
    def from_settings(cls, s: EngineSettings) -> FakeEngine:
        eng = cls(token_delay_s=s.fake_token_delay_s, chunk_chars=s.fake_chunk_chars)
        if s.fake_output is not None:
            eng.output = s.fake_output
        return eng

    def queue(self, *scripts: FakeScript) -> None:
        self.scripts.extend(scripts)

    async def stream(self, req: EngineRequest) -> AsyncIterator[Chunk]:
        if not self.healthy:
            raise EngineUnavailable("fake engine is down")
        script = self.scripts.pop(0) if self.scripts else FakeScript()
        self.calls.append(req.job_id)
        self.messages_seen.append(req.messages)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if script.gate is not None:
                await script.gate.wait()
            if script.request_error:
                raise EngineRequestError("fake request error")
            if script.hang:
                await asyncio.Event().wait()
            output = script.output
            if output is None:
                output = self.responder(req) if self.responder else self.output
            delay = script.token_delay_s if script.token_delay_s is not None else self.token_delay_s
            chunks = [output[i : i + self.chunk_chars] for i in range(0, len(output), self.chunk_chars)]
            for n, c in enumerate(chunks):
                if script.fail_after_tokens is not None and n >= script.fail_after_tokens:
                    raise EngineUnavailable("fake engine crashed")
                if delay:
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(0)
                yield Delta(c)
            self.finished.append(req.job_id)
            chars = sum(len(str(m.get("content", ""))) for m in req.messages)
            yield Final(output, int(chars / 1.5), max(1, len(output) // 3), "stop")
        finally:
            self.active -= 1

    async def health(self) -> bool:
        return self.healthy

    async def activate_lora(self, name: str | None, path: str | None) -> None:
        self.active_lora = name
        self.lora_activations.append((name, path))

    async def aclose(self) -> None:
        return None


def build_engine(s: EngineSettings) -> InferenceEngine:
    if s.kind == "fake":
        return FakeEngine.from_settings(s)
    if s.kind == "ollama":
        return OllamaEngine(s)
    return VLLMEngine(s)
