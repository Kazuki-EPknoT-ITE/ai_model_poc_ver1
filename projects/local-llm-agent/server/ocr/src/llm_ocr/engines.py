"""OCR エンジン（差し替え可能）。どれを使うかは評価セット（帳票抽出）で決める（要件 PDF-03、ADR-006）。"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from PIL import Image


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass
class OcrResult:
    text: str
    lines: list[OcrLine] = field(default_factory=list)
    engine_used: str = ""
    confidence: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "lines": [
                {"text": ln.text, "bbox": list(ln.bbox), "confidence": ln.confidence} for ln in self.lines
            ],
            "engine_used": self.engine_used,
            "confidence": self.confidence,
        }


class OcrEngine(Protocol):
    name: str

    async def read(self, image: Image.Image, lang: str) -> OcrResult: ...


class EngineError(Exception):
    """エンジンが使えない・失敗した（Gateway には 503 で返し、Runtime は一時的な失敗として再試行する）。"""


def average_confidence(lines: list[OcrLine]) -> float | None:
    if not lines:
        return None
    total = sum(len(ln.text) for ln in lines) or 1
    return round(sum(ln.confidence * len(ln.text) for ln in lines) / total, 4)


class PaddleEngine:
    """PaddleOCR（Apache-2.0）。GPU のサーバーで使う。初回の呼び出しでモデルを読み込む。"""

    name = "ocr"

    def __init__(self) -> None:
        self._ocr: Any = None

    def _load(self) -> Any:
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415 - 必要になってから読み込む
            except ImportError as e:  # pragma: no cover - 開発 PC には入っていない
                raise EngineError("PaddleOCR が入っていません（pip install llm-ocr[paddle]）") from e
            self._ocr = PaddleOCR(lang="japan", use_angle_cls=True, show_log=False)
        return self._ocr

    async def read(
        self, image: Image.Image, lang: str
    ) -> OcrResult:  # pragma: no cover - GPU サーバーで評価する
        import numpy as np  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415

        ocr = self._load()
        raw = ocr.ocr(np.array(image.convert("RGB")), cls=True)
        lines: list[OcrLine] = []
        for page in raw or []:
            for box, (text, conf) in page or []:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                lines.append(OcrLine(str(text), (min(xs), min(ys), max(xs), max(ys)), float(conf)))
        lines.sort(key=lambda ln: (round(ln.bbox[1] / 10), ln.bbox[0]))  # 上から下、左から右
        return OcrResult("\n".join(ln.text for ln in lines), lines, self.name, average_confidence(lines))


class VlmEngine:
    """推論サーバー（vLLM の OpenAI 互換 API）の画像入力で読む。Gemma 4 の画像入力。"""

    name = "vlm"

    PROMPT = (
        "この画像は請求書・FAX などの業務文書です。書かれている文字を、上から順にそのまま書き起こしてください。"
        "表は1行ずつ、セルをタブで区切ってください。読めない文字は〓にしてください。説明や要約は書かないでください。"
    )

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._client = client

    async def read(self, image: Image.Image, lang: str) -> OcrResult:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": self.PROMPT},
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await client.post(f"{self.base_url}/v1/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise EngineError(f"推論サーバーに接続できません: {type(e).__name__}") from e
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code >= 400:
            raise EngineError(f"推論サーバーがエラーを返しました（HTTP {r.status_code}）")
        text = str(r.json()["choices"][0]["message"]["content"]).strip()
        # VLM は確信度を返さないので None（invoice.extract 側で要確認として扱う）
        lines = [OcrLine(t, (0, 0, 0, 0), 0.0) for t in text.splitlines() if t.strip()]
        return OcrResult(text, lines, self.name, None)


class FakeEngine:
    """試験用。決め打ちの結果を返す（画像の大きさを本文に入れて、受け取った画像を確認できるようにする）。"""

    name = "fake"

    def __init__(self, text: str = "請求書\n合計 USD 1,000.00", confidence: float = 0.95, fail: bool = False):
        self.text = text
        self.confidence = confidence
        self.fail = fail
        self.calls = 0

    async def read(self, image: Image.Image, lang: str) -> OcrResult:
        self.calls += 1
        if self.fail:
            raise EngineError("（試験）エンジンの失敗")
        lines = [
            OcrLine(t, (0, i * 20.0, image.width, i * 20.0 + 18), self.confidence)
            for i, t in enumerate(self.text.splitlines())
        ]
        return OcrResult(self.text, lines, self.name, average_confidence(lines))
