"""ファイル → 単位（見出しの節・表・ページ・本文）。読み取りは office-tools と共通（設計書 §6.4・§7）。

- docx: office_tools.docx.model のブロック列（見出しレベル・表）を使う。ヘッダー・フッター・画像は入れない。
- xlsx: office_tools.xlsx.book で開き、シートを表として扱う（キャッシュされた値を読む）。
- pdf: office_tools.pdflib の読み順のテキストをページ単位で。条・章の見出しを出典に付ける。
- csv: office_tools.tools.csvtool の文字コード・区切り文字の判定を使う。
- md / txt: ここで分ける（md は # の見出しと | の表）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from llm_rag.chunking import Unit

MAX_LOCATION = 160
_PDF_HEADING = re.compile(
    r"^\s*((?:第[0-9０-９一二三四五六七八九十百千]+[編章節条款項])(?:[ 　]*[^\s。、]{0,30})?|【[^】]{1,30}】)\s*$"
)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


class ExtractError(Exception):
    """読めないファイル。code は protocol §2.4 のコード（CORRUPT_FILE など）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _clip(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= MAX_LOCATION else s[: MAX_LOCATION - 1] + "…"


def _heading_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(t for _, t in stack)


def _push_heading(stack: list[tuple[int, str]], level: int, text: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, " ".join(text.split())[:60]))


def _rows_locate(
    base: str, row_numbers: list[int], fmt: Callable[[int, int], str]
) -> Callable[[int, int], str]:
    def locate(first: int, last: int) -> str:
        return _clip(f"{base}{fmt(row_numbers[first], row_numbers[last])}")

    return locate


# ---------------------------------------------------------------- docx


def _docx_units(path: str, real: Path) -> list[Unit]:
    from office_tools.docx.model import W_TC, element_text, index_document, open_document

    idx = index_document(open_document(path, real))
    units: list[Unit] = []
    stack: list[tuple[int, str]] = []
    cur: list[str] = []
    tables = 0

    def flush() -> None:
        if any(x.strip() for x in cur):
            ctx = _heading_path(stack)
            units.append(Unit("section", list(cur), location=_clip(ctx or "本文"), context=ctx))
        cur.clear()

    for b in idx.blocks:
        if b.kind == "heading":
            flush()
            _push_heading(stack, int(b.info.get("level") or 1), b.text)
        elif b.kind == "paragraph":
            if b.text.strip():
                cur.append(b.text)
        elif b.kind == "table":
            flush()
            tables += 1
            rows: list[str] = []
            for tr in b.rows:
                cells = [" ".join(element_text(tc).split()) for tc in tr.findall(W_TC)]
                rows.append(" | ".join(cells))
            ctx = _heading_path(stack)
            base = f"{ctx} / 表{tables}" if ctx else f"表{tables}"
            header, body = rows[:1], rows[1:]
            if not body:
                header, body = [], rows
            nums = list(range(len(rows) - len(body) + 1, len(rows) + 1))
            units.append(
                Unit(
                    "table",
                    body,
                    location=_clip(base),
                    context=ctx,
                    header=header,
                    locate=_rows_locate(base, nums, lambda a, z: f"（行{a}-{z}）"),
                )
            )
    flush()
    return units


# ---------------------------------------------------------------- xlsx


def _cell_text(v: Any) -> str:
    from office_tools.scalars import to_json, to_text

    return " ".join(to_text(to_json(v)).split())


def _xlsx_units(path: str, real: Path, header_rows: int) -> list[Unit]:
    from office_tools.xlsx.book import open_book

    wb = open_book(path, real, data_only=True, read_only=True)
    units: list[Unit] = []
    try:
        for ws in wb.worksheets:
            rows: list[tuple[int, str]] = []
            for r, values in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = [_cell_text(v) for v in values]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append((r, " | ".join(cells)))
            if not rows:
                continue
            hn = min(header_rows, max(0, len(rows) - 1))
            header = [t for _, t in rows[:hn]]
            body = rows[hn:]
            title = str(ws.title)
            units.append(
                Unit(
                    "table",
                    [t for _, t in body],
                    location=title,
                    context=f"シート {title}",
                    header=header,
                    locate=_rows_locate(f"{title}!", [n for n, _ in body], lambda a, z: f"{a}:{z}"),
                )
            )
    finally:
        wb.close()
    return units


# ---------------------------------------------------------------- csv


def _csv_units(path: str, real: Path, header_rows: int) -> list[Unit]:
    from office_tools.tools.csvtool import read_csv_file

    table = read_csv_file(path, real)
    rows = [(i, " | ".join(" ".join(c.split()) for c in row)) for i, row in enumerate(table.rows, start=1)]
    rows = [(i, t) for i, t in rows if t.replace("|", "").strip()]
    if not rows:
        return []
    hn = min(header_rows, max(0, len(rows) - 1))
    body = rows[hn:]
    return [
        Unit(
            "table",
            [t for _, t in body],
            location="表",
            header=[t for _, t in rows[:hn]],
            locate=_rows_locate("", [n for n, _ in body], lambda a, z: f"行{a}-{z}"),
        )
    ]


# ---------------------------------------------------------------- pdf


def _pdf_units(path: str, real: Path) -> list[Unit]:
    from office_tools.pdflib import open_plumber, reading_text

    units: list[Unit] = []
    heading = ""
    with open_plumber(path, real, None) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            lines = reading_text(page).splitlines()
            in_effect: list[str] = []
            for ln in lines:
                m = _PDF_HEADING.match(ln)
                if m:
                    heading = " ".join(m.group(1).split())
                in_effect.append(heading)
            if not any(ln.strip() for ln in lines):
                continue  # スキャンのページ（OCR は取り込みの対象外）

            def locate(first: int, last: int, p: int = page_no, h: list[str] = in_effect) -> str:
                hd = h[first] or next((x for x in h[first : last + 1] if x), "")
                return _clip(f"p.{p} / {hd}" if hd else f"p.{p}")

            units.append(Unit("page", lines, location=f"p.{page_no}", locate=locate))
    return units


# ---------------------------------------------------------------- md / txt


def _read_text(real: Path) -> str:
    from office_tools.tools.csvtool import detect_encoding

    raw = real.read_bytes()
    text = raw.decode(detect_encoding(raw), errors="replace")
    return text[1:] if text.startswith("﻿") else text


def _line_locate(base: str, offset: int) -> Callable[[int, int], str]:
    def locate(first: int, last: int) -> str:
        span = f"行{first + offset}-{last + offset}"
        return _clip(f"{base} / {span}" if base else span)

    return locate


def _md_units(real: Path) -> list[Unit]:
    lines = _read_text(real).splitlines()
    units: list[Unit] = []
    stack: list[tuple[int, str]] = []
    cur: list[str] = []
    cur_start = 0
    tables = 0
    in_fence = False

    def flush(end: int) -> None:
        if any(x.strip() for x in cur):
            ctx = _heading_path(stack)
            units.append(
                Unit(
                    "section",
                    list(cur),
                    location=_clip(ctx or "本文"),
                    context=ctx,
                    locate=_line_locate(ctx, cur_start + 1),
                )
            )
        cur.clear()

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _MD_HEADING.match(ln)
        is_table = (
            not in_fence
            and ln.lstrip().startswith("|")
            and i + 1 < len(lines)
            and _MD_TABLE_SEP.match(lines[i + 1]) is not None
        )
        if m:
            flush(i)
            _push_heading(stack, len(m.group(1)), m.group(2))
            cur_start = i + 1
        elif is_table:
            flush(i)
            tables += 1
            header = lines[i]
            j = i + 2
            body: list[str] = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                body.append(lines[j].strip())
                j += 1
            ctx = _heading_path(stack)
            base = f"{ctx} / 表{tables}" if ctx else f"表{tables}"
            nums = list(range(i + 3, i + 3 + len(body)))
            units.append(
                Unit(
                    "table",
                    body or [header],
                    location=_clip(base),
                    context=ctx,
                    header=[header.strip()] if body else [],
                    locate=_rows_locate(base, nums or [i + 1], lambda a, z: f"（行{a}-{z}）"),
                )
            )
            i = j
            cur_start = j
            continue
        else:
            if not cur:
                cur_start = i
            cur.append(ln)
        i += 1
    flush(len(lines))
    return units


def _txt_units(real: Path) -> list[Unit]:
    lines = _read_text(real).splitlines()
    if not any(ln.strip() for ln in lines):
        return []
    return [Unit("text", lines, location="本文", locate=_line_locate("", 1))]


# ---------------------------------------------------------------- 入口


def extract_units(real: Path, *, header_rows: int = 1) -> list[Unit]:
    """読めないときは ExtractError（コードだけを持つ。パスや本文は持たない）。"""
    from office_tools.errors import ToolFailure

    path = str(real)
    ext = real.suffix.lower()
    try:
        if ext in (".docx", ".docm"):
            return _docx_units(path, real)
        if ext in (".xlsx", ".xlsm"):
            return _xlsx_units(path, real, header_rows)
        if ext == ".pdf":
            return _pdf_units(path, real)
        if ext == ".csv":
            return _csv_units(path, real, header_rows)
        if ext in (".md", ".markdown"):
            return _md_units(real)
        if ext == ".txt":
            return _txt_units(real)
    except ToolFailure as e:
        raise ExtractError(e.code) from None
    except PermissionError:
        raise ExtractError("FILE_LOCKED") from None
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        raise ExtractError("CORRUPT_FILE") from e
    raise ExtractError("UNSUPPORTED_FORMAT")
