"""data.transform の手順の実行（金額は Decimal）。"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from office_tools.errors import ToolFailure, invalid
from office_tools.transform.expr import compile_expr, to_date, to_decimal, truthy


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)

    def require(self, cols: list[str], where: str) -> None:
        missing = [c for c in cols if c not in self.columns]
        if missing:
            raise invalid(
                f"{where}: 列 {', '.join(missing)} はありません。使える列: {', '.join(self.columns[:50])}"
            )

    def matrix(self) -> list[list[Any]]:
        return [[r.get(c) for c in self.columns] for r in self.rows]


def unique_header(header: list[Any]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, h in enumerate(header, start=1):
        name = str(h).strip() if h is not None and str(h).strip() else f"列{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def make_table(header: list[Any], rows: list[list[Any]]) -> Table:
    cols = unique_header(header)
    t = Table(cols)
    for row in rows:
        if all(v is None or v == "" for v in row):
            continue
        t.rows.append({c: (row[i] if i < len(row) else None) for i, c in enumerate(cols)})
    return t


def _norm_key(v: Any) -> Any:
    d = to_decimal(v) if not isinstance(v, str) else None
    if d is not None:
        return ("n", d.normalize())
    if isinstance(v, str):
        return ("s", v.strip())
    if isinstance(v, dt.date):
        return ("d", v)
    return ("o", v)


def _sort_key(v: Any) -> tuple[int, Any]:
    if v is None or v == "":
        return (9, 0)
    if isinstance(v, bool):
        return (0, int(v))
    d = to_decimal(v) if not isinstance(v, str) else None
    if d is not None:
        return (1, d)
    if isinstance(v, dt.date):
        return (2, v.toordinal())
    return (3, str(v))


# ---------------------------------------------------------------- 各 op


def op_filter(t: Table, where: str) -> Table:
    fn = compile_expr(where, t.columns)
    return Table(list(t.columns), [r for r in t.rows if truthy(fn(r))])


def op_select(t: Table, columns: list[str]) -> Table:
    t.require(columns, "select")
    return Table(list(columns), [{c: r.get(c) for c in columns} for r in t.rows])


def op_rename(t: Table, mapping: dict[str, str]) -> Table:
    t.require(list(mapping), "rename")
    cols = [mapping.get(c, c) for c in t.columns]
    if len(set(cols)) != len(cols):
        raise invalid("rename: 名前を変えた結果、列名が重複します")
    return Table(cols, [{mapping.get(k, k): v for k, v in r.items()} for r in t.rows])


def op_derive(t: Table, column: str, expr: str) -> Table:
    fn = compile_expr(expr, t.columns)
    cols = list(t.columns) + ([column] if column not in t.columns else [])
    return Table(cols, [{**r, column: fn(r)} for r in t.rows])


def _agg(fn: str, values: list[Any], column: str) -> Any:
    if fn == "first":
        return values[0] if values else None
    present = [v for v in values if v is not None and v != ""]
    if fn == "count":
        return Decimal(len(present))
    if fn in ("sum", "avg"):
        nums = []
        for v in present:
            d = to_decimal(v)
            if d is None:
                raise invalid(f"group: 列 {column} に数値でない値「{str(v)[:50]}」があるため {fn} できません")
            nums.append(d)
        if fn == "sum":
            return sum(nums, Decimal(0))
        return (sum(nums, Decimal(0)) / len(nums)) if nums else None
    if not present:
        return None
    keyed = sorted(present, key=_sort_key)
    return keyed[0] if fn == "min" else keyed[-1]


def op_group(t: Table, by: list[str], aggs: list[dict[str, str]]) -> Table:
    t.require(by + [a["column"] for a in aggs], "group")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in t.rows:
        groups.setdefault(tuple(_norm_key(r.get(c)) for c in by), []).append(r)
    cols = list(by) + [a["as"] for a in aggs]
    if len(set(cols)) != len(cols):
        raise invalid("group: 出力の列名が重複します")
    out = Table(cols)
    for members in groups.values():
        row = {c: members[0].get(c) for c in by}
        for a in aggs:
            row[a["as"]] = _agg(a["fn"], [m.get(a["column"]) for m in members], a["column"])
        out.rows.append(row)
    return out


def op_sort(t: Table, by: list[dict[str, Any]]) -> Table:
    t.require([b["column"] for b in by], "sort")
    rows = list(t.rows)
    for b in reversed(by):
        col = b["column"]
        desc = bool(b.get("desc", False))
        present = [r for r in rows if r.get(col) is not None and r.get(col) != ""]
        empty = [r for r in rows if r.get(col) is None or r.get(col) == ""]
        present.sort(key=lambda r: _sort_key(r.get(col)), reverse=desc)
        rows = present + empty
    return Table(list(t.columns), rows)


def op_join(t: Table, other: Table, name: str, on: list[dict[str, str]], how: str) -> Table:
    lk = [o["left"] for o in on]
    rk = [o["right"] for o in on]
    t.require(lk, "join")
    other.require(rk, f"join（{name}）")
    extra = [c for c in other.columns if c not in rk]
    rename = {c: (f"{c}_{name}" if c in t.columns else c) for c in extra}
    cols = list(t.columns) + [rename[c] for c in extra]
    index: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in other.rows:
        index[tuple(_norm_key(r.get(c)) for c in rk)].append(r)
    used: set[int] = set()
    out = Table(cols)
    for lr in t.rows:
        key = tuple(_norm_key(lr.get(c)) for c in lk)
        matches = index.get(key, [])
        if matches:
            for m in matches:
                used.add(id(m))
                out.rows.append({**lr, **{rename[c]: m.get(c) for c in extra}})
        elif how in ("left", "outer"):
            out.rows.append({**lr, **{rename[c]: None for c in extra}})
    if how == "outer":
        for r in other.rows:
            if id(r) in used:
                continue
            row: dict[str, Any] = {c: None for c in t.columns}
            for lc, rc in zip(lk, rk, strict=True):
                row[lc] = r.get(rc)
            row.update({rename[c]: r.get(c) for c in extra})
            out.rows.append(row)
    return out


def _same(a: Any, b: Any, tolerance: Decimal) -> bool:
    if a is None or a == "":
        return b is None or b == ""
    da, db = to_decimal(a), to_decimal(b)
    if da is not None and db is not None:
        return abs(da - db) <= tolerance
    if isinstance(a, dt.date) or isinstance(b, dt.date):
        return to_date(a) == to_date(b)
    return str(a).strip() == ("" if b is None else str(b).strip())


def op_reconcile(
    t: Table, other: Table, name: str, key: list[str], compare: list[str], tolerance: float | int
) -> Table:
    """突合。key で対応を取り、compare の列を比べて _status（match/mismatch/left_only/right_only）を付ける。"""
    t.require(key + compare, "reconcile")
    other.require(key + compare, f"reconcile（{name}）")
    tol = Decimal(str(tolerance))
    right_cols = [f"{c}_{name}" for c in compare]
    cols = list(t.columns) + [c for c in right_cols if c not in t.columns] + ["_status", "_diff"]
    queues: dict[tuple[Any, ...], deque[dict[str, Any]]] = defaultdict(deque)
    for r in other.rows:
        queues[tuple(_norm_key(r.get(c)) for c in key)].append(r)
    out = Table(cols)
    for lr in t.rows:
        k = tuple(_norm_key(lr.get(c)) for c in key)
        q = queues.get(k)
        row = dict(lr)
        if q:
            rr = q.popleft()
            diffs = [c for c in compare if not _same(lr.get(c), rr.get(c), tol)]
            for c, rc in zip(compare, right_cols, strict=True):
                row[rc] = rr.get(c)
            row["_status"] = "mismatch" if diffs else "match"
            row["_diff"] = ",".join(diffs) if diffs else None
        else:
            for rc in right_cols:
                row[rc] = None
            row["_status"] = "left_only"
            row["_diff"] = None
        out.rows.append(row)
    for q in queues.values():
        for rr in q:
            row = {c: None for c in t.columns}
            for c in key:
                row[c] = rr.get(c)
            for c, rc in zip(compare, right_cols, strict=True):
                row[rc] = rr.get(c)
            row["_status"] = "right_only"
            row["_diff"] = None
            out.rows.append(row)
    return out


def op_dedupe(t: Table, by: list[str]) -> Table:
    t.require(by, "dedupe")
    seen: set[tuple[Any, ...]] = set()
    out = Table(list(t.columns))
    for r in t.rows:
        k = tuple(_norm_key(r.get(c)) for c in by)
        if k in seen:
            continue
        seen.add(k)
        out.rows.append(r)
    return out


def run_steps(tables: dict[str, Table], first: str, steps: list[dict[str, Any]]) -> Table:
    cur = tables[first]

    def other(name: str, op: str) -> Table:
        if name not in tables:
            raise invalid(f"{op}: 入力 {name} はありません。inputs の name を指定してください")
        return tables[name]

    for i, st in enumerate(steps):
        op = st["op"]
        try:
            if op == "filter":
                cur = op_filter(cur, st["where"])
            elif op == "select":
                cur = op_select(cur, st["columns"])
            elif op == "rename":
                cur = op_rename(cur, st["map"])
            elif op == "derive":
                cur = op_derive(cur, st["column"], st["expr"])
            elif op == "group":
                cur = op_group(cur, st["by"], st["aggs"])
            elif op == "sort":
                cur = op_sort(cur, st["by"])
            elif op == "join":
                cur = op_join(cur, other(st["with"], "join"), st["with"], st["on"], st["how"])
            elif op == "reconcile":
                cur = op_reconcile(
                    cur,
                    other(st["with"], "reconcile"),
                    st["with"],
                    st["key"],
                    st["compare"],
                    st.get("tolerance", 0),
                )
            elif op == "dedupe":
                cur = op_dedupe(cur, st["by"])
            else:  # pragma: no cover  (スキーマで弾かれる)
                raise invalid(f"不明な op: {op}")
        except ToolFailure as e:
            e.message = f"steps[{i}] ({op}): {e.message}"[:1000]
            raise
    return cur
