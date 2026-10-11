"""本文抽出（NET-09）。

- HTML: まず自前の構文解析で script / style / コメント / hidden 要素などを取り除いた HTML を作り、
  Trafilatura があればそれで本文を抽出する（無い・失敗した場合は自前の簡易抽出）。
  Trafilatura に渡す前に取り除くので、hidden 要素に仕込んだ指示文は Trafilatura の挙動に関わらず入らない。
- PDF: pdfplumber でページごとに本文を取り出す（ページ数の上限あり）。
- text/plain: 文字コードを判定してそのまま。
- 文字数は `max_chars` で切り詰める。指示文の扱い（`<untrusted>` で包む）は Runtime 側の責務。
"""

from __future__ import annotations

import codecs
import html
import importlib
import io
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, cast

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "svg",
        "math",
        "canvas",
        "select",
        "datalist",
        "dialog",
        "audio",
        "video",
        "map",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
# 見えない要素とみなす class（CSS フレームワークでよく使う名前）
_HIDDEN_CLASSES = frozenset(
    {"hidden", "d-none", "invisible", "sr-only", "visually-hidden", "screen-reader-text"}
)
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|font-size\s*:\s*0(?:\.0+)?(?:px|em|rem|pt|%)?\s*(?:;|$|!)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$|!)|(?:height|width)\s*:\s*0(?:px)?\s*(?:;|$|!)",
    re.IGNORECASE,
)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_KEEP_ATTRS = frozenset({"id", "class", "lang", "dir", "role", "itemprop"})


def _is_hidden(tag: str, attrs: dict[str, str | None]) -> bool:
    if "hidden" in attrs:
        return True
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if tag == "input" and (attrs.get("type") or "").strip().lower() == "hidden":
        return True
    style = attrs.get("style") or ""
    if style and _HIDDEN_STYLE.search(style):
        return True
    classes = set((attrs.get("class") or "").lower().split())
    return bool(classes & _HIDDEN_CLASSES)


class _Sanitizer(HTMLParser):
    """不要な要素を除いた HTML と、簡易抽出のテキストを同時に作る。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self._skipping = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.html_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): v for k, v in attrs}
        skip = tag in _SKIP_TAGS or _is_hidden(tag, a)
        if tag == "title" and not self._skipping:
            self._in_title = True
            return
        if tag not in _VOID_TAGS:
            self._stack.append((tag, skip))
            if skip:
                self._skipping += 1
        if self._skipping or skip:
            return
        kept = "".join(
            f' {k}="{html.escape(v or "", quote=True)}"'
            for k, v in a.items()
            if k in _KEEP_ATTRS and v is not None
        )
        self.html_parts.append(f"<{tag}{kept}>")
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): v for k, v in attrs}
        if self._skipping or tag in _SKIP_TAGS or _is_hidden(tag, a):
            return
        self.html_parts.append(f"<{tag}>")
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            return
        if tag in _VOID_TAGS:
            return
        if not any(t == tag for t, _ in self._stack):
            return  # 対応する開始タグがない終了タグは無視
        while self._stack:
            t, skip = self._stack.pop()
            if skip:
                self._skipping -= 1
            elif not self._skipping:
                self.html_parts.append(f"</{t}>")
                if t in _BLOCK_TAGS:
                    self.text_parts.append("\n")
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skipping:
            return
        self.text_parts.append(data)
        self.html_parts.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return  # コメントは捨てる

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


@dataclass(frozen=True)
class Extracted:
    title: str
    text: str
    chars: int
    truncated: bool


def clean_text(text: str) -> str:
    """制御文字を除き、行ごとに空白をそろえ、連続する空行をまとめる。"""
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co")
    )
    lines = [re.sub(r"[ \t　\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln:
            if out and out[-1] == "":
                continue
            out.append("")
        else:
            out.append(ln)
    return "\n".join(out).strip()


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def decode_bytes(body: bytes, charset: str | None, *, sniff_html: bool) -> str:
    candidates: list[str] = []
    if body.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    if charset:
        candidates.append(charset)
    if sniff_html:
        m = _META_CHARSET.search(body[:4096])
        if m:
            candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp932", "euc_jp"]
    for enc in candidates:
        try:
            codecs.lookup(enc)
        except LookupError:
            continue
        name = enc.lower().replace("_", "-")
        if name in ("shift-jis", "sjis", "x-sjis", "shift-jisx0213"):
            enc = "cp932"
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def _trafilatura_extract() -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module("trafilatura")
    except ImportError:
        return None
    fn = getattr(mod, "extract", None)
    return cast(Callable[..., Any], fn) if callable(fn) else None


def extract_html(raw: str, *, max_chars: int, use_trafilatura: bool = True) -> Extracted:
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    title = clean_text(" ".join(parser.title_parts))[:300]
    fallback = clean_text("".join(parser.text_parts))
    text = ""
    if use_trafilatura:
        extract = _trafilatura_extract()
        if extract is not None:
            cleaned_html = "<html><body>" + "".join(parser.html_parts) + "</body></html>"
            try:
                result = extract(
                    cleaned_html,
                    include_comments=False,
                    include_tables=True,
                    include_images=False,
                    include_links=False,
                    favor_recall=True,
                    output_format="txt",
                )
            except Exception:  # noqa: BLE001 - 抽出器の失敗は簡易抽出で補う
                result = None
            if isinstance(result, str):
                text = clean_text(result)
    if not text:
        text = fallback
    body, truncated = truncate(text, max_chars)
    return Extracted(title=title, text=body, chars=len(body), truncated=truncated)


def extract_text_plain(raw: str, *, max_chars: int) -> Extracted:
    body, truncated = truncate(clean_text(raw), max_chars)
    return Extracted(title="", text=body, chars=len(body), truncated=truncated)


def extract_pdf(data: bytes, *, max_chars: int, max_pages: int) -> Extracted:
    """pdfplumber で本文を取り出す（同期処理。呼び出し側でスレッドに逃がす）。"""
    import pdfplumber

    parts: list[str] = []
    total = 0
    title = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        meta = pdf.metadata or {}
        t = meta.get("Title")
        if isinstance(t, str):
            title = clean_text(t)[:300]
        for i, page in enumerate(pdf.pages):
            if i >= max_pages or total > max_chars:
                break
            page_text = page.extract_text() or ""
            parts.append(page_text)
            total += len(page_text)
    text = clean_text("\n\n".join(parts))
    body, truncated = truncate(text, max_chars)
    return Extracted(title=title, text=body, chars=len(body), truncated=truncated)
