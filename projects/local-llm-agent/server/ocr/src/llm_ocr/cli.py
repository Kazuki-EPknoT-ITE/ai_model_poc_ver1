"""`llm-ocr serve` — 設定は環境変数で渡す（秘密情報を設定ファイルに書かない）。

LLM_OCR_SECRET           Gateway と共有する内部トークン（必須）
LLM_OCR_ENGINES          使うエンジン（カンマ区切り: ocr,vlm,fake）。既定 "vlm"
LLM_OCR_DEFAULT          engine=auto のときのエンジン。既定は LLM_OCR_ENGINES の先頭
LLM_OCR_VLM_URL          vLLM の URL（例 http://vllm-12b:8000）
LLM_OCR_VLM_MODEL        モデル名（例 gemma-4-12b-it）
LLM_OCR_CONCURRENCY      同時に処理する枚数（既定 2）
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .app import create_app
from .engines import FakeEngine, OcrEngine, PaddleEngine, VlmEngine


def build_engines(names: list[str]) -> dict[str, OcrEngine]:
    engines: dict[str, OcrEngine] = {}
    for n in names:
        if n == "ocr":
            engines["ocr"] = PaddleEngine()
        elif n == "vlm":
            engines["vlm"] = VlmEngine(
                os.environ["LLM_OCR_VLM_URL"], os.environ.get("LLM_OCR_VLM_MODEL", "gemma-4-12b-it")
            )
        elif n == "fake":
            engines["fake"] = FakeEngine()
        else:
            raise SystemExit(f"unknown engine: {n}")
    return engines


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="llm-ocr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    args = ap.parse_args(argv)
    secret = os.environ.get("LLM_OCR_SECRET")
    if not secret:
        print("LLM_OCR_SECRET を設定してください", file=sys.stderr)
        raise SystemExit(2)
    names = [n.strip() for n in os.environ.get("LLM_OCR_ENGINES", "vlm").split(",") if n.strip()]
    engines = build_engines(names)
    app = create_app(
        engines,
        default=os.environ.get("LLM_OCR_DEFAULT", names[0]),
        secret=secret,
        concurrency=int(os.environ.get("LLM_OCR_CONCURRENCY", "2")),
    )
    if args.cmd == "serve":
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
