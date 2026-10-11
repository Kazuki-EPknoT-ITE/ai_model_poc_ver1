"""配布物（インストーラ）を作る（実装計画 T-46、ADR-001 改訂）。

前提: `pnpm --filter @lla/desktop build`（dist/ を作る）と
      `uv run --with pyinstaller python tools/office/packaging/build.py`（office-tools.exe を作る）が済んでいること。
使い方（projects/local-llm-agent で）:
    uv run python apps/desktop/scripts/package.py [--dir]
      --dir  インストーラを作らず、展開済みのフォルダ（release/win-unpacked）だけを作る（試験用）

同梱するもの（resources/）:
    node/node.exe            Runtime を動かす Node（Electron とは別。ネイティブモジュールの ABI を合わせるため）
    runtime/host.mjs         Runtime ホスト（esbuild でまとめたもの）
    runtime/node_modules/    暗号化 SQLite のネイティブモジュール（上の Node 用のビルド済みバイナリ）
    office-tools/            Word/Excel/PDF ツール（PyInstaller）
ダウンロードしたものは、公式の SHASUMS256 で検証する。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

NODE_VERSION = "22.23.3"  # LTS（Jod）。better-sqlite3-multiple-ciphers の node-v127 用バイナリと組み合わせる
NODE_ABI = "127"
SQLITE_PKG = "better-sqlite3-multiple-ciphers"

APP = Path(__file__).resolve().parents[1]
PROJECT = APP.parents[1]
RELEASE = APP / "release"
STAGE = RELEASE / "stage"
CACHE = RELEASE / "cache"


def fetch(url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / url.rsplit("/", 1)[-1]
    if cached.exists():
        return cached.read_bytes()
    with urllib.request.urlopen(url) as r:  # noqa: S310 - 固定の公式 URL
        data: bytes = r.read()
    cached.write_bytes(data)
    return data


def node_exe(dest: Path) -> None:
    base = f"https://nodejs.org/dist/v{NODE_VERSION}"
    name = f"node-v{NODE_VERSION}-win-x64.zip"
    sums = fetch(f"{base}/SHASUMS256.txt").decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.endswith(name))
    data = fetch(f"{base}/{name}")
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit(f"checksum mismatch: {name}")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member = f"node-v{NODE_VERSION}-win-x64/node.exe"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "node.exe").write_bytes(z.read(member))
        (dest / "LICENSE").write_bytes(z.read(f"node-v{NODE_VERSION}-win-x64/LICENSE"))


def sqlite_module(node_modules: Path) -> None:
    """ネイティブモジュールを、同梱する Node の ABI 用のバイナリに差し替えて置く。"""
    src_pkg = Path(
        subprocess.check_output(
            ["node", "-p", f"require.resolve('{SQLITE_PKG}/package.json')"], cwd=APP, text=True
        ).strip()  # noqa: S603, S607
    ).parent
    version = json.loads((src_pkg / "package.json").read_text(encoding="utf-8"))["version"]
    dst = node_modules / SQLITE_PKG
    shutil.copytree(src_pkg / "lib", dst / "lib")
    shutil.copy2(src_pkg / "package.json", dst / "package.json")
    if (src_pkg / "LICENSE").exists():
        shutil.copy2(src_pkg / "LICENSE", dst / "LICENSE")
    url = f"https://github.com/m4heshd/{SQLITE_PKG}/releases/download/v{version}/{SQLITE_PKG}-v{version}-node-v{NODE_ABI}-win32-x64.tar.gz"
    with tarfile.open(fileobj=io.BytesIO(fetch(url)), mode="r:gz") as t:
        member = next(m for m in t.getmembers() if m.name.endswith("better_sqlite3.node"))
        f = t.extractfile(member)
        assert f is not None
        (dst / "build" / "Release").mkdir(parents=True, exist_ok=True)
        (dst / "build" / "Release" / "better_sqlite3.node").write_bytes(f.read())
    # lib/ が require する小さな依存
    for dep in ("bindings", "file-uri-to-path"):
        p = Path(
            subprocess.check_output(
                [
                    "node",
                    "-p",
                    f"require.resolve('{dep}/package.json', {{paths: [require.resolve('{SQLITE_PKG}')]}})",
                ],
                cwd=APP,
                text=True,
            ).strip()
        ).parent  # noqa: S603, S607
        shutil.copytree(p, node_modules / dep, ignore=shutil.ignore_patterns("test", "*.md"))


def stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    app_dir = STAGE / "app"
    res = STAGE / "resources"
    for sub in ("main", "preload", "renderer"):
        src = APP / "dist" / sub
        if not src.exists():
            raise SystemExit(f"{src} がありません。先に pnpm --filter @lla/desktop build を実行してください")
        shutil.copytree(src, app_dir / "dist" / sub, ignore=shutil.ignore_patterns("*.map"))
    shutil.copytree(APP / "assets", app_dir / "assets")
    pkg = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    (app_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "local-llm-agent",
                "productName": "LocalAIAssistant",
                "version": pkg["version"],
                "description": "社内ローカルAIアシスタント",
                "author": "EPknoT",
                "main": "dist/main/main.cjs",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    node_exe(res / "node")
    (res / "runtime").mkdir(parents=True)
    shutil.copy2(APP / "dist" / "runtime-host" / "host.mjs", res / "runtime" / "host.mjs")
    sqlite_module(res / "runtime" / "node_modules")
    office = PROJECT / "tools" / "office" / "dist" / "office-tools"
    if not office.exists():
        raise SystemExit(
            "office-tools.exe がありません。tools/office/packaging/build.py を先に実行してください"
        )
    shutil.copytree(office, res / "office-tools")


def builder(dir_only: bool) -> None:
    electron = json.loads((APP / "package.json").read_text(encoding="utf-8"))["devDependencies"]["electron"]
    config = {
        "appId": "jp.epknot.local-llm-agent",
        "productName": "LocalAIAssistant",
        "electronVersion": electron,
        "directories": {"app": str(STAGE / "app"), "output": str(RELEASE)},
        "files": ["**/*"],
        "asar": True,
        "npmRebuild": False,
        "extraResources": [
            {"from": str(STAGE / "resources" / "node"), "to": "node"},
            {"from": str(STAGE / "resources" / "runtime"), "to": "runtime", "filter": ["host.mjs"]},
            # electron-builder は extraResources でも node_modules を既定で除外するので、明示して同梱する
            {
                "from": str(STAGE / "resources" / "runtime" / "node_modules"),
                "to": "runtime/node_modules",
                "filter": ["**/*"],
            },
            {"from": str(STAGE / "resources" / "office-tools"), "to": "office-tools"},
        ],
        "win": {
            "target": ["dir" if dir_only else "nsis"],
            "icon": str(STAGE / "app" / "assets" / "icon.png"),
        },
        "nsis": {
            "oneClick": False,
            "perMachine": False,
            "allowToChangeInstallationDirectory": True,
            "shortcutName": "ローカルAIアシスタント",
            "artifactName": "LocalAIAssistant-Setup-${version}.${ext}",
        },
    }
    cfg = RELEASE / "electron-builder.json"
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [
        "pnpm",
        "exec",
        "electron-builder",
        "--win",
        "--x64",
        "--config",
        str(cfg),
        "--projectDir",
        str(STAGE / "app"),
    ]
    subprocess.run(cmd, cwd=APP, check=True, shell=sys.platform == "win32")  # noqa: S602, S603 - 固定の引数（Windows の pnpm.cmd のため）


if __name__ == "__main__":
    dir_only = "--dir" in sys.argv
    stage()
    builder(dir_only)
    out = RELEASE / "win-unpacked" if dir_only else next(RELEASE.glob("*.exe"))
    print(f"done: {out}")
