"""BM25 用の日本語の分かち書き。

SudachiPy（＋辞書）が入っていればそれを使い、無ければ文字 bi-gram にフォールバックする。
索引には使ったトークナイザの名前を記録し、検索時に違うものを使わないようにする。
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from typing import Any, Protocol

_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿々ー"
_RUN = re.compile(rf"[{_CJK}]+|[0-9a-z]+(?:[._\-][0-9a-z]+)*")
_WORDLIKE = re.compile(rf"[{_CJK}0-9a-z]")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


class Tokenizer(Protocol):
    name: str

    def tokenize(self, text: str) -> list[str]: ...


class BigramTokenizer:
    """日本語（かな・漢字）の連続は文字 bi-gram、英数字は単語単位。"""

    name = "bigram"

    def tokenize(self, text: str) -> list[str]:
        out: list[str] = []
        for m in _RUN.finditer(normalize(text)):
            run = m.group(0)
            if run[0].isascii():
                out.append(run)
            elif len(run) == 1:
                out.append(run)
            else:
                out.extend(run[i : i + 2] for i in range(len(run) - 1))
        return out


class SudachiTokenizer:
    """SudachiPy の分割モード B（検索向けに短め）。正規化形を使う。"""

    name = "sudachi"

    def __init__(self) -> None:
        dictionary: Any = importlib.import_module("sudachipy.dictionary")
        tokenizer_mod: Any = importlib.import_module("sudachipy.tokenizer")
        self._tok: Any = dictionary.Dictionary().create()
        self._mode: Any = tokenizer_mod.Tokenizer.SplitMode.B

    def tokenize(self, text: str) -> list[str]:
        out: list[str] = []
        norm = normalize(text)
        # Sudachi は1回の入力の長さに上限があるので行ごとに分ける
        for line in norm.splitlines():
            for start in range(0, len(line), 4000):
                for m in self._tok.tokenize(line[start : start + 4000], self._mode):
                    surface = str(m.normalized_form())
                    if _WORDLIKE.search(surface):
                        out.append(surface)
        return out


def sudachi_available() -> bool:
    try:
        importlib.import_module("sudachipy")
        importlib.import_module("sudachidict_core")
    except ImportError:
        return False
    return True


def make_tokenizer(kind: str = "auto") -> Tokenizer:
    if kind == "bigram":
        return BigramTokenizer()
    if kind == "sudachi":
        return SudachiTokenizer()
    if kind == "auto":
        return SudachiTokenizer() if sudachi_available() else BigramTokenizer()
    raise ValueError(f"unknown tokenizer: {kind}")
