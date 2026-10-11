"""`uv run mock-gateway --scenario <file> --port 8787`"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_app
from .scenario import load_scenario


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="local-llm-agent mock gateway")
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--record", type=Path, default=None, help="受け取ったリクエストを書き出す JSON Lines")
    ap.add_argument("--ping-interval", type=float, default=15.0)
    args = ap.parse_args(argv)

    scenario = load_scenario(args.scenario)
    app = create_app(scenario, record_path=args.record, ping_interval_s=args.ping_interval)
    print(f"mock gateway: scenario={scenario.name} http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
