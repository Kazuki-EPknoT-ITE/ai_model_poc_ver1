"""Aho-Corasick 法による複数語の同時照合（NET-07 の機密語辞書）。

外部ライブラリに頼らず、型付きの純 Python で実装する（辞書は数千語規模を想定）。
照合結果は「該当したか」だけを返し、どの語に該当したかは外へ出さない。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable


class AhoCorasick:
    def __init__(self, patterns: Iterable[str]) -> None:
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[bool] = [False]
        self._count = 0
        for p in patterns:
            if p:
                self._add(p)
        self._build()

    def __len__(self) -> int:
        return self._count

    def _add(self, pattern: str) -> None:
        node = 0
        for ch in pattern:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._out.append(False)
                self._goto[node][ch] = nxt
            node = nxt
        if not self._out[node]:
            self._count += 1
        self._out[node] = True

    def _build(self) -> None:
        queue: deque[int] = deque()
        for nxt in self._goto[0].values():
            self._fail[nxt] = 0
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            for ch, nxt in self._goto[node].items():
                queue.append(nxt)
                f = self._fail[node]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                cand = self._goto[f].get(ch, 0)
                self._fail[nxt] = cand if cand != nxt else 0
                # 出力を失敗リンクから引き継ぐ（短い語が長い語の途中にある場合）
                self._out[nxt] = self._out[nxt] or self._out[self._fail[nxt]]

    def contains_any(self, text: str) -> bool:
        if self._count == 0:
            return False
        node = 0
        goto = self._goto
        fail = self._fail
        out = self._out
        for ch in text:
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            if out[node]:
                return True
        return False
