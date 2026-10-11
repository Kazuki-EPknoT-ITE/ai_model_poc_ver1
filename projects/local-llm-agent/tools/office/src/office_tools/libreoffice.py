"""LibreOffice（soffice）のヘッドレス実行。

場所は環境変数 LIBREOFFICE_PATH、無ければ既定のインストール先、PATH 上の soffice の順に探す。
毎回使い捨てのユーザープロファイルを使い、xlsx/ods を開いたときに必ず再計算させる。
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404  (固定の実行ファイルを引数配列で起動する)
import tempfile
from pathlib import Path

from office_tools.errors import ToolFailure, failed

LIBREOFFICE_ENV = "LIBREOFFICE_PATH"
DEFAULT_PATHS = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
)
TIMEOUT_S = 170

_RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


def find_soffice() -> Path | None:
    env = os.environ.get(LIBREOFFICE_ENV)
    if env:
        p = Path(env)
        return p if p.is_file() else None
    for p in DEFAULT_PATHS:
        if p.is_file():
            return p
    found = shutil.which("soffice")
    return Path(found) if found else None


def convert(src: Path, target_ext: str, out_dir: Path) -> Path:
    """src を target_ext（"xlsx" "docx" "pdf"）に変換し、out_dir に書いたファイルを返す。"""
    soffice = find_soffice()
    if soffice is None:
        raise failed(
            "変換エンジン(LibreOffice)が見つかりません。管理者に LibreOffice の導入を依頼してください"
        )
    with tempfile.TemporaryDirectory(prefix="lla-lo-") as profile:
        user = Path(profile) / "user"
        user.mkdir(parents=True)
        (user / "registrymodifications.xcu").write_text(_RECALC_PROFILE, encoding="utf-8")
        cmd = [
            str(soffice),
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            target_ext,
            "--outdir",
            str(out_dir),
            str(src),
        ]
        try:
            proc = subprocess.run(  # noqa: S603
                cmd, capture_output=True, timeout=TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise ToolFailure("TIMEOUT", "LibreOffice の処理がタイムアウトしました") from e
        except OSError as e:
            raise failed(f"LibreOffice を起動できません（{type(e).__name__}）") from e
    out = out_dir / f"{src.stem}.{target_ext}"
    if proc.returncode != 0 or not out.exists():
        raise failed(f"LibreOffice での変換に失敗しました（終了コード {proc.returncode}）")
    return out
