"""ツールの登録・呼び出し（引数の検証・continuation・エラー変換）。"""

from __future__ import annotations

import base64
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anyio.to_thread

from lla_protocol import ValidationError, load_tools, validate_tool_args
from office_tools.envelope import ContinuationStore, Outcome, failure, resume, success
from office_tools.errors import ToolFailure

log = logging.getLogger("office_tools")

NAMESPACES = ("docx", "xlsx", "csv", "data", "pdf", "invoice", "convert")


@dataclass
class SamplingRequest:
    """Runtime（MCP クライアント）への推論依頼。"""

    purpose: str
    text: str | None = None
    image_png: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 2000
    system_prompt: str | None = None

    def image_b64(self) -> str:
        return base64.b64encode(self.image_png or b"").decode("ascii")


class Sampler(Protocol):
    async def __call__(self, request: SamplingRequest) -> str: ...


@dataclass
class ToolContext:
    store: ContinuationStore
    sampler: Sampler | None = None
    passwords: dict[str, str] = field(default_factory=dict)

    async def sample(self, request: SamplingRequest) -> str:
        if self.sampler is None:
            raise ToolFailure(
                "TOOL_FAILED",
                "この操作は失敗しました。理由: 推論（sampling）を依頼できるクライアントに接続されていません",
            )
        return await self.sampler(request)

    def password(self, ref: str | None) -> str | None:
        # 利用者が入力していない参照（モデルが要らないのに付けた・作った参照）は、パスワードなしとして扱う。
        # 本当にパスワード付きなら、開くときに PASSWORD_REQUIRED になる（pdflib）。パスワードの無い PDF で、
        # 作り話の参照のために利用者へパスワードを尋ねて止まらないようにする
        if ref is None or ref not in self.passwords:
            return None
        return self.passwords[ref]


SyncHandler = Callable[[ToolContext, dict[str, Any]], Outcome]
AsyncHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[Outcome]]


@dataclass
class Entry:
    name: str
    sync: SyncHandler | None = None
    async_: AsyncHandler | None = None


REGISTRY: dict[str, Entry] = {}


def tool(name: str) -> Callable[[SyncHandler], SyncHandler]:
    def deco(fn: SyncHandler) -> SyncHandler:
        REGISTRY[name] = Entry(name, sync=fn)
        return fn

    return deco


def async_tool(name: str) -> Callable[[AsyncHandler], AsyncHandler]:
    def deco(fn: AsyncHandler) -> AsyncHandler:
        REGISTRY[name] = Entry(name, async_=fn)
        return fn

    return deco


def office_specs() -> list[dict[str, Any]]:
    """tools.json のうち office-tools 担当分。"""
    return [spec for name, spec in sorted(load_tools().items()) if name.split(".", 1)[0] in NAMESPACES]


def _validation_message(err: ValidationError) -> str:
    where = "/".join(str(p) for p in err.absolute_path) or "(引数全体)"
    return f"引数が不正です（{where}）: {err.message[:300]}"


async def call(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """1回のツール呼び出し。必ず ToolResult（dict）を返す。"""
    started = time.monotonic()
    try:
        env = success(await _call(ctx, name, args))
    except ToolFailure as e:
        env = failure(e)
    except Exception as e:  # 想定外の失敗も封筒にする（本文はログに出さない）
        # 例外のメッセージや traceback には文書の中身が含まれうるので、型と発生箇所だけを出す
        frames = traceback.extract_tb(e.__traceback__)
        where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "-"
        log.error("tool=%s unexpected error type=%s at=%s", name, type(e).__name__, where)
        env = failure(
            ToolFailure("TOOL_FAILED", f"この操作は失敗しました。理由: 内部エラー（{type(e).__name__}）")
        )
    code = env.get("error", {}).get("code", "-")
    log.info(
        "tool=%s ok=%s code=%s ms=%d tokens=%d",
        name,
        env["ok"],
        code,
        (time.monotonic() - started) * 1000,
        env["tokens_estimate"],
    )
    return env


def ensure_loaded() -> None:
    """ツールのモジュールを読み込んでレジストリに登録する。"""
    import office_tools.tools  # noqa: F401


async def _call(ctx: ToolContext, name: str, args: dict[str, Any]) -> Outcome:
    ensure_loaded()
    entry = REGISTRY.get(name)
    if entry is None or name.split(".", 1)[0] not in NAMESPACES:
        raise ToolFailure("UNKNOWN_TOOL", f"{name} はこのツールサーバーにはありません")
    try:
        validate_tool_args(name, args)
    except ValidationError as e:
        raise ToolFailure("INVALID_ARGS", _validation_message(e)) from e
    if "continuation" in args:
        return resume(ctx.store, name, str(args["continuation"]))
    if entry.async_ is not None:
        return await entry.async_(ctx, args)
    assert entry.sync is not None
    fn = entry.sync
    return await anyio.to_thread.run_sync(lambda: fn(ctx, args))
