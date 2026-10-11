"""docx.edit の op の適用（protocol §3.2）。

- op は元の文書の ID を基準にする。先に全 op の参照（要素）を解決してから、順に適用する。
- replace_text は最初の run の書式を引き継ぎ、replace_in_block はほかの run の書式を保つ。
- track_changes では w:ins / w:del（段落記号・表の行は w:rPr / w:trPr の中の ins/del）で書く。
- 編集しない要素の XML には触らない。
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from docx.document import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from office_tools.docx.model import (
    MC_ALT,
    W_DEL,
    W_DRAWING,
    W_INS,
    W_P,
    W_PICT,
    W_R,
    W_T,
    W_TBL,
    W_TC,
    W_TR,
    Block,
    DocIndex,
)
from office_tools.errors import ToolFailure, invalid

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_TEXT_CONTAINERS = {qn("w:hyperlink"), qn("w:smartTag"), W_INS, qn("w:fldSimple"), qn("w:customXml")}


@dataclass
class Tracker:
    """変更履歴の属性（w:id / w:author / w:date）。track が False なら直接編集する。"""

    track: bool
    author: str
    next_id: int
    date: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def el(self, tag: str) -> Any:
        e = OxmlElement(tag)
        e.set(qn("w:id"), str(self.next_id))
        e.set(qn("w:author"), self.author)
        e.set(qn("w:date"), self.date)
        self.next_id += 1
        return e


def max_change_id(doc: Document) -> int:
    best = 0
    for el in doc.element.iter():
        v = el.get(qn("w:id")) if isinstance(el.tag, str) else None
        if v is not None and v.lstrip("-").isdigit():
            best = max(best, int(v))
    return best


# ---------------------------------------------------------------- run の操作


def text_runs(p: Any) -> list[Any]:
    """段落内の文字を持つ run（画像を除く。削除済みは除く）。"""
    out: list[Any] = []

    def walk(node: Any) -> None:
        for child in node:
            if child.tag == W_R:
                if not any(c.tag in (W_DRAWING, W_PICT, MC_ALT) for c in child):
                    out.append(child)
            elif child.tag in _TEXT_CONTAINERS:
                walk(child)

    walk(p)
    return out


def first_rpr(runs: list[Any]) -> Any:
    for r in runs:
        rpr = r.find(qn("w:rPr"))
        if rpr is not None:
            return copy.deepcopy(rpr)
        if r.find(W_T) is not None:
            return None
    return None


def make_run(text: str, rpr: Any) -> Any:
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    Run(r, None).text = text  # type: ignore[arg-type]  # \n → w:br, \t → w:tab
    return r


def mark_run_deleted(run: Any, tr: Tracker) -> None:
    for t in run.findall(W_T):
        t.tag = qn("w:delText")
    for t in run.findall(qn("w:instrText")):
        t.tag = qn("w:delInstrText")
    d = tr.el("w:del")
    run.addprevious(d)
    d.append(run)


def wrap_inserted(run: Any, tr: Tracker) -> Any:
    ins = tr.el("w:ins")
    ins.append(run)
    return ins


def _mark_para(p: Any, tag: str, tr: Tracker) -> None:
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p.insert(0, ppr)
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        ppr.append(rpr)
    rpr.append(tr.el(tag))


def _mark_row(row: Any, tag: str, tr: Tracker) -> None:
    trpr = row.find(qn("w:trPr"))
    if trpr is None:
        trpr = OxmlElement("w:trPr")
        tblpr_ex = row.find(qn("w:tblPrEx"))
        if tblpr_ex is not None:
            tblpr_ex.addnext(trpr)
        else:
            row.insert(0, trpr)
    trpr.append(tr.el(tag))


def _all_runs(el: Any) -> list[Any]:
    return [r for r in el.iter(W_R) if not any(a.tag == W_DEL for a in r.iterancestors())]


def mark_inserted_tree(el: Any, tr: Tracker) -> None:
    """新しく作った段落・表を「挿入」として記録する。"""
    paras = [el] if el.tag == W_P else list(el.iter(W_P))
    for r in _all_runs(el):
        wrap_here = tr.el("w:ins")
        r.addprevious(wrap_here)
        wrap_here.append(r)
    for p in paras:
        _mark_para(p, "w:ins", tr)
    if el.tag == W_TBL:
        for row in el.findall(W_TR):
            _mark_row(row, "w:ins", tr)


def delete_tree(el: Any, tr: Tracker) -> None:
    """段落・表を削除する（変更履歴なら削除として記録）。"""
    if not tr.track:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
        return
    for r in _all_runs(el):
        mark_run_deleted(r, tr)
    paras = [el] if el.tag == W_P else list(el.iter(W_P))
    for p in paras:
        _mark_para(p, "w:del", tr)
    if el.tag == W_TBL:
        for row in el.findall(W_TR):
            _mark_row(row, "w:del", tr)


# ---------------------------------------------------------------- テキストの置き換え


def set_paragraph_text(p: Any, text: str, tr: Tracker) -> None:
    """段落の文字を置き換える。最初の run の書式を引き継ぐ。画像の run は残す。"""
    runs = text_runs(p)
    rpr = first_rpr(runs)
    new_run = make_run(text, rpr)
    if tr.track:
        for r in runs:
            mark_run_deleted(r, tr)
        node = wrap_inserted(new_run, tr)
        last = runs[-1].getparent() if runs else None
        if last is not None:
            last.addnext(node)
        else:
            p.append(node)
        return
    insert_at: int | None = None
    for r in runs:
        top = r
        while top.getparent() is not p:
            top = top.getparent()
        if insert_at is None:
            insert_at = list(p).index(top)
        parent = r.getparent()
        parent.remove(r)
        # 空になったコンテナ（ハイパーリンクなど）を取り除く
        while parent is not p and not any(c.tag == W_R for c in parent.iter()):
            gp = parent.getparent()
            gp.remove(parent)
            parent = gp
    if insert_at is None:
        p.append(new_run)
    else:
        p.insert(min(insert_at, len(p)), new_run)


def container_paragraphs(el: Any) -> list[Any]:
    if el.tag == W_P:
        return [el]
    return [c for c in el if c.tag == W_P]


def set_container_text(el: Any, text: str, tr: Tracker) -> None:
    """セル・ヘッダーの文字を置き換える（最初の段落に入れ、ほかの段落は削除）。"""
    paras = container_paragraphs(el)
    if not paras:
        p = OxmlElement("w:p")
        el.append(p)
        paras = [p]
    set_paragraph_text(paras[0], text, tr)
    for p in paras[1:]:
        delete_tree(p, tr)


def _isolate(run: Any, t: Any, a: int, b: int) -> Any:
    """run 内の w:t ノード t の [a, b) を別の run に切り出して返す（前後も別の run にする）。"""
    children = list(run)
    rpr = run.find(qn("w:rPr"))
    k = children.index(t)
    text = t.text or ""

    def new_run(nodes: list[Any]) -> Any:
        r = OxmlElement("w:r")
        for attr, v in run.attrib.items():
            r.set(attr, v)
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        for n in nodes:
            r.append(n)
        return r

    def t_node(s: str) -> Any:
        n = OxmlElement("w:t")
        n.text = s
        n.set(XML_SPACE, "preserve")
        return n

    before = [c for c in children[:k] if c is not rpr] + ([t_node(text[:a])] if a > 0 else [])
    after = ([t_node(text[b:])] if b < len(text) else []) + children[k + 1 :]
    mid = new_run([t_node(text[a:b])])
    if before:
        run.addprevious(new_run(before))
    run.addprevious(mid)
    if after:
        run.addprevious(new_run(after))
    run.getparent().remove(run)
    return mid


def _common_affixes(find: str, replace: str) -> tuple[int, int]:
    """find と replace の共通の先頭・末尾の長さ（変わる部分だけを書き換えるため）。"""
    n = min(len(find), len(replace))
    pre = 0
    while pre < n and find[pre] == replace[pre]:
        pre += 1
    suf = 0
    while suf < n - pre and find[len(find) - 1 - suf] == replace[len(replace) - 1 - suf]:
        suf += 1
    return pre, suf


def _locate(nodes: list[tuple[Any, Any]], start: int, end: int) -> list[tuple[Any, Any, int, int]]:
    """[start, end) にかかる w:t ノードと、ノード内の範囲。end == start なら挿入位置のノード。"""
    spans: list[tuple[Any, Any, int, int]] = []
    off = 0
    for r, t in nodes:
        s = t.text or ""
        lo, hi = off, off + len(s)
        if end > start and hi > start and lo < end:
            spans.append((r, t, max(start, lo) - lo, min(end, hi) - lo))
        elif end == start and lo <= start <= hi and not spans:
            spans.append((r, t, start - lo, start - lo))
        off = hi
    return spans


def replace_in_paragraph(p: Any, find: str, replace: str, limit: int, tr: Tracker) -> int:
    """段落内の find を replace に置き換える（run をまたいでもよい）。置き換えた数を返す。

    find と replace の共通の先頭・末尾は書き換えない（例: 30日以内 → 45日以内 は 30 → 45 だけ）。
    挿入する文字は、変わる部分の先頭がある run の書式になる。
    """
    pre, suf = _common_affixes(find, replace)
    new_text = replace[pre : len(replace) - suf]
    count = 0
    pos = 0
    while count < limit:
        nodes = [(r, t) for r in text_runs(p) for t in r.findall(W_T)]
        full = "".join(t.text or "" for _, t in nodes)
        start = full.find(find, pos)
        if start < 0:
            break
        a_off, b_off = start + pre, start + len(find) - suf
        spans = _locate(nodes, a_off, b_off)
        if spans and (b_off > a_off or new_text):
            if tr.track:
                rpr = spans[0][0].find(qn("w:rPr"))
                mids = [_isolate(r, t, a, b) for r, t, a, b in spans]
                if b_off > a_off:
                    for m in mids:
                        mark_run_deleted(m, tr)
                    anchor = mids[-1].getparent()
                else:
                    anchor = mids[0]
                if new_text:
                    anchor.addnext(wrap_inserted(make_run(new_text, rpr), tr))
                if b_off == a_off:
                    mids[0].getparent().remove(mids[0])
            else:
                _, t0, a0, b0 = spans[0]
                s0 = t0.text or ""
                t0.text = s0[:a0] + new_text + (s0[b0:] if len(spans) == 1 else "")
                for _, tm, am, bm in spans[1:]:
                    sm = tm.text or ""
                    tm.text = sm[:am] + sm[bm:]
                for _, tm, _, _ in spans:
                    tm.set(XML_SPACE, "preserve")
        pos = start + len(replace)
        count += 1
    return count


def replace_in_container(el: Any, find: str, replace: str, limit: int, tr: Tracker) -> int:
    total = 0
    paras = [el] if el.tag == W_P else list(el.iter(W_P))
    for p in paras:
        if total >= limit:
            break
        total += replace_in_paragraph(p, find, replace, limit - total, tr)
    return total


# ---------------------------------------------------------------- 新しいブロック


class BlockFactory:
    """NewBlock から本文の要素を作る（python-docx で作って本文の末尾から取り出す）。"""

    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.warnings: list[str] = []

    def _style(self, name: str) -> Any:
        try:
            return self.doc.styles[name]
        except KeyError:
            for s in self.doc.styles:
                if getattr(s, "style_id", None) == name:
                    return s
            return None

    def _detach(self, el: Any) -> Any:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
        return el

    def build(self, nb: dict[str, Any]) -> Any:
        kind = nb["kind"]
        if kind == "heading":
            level = int(nb["level"])
            style = self._style(f"Heading {level}")
            p = self.doc.add_paragraph(style=style) if style is not None else self.doc.add_paragraph()
            if style is None:
                p.paragraph_format.element.get_or_add_pPr()
                ol = OxmlElement("w:outlineLvl")
                ol.set(qn("w:val"), str(level - 1))
                p._p.get_or_add_pPr().append(ol)
                self.warnings.append(
                    f"見出し{level}のスタイルが無いため、アウトラインレベルだけを設定しました"
                )
            p.add_run(nb["text"])
            return self._detach(p._p)
        if kind == "paragraph":
            style = None
            if nb.get("style"):
                style = self._style(nb["style"])
                if style is None:
                    self.warnings.append(f"スタイル {nb['style']} が無いため、標準のスタイルにしました")
            text = nb["text"]
            if nb.get("list") and style is None:
                lst = nb["list"]
                base = "List Number" if lst["numbered"] else "List Bullet"
                name = base if lst["level"] == 0 else f"{base} {lst['level'] + 1}"
                style = self._style(name) or self._style(base)
                if style is None:
                    text = ("・" if not lst["numbered"] else "") + text
                    self.warnings.append(f"箇条書きのスタイル {name} が無いため、記号を付けた段落にしました")
            p = self.doc.add_paragraph(style=style) if style is not None else self.doc.add_paragraph()
            if text:
                p.add_run(text)
            return self._detach(p._p)
        if kind == "table":
            header = nb.get("header") or []
            rows = nb["rows"]
            ncols = max([len(header)] + [len(r) for r in rows] + [1])
            table = self.doc.add_table(rows=(1 if header else 0) + len(rows), cols=ncols)
            style = self._style(nb.get("style") or "Table Grid")
            if style is not None:
                table.style = style
            elif nb.get("style"):
                self.warnings.append(f"表のスタイル {nb['style']} が無いため、標準のスタイルにしました")
            r0 = 0
            if header:
                for j, h in enumerate(header):
                    cell = table.cell(0, j)
                    cell.paragraphs[0].add_run(h).bold = True
                r0 = 1
            for i, row in enumerate(rows):
                for j, v in enumerate(row):
                    if v:
                        table.cell(r0 + i, j).paragraphs[0].add_run(v)
            return self._detach(table._tbl)
        # page_break
        p = self.doc.add_paragraph()
        p.add_run().add_break(WD_BREAK.PAGE)
        return self._detach(p._p)


# ---------------------------------------------------------------- op の実行


@dataclass
class Resolved:
    op: dict[str, Any]
    block: Block | None = None
    cell: Any = None
    merged: str = ""
    row: Any = None


def resolve_ops(index: DocIndex, ops: list[dict[str, Any]]) -> list[Resolved]:
    """全 op の参照を元の文書の ID で解決する（無い ID は BLOCK_NOT_FOUND）。"""
    out: list[Resolved] = []
    for op in ops:
        kind = op["op"]
        if kind == "table_set_cell":
            table, tc, merged = index.cell(op["cell_id"])
            out.append(Resolved(op, table, tc, merged))
        elif kind in ("table_add_row", "table_delete_row"):
            block = index.get(op["table_id"])
            if block.kind != "table":
                raise invalid(f"{op['table_id']} は表ではありません")
            r = Resolved(op, block)
            idx = op.get("row") if kind == "table_delete_row" else op.get("after_row", len(block.rows) - 1)
            if idx is not None and idx >= 0:
                if idx >= len(block.rows):
                    raise invalid(f"{op['table_id']} に行 {idx} はありません（{len(block.rows)} 行）")
                r.row = block.rows[idx]
            out.append(r)
        elif "/" in op.get("block_id", ""):
            table, tc, merged = index.cell(op["block_id"])
            out.append(Resolved(op, table, tc, merged))
        else:
            out.append(Resolved(op, index.get(op["block_id"])))
    return out


class Editor:
    def __init__(self, doc: Document, tracker: Tracker) -> None:
        self.doc = doc
        self.tr = tracker
        self.factory = BlockFactory(doc)
        self._after: dict[int, Any] = {}

    @property
    def warnings(self) -> list[str]:
        return self.factory.warnings

    def _target_text_el(self, r: Resolved) -> Any:
        """文字を置き換える対象（段落・セル・ヘッダー）。"""
        if r.cell is not None:
            if r.merged:
                raise invalid("結合されたセルです。結合元（左上）のセルを指定してください")
            return r.cell
        b = r.block
        assert b is not None
        if b.kind in ("paragraph", "heading", "header", "footer"):
            return b.el
        raise invalid(f"{b.id} は文字を置き換えられるブロックではありません（{b.kind}）")

    def _body_anchor(self, b: Block) -> Any:
        if b.kind in ("header", "footer"):
            raise invalid(f"{b.id} の前後には挿入できません（本文のブロックを指定してください）")
        if b.kind in ("image", "page_break"):
            return b.para
        return b.el

    def _alive(self, el: Any, block_id: str) -> None:
        is_part_root = el is not None and el.tag in (qn("w:hdr"), qn("w:ftr"))
        if el is None or (el.getparent() is None and not is_part_root):
            raise invalid(f"{block_id} は前の op で削除されています")

    def apply(self, r: Resolved) -> None:
        op = r.op
        kind = op["op"]
        if kind == "replace_text":
            el = self._target_text_el(r)
            self._alive(el, op["block_id"])
            if el.tag == W_P:
                set_paragraph_text(el, op["text"], self.tr)
            else:
                set_container_text(el, op["text"], self.tr)
        elif kind == "replace_in_block":
            el = self._target_text_el(r)
            self._alive(el, op["block_id"])
            n = replace_in_container(el, op["find"], op["replace"], int(op.get("count", 1_000_000)), self.tr)
            if n == 0:
                raise invalid(f"{op['block_id']} に「{op['find'][:50]}」が見つかりません")
        elif kind in ("insert_after", "insert_before"):
            assert r.block is not None
            anchor = self._body_anchor(r.block)
            self._alive(anchor, op["block_id"])
            new_els = [self.factory.build(nb) for nb in op["blocks"]]
            if kind == "insert_after":
                cur = self._after.get(id(anchor), anchor)
                for el in new_els:
                    cur.addnext(el)
                    cur = el
                self._after[id(anchor)] = cur
            else:
                for el in new_els:
                    anchor.addprevious(el)
            if self.tr.track:
                for el in new_els:
                    mark_inserted_tree(el, self.tr)
        elif kind == "delete_block":
            b = r.block
            assert b is not None
            if r.cell is not None or b.kind in ("header", "footer"):
                raise invalid(
                    f"{op['block_id']} は削除できません（文字を消す場合は replace_text を使ってください）"
                )
            self._alive(b.el, op["block_id"])
            if b.kind == "image" or (b.kind == "page_break" and b.el.tag != W_P):
                self._delete_inline(b.el)
            else:
                delete_tree(b.el, self.tr)
        elif kind == "table_set_cell":
            self._alive(r.cell, op["cell_id"])
            if r.merged:
                raise invalid("結合されたセルです。結合元（左上）のセルを指定してください")
            set_container_text(r.cell, op["text"], self.tr)
        elif kind == "table_add_row":
            self._add_row(r)
        elif kind == "table_delete_row":
            self._alive(r.row, op["table_id"])
            if self.tr.track:
                for run in _all_runs(r.row):
                    mark_run_deleted(run, self.tr)
                _mark_row(r.row, "w:del", self.tr)
            else:
                r.row.getparent().remove(r.row)
        elif kind == "set_style":
            self._set_style(r)
        else:  # pragma: no cover
            raise invalid(f"不明な op: {kind}")

    def _delete_inline(self, el: Any) -> None:
        """画像の run や段落内の改ページ（w:br）を削除する。"""
        if el.tag == W_R:
            run = el
        else:
            run = el.getparent()
            if run is not None and run.tag == W_R and len([c for c in run if c.tag != qn("w:rPr")]) > 1:
                # run に他の内容があるときは改ページだけを別の run に分ける
                new_run = OxmlElement("w:r")
                rpr = run.find(qn("w:rPr"))
                if rpr is not None:
                    new_run.append(copy.deepcopy(rpr))
                run.addnext(new_run)
                new_run.append(el)
                run = new_run
        if self.tr.track:
            mark_run_deleted(run, self.tr)
        else:
            run.getparent().remove(run)

    def _add_row(self, r: Resolved) -> None:
        op = r.op
        assert r.block is not None
        rows = r.block.rows
        if not rows:
            raise invalid(f"{op['table_id']} に行がありません")
        after = op.get("after_row", len(rows) - 1)
        template = r.row if r.row is not None else rows[0]
        self._alive(template, op["table_id"])
        new_tr = copy.deepcopy(template)
        for tag in (
            "w:ins",
            "w:del",
            "w:bookmarkStart",
            "w:bookmarkEnd",
            "w:commentRangeStart",
            "w:commentRangeEnd",
        ):
            for e in list(new_tr.iter(qn(tag))):
                e.getparent().remove(e)
        tcs = new_tr.findall(W_TC)
        values = op["values"]
        if len(values) > len(tcs):
            raise invalid(f"values が {len(values)} 個ありますが、行のセルは {len(tcs)} 個です")
        for k, tc in enumerate(tcs):
            tcpr = tc.find(qn("w:tcPr"))
            if tcpr is not None:
                for vm in tcpr.findall(qn("w:vMerge")):
                    tcpr.remove(vm)
            paras = [c for c in tc if c.tag == W_P]
            rpr = first_rpr([run for p in paras for run in text_runs(p)])
            for p in paras[1:]:
                tc.remove(p)
            if not paras:
                p0 = OxmlElement("w:p")
                tc.append(p0)
            else:
                p0 = paras[0]
                for run in list(p0.iter(W_R)):
                    run.getparent().remove(run)
                for ref in list(p0.iter(qn("w:commentReference"))):
                    ref.getparent().remove(ref)
            text = values[k] if k < len(values) else ""
            if text:
                p0.append(make_run(text, rpr))
        if after is not None and after < 0:
            template.addprevious(new_tr)
        else:
            cur = self._after.get(id(template), template)
            cur.addnext(new_tr)
            self._after[id(template)] = new_tr
        if self.tr.track:
            for run in _all_runs(new_tr):
                ins = self.tr.el("w:ins")
                run.addprevious(ins)
                ins.append(run)
            _mark_row(new_tr, "w:ins", self.tr)

    def _set_style(self, r: Resolved) -> None:
        op = r.op
        b = r.block
        assert b is not None
        style = self.factory._style(op["style"])
        if style is None:
            raise invalid(f"スタイル {op['style']} はこの文書にありません")
        if r.cell is None and b.kind in ("paragraph", "heading"):
            Paragraph(b.el, self.doc._body).style = style
        elif r.cell is None and b.kind == "table":
            Table(b.el, self.doc._body).style = style
        else:
            raise invalid(f"{op['block_id']} にはスタイルを設定できません")


def apply_ops(
    doc: Document, index: DocIndex, ops: list[dict[str, Any]], track: bool, author: str
) -> tuple[list[dict[str, Any]], int, list[str]]:
    resolved = resolve_ops(index, ops)
    editor = Editor(doc, Tracker(track, author, max_change_id(doc) + 1))
    results: list[dict[str, Any]] = []
    applied = 0
    for i, r in enumerate(resolved):
        try:
            editor.apply(r)
            results.append({"op_index": i, "ok": True})
            applied += 1
        except ToolFailure as e:
            results.append({"op_index": i, "ok": False, "error": {"code": e.code, "message": e.message}})
    return results, applied, editor.warnings
