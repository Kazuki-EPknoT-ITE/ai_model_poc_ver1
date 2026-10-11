"""office-tools のテスト共通部品（conftest から移した。全体で pytest を回すと conftest の名前が衝突するため）。テスト用の文書はすべてここ（とテスト内）で合成する。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest

import lla_protocol
from office_tools import libreoffice
from office_tools.envelope import ContinuationStore
from office_tools.registry import SamplingRequest, ToolContext, call

REAL_SOFFICE = libreoffice.find_soffice()
needs_libreoffice = pytest.mark.skipif(
    REAL_SOFFICE is None,
    reason="LibreOffice が未導入のため（LIBREOFFICE_PATH か C:\\Program Files\\LibreOffice に soffice.exe が必要）",
)


def check_envelope(name: str, env: dict[str, Any]) -> None:
    """ToolResult の封筒と、成功時は data が tools.json の result_schema に合うこと。"""
    lla_protocol.validate("ToolResult", env)
    if env["ok"]:
        schema = lla_protocol.tool(name)["result_schema"]
        jsonschema.validate(env["data"], schema)
    assert env["tokens_estimate"] > 0


class FakeSampler:
    """MCP sampling の代わり。purpose ごとの応答を返し、依頼を記録する。"""

    def __init__(self, replies: dict[str, Callable[[SamplingRequest], str] | str]) -> None:
        self.replies = replies
        self.requests: list[SamplingRequest] = []

    async def __call__(self, request: SamplingRequest) -> str:
        self.requests.append(request)
        reply = self.replies[request.purpose]
        return reply(request) if callable(reply) else reply


class Runner:
    def __init__(self) -> None:
        self.store = ContinuationStore()
        self.sampler: FakeSampler | None = None
        self.passwords: dict[str, str] = {}

    def __call__(self, name: str, args: dict[str, Any], *, expect_ok: bool | None = True) -> dict[str, Any]:
        ctx = ToolContext(store=self.store, sampler=self.sampler, passwords=self.passwords)

        async def go() -> dict[str, Any]:
            return await call(ctx, name, args)

        env = anyio.run(go)
        check_envelope(name, env)
        if expect_ok is True:
            assert env["ok"], env.get("error")
        elif expect_ok is False:
            assert not env["ok"], env
        return env

    def error(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        env = self(name, args, expect_ok=False)
        err: dict[str, Any] = env["error"]
        return err


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """作業フォルダ（OFFICE_TOOLS_WORKSPACE）。既定では LibreOffice を「無い」ことにする。"""
    monkeypatch.setenv("OFFICE_TOOLS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OFFICE_TOOLS_EXTRACT_DIR", str(tmp_path / "_extract"))
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    return tmp_path


@pytest.fixture
def run(ws: Path) -> Iterator[Runner]:
    yield Runner()
