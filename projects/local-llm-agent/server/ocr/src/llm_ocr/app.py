"""画像読取サービスの HTTP API（Gateway から内部ネットワークで呼ぶ。local_llm_api.md §5）。

画像はメモリ上だけで扱い、ディスクにもログにも残さない（ADR-006）。記録するのはサイズと所要時間だけ。
"""

from __future__ import annotations

import asyncio
import hmac
import io
import logging
import time
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError

from .engines import EngineError, OcrEngine

log = logging.getLogger("llm_ocr")

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 60_000_000  # 約 A3・600dpi。これを超える画像は解凍爆弾として拒否する


def create_app(engines: dict[str, OcrEngine], *, default: str, secret: str, concurrency: int = 2) -> FastAPI:
    if default not in engines:
        raise ValueError(f"default engine {default} is not configured")
    app = FastAPI(title="llm-ocr", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    sem = asyncio.Semaphore(concurrency)  # GPU を推論と分け合うので同時数を絞る
    stats = {"requests": 0, "errors": 0, "seconds": 0.0}

    def error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": code, "message": message, "retryable": retryable}}, status_code=status
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "engines": sorted(engines), "default": default}

    @app.get("/metrics")
    async def metrics() -> Any:
        from fastapi.responses import PlainTextResponse  # noqa: PLC0415

        return PlainTextResponse(
            f"llm_ocr_requests_total {stats['requests']}\n"
            f"llm_ocr_errors_total {stats['errors']}\n"
            f"llm_ocr_seconds_total {stats['seconds']:.3f}\n"
        )

    @app.post("/v1/vision/ocr")
    async def ocr(
        image: Annotated[UploadFile, File()],
        engine: Annotated[str, Form()] = "auto",
        lang: Annotated[str, Form()] = "ja",
        x_internal_token: Annotated[str | None, Header()] = None,
    ) -> Any:
        if not x_internal_token or not hmac.compare_digest(x_internal_token, secret):
            raise HTTPException(status_code=401, detail="unauthorized")
        data = await image.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return error(413, "IMAGE_TOO_LARGE", "画像が大きすぎます（20MB まで）")
        name = default if engine == "auto" else engine
        if name not in engines:
            return error(400, "INVALID_REQUEST", f"エンジン {engine} は使えません")
        try:
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS
            img = Image.open(io.BytesIO(data))
            # Pillow は上限の2倍を超えるまで警告しか出さないので、展開する前に自分で確かめる
            if img.width * img.height > MAX_PIXELS:
                return error(400, "INVALID_IMAGE", "画像の画素数が多すぎます")
            img.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return error(400, "INVALID_IMAGE", "画像として読めません")
        started = time.monotonic()
        stats["requests"] += 1
        try:
            async with sem:
                result = await engines[name].read(img, lang)
        except EngineError as e:
            stats["errors"] += 1
            log.warning("ocr engine=%s failed: %s", name, e)
            return error(503, "ENGINE_UNAVAILABLE", str(e), retryable=True)
        finally:
            elapsed = time.monotonic() - started
            stats["seconds"] += elapsed
            del data
        log.info(
            "ocr engine=%s bytes=%d size=%dx%d seconds=%.2f",
            name,
            image.size or 0,
            img.width,
            img.height,
            elapsed,
        )
        return result.to_json()

    return app
