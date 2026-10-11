"""invoice.extract（protocol §3.5、PDF-04・07）。

本文を読み、テキストの無いページは OCR（sampling, purpose=ocr）→ LLM がスキーマどおりに抽出
（sampling, purpose=invoice_extract, metadata.response_schema）→ ツール側で検算と確信度の計算。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anyio.to_thread
import jsonschema

from office_tools.envelope import Outcome
from office_tools.errors import ToolFailure, invalid
from office_tools.fsutil import existing_file, normalize_arg
from office_tools.invoice.checks import UNKNOWN_OCR_CONFIDENCE, FieldResult, PageText, evaluate
from office_tools.invoice.schema import PROMPT, SCHEMA_ID, response_schema
from office_tools.pdflib import has_text, open_plumber, parse_pages, reading_text
from office_tools.registry import SamplingRequest, ToolContext, async_tool
from office_tools.tools.pdf import ocr_pages, page_count

MAX_PAGES = 20
MAX_TEXT_CHARS = 40_000


def _read_texts(path: str, real: Path, pages_spec: str | None) -> tuple[list[int], dict[int, str]]:
    total = page_count(path, real, None)
    pages = parse_pages(pages_spec, total)
    if len(pages) > MAX_PAGES:
        raise invalid(f"一度に読めるのは {MAX_PAGES} ページまでです。pages で範囲を指定してください")
    texts: dict[int, str] = {}
    with open_plumber(path, real, None) as pdf:
        for p in pages:
            texts[p] = reading_text(pdf.pages[p - 1])
    return pages, texts


def _bboxes(path: str, real: Path, fields: dict[str, FieldResult], text_pages: set[int]) -> None:
    """テキストのあるページで、値の位置（PDF 座標: x0, top, x1, bottom）を付ける。"""
    targets = [f for f in fields.values() if f.found_text and f.page in text_pages]
    if not targets:
        return
    with open_plumber(path, real, None) as pdf:
        for f in targets:
            assert f.page is not None and f.found_text is not None
            try:
                hits = pdf.pages[f.page - 1].search(f.found_text, regex=False, case=False)
            except Exception:  # noqa: S112  位置は付加情報なので失敗しても続ける
                continue
            if hits:
                h = hits[0]
                f.bbox = [
                    round(float(h["x0"]), 1),
                    round(float(h["top"]), 1),
                    round(float(h["x1"]), 1),
                    round(float(h["bottom"]), 1),
                ]


def _parse_reply(reply: str) -> dict[str, Any]:
    s = reply.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if m:
        s = m.group(1)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ToolFailure(
            "TOOL_FAILED", "この操作は失敗しました。理由: 抽出結果が JSON ではありません"
        ) from e
    try:
        jsonschema.validate(obj, response_schema())
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path)
        raise ToolFailure(
            "TOOL_FAILED", f"この操作は失敗しました。理由: 抽出結果がスキーマに合いません（{where}）"
        ) from e
    assert isinstance(obj, dict)
    return obj


@async_tool("invoice.extract")
async def invoice_extract(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    if args["schema"] != SCHEMA_ID:
        raise invalid(f"スキーマ {args['schema']} には対応していません（対応: {SCHEMA_ID}）")
    path = args["path"]
    real = existing_file(path)
    pages, texts = await anyio.to_thread.run_sync(lambda: _read_texts(path, real, args.get("pages")))
    scanned = [p for p in pages if not has_text(texts[p])]
    confidence = {p: 1.0 for p in pages}
    warnings: list[str] = []
    if scanned:
        ocr = await ocr_pages(ctx, path, real, None, scanned, "auto")
        for p, (text, conf) in ocr.items():
            texts[p] = text
            confidence[p] = conf if conf is not None else UNKNOWN_OCR_CONFIDENCE
    body = "\n".join(f"--- page {p} ---\n{texts[p]}" for p in pages)
    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS]
        warnings.append("本文が長いため先頭だけを抽出に使いました。pages で範囲を指定してください")
    body = body.replace("</untrusted>", "&lt;/untrusted&gt;")
    prompt = PROMPT.format(source="ocr" if scanned else "file", ref=normalize_arg(path), text=body)
    reply = await ctx.sample(
        SamplingRequest(
            purpose="invoice_extract",
            text=prompt,
            metadata={"schema_id": SCHEMA_ID, "response_schema": response_schema()},
            max_tokens=4000,
        )
    )
    raw = _parse_reply(reply)
    page_text = PageText(texts, confidence, set(scanned))
    fields, checks, needs_review = evaluate(raw, page_text)
    text_pages = {p for p in pages if p not in scanned}
    await anyio.to_thread.run_sync(lambda: _bboxes(path, real, fields, text_pages))
    out_fields: dict[str, Any] = {}
    for key, f in fields.items():
        entry: dict[str, Any] = {"value": f.value, "page": f.page or 0, "confidence": f.confidence}
        if f.bbox:
            entry["bbox"] = f.bbox
        out_fields[key] = entry
    if scanned:
        warnings.append(f"テキストの無いページ（{','.join(map(str, scanned))}）は OCR で読みました")
    return Outcome(
        data={"fields": out_fields, "checks": checks, "needs_review": needs_review, "schema": SCHEMA_ID},
        warnings=warnings,
    )
