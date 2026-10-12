"""アプリの自動更新（ADM-05）: 管理者が版を登録し、ログインした利用者だけが最新の版を確かめて取れる。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from llm_gateway.updates import publish

from .gw_helpers import rig

pytestmark = pytest.mark.anyio


def test_publish_validates_and_writes_latest(tmp_path: Path) -> None:
    exe = tmp_path / "LocalLLMAgent-Setup-0.2.0.exe"
    exe.write_bytes(b"MZ" + b"\0" * 100)
    info = publish(tmp_path / "updates", exe, "0.2.0", "不具合の修正")
    assert info["sha512"] == hashlib.sha512(exe.read_bytes()).hexdigest()
    assert info["size"] == 102 and (tmp_path / "updates" / exe.name).exists()
    with pytest.raises(ValueError):
        publish(tmp_path / "updates", exe, "v2", "")
    bad = tmp_path / "evil;name.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError):
        publish(tmp_path / "updates", bad, "0.3.0", "")


async def test_latest_and_download(db_url: str, tmp_path: Path) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        h = r.h("alice")
        assert (await r.http.get("/v1/app/latest", headers=h)).json() == {"available": False}
        r.gw.settings.updates_dir = str(tmp_path / "updates")
        assert (await r.http.get("/v1/app/latest", headers=h)).json() == {"available": False}

        exe = tmp_path / "LocalLLMAgent-Setup-0.2.0.exe"
        exe.write_bytes(b"MZ-installer")
        publish(tmp_path / "updates", exe, "0.2.0", "表示の改善")
        (tmp_path / "updates" / "other.exe").write_bytes(b"old")
        latest = (await r.http.get("/v1/app/latest", headers=h)).json()
        assert (
            latest["available"] is True and latest["version"] == "0.2.0" and latest["notes"] == "表示の改善"
        )
        # ログインしていなければ取れない
        assert (await r.http.get("/v1/app/latest")).status_code == 401
        assert (await r.http.get(f"/v1/app/download/{exe.name}")).status_code == 401
        got = await r.http.get(f"/v1/app/download/{exe.name}", headers=h)
        assert got.status_code == 200 and got.content == b"MZ-installer"
        assert hashlib.sha512(got.content).hexdigest() == latest["sha512"]
        # 配っている版以外・フォルダの外は取れない
        for name in ("other.exe", "latest.json", "..%2F..%2Fgw.db", "..\\gw.db"):
            assert (await r.http.get(f"/v1/app/download/{name}", headers=h)).status_code == 404, name
