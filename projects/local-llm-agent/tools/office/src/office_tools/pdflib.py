"""PDF の読み取り（pypdfium2 ＋ pdfplumber。PyMuPDF は使わない）。"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from office_tools.errors import ToolFailure, corrupt, invalid, locked, unsupported
from office_tools.fsutil import ext

MIN_TEXT_CHARS = 5
"""空白以外の文字がこれ未満のページは「テキストの無いページ」（スキャン）とみなす。"""
OCR_DPI = 200


def check_pdf(path: str, real: Path) -> None:
    if ext(real) != ".pdf":
        raise unsupported(path, f"{ext(real) or '拡張子なし'} は PDF として扱えません")


def _password_failure(path: str, wrong: bool) -> ToolFailure:
    msg = (
        "パスワードが違います。利用者にパスワードを確認してください"
        if wrong
        else "パスワード付きの PDF です。利用者にパスワードを確認してください"
    )
    return ToolFailure("PASSWORD_REQUIRED", msg, detail={"path": path})


def open_pdfium(path: str, real: Path, password: str | None) -> pdfium.PdfDocument:
    check_pdf(path, real)
    try:
        return pdfium.PdfDocument(str(real), password=password)
    except pdfium.PdfiumError as e:
        if e.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            raise _password_failure(path, password is not None) from e
        raise corrupt(path, "pdfium") from e
    except PermissionError as e:
        raise locked(path) from e


@contextlib.contextmanager
def open_plumber(path: str, real: Path, password: str | None) -> Iterator[Any]:
    check_pdf(path, real)
    try:
        doc = pdfplumber.open(str(real), password=password or "")
    except PermissionError as e:
        raise locked(path) from e
    except Exception as e:
        name = type(e).__name__
        if "Password" in name or "Encrypt" in name:
            raise _password_failure(path, password is not None) from e
        raise corrupt(path, name) from e
    try:
        yield doc
    finally:
        doc.close()


def is_encrypted(doc: pdfium.PdfDocument) -> bool:
    return int(pdfium_c.FPDF_GetSecurityHandlerRevision(doc.raw)) != -1


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-5,8' → [1,2,3,4,5,8]（1 始まり）。"""
    if not spec:
        return list(range(1, total + 1))
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        lo = int(a)
        hi = int(b) if b else lo
        if lo < 1 or hi < lo:
            raise invalid(f"pages の {part} が不正です。1-5,8 の形式で指定してください")
        if hi > total:
            raise invalid(
                f"pages の {part} がページ数（{total}）を超えています。pages はページ番号（1〜{total}）です。"
                f"条項（Clause）や章の番号ではありません。全ページを読むなら pages に 1-{total} を指定します"
            )
        out.extend(p for p in range(lo, hi + 1) if p not in out)
    return out


def raw_text(doc: pdfium.PdfDocument, page_no: int) -> str:
    page = doc[page_no - 1]
    try:
        tp = page.get_textpage()
        try:
            return str(tp.get_text_range()).replace("\r\n", "\n").replace("\r", "\n")
        finally:
            tp.close()
    finally:
        page.close()


def has_text(text: str) -> bool:
    return sum(1 for ch in text if not ch.isspace()) >= MIN_TEXT_CHARS


# ---------------------------------------------------------------- 読み順（段組の考慮）


def _lines(words: list[dict[str, Any]], tol: float = 3.0) -> list[list[dict[str, Any]]]:
    lines: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    return lines


def _join(line: list[dict[str, Any]]) -> str:
    out = ""
    prev: dict[str, Any] | None = None
    for w in line:
        if prev is not None:
            gap = w["x0"] - prev["x1"]
            out += " " if gap > 1.5 else ""
        out += w["text"]
        prev = w
    return out


def _gutter(lines: list[list[dict[str, Any]]], width: float) -> float | None:
    """2段組の境目（x 座標）を探す。見つからなければ None。"""
    if len(lines) < 6 or width <= 0:
        return None
    n = int(width) + 1
    count = [0] * n
    for ln in lines:
        covered = [False] * n
        for w in ln:
            for x in range(max(0, int(w["x0"])), min(n, int(w["x1"]) + 1)):
                covered[x] = True
        for x in range(n):
            if covered[x]:
                count[x] += 1
    limit = max(1, len(lines) // 10)
    lo, hi = int(width * 0.3), int(width * 0.7)
    best_len, best_mid, run_start = 0, None, None
    for x in range(lo, hi + 1):
        if count[x] <= limit:
            if run_start is None:
                run_start = x
            if x - run_start + 1 > best_len:
                best_len = x - run_start + 1
                best_mid = (run_start + x) / 2
        else:
            run_start = None
    if best_mid is None or best_len < max(6, width * 0.015):
        return None
    left = sum(1 for ln in lines if any(w["x1"] <= best_mid for w in ln))
    right = sum(1 for ln in lines if any(w["x0"] >= best_mid for w in ln))
    if left < 3 or right < 3:
        return None
    return best_mid


def reading_text(page: Any) -> str:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if not words:
        return ""
    lines = _lines(words)
    g = _gutter(lines, float(page.width))
    if g is None:
        return "\n".join(_join(ln) for ln in lines)
    out: list[str] = []
    left: list[str] = []
    right: list[str] = []

    def flush() -> None:
        out.extend(left)
        out.extend(right)
        left.clear()
        right.clear()

    for ln in lines:
        lw = [w for w in ln if w["x1"] <= g]
        rw = [w for w in ln if w["x0"] >= g]
        if len(lw) + len(rw) < len(ln):  # 境目をまたぐ行（見出しなど）
            flush()
            out.append(_join(ln))
            continue
        if lw:
            left.append(_join(lw))
        if rw:
            right.append(_join(rw))
    flush()
    return "\n".join(out)


def render_png(doc: pdfium.PdfDocument, page_no: int, dpi: int = OCR_DPI) -> bytes:
    page = doc[page_no - 1]
    try:
        bitmap = page.render(scale=dpi / 72)
        img = bitmap.to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        page.close()
