"""部品の単体試験: vLLM エンジン（guided json の切替）・暗号化・CLI。"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from llm_gateway.config import EngineSettings, load_settings
from llm_gateway.crypto import CryptoError, PayloadCipher, parse_payload_keys
from llm_gateway.engines import Delta, EngineRequest, EngineRequestError, EngineUnavailable, Final, VLLMEngine
from llm_gateway.util import new_ulid

SCHEMA = {"type": "object", "properties": {"type": {"const": "final"}}, "required": ["type"]}


def _req(rf: dict[str, object] | None = None) -> EngineRequest:
    return EngineRequest(
        job_id="j1",
        model="local-12b",
        messages=[{"role": "user", "content": "hi"}],
        response_format=rf if rf is not None else {"type": "json_schema", "schema": SCHEMA},
        max_tokens=32,
        temperature=0.1,
        purpose="agent",
    )


def _sse(*chunks: dict[str, object]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


@pytest.mark.parametrize(
    ("mode", "check"),
    [
        ("guided_json", lambda b: b["guided_json"] == SCHEMA and "response_format" not in b),
        (
            "json_schema",
            lambda b: (
                b["response_format"]["type"] == "json_schema"
                and b["response_format"]["json_schema"]["schema"] == SCHEMA
            ),
        ),
        ("structured_outputs", lambda b: b["structured_outputs"] == {"json": SCHEMA}),
    ],
)
@pytest.mark.anyio
async def test_vllm_guided_modes_and_stream(mode: str, check: object) -> None:
    seen: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/chat/completions"
        seen.append(json.loads(req.content))
        body = _sse(
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": '{"type":'}}]},
            {"choices": [{"delta": {"content": '"final"}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 4}},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    s = EngineSettings(kind="vllm", base_url="http://vllm:8000", served_model="gemma-qat", guided_mode=mode)  # type: ignore[arg-type]
    eng = VLLMEngine(s, transport=httpx.MockTransport(handler))
    chunks = [c async for c in eng.stream(_req())]
    await eng.aclose()
    assert [c.text for c in chunks if isinstance(c, Delta)] == ['{"type":', '"final"}']
    final = chunks[-1]
    assert isinstance(final, Final)
    assert (final.output, final.input_tokens, final.output_tokens, final.finish_reason) == (
        '{"type":"final"}',
        11,
        4,
        "stop",
    )
    body = seen[0]
    assert body["model"] == "gemma-qat" and body["stream"] is True
    assert check(body)  # type: ignore[operator]


@pytest.mark.anyio
async def test_vllm_errors() -> None:
    s = EngineSettings(kind="vllm", base_url="http://vllm:8000")

    async def run(handler: object) -> None:
        eng = VLLMEngine(s, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        try:
            async for _ in eng.stream(_req()):
                pass
        finally:
            await eng.aclose()

    with pytest.raises(EngineUnavailable):
        await run(lambda r: httpx.Response(503))
    with pytest.raises(EngineRequestError):
        await run(lambda r: httpx.Response(400, json={"error": "bad schema"}))

    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(EngineUnavailable):
        await run(refuse)

    def health(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if r.url.path == "/health" else 404)

    eng = VLLMEngine(s, transport=httpx.MockTransport(health))
    assert await eng.health() is True
    await eng.aclose()


def test_cipher_aad_tamper_and_rotation() -> None:
    k1, k2 = os.urandom(32), os.urandom(32)
    old = PayloadCipher({"k1": k1}, "k1")
    blob = old.encrypt("job-a", "request", "機密の依頼".encode())
    assert "機密".encode() not in blob
    # 別のジョブ・別の用途としては復号できない（AAD = job_id|用途）
    with pytest.raises(CryptoError):
        old.decrypt("job-b", "request", blob)
    with pytest.raises(CryptoError):
        old.decrypt("job-a", "response", blob)
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(CryptoError):
        old.decrypt("job-a", "request", tampered)
    # 同じ平文でも nonce が毎回違う
    assert old.encrypt("job-a", "request", b"x") != old.encrypt("job-a", "request", b"x")
    # 鍵のローテーション: 新しい鍵で書き、古い鍵のものも読める
    rotated = PayloadCipher({"k1": k1, "k2": k2}, "k2")
    assert rotated.decrypt("job-a", "request", blob) == "機密の依頼".encode()
    assert rotated.seal("job-c", "request", b"y").key_id == "k2"
    # 鍵を失ったら読めない
    with pytest.raises(CryptoError):
        PayloadCipher({"k2": k2}, "k2").decrypt("job-a", "request", blob)
    with pytest.raises(CryptoError):
        PayloadCipher({"k1": b"short"}, "k1")


def test_ulid_monotonic() -> None:
    ids = [new_ulid() for _ in range(2000)]
    assert ids == sorted(ids) and len(set(ids)) == 2000 and all(len(i) == 26 for i in ids)


def _cli(args: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 試験用に自分の CLI を起動する
        [sys.executable, "-m", "llm_gateway.cli", *args],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_cli_keygen_and_create_user(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    res = _cli(["keygen", "--out-dir", str(keys)], {}, tmp_path)
    assert res.returncode == 0, res.stderr
    pem = (keys / "jwt_ed25519.pem").read_bytes()
    assert b"PRIVATE KEY" in pem
    data = json.loads((keys / "payload_keys.json").read_text(encoding="utf-8"))
    assert data["active"] == "k1" and len(base64.b64decode(data["keys"]["k1"])) == 32
    assert _cli(["keygen", "--out-dir", str(keys)], {}, tmp_path).returncode == 2  # 上書きしない
    res = _cli(["keygen", "--out-dir", str(keys), "--rotate", "--key-id", "k2"], {}, tmp_path)
    assert res.returncode == 0, res.stderr
    rotated = parse_payload_keys((keys / "payload_keys.json").read_text(encoding="utf-8"))
    assert rotated.active == "k2" and set(rotated.key_ids) == {"k1", "k2"}

    db = (tmp_path / "gw.db").as_posix()
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        f"database: {{ url: 'sqlite+aiosqlite:///{db}' }}\n"
        "auth: { argon2: { memory_kib: 1024, time_cost: 1, parallelism: 1 } }\n"
        "models: [{ id: local-12b, label: 高速, engine: { kind: fake } }]\n",
        encoding="utf-8",
    )
    assert load_settings(cfg).models[0].engine.kind == "fake"
    env = {"LLMGW_NEW_PASSWORD": "initial-pass-1"}
    res = _cli(["create-user", "--config", str(cfg), "--login", "admin", "--role", "admin"], env, tmp_path)
    assert res.returncode == 0, res.stderr
    assert "created user" in res.stdout and "initial-pass-1" not in res.stdout + res.stderr
    dup = _cli(["create-user", "--config", str(cfg), "--login", "admin"], env, tmp_path)
    assert dup.returncode != 0


def test_example_configs_load() -> None:
    from llm_gateway.config import load_profile

    examples = Path(__file__).resolve().parents[1] / "examples"
    for name in ("gateway.yaml", "gateway.dev.yaml"):
        s = load_settings(examples / name)
        assert s.profile is not None
        prof = load_profile(s.resolve(s.profile))
        assert prof.instructions_body and [m.name for m in prof.macros] == ["請求書の読み取り", "月次締め"]
