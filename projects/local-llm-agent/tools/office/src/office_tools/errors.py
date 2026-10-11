"""ToolError（protocol §2.4）への変換。

ツールの実装は ToolFailure を投げ、サーバーが ToolResult の error に変換する。
例外をそのまま MCP の外へ出さない。
"""

from __future__ import annotations

from typing import Any

# code → kind（protocol §2.4 の表）
ERROR_KINDS: dict[str, str] = {
    "INVALID_ARGS": "permanent",
    "UNKNOWN_TOOL": "permanent",
    "NOT_FOUND": "permanent",
    "BLOCK_NOT_FOUND": "permanent",
    "STALE_DOCUMENT": "permanent",
    "CORRUPT_FILE": "permanent",
    "UNSUPPORTED_FORMAT": "permanent",
    "PASSWORD_REQUIRED": "deferred",
    "FORMULA_ERROR": "permanent",
    "RESULT_TOO_LARGE": "permanent",
    "FORBIDDEN_PATH": "policy",
    "FILE_LOCKED": "transient",
    "TIMEOUT": "transient",
    "TOOL_CRASHED": "transient",
    "TOOL_FAILED": "permanent",
}

DEFAULT_RETRY_MS = {"FILE_LOCKED": 5000, "TIMEOUT": 10000, "TOOL_CRASHED": 2000}


class ToolFailure(Exception):
    """ツールの失敗。ToolResult.error に変換される。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        if code not in ERROR_KINDS:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.kind = ERROR_KINDS[code]
        self.message = message[:1000]
        self.detail = detail
        self.retry_after_ms = retry_after_ms if retry_after_ms is not None else DEFAULT_RETRY_MS.get(code)

    def to_error(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "kind": self.kind, "message": self.message}
        if self.kind == "transient" and self.retry_after_ms is not None:
            err["retry_after_ms"] = self.retry_after_ms
        if self.detail:
            err["detail"] = self.detail
        return err


def invalid(message: str, **detail: Any) -> ToolFailure:
    return ToolFailure("INVALID_ARGS", message, detail=detail or None)


def not_found(path: str) -> ToolFailure:
    return ToolFailure(
        "NOT_FOUND",
        f"{path} は存在しません。fs.list で確認してください",
        detail={"path": path},
    )


def corrupt(path: str, reason: str = "") -> ToolFailure:
    msg = "このファイルは壊れているため読めません。この件は失敗として記録してください"
    detail: dict[str, Any] = {"path": path}
    if reason:
        detail["reason"] = reason[:300]
    return ToolFailure("CORRUPT_FILE", msg, detail=detail)


def unsupported(path: str, hint: str) -> ToolFailure:
    return ToolFailure("UNSUPPORTED_FORMAT", hint, detail={"path": path})


def block_not_found(block_id: str) -> ToolFailure:
    return ToolFailure(
        "BLOCK_NOT_FOUND",
        f"{block_id} はありません。docx.read で最新の ID を取得してください",
        detail={"block_id": block_id},
    )


def locked(path: str) -> ToolFailure:
    return ToolFailure(
        "FILE_LOCKED",
        f"{path} は他のアプリで開かれているため書き込めません",
        detail={"path": path},
    )


def failed(message: str, **detail: Any) -> ToolFailure:
    return ToolFailure("TOOL_FAILED", f"この操作は失敗しました。理由: {message}", detail=detail or None)
