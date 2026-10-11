"""Word 文書をブロック列（protocol §3.2 のブロックID）にする。

ID は種類ごとに 1 から振る（p: 段落・見出し、tbl: 表、hdr:/ftr: ヘッダー/フッター、img: 画像、
brk: 改ページ）。読み取りのたびに文書の先頭から振り直す。表のセルは tbl:N/rRcC（0 始まり）。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docx
from docx.document import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.styles import BabelFish
from lxml import etree

from office_tools.errors import block_not_found, corrupt, invalid, locked, not_found, unsupported
from office_tools.fsutil import ext

EMU_PER_MM = 36000
_HEADING_NAME = re.compile(r"^(?:heading|見出し)\s*([1-9])$", re.IGNORECASE)
_CELL_ID = re.compile(r"^(tbl:\d+)/r(\d+)c(\d+)$")

W_P, W_TBL, W_TR, W_TC, W_R, W_T = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:tc"), qn("w:r"), qn("w:t")
W_DEL, W_INS, W_BR, W_TAB, W_CR = qn("w:del"), qn("w:ins"), qn("w:br"), qn("w:tab"), qn("w:cr")
W_SDT, W_SDTCONTENT, W_CUSTOMXML = qn("w:sdt"), qn("w:sdtContent"), qn("w:customXml")
W_DRAWING, W_PICT = qn("w:drawing"), qn("w:pict")
MC_ALT = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
_SKIP_TEXT = {W_DEL, W_DRAWING, W_PICT, MC_ALT, qn("w:pPr"), qn("w:rPr"), qn("w:instrText"), qn("w:delText")}

_TEMPLATE_CT = (
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    b"application/vnd.ms-word.document.macroEnabled.main+xml",
    b"application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
)
_DOCUMENT_CT = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


# ---------------------------------------------------------------- 開く


def _as_document_package(real: Path) -> io.BytesIO:
    """.dotx/.docm の content type を .docx に読み替えたメモリ上のパッケージ。"""
    src = zipfile.ZipFile(real)
    buf = io.BytesIO()
    with src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "[Content_Types].xml":
                for ct in _TEMPLATE_CT:
                    data = data.replace(ct, _DOCUMENT_CT)
            out.writestr(info, data)
    buf.seek(0)
    return buf


def open_document(path: str, real: Path, *, for_edit: bool = False) -> Document:
    e = ext(real)
    if e == ".doc":
        raise unsupported(path, ".doc は convert.to_docx で .docx に変換してから扱ってください")
    if e not in (".docx", ".docm", ".dotx", ".dotm"):
        raise unsupported(path, f"{e or '拡張子なし'} は Word 文書として扱えません（.docx に対応）")
    if for_edit and e != ".docx":
        raise unsupported(path, f"{e} は編集できません。.docx で保存したものを編集してください")
    if not real.exists():
        raise not_found(path)
    try:
        if e == ".docx":
            return docx.Document(str(real))
        return docx.Document(_as_document_package(real))
    except PermissionError as err:
        raise locked(path) from err
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError, etree.XMLSyntaxError) as err:
        raise corrupt(path, type(err).__name__) from err


# ---------------------------------------------------------------- テキスト


def element_text(el: Any) -> str:
    """段落などのテキスト（削除済み・図形内を除く。タブは \\t、改行は \\n）。"""
    parts: list[str] = []

    def walk(node: Any) -> None:
        for child in node:
            tag = child.tag
            if not isinstance(tag, str) or tag in _SKIP_TEXT:
                continue
            if tag == W_T:
                parts.append(child.text or "")
            elif tag == W_TAB:
                parts.append("\t")
            elif tag == W_CR or (tag == W_BR and child.get(qn("w:type")) not in ("page", "column")):
                parts.append("\n")
            elif tag == qn("w:noBreakHyphen"):
                parts.append("-")
            elif tag == W_P:
                if parts and not parts[-1].endswith("\n"):
                    parts.append("\n")
                walk(child)
            else:
                walk(child)

    walk(el)
    text = "".join(parts)
    return text if el.tag == W_P else text.strip("\n")


def run_has_drawing(r: Any) -> bool:
    return any(child.tag in (W_DRAWING, W_PICT, MC_ALT) for child in r)


def page_breaks(p: Any) -> list[Any]:
    out = []
    for br in p.iter(W_BR):
        if br.get(qn("w:type")) == "page" and not any(a.tag == W_DEL for a in br.iterancestors()):
            out.append(br)
    return out


def drawings(p: Any) -> list[Any]:
    """段落内の画像を含む run。"""
    out = []
    for r in p.iter(W_R):
        if any(a.tag == W_DEL for a in r.iterancestors()):
            continue
        if any(child.tag == W_DRAWING for child in r):
            out.append(r)
    return out


# ---------------------------------------------------------------- スタイル・番号


@dataclass
class StyleInfo:
    name: str
    outline: int | None
    num_id: str | None
    num_ilvl: int
    based_on: str | None


class Styles:
    def __init__(self, doc: Document) -> None:
        self.by_id: dict[str, StyleInfo] = {}
        self.default_para = "Normal"
        root = doc.styles.element
        for s in root.findall(qn("w:style")):
            sid = s.get(qn("w:styleId")) or ""
            name_el = s.find(qn("w:name"))
            name = name_el.get(qn("w:val")) if name_el is not None else sid
            name = BabelFish.internal2ui(name) if name else sid
            ppr = s.find(qn("w:pPr"))
            outline = None
            num_id = None
            ilvl = 0
            if ppr is not None:
                o = ppr.find(qn("w:outlineLvl"))
                if o is not None:
                    outline = int(o.get(qn("w:val"), "9"))
                num = ppr.find(qn("w:numPr"))
                if num is not None:
                    nid = num.find(qn("w:numId"))
                    lvl = num.find(qn("w:ilvl"))
                    num_id = nid.get(qn("w:val")) if nid is not None else None
                    ilvl = int(lvl.get(qn("w:val"), "0")) if lvl is not None else 0
            based = s.find(qn("w:basedOn"))
            self.by_id[sid] = StyleInfo(
                name or sid, outline, num_id, ilvl, based.get(qn("w:val")) if based is not None else None
            )
            if s.get(qn("w:type")) == "paragraph" and s.get(qn("w:default")) in ("1", "true"):
                self.default_para = sid
        self._numfmt: dict[tuple[str, int], str] = {}
        try:
            numbering = doc.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            numbering = None
        if numbering is not None:
            abstract = {a.get(qn("w:abstractNumId")): a for a in numbering.findall(qn("w:abstractNum"))}
            for num in numbering.findall(qn("w:num")):
                nid = num.get(qn("w:numId")) or ""
                aid_el = num.find(qn("w:abstractNumId"))
                a = abstract.get(aid_el.get(qn("w:val")) if aid_el is not None else None)
                if a is None:
                    continue
                for lvl in a.findall(qn("w:lvl")):
                    fmt = lvl.find(qn("w:numFmt"))
                    self._numfmt[(nid, int(lvl.get(qn("w:ilvl"), "0")))] = (
                        fmt.get(qn("w:val"), "decimal") if fmt is not None else "decimal"
                    )

    def chain(self, sid: str | None) -> list[StyleInfo]:
        out: list[StyleInfo] = []
        seen: set[str] = set()
        while sid and sid in self.by_id and sid not in seen:
            seen.add(sid)
            info = self.by_id[sid]
            out.append(info)
            sid = info.based_on
        return out

    def describe(self, p: Any) -> dict[str, Any]:
        ppr = p.find(qn("w:pPr"))
        sid = None
        outline = None
        num_id = None
        ilvl = 0
        if ppr is not None:
            ps = ppr.find(qn("w:pStyle"))
            sid = ps.get(qn("w:val")) if ps is not None else None
            o = ppr.find(qn("w:outlineLvl"))
            if o is not None:
                outline = int(o.get(qn("w:val"), "9"))
            num = ppr.find(qn("w:numPr"))
            if num is not None:
                nid = num.find(qn("w:numId"))
                lvl = num.find(qn("w:ilvl"))
                num_id = nid.get(qn("w:val")) if nid is not None else None
                ilvl = int(lvl.get(qn("w:val"), "0")) if lvl is not None else 0
        sid = sid or self.default_para
        chain = self.chain(sid)
        name = chain[0].name if chain else sid
        level = None
        m = _HEADING_NAME.match(name.strip())
        if m:
            level = int(m.group(1))
        else:
            if outline is None:
                outline = next((s.outline for s in chain if s.outline is not None), None)
            if outline is not None and 0 <= outline <= 8:
                level = outline + 1
        if num_id is None:
            for s in chain:
                if s.num_id is not None:
                    num_id, ilvl = s.num_id, s.num_ilvl
                    break
        out: dict[str, Any] = {"style": name, "level": level}
        if num_id and num_id != "0":
            fmt = self._numfmt.get((num_id, ilvl), "decimal")
            out["list"] = {"level": ilvl, "numbered": fmt not in ("bullet", "none")}
        return out


# ---------------------------------------------------------------- ブロック


@dataclass
class Block:
    id: str
    kind: str
    el: Any
    info: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    para: Any = None
    cells: dict[str, Any] = field(default_factory=dict)
    """表: "rRcC" → w:tc（横結合で覆われたセルは結合元の w:tc）。"""
    rows: list[Any] = field(default_factory=list)
    merged: dict[str, str] = field(default_factory=dict)


@dataclass
class DocIndex:
    blocks: list[Block]
    by_id: dict[str, Block]
    comment_anchor: dict[str, str]

    def get(self, block_id: str) -> Block:
        if block_id not in self.by_id:
            raise block_not_found(block_id)
        return self.by_id[block_id]

    def cell(self, cell_id: str) -> tuple[Block, Any, str]:
        m = _CELL_ID.match(cell_id)
        if not m:
            raise invalid(f"{cell_id} はセルの ID ではありません（tbl:3/r2c4 の形式）")
        table = self.get(m.group(1))
        if table.kind != "table":
            raise invalid(f"{m.group(1)} は表ではありません")
        key = f"r{m.group(2)}c{m.group(3)}"
        if key not in table.cells:
            raise block_not_found(cell_id)
        return table, table.cells[key], table.merged.get(key, "")


def _table_block(tbl: Any, bid: str) -> Block:
    rows = tbl.findall(W_TR)
    cells_out: list[list[dict[str, Any]]] = []
    lines: list[str] = []
    b = Block(bid, "table", tbl, rows=rows)
    max_cols = 0
    for ri, tr in enumerate(rows):
        col = 0
        trpr = tr.find(qn("w:trPr"))
        if trpr is not None:
            gb = trpr.find(qn("w:gridBefore"))
            if gb is not None:
                col = int(gb.get(qn("w:val"), "0"))
        row_out: list[dict[str, Any]] = []
        for tc in tr.findall(W_TC):
            tcpr = tc.find(qn("w:tcPr"))
            span = 1
            vmerge_cont = False
            if tcpr is not None:
                gs = tcpr.find(qn("w:gridSpan"))
                if gs is not None:
                    span = max(1, int(gs.get(qn("w:val"), "1")))
                vm = tcpr.find(qn("w:vMerge"))
                if vm is not None and vm.get(qn("w:val"), "continue") != "restart":
                    vmerge_cont = True
            text = element_text(tc)
            for k in range(span):
                key = f"r{ri}c{col + k}"
                b.cells[key] = tc
                cell: dict[str, Any] = {"id": f"{bid}/{key}"}
                if k > 0:
                    cell["merged"] = "h"
                    b.merged[key] = "h"
                elif vmerge_cont:
                    cell["merged"] = "v"
                    b.merged[key] = "v"
                elif text:
                    lines.append(f"[{key}] {text}")
                row_out.append(cell)
            col += span
        max_cols = max(max_cols, col)
        cells_out.append(row_out)
    b.info = {"rows": len(rows), "cols": max_cols, "cells": cells_out}
    b.text = "\n".join(lines)
    return b


def _image_info(run: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"width_mm": 0.0, "height_mm": 0.0}
    for extent in run.iter(qn("wp:extent")):
        info["width_mm"] = round(int(extent.get("cx", "0")) / EMU_PER_MM, 1)
        info["height_mm"] = round(int(extent.get("cy", "0")) / EMU_PER_MM, 1)
        break
    for pr in run.iter(qn("wp:docPr")):
        alt = pr.get("descr") or pr.get("title")
        if alt:
            info["alt"] = alt
        break
    return info


def index_document(doc: Document) -> DocIndex:
    styles = Styles(doc)
    counters = {"p": 0, "tbl": 0, "hdr": 0, "ftr": 0, "img": 0, "brk": 0}
    blocks: list[Block] = []
    comment_anchor: dict[str, str] = {}

    def next_id(prefix: str) -> str:
        counters[prefix] += 1
        return f"{prefix}:{counters[prefix]}"

    # ヘッダー・フッター（セクション順、同じパーツは1回だけ）
    seen_parts: set[str] = set()
    for si, section in enumerate(doc.sections, start=1):
        sect = section._sectPr
        for tag, kind, prefix in (
            ("w:headerReference", "header", "hdr"),
            ("w:footerReference", "footer", "ftr"),
        ):
            for ref in sect.findall(qn(tag)):
                rid = ref.get(qn("r:id"))
                part = doc.part.related_parts.get(rid) if rid else None
                if part is None or str(part.partname) in seen_parts:
                    continue
                seen_parts.add(str(part.partname))
                el = part.element
                b = Block(next_id(prefix), kind, el, text=element_text(el))
                b.info = {"section": si, "type": ref.get(qn("w:type"), "default")}
                blocks.append(b)

    def note_comments(el: Any, bid: str) -> None:
        for c in el.iter(qn("w:commentRangeStart"), qn("w:commentReference")):
            cid = c.get(qn("w:id"))
            if cid is not None and cid not in comment_anchor:
                comment_anchor[cid] = bid

    def visit(parent: Any) -> None:
        for child in parent:
            tag = child.tag
            if tag == W_P:
                visit_p(child)
            elif tag == W_TBL:
                b = _table_block(child, next_id("tbl"))
                note_comments(child, b.id)
                blocks.append(b)
            elif tag == W_SDT:
                content = child.find(W_SDTCONTENT)
                if content is not None:
                    visit(content)
            elif tag == W_CUSTOMXML:
                visit(child)

    def visit_p(p: Any) -> None:
        text = element_text(p)
        imgs = drawings(p)
        brks = page_breaks(p)
        if not text.strip() and brks and not imgs:
            b = Block(next_id("brk"), "page_break", p, para=p)
            blocks.append(b)
            return
        if text.strip() or not imgs:
            d = styles.describe(p)
            pid = next_id("p")
            if d["level"] is not None:
                b = Block(pid, "heading", p, {"level": d["level"], "style": d["style"]}, text)
            else:
                info: dict[str, Any] = {"style": d["style"]}
                if "list" in d:
                    info["list"] = d["list"]
                b = Block(pid, "paragraph", p, info, text)
            b.para = p
            note_comments(p, pid)
            blocks.append(b)
        for r in imgs:
            blocks.append(Block(next_id("img"), "image", r, _image_info(r), para=p))
        for br in brks if text.strip() or imgs else []:
            blocks.append(Block(next_id("brk"), "page_break", br, para=p))

    visit(doc.element.body)
    return DocIndex(blocks, {b.id: b for b in blocks}, comment_anchor)
