"""Electron 本体を公式リリースから取得して node_modules/electron/dist に展開する。

Electron 44 の install.js は Node 22 以上が必要なため、Node 20 の開発環境ではこれで代替する
（install.js と同じ配置: dist/ と path.txt）。SHASUMS256.txt でハッシュを検証する。
使い方: uv run python apps/desktop/scripts/fetch-electron.py <electron パッケージのディレクトリ>
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://github.com/electron/electron/releases/download"


def main() -> int:
    pkg = Path(sys.argv[1]).resolve()
    version = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["version"]
    name = f"electron-v{version}-win32-x64.zip"
    dist = pkg / "dist"
    if (dist / "electron.exe").exists() and (dist / "version").read_text().strip().lstrip("v") == version:
        print(f"already installed: {version}")
        return 0
    with urllib.request.urlopen(f"{BASE}/v{version}/SHASUMS256.txt") as r:  # noqa: S310 - 固定の公式URL
        sums = dict(line.split(" *")[::-1] for line in r.read().decode().splitlines() if " *" in line)
    with urllib.request.urlopen(f"{BASE}/v{version}/{name}") as r:  # noqa: S310
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if sums.get(name) != digest:
        print(f"checksum mismatch: {digest}", file=sys.stderr)
        return 1
    dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dist)
    (pkg / "path.txt").write_text("electron.exe", encoding="utf-8")
    print(f"installed electron {version} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
