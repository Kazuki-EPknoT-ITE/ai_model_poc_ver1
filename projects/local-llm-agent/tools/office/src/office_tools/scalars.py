"""Scalar（protocol §3.3: string | number | boolean | null | {date}）と Python 値の変換。"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

from office_tools.errors import invalid

JsonScalar = str | int | float | bool | None | dict[str, str]


def scalar_to_python(value: Any) -> Any:
    """引数の Scalar → セルに書く Python 値（日付は datetime.date）。"""
    if isinstance(value, dict):
        try:
            return dt.date.fromisoformat(str(value["date"]))
        except (KeyError, ValueError) as e:
            raise invalid(f"日付 {value} が不正です。YYYY-MM-DD の形式で指定してください") from e
    return value


def to_json(value: Any) -> JsonScalar:
    """セル・表の値 → JSON にできる Scalar。"""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return {"date": value.date().isoformat()}
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return {"date": value.isoformat()}
    if isinstance(value, dt.time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return str(value)


def to_text(value: Any) -> str:
    """CSV などに書くときの文字列表現。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dict) and "date" in value:
        return str(value["date"])
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return format(Decimal(repr(value)), "f")
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
