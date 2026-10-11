"""pdf.*（protocol §3.5、PDF-01〜03）。"""

from __future__ import annotations

import functools
import json
from typing import Any

import anyio.to_thread

from office_tools.envelope import Item, Outcome, paginate, untrusted_block
from office_tools.fsutil import existing_file, normalize_arg, sha256_file
from office_tools.pdflib import (
    has_text,
    is_encrypted,
    open_pdfium,
    open_plumber,
    parse_pages,
    raw_text,
    reading_text,
    render_png,
)
from office_tools.registry import SamplingRequest, ToolContext, async_tool, tool

MAX_PAGES_PER_CALL = 500


def page_ref(path: str, page: int) -> str:
    return f"{normalize_arg(path)}#p.{page}"


@tool("pdf.info")
def pdf_info(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = existing_file(path)
    doc = open_pdfium(path, real, ctx.password(args.get("password_ref")))
    try:
        n = len(doc)
        text_pages: list[int] = []
        scanned: list[int] = []
        for p in range(1, n + 1):
            (text_pages if has_text(raw_text(doc, p)) else scanned).append(p)
        meta = doc.get_metadata_dict(skip_empty=True)
        data: dict[str, Any] = {
            "file_hash": sha256_file(real),
            "pages": n,
            "encrypted": is_encrypted(doc),
            "text_pages": text_pages,
            "scanned_pages": scanned,
        }
        if meta.get("Title"):
            data["title"] = str(meta["Title"])[:300]
        if meta.get("Producer"):
            data["producer"] = str(meta["Producer"])[:300]
    finally:
        doc.close()
    return Outcome(data=data)


def read_page_texts(
    path: str, real: Any, password: str | None, pages: list[int], layout: str
) -> dict[int, str]:
    out: dict[int, str] = {}
    if layout == "raw":
        doc = open_pdfium(path, real, password)
        try:
            for p in pages:
                out[p] = raw_text(doc, p)
        finally:
            doc.close()
        return out
    with open_plumber(path, real, password) as pdf:
        for p in pages:
            out[p] = reading_text(pdf.pages[p - 1])
    return out


def page_count(path: str, real: Any, password: str | None) -> int:
    doc = open_pdfium(path, real, password)
    try:
        return len(doc)
    finally:
        doc.close()


@tool("pdf.read_pages")
def pdf_read_pages(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = existing_file(path)
    pw = ctx.password(args.get("password_ref"))
    pages = parse_pages(args["pages"], page_count(path, real, pw))[:MAX_PAGES_PER_CALL]
    texts = read_page_texts(path, real, pw, pages, args.get("layout", "reading"))
    items = []
    scanned = []
    for p in pages:
        text = texts[p]
        ref = page_ref(path, p)
        ok = has_text(text)
        if not ok:
            scanned.append(p)
        items.append(
            Item(
                {"pages": {"page": p, "chars": len(text), "has_text": ok, "text_ref": ref}},
                [untrusted_block(ref, text)] if text.strip() else [],
            )
        )
    warnings = []
    if scanned:
        warnings.append(
            f"テキストの無いページがあります（{','.join(map(str, scanned[:50]))}）。pdf.ocr で読んでください"
        )
    return paginate(
        ctx.store, "pdf.read_pages", {}, ("pages",), items, max_items=MAX_PAGES_PER_CALL, warnings=warnings
    )


@tool("pdf.extract_tables")
def pdf_extract_tables(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = existing_file(path)
    pw = ctx.password(args.get("password_ref"))
    items: list[Item] = []
    with open_plumber(path, real, pw) as pdf:
        pages = parse_pages(args["pages"], len(pdf.pages))[:MAX_PAGES_PER_CALL]
        for p in pages:
            page = pdf.pages[p - 1]
            for idx, t in enumerate(page.find_tables(), start=1):
                rows = [["" if c is None else str(c) for c in row] for row in t.extract()]
                if not rows:
                    continue
                entry: dict[str, Any] = {
                    "page": p,
                    "index": idx,
                    "bbox": [round(float(v), 1) for v in t.bbox],
                }
                if len(rows) > 1:
                    entry["header"] = rows[0]
                    entry["rows"] = rows[1:]
                else:
                    entry["rows"] = rows
                items.append(Item({"tables": entry}))
    return paginate(ctx.store, "pdf.extract_tables", {}, ("tables",), items, max_items=200)


def parse_ocr_response(text: str) -> tuple[str, float | None]:
    """OCR の応答: JSON {"text": str, "confidence": 0..1} か、プレーンテキスト。"""
    s = text.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return text, None
        if isinstance(obj, dict) and isinstance(obj.get("text"), str):
            conf = obj.get("confidence")
            c = float(conf) if isinstance(conf, int | float) and not isinstance(conf, bool) else None
            if c is not None:
                c = min(1.0, max(0.0, c))
            return obj["text"], c
    return text, None


async def ocr_pages(
    ctx: ToolContext, path: str, real: Any, password: str | None, pages: list[int], engine: str
) -> dict[int, tuple[str, float | None]]:
    out: dict[int, tuple[str, float | None]] = {}
    for p in pages:
        png = await anyio.to_thread.run_sync(functools.partial(_render, path, real, password, p))
        reply = await ctx.sample(
            SamplingRequest(
                purpose="ocr",
                image_png=png,
                metadata={"engine": engine, "page": p, "lang": "ja"},
                max_tokens=4000,
            )
        )
        out[p] = parse_ocr_response(reply)
    return out


def _render(path: str, real: Any, password: str | None, page: int) -> bytes:
    doc = open_pdfium(path, real, password)
    try:
        return render_png(doc, page)
    finally:
        doc.close()


@async_tool("pdf.ocr")
async def pdf_ocr(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = existing_file(path)
    pw = ctx.password(args.get("password_ref"))
    total = await anyio.to_thread.run_sync(lambda: page_count(path, real, pw))
    pages = parse_pages(args["pages"], total)[:50]
    results = await ocr_pages(ctx, path, real, pw, pages, args.get("engine", "auto"))
    items = []
    for p in pages:
        text, conf = results[p]
        ref = page_ref(path, p)
        items.append(
            Item(
                {"pages": {"page": p, "confidence": conf, "chars": len(text), "text_ref": ref}},
                [untrusted_block(ref, text, source="ocr")],
            )
        )
    return paginate(ctx.store, "pdf.ocr", {}, ("pages",), items, max_items=50)
