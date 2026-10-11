"""開発・E2E 用に、本物の Gateway を一時フォルダで起動する（推論は FakeEngine、DB は SQLite）。

使い方（projects/local-llm-agent で）:
    uv run python server/gateway/scripts/dev_server.py --port 8789 [--dir <作業フォルダ>]
用意するもの: 鍵（Ed25519・暗号鍵）、利用者 dev（利用者）と admin（管理者）、設定ファイル、管理画面の配置。
パスワードは試験用の固定値（dev-password-1 / admin-password-1）。本番では使わないこと。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATEWAY = HERE.parent
PROJECT = GATEWAY.parents[1]
USERS = (("dev", "user", "dev-password-1"), ("admin", "admin", "admin-password-1"))
FAKE_OUTPUT = json.dumps(
    {"type": "final", "summary": "（本物の Gateway）完了しました。", "outputs": [], "failures": [], "needs_review": []},
    ensure_ascii=False,
)


def run(args: list[str], env: dict[str, str]) -> None:
    subprocess.run([sys.executable, "-m", "llm_gateway.cli", *args], check=True, env=env, cwd=PROJECT)  # noqa: S603


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8789)
    ap.add_argument("--dir", type=Path, default=None)
    a = ap.parse_args()
    work = a.dir or Path(tempfile.mkdtemp(prefix="llmgw-dev-"))
    work.mkdir(parents=True, exist_ok=True)
    keys = work / "keys"
    env = dict(os.environ)
    env["LLMGW_JWT_PRIVATE_KEY_FILE"] = str(keys / "jwt_ed25519.pem")
    env["LLMGW_PAYLOAD_KEYS_FILE"] = str(keys / "payload_keys.json")
    if not (keys / "jwt_ed25519.pem").exists():
        run(["keygen", "--out-dir", str(keys)], env)
    config = {
        "server": {"host": "127.0.0.1", "port": a.port},
        "database": {"url": f"sqlite+aiosqlite:///{(work / 'gw.db').as_posix()}"},
        "profile": str((GATEWAY / "examples" / "profile" / "profile.yaml").resolve()),
        "admin_web_dir": str((PROJECT / "apps" / "admin-web" / "dist").resolve()),
        "models": [
            {
                "id": "local-12b",
                "label": "高速",
                "context_budget": 32768,
                "engine": {"kind": "fake", "fake_token_delay_s": 0.01, "fake_output": FAKE_OUTPUT},
            }
        ],
        "upstreams": {},
    }
    cfg = work / "gateway.yaml"
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")  # JSON は YAML として読める
    marker = work / "users.created"
    if not marker.exists():
        for login, role, pw in USERS:
            run(["create-user", "--config", str(cfg), "--login", login, "--role", role], {**env, "LLMGW_NEW_PASSWORD": pw})
        marker.write_text("ok", encoding="utf-8")
    print(f"gateway dev server: http://127.0.0.1:{a.port} (dir={work})", flush=True)
    os.execve(sys.executable, [sys.executable, "-m", "llm_gateway.cli", "serve", "--config", str(cfg)], env) if os.name != "nt" else run(
        ["serve", "--config", str(cfg)], env
    )


if __name__ == "__main__":
    main()
