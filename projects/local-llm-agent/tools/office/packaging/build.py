"""office-tools を PyInstaller で1フォルダにまとめる（実装計画 T-19）。

使い方（projects/local-llm-agent で）:
    uv run --with pyinstaller python tools/office/packaging/build.py
出力: tools/office/dist/office-tools/office-tools.exe（dist/ は git の対象外）
その後、MCP の tools/list が返るかを自動で確かめる。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICE = HERE.parent
DIST = OFFICE / "dist"
WORK = OFFICE / "build-pyinstaller"


def build() -> Path:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",  # MCP は標準入出力で話すのでコンソールアプリにする（起動側で窓は隠す）
        "--name",
        "office-tools",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        "--specpath",
        str(WORK),
        # プロトコルのスキーマ（JSON）とネイティブの DLL を含むパッケージは丸ごと取り込む
        "--collect-all",
        "lla_protocol",
        "--collect-all",
        "pypdfium2",
        "--collect-all",
        "pypdfium2_raw",
        "--collect-submodules",
        "office_tools",
        # mcp は CLI（typer が必要）を除き、サーバーと共通部分だけを取り込む
        "--collect-submodules",
        "mcp.server",
        "--collect-submodules",
        "mcp.shared",
        "--collect-data",
        "docx",
        "--collect-data",
        "openpyxl",
        str(HERE / "entry.py"),
    ]
    subprocess.run(args, check=True)  # noqa: S603 - 固定の引数
    shutil.rmtree(WORK, ignore_errors=True)
    exe = DIST / "office-tools" / "office-tools.exe"
    if not exe.exists():
        raise SystemExit(f"build failed: {exe} not found")
    return exe


def smoke(exe: Path) -> int:
    """MCP の initialize → tools/list を送り、ツール数を確かめる。"""
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in msgs)
    p = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )  # noqa: S603
    try:
        out, _err = p.communicate(stdin, timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _err = p.communicate()
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            n = len(msg["result"]["tools"])
            print(f"smoke: tools/list returned {n} tools")
            return n
    raise SystemExit("smoke failed: no tools/list response")


if __name__ == "__main__":
    exe = build()
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"built {exe} ({size:.0f} MB)")
    if smoke(exe) != 32:
        raise SystemExit("unexpected tool count")
