"""DLP 照合の前にそろえる正規化（NET-07, test_spec §5.3）。

- `soft`: NFKC（全角/半角・半角カナ・丸数字など）→ casefold → カタカナをひらがなへ → 書式文字の除去 → 空白の圧縮。
  正規表現（口座・金額・電話など）はこちらで照合する（数字の区切りや記号が意味を持つため）。
- `compact`: `soft` からさらに空白・句読点・記号・制御文字をすべて除いたもの。機密語辞書の照合に使う
  （「Ｈｉｒａｉ　Ｍａｒｕ」「hirai-maru」「平井・丸」→ 同じ形になる）。
- URL は多重のパーセントエンコード・`%uXXXX`・`+`（空白）を戻し、ホストの punycode（xn--）を Unicode に戻す。
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import SplitResult, unquote, urlsplit

_KATA_START = 0x30A1
_KATA_END = 0x30F6
_WS = re.compile(r"\s+")
_PCT_U = re.compile(r"%u([0-9a-fA-F]{4})")
_MAX_DECODE_ROUNDS = 8


def to_hiragana(text: str) -> str:
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if _KATA_START <= cp <= _KATA_END:
            out.append(chr(cp - 0x60))
        elif ch == "ヽ":
            out.append("ゝ")
        elif ch == "ヾ":
            out.append("ゞ")
        else:
            out.append(ch)
    return "".join(out)


def _drop_format_chars(text: str) -> str:
    # ゼロ幅スペース・方向制御などの書式文字（Cf）で語を分断する回避を防ぐ
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def soft(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = t.casefold()
    # casefold の後に NFKC をもう一度かけて合成を安定させる
    t = unicodedata.normalize("NFKC", t)
    t = to_hiragana(t)
    t = _drop_format_chars(t)
    return _WS.sub(" ", t).strip()


def compact(text: str) -> str:
    """文字（L）・数字（N）・結合文字（M）だけを残す。長音「ー」は Lm なので残る。"""
    s = soft(text)
    return "".join(ch for ch in s if unicodedata.category(ch)[0] in ("L", "N", "M"))


def percent_decode_fully(text: str, *, plus_as_space: bool = False) -> str:
    """多重にエンコードされていても、変化しなくなるまで戻す（上限あり）。"""
    t = text
    for _ in range(_MAX_DECODE_ROUNDS):
        prev = t
        if plus_as_space:
            t = t.replace("+", " ")
        t = _PCT_U.sub(lambda m: chr(int(m.group(1), 16)), t)
        t = unquote(t, errors="replace")
        if t == prev:
            break
    return t


def decode_host(host: str) -> str:
    """ホスト名の各ラベルの punycode を Unicode に戻す（戻せないラベルはそのまま）。"""
    labels: list[str] = []
    for label in host.split("."):
        if label.lower().startswith("xn--"):
            try:
                labels.append(label[4:].encode("ascii").decode("punycode"))
                continue
            except (UnicodeError, ValueError):
                pass
        labels.append(label)
    return ".".join(labels)


def url_parts_for_inspection(url: str) -> list[tuple[str, bool]]:
    """DLP で検査する URL の各部分をデコードして返す。

    戻り値は (文字列, 自由入力か)。ホスト・パスは False、クエリ・フラグメント・userinfo は True。
    """
    try:
        parts: SplitResult = urlsplit(url.strip())
        hostname = parts.hostname or ""
    except ValueError:
        return [(percent_decode_fully(url, plus_as_space=True), True)]
    out = [
        (decode_host(percent_decode_fully(hostname)), False),
        (percent_decode_fully(parts.path), False),
        (percent_decode_fully(parts.query, plus_as_space=True), True),
        (percent_decode_fully(parts.fragment, plus_as_space=True), True),
    ]
    if parts.username or parts.password:
        out.append((percent_decode_fully(f"{parts.username or ''} {parts.password or ''}"), True))
    return [(text, free) for text, free in out if text]
