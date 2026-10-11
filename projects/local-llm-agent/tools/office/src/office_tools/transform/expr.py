"""data.transform の限定式（protocol §3.4）。eval/exec は使わず、専用の構文解析器で評価する。

文法:
    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := add (("=="|"="|"!="|"<"|"<="|">"|">=") add)?
    add     := mul (("+"|"-") mul)*
    mul     := unary (("*"|"/") unary)*
    unary   := "-" unary | primary
    primary := 数値 | '文字列' | "文字列" | true | false | null
             | 関数名 "(" [expr ("," expr)*] ")" | 列名 | `列 名` | [列 名] | "(" expr ")"
関数: contains(a,b) startswith(a,b) round(x,n) date(x) coalesce(a,...)
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from office_tools.errors import ToolFailure, invalid

MAX_DEPTH = 64


class ExprError(ToolFailure):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_ARGS", message)


# ---------------------------------------------------------------- 字句解析

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?|\.\d+)
  | (?P<str>'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.)*")
  | (?P<bq>`[^`]+`)
  | (?P<br>\[[^\]]+\])
  | (?P<op>==|!=|<=|>=|[-+*/<>=(),])
  | (?P<ident>[^\W\d][\w]*)
    """,
    re.VERBOSE | re.UNICODE,
)

KEYWORDS = {"and", "or", "not", "true", "false", "null"}


@dataclass(frozen=True)
class Tok:
    kind: str  # num str col ident op kw end
    value: str
    pos: int


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise ExprError(f"式の {i + 1} 文字目「{src[i]}」を解釈できません: {src}")
        kind = m.lastgroup or ""
        text = m.group()
        if kind == "str":
            body = text[1:-1]
            if text[0] == "'":
                body = body.replace("''", "'")
            body = re.sub(r"\\(.)", r"\1", body)
            out.append(Tok("str", body, i))
        elif kind == "bq" or kind == "br":
            out.append(Tok("col", text[1:-1].strip(), i))
        elif kind == "ident":
            if text.lower() in KEYWORDS:
                out.append(Tok("kw", text.lower(), i))
            else:
                out.append(Tok("ident", text, i))
        elif kind != "ws":
            out.append(Tok(kind, text, i))
        i = m.end()
    out.append(Tok("end", "", len(src)))
    return out


# ---------------------------------------------------------------- 構文木


@dataclass(frozen=True)
class Node:
    kind: str  # lit col call unop binop
    value: Any = None
    args: tuple[Node, ...] = ()


FUNCTIONS: dict[str, tuple[int, int]] = {
    "contains": (2, 2),
    "startswith": (2, 2),
    "round": (1, 2),
    "date": (1, 1),
    "coalesce": (1, 20),
}


class Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.toks = tokenize(src)
        self.i = 0
        self.depth = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def take(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect_op(self, op: str) -> None:
        t = self.take()
        if t.kind != "op" or t.value != op:
            raise ExprError(f"式の {t.pos + 1} 文字目に「{op}」が必要です: {self.src}")

    def parse(self) -> Node:
        node = self.or_()
        if self.peek().kind != "end":
            t = self.peek()
            raise ExprError(f"式の {t.pos + 1} 文字目「{t.value}」が余分です: {self.src}")
        return node

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError("式が深すぎます")

    def or_(self) -> Node:
        node = self.and_()
        while self.peek().kind == "kw" and self.peek().value == "or":
            self.take()
            node = Node("binop", "or", (node, self.and_()))
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.peek().kind == "kw" and self.peek().value == "and":
            self.take()
            node = Node("binop", "and", (node, self.not_()))
        return node

    def not_(self) -> Node:
        if self.peek().kind == "kw" and self.peek().value == "not":
            self.take()
            self._enter()
            try:
                return Node("unop", "not", (self.not_(),))
            finally:
                self.depth -= 1
        return self.cmp()

    def cmp(self) -> Node:
        node = self.add()
        t = self.peek()
        if t.kind == "op" and t.value in ("==", "=", "!=", "<", "<=", ">", ">="):
            self.take()
            op = "==" if t.value == "=" else t.value
            node = Node("binop", op, (node, self.add()))
        return node

    def add(self) -> Node:
        node = self.mul()
        while self.peek().kind == "op" and self.peek().value in ("+", "-"):
            op = self.take().value
            node = Node("binop", op, (node, self.mul()))
        return node

    def mul(self) -> Node:
        node = self.unary()
        while self.peek().kind == "op" and self.peek().value in ("*", "/"):
            op = self.take().value
            node = Node("binop", op, (node, self.unary()))
        return node

    def unary(self) -> Node:
        if self.peek().kind == "op" and self.peek().value == "-":
            self.take()
            self._enter()
            try:
                return Node("unop", "-", (self.unary(),))
            finally:
                self.depth -= 1
        return self.primary()

    def primary(self) -> Node:
        t = self.take()
        if t.kind == "num":
            return Node("lit", Decimal(t.value))
        if t.kind == "str":
            return Node("lit", t.value)
        if t.kind == "kw" and t.value in ("true", "false", "null"):
            return Node("lit", {"true": True, "false": False, "null": None}[t.value])
        if t.kind == "col":
            return Node("col", t.value)
        if t.kind == "ident":
            if self.peek().kind == "op" and self.peek().value == "(":
                return self.call(t)
            return Node("col", t.value)
        if t.kind == "op" and t.value == "(":
            self._enter()
            try:
                node = self.or_()
            finally:
                self.depth -= 1
            self.expect_op(")")
            return node
        where = f"{t.pos + 1} 文字目" if t.kind != "end" else "末尾"
        raise ExprError(f"式の{where}で値が必要です: {self.src}")

    def call(self, name_tok: Tok) -> Node:
        name = name_tok.value.lower()
        if name not in FUNCTIONS:
            raise ExprError(f"関数 {name_tok.value} は使えません（使える関数: {', '.join(FUNCTIONS)}）")
        self.expect_op("(")
        args: list[Node] = []
        if not (self.peek().kind == "op" and self.peek().value == ")"):
            self._enter()
            try:
                args.append(self.or_())
                while self.peek().kind == "op" and self.peek().value == ",":
                    self.take()
                    args.append(self.or_())
            finally:
                self.depth -= 1
        self.expect_op(")")
        lo, hi = FUNCTIONS[name]
        if not lo <= len(args) <= hi:
            raise ExprError(f"関数 {name} の引数の数が不正です")
        return Node("call", name, tuple(args))


def parse(src: str) -> Node:
    return Parser(src).parse()


def columns_of(node: Node) -> set[str]:
    if node.kind == "col":
        return {str(node.value)}
    out: set[str] = set()
    for a in node.args:
        out |= columns_of(a)
    return out


# ---------------------------------------------------------------- 評価

_NUM = re.compile(r"^[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")


def to_decimal(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, str) and _NUM.match(v.strip()):
        try:
            return Decimal(v.strip().replace(",", ""))
        except InvalidOperation:
            return None
    return None


def to_date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        s = v.strip().replace("/", "-").replace(".", "-")
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return True


def _compare(op: str, a: Any, b: Any) -> bool:
    if a is None or b is None:
        if op == "==":
            return a is None and b is None
        if op == "!=":
            return not (a is None and b is None)
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        if op not in ("==", "!="):
            raise ExprError("真偽値は大小を比較できません")
        same = isinstance(a, bool) and isinstance(b, bool) and a == b
        return same if op == "==" else not same
    x: Any
    y: Any
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        x, y = to_decimal(a), to_decimal(b)
        if x is None or y is None:
            x, y = str(a), str(b)
    elif isinstance(a, dt.date) or isinstance(b, dt.date):
        x, y = to_date(a), to_date(b)
        if x is None or y is None:
            x, y = str(a), str(b)
    else:
        x, y = str(a), str(b)
    if op == "==":
        return bool(x == y)
    if op == "!=":
        return bool(x != y)
    try:
        if op == "<":
            return bool(x < y)
        if op == "<=":
            return bool(x <= y)
        if op == ">":
            return bool(x > y)
        return bool(x >= y)
    except TypeError as e:
        raise ExprError(f"比較できない値です: {type(a).__name__} と {type(b).__name__}") from e


def _arith(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if (
        op == "+"
        and isinstance(a, str)
        and isinstance(b, str)
        and (to_decimal(a) is None or to_decimal(b) is None)
    ):
        return a + b
    if isinstance(a, dt.date) and op in ("+", "-"):
        n = to_decimal(b)
        if n is not None:
            days = dt.timedelta(days=int(n))
            return a + days if op == "+" else a - days
        d2 = to_date(b)
        if op == "-" and d2 is not None:
            return Decimal((a - d2).days)
    x, y = to_decimal(a), to_decimal(b)
    if x is None or y is None:
        raise ExprError(f"数値でない値で計算しようとしました（{op}）")
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if y == 0:
        return None
    return x / y


def _round(x: Any, n: Any = Decimal(0)) -> Any:
    d = to_decimal(x)
    if d is None:
        return None
    digits = int(to_decimal(n) or 0)
    return d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _call(name: str, args: list[Any]) -> Any:
    if name == "contains":
        a, b = args
        return a is not None and b is not None and str(b) in str(a)
    if name == "startswith":
        a, b = args
        return a is not None and b is not None and str(a).startswith(str(b))
    if name == "round":
        return _round(*args)
    if name == "date":
        return to_date(args[0])
    if name == "coalesce":
        for a in args:
            if a is not None and a != "":
                return a
        return None
    raise ExprError(f"関数 {name} は使えません")


Row = Mapping[str, Any]


def evaluate(node: Node, row: Row) -> Any:
    k = node.kind
    if k == "lit":
        return node.value
    if k == "col":
        return row.get(str(node.value))
    if k == "unop":
        v = evaluate(node.args[0], row)
        if node.value == "not":
            return not truthy(v)
        d = to_decimal(v)
        if v is None:
            return None
        if d is None:
            raise ExprError("数値でない値に - を付けています")
        return -d
    if k == "binop":
        op = str(node.value)
        if op == "and":
            return truthy(evaluate(node.args[0], row)) and truthy(evaluate(node.args[1], row))
        if op == "or":
            return truthy(evaluate(node.args[0], row)) or truthy(evaluate(node.args[1], row))
        a = evaluate(node.args[0], row)
        b = evaluate(node.args[1], row)
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return _compare(op, a, b)
        return _arith(op, a, b)
    if k == "call":
        return _call(str(node.value), [evaluate(a, row) for a in node.args])
    raise ExprError(f"不明な式です: {k}")


def compile_expr(src: str, columns: list[str]) -> Callable[[Row], Any]:
    node = parse(src)
    missing = sorted(columns_of(node) - set(columns))
    if missing:
        raise invalid(
            f"式 {src} の列 {', '.join(missing)} はありません。使える列: {', '.join(columns[:50])}",
        )
    return lambda row: evaluate(node, row)
