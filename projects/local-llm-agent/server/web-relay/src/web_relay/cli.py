"""起動: `web-relay --config web-relay.yaml`（共有シークレットは環境変数 WEB_RELAY_SECRET など）。"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import load_secret, load_settings


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="web-relay")
    ap.add_argument("--config", help="設定ファイル（YAML）", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings(args.config)
    secret = load_secret(settings.server)
    app = create_app(settings, secret)
    # アクセスログは URL（クエリ）を含まないが、念のため無効にする（本文・検索語を出さない）
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, access_log=False)


if __name__ == "__main__":
    main()
