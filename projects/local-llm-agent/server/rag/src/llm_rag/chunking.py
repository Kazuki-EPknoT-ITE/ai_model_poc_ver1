"""構造を保った分割（設計書 §6.4: 見出し・表・ページ単位、約800トークン、重なり100）。

- 抽出側（`extract.py`）が文書を「単位」（見出しの節・表・ページ・本文）に分ける。
- チャンクは単位をまたがない。1つの単位が長いときだけ、行の区切りで窓に分ける。
- 本文の窓は直前の窓の末尾（約 overlap 文字）を先頭に重ねる。表は重ねる代わりに見出し行を繰り返す。
- 見出しの経路（context）は各チャンクの先頭に入れる（検索の手がかりと出典表示のため）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from llm_rag.config import ChunkSettings

MAX_CONTEXT_CHARS = 200
_BOUNDARIES = ("\n", "。", "．", ". ", "！", "？")


@dataclass
class Unit:
    kind: str
    """section（見出しの節）/ table / page / text。"""
    lines: list[str]
    location: str = ""
    """単位全体の出典表示（例 "第2章 > 第5条"、"p.12"）。"""
    context: str = ""
    """チャンクの先頭に付ける見出しの経路。"""
    header: list[str] = field(default_factory=list)
    """表: 各チャンクの先頭に繰り返す見出し行。"""
    locate: Callable[[int, int], str] | None = None
    """行の範囲（0 始まり、両端を含む）→ 出典表示。省略時は location。"""


@dataclass
class Chunk:
    index: int
    location: str
    text: str
    kind: str


def _tail(text: str, overlap: int) -> str:
    """直前の窓の末尾 overlap 文字。なるべく文や行の区切りから始める。"""
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    best = -1
    for b in _BOUNDARIES:
        pos = tail.find(b)
        if pos != -1 and (best == -1 or pos < best):
            best = pos + len(b)
    if best != -1 and len(tail) - best >= overlap // 3:
        tail = tail[best:]
    return tail.lstrip("\n")


def _pieces(lines: list[str], limit: int, overlap: int) -> list[tuple[int, str]]:
    """(元の行番号, テキスト)。limit を超える行は重なりを付けて切る。"""
    out: list[tuple[int, str]] = []
    step = max(1, limit - overlap)
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        if len(ln) <= limit:
            out.append((i, ln))
            continue
        for s in range(0, len(ln), step):
            out.append((i, ln[s : s + limit]))
            if s + limit >= len(ln):
                break
    return out


def _windows(lines: list[str], budget: int, overlap: int) -> list[tuple[int, int, str]]:
    """(最初の行, 最後の行, テキスト) の窓。テキストには直前の窓との重なりを含む。"""
    pieces = _pieces(lines, max(1, budget - overlap), overlap)
    out: list[tuple[int, int, str]] = []
    cur: list[str] = []
    cur_len = 0
    first = last = -1
    lead = ""
    for line_no, text in pieces:
        add = len(text) + (1 if cur or lead else 0)
        if cur and len(lead) + cur_len + add > budget:
            body = "\n".join(cur)
            out.append((first, last, (lead + "\n" + body) if lead else body))
            lead = _tail(body, overlap)
            cur, cur_len, first = [], 0, -1
            add = len(text) + (1 if lead else 0)
        if first == -1:
            first = line_no
        last = line_no
        cur.append(text)
        cur_len += add
    if cur:
        body = "\n".join(cur)
        out.append((first, last, (lead + "\n" + body) if lead else body))
    return out


def _table_windows(lines: list[str], budget: int) -> list[tuple[int, int, str]]:
    """表の行を重ねずに詰める（各窓の先頭には呼び出し側が見出し行を付ける）。"""
    return _windows(lines, budget, 0)


def chunk_units(units: list[Unit], settings: ChunkSettings) -> list[Chunk]:
    chunks: list[Chunk] = []
    for u in units:
        context = u.context[:MAX_CONTEXT_CHARS]
        prefix_lines = ([context] if context else []) + [h for h in u.header if h.strip()]
        prefix = "\n".join(prefix_lines)
        budget = max(settings.max_chars // 4, settings.max_chars - len(prefix) - 1)
        if u.kind == "table":
            wins = _table_windows(u.lines, budget)
        else:
            wins = _windows(u.lines, budget, min(settings.overlap_chars, budget // 3))
        for first, last, body in wins:
            if not body.strip():
                continue
            loc = u.locate(first, last) if u.locate is not None else u.location
            text = f"{prefix}\n{body}" if prefix else body
            chunks.append(Chunk(len(chunks), loc, text, u.kind))
    return chunks
