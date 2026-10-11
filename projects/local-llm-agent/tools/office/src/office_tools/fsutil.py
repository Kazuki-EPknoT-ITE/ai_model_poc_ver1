"""パスの解決・ハッシュ・一時ファイル経由の置き換え。

ポリシー判定・実体パスの解決・バックアップは Runtime の責務。ここでは引数のパスを
そのまま（作業フォルダ基準で）扱う。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from office_tools.errors import ToolFailure, invalid, locked, not_found

WORKSPACE_ENV = "OFFICE_TOOLS_WORKSPACE"


def workspace() -> Path:
    ws = os.environ.get(WORKSPACE_ENV)
    return Path(ws) if ws else Path.cwd()


def normalize_arg(path: str) -> str:
    return unicodedata.normalize("NFC", path)


def resolve(path: str) -> Path:
    """引数のパス → 実際のパス。相対パスは OFFICE_TOOLS_WORKSPACE（無ければカレント）基準。"""
    p = normalize_arg(path).replace("/", os.sep).replace("\\", os.sep)
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = workspace() / candidate
    return candidate


def existing_file(path: str) -> Path:
    real = resolve(path)
    if not real.exists():
        raise not_found(path)
    if real.is_dir():
        raise invalid(f"{path} はフォルダです。ファイルを指定してください", path=path)
    return real


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except PermissionError as e:
        raise locked(str(path.name)) from e
    return h.hexdigest()


def check_expect_hash(real: Path, expect_hash: str | None) -> None:
    if expect_hash is None:
        return
    if sha256_file(real) != expect_hash:
        raise ToolFailure(
            "STALE_DOCUMENT",
            "ファイルが更新されています。読み直してから編集してください",
        )


def ensure_absent(path: str, real: Path, overwrite: bool) -> None:
    if real.exists() and not overwrite:
        raise invalid(
            f"{path} は既に存在します。上書きする場合は overwrite: true を指定するか、別の名前にしてください",
            path=path,
        )


def atomic_write(path_arg: str, real: Path, writer: Callable[[Path], object]) -> dict[str, Any]:
    """同じフォルダの一時ファイルに書いてから os.replace する。Artifact を返す。"""
    pre_hash = sha256_file(real) if real.exists() else None
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_name(f".~{real.stem[:40]}.{secrets.token_hex(4)}.tmp{real.suffix}")
    try:
        writer(tmp)
        try:
            os.replace(tmp, real)
        except PermissionError as e:
            raise locked(path_arg) from e
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
    artifact: dict[str, Any] = {
        "path": normalize_arg(path_arg),
        "op": "overwrite" if pre_hash else "create",
        "post_hash": sha256_file(real),
    }
    if pre_hash:
        artifact["pre_hash"] = pre_hash
    return artifact


def ext(real: Path) -> str:
    return real.suffix.lower()
