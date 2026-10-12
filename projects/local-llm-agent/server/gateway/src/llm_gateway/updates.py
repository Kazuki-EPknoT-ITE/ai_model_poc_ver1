"""アプリの自動更新（ADM-05）: デスクトップアプリの新しい版を Gateway から配る。

- 管理者が `llm-gateway publish-update --config … --file Setup.exe --version 0.2.0` で登録する
  （`updates_dir` にインストーラーを置き、sha512・大きさ・版を `latest.json` に書く）。
- アプリはログイン中のトークンで `GET /v1/app/latest` を見て、新しければ `GET /v1/app/download/<ファイル名>` で取り、
  sha512 を確かめてからインストールする（社内の TLS の上で、社外には出ない）。
"""

# from __future__ import annotations は使わない（関数内で作る依存性を FastAPI が解決できなくなるため）
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .util import ApiError

if TYPE_CHECKING:
    from .gateway import Gateway

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.exe$")
VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,6}$")


def sha512_file(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(updates_dir: Path, file: Path, version: str, notes: str = "") -> dict[str, Any]:
    """インストーラーを配る場所に置き、latest.json を書き換える（書き換えは最後に1回だけ）。"""
    if not VERSION.match(version):
        raise ValueError("版は 0.2.0 の形で指定してください")
    if not SAFE_NAME.match(file.name):
        raise ValueError("ファイル名は英数字・._- の .exe にしてください")
    updates_dir.mkdir(parents=True, exist_ok=True)
    target = updates_dir / file.name
    if file.resolve() != target.resolve():
        shutil.copyfile(file, target)
    info = {
        "version": version,
        "file": file.name,
        "sha512": sha512_file(target),
        "size": target.stat().st_size,
        "notes": notes[:2000],
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = updates_dir / "latest.json.part"
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(updates_dir / "latest.json")
    return info


def _latest(gw: "Gateway") -> tuple[Path, dict[str, Any]] | None:
    if not gw.settings.updates_dir:
        return None
    d = gw.settings.resolve(gw.settings.updates_dir)
    try:
        info: dict[str, Any] = json.loads((d / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d, info


def register_updates(app: FastAPI, gw: "Gateway", user_dep: Any) -> None:
    from typing import Annotated

    from .auth import Principal

    UserDep = Annotated[Principal, user_dep]

    @app.get("/v1/app/latest")
    async def latest(_p: UserDep) -> dict[str, Any]:
        found = _latest(gw)
        if found is None:
            return {"available": False}
        _, info = found
        return {
            "available": True,
            **{k: info[k] for k in ("version", "file", "sha512", "size", "notes", "published_at")},
        }

    @app.get("/v1/app/download/{name}")
    async def download(name: str, _p: UserDep) -> FileResponse:
        found = _latest(gw)
        # 配っている版のファイルだけを返す（名前の形も確かめ、フォルダの外には出ない）
        if found is None or name != found[1].get("file") or not SAFE_NAME.match(name):
            raise ApiError(404, "NOT_FOUND", "配布しているファイルではありません")
        path = found[0] / name
        if not path.is_file():
            raise ApiError(404, "NOT_FOUND", "配布しているファイルではありません")
        return FileResponse(path, media_type="application/octet-stream", filename=name)
