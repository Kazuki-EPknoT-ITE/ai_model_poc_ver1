"""DLP: 検索語と URL に機密情報が含まれていないかを検査する（NET-07）。

1. 正規表現: 口座番号（文脈つき）・カード番号（Luhn）・通貨記号/通貨コード付きの金額・「万円」等の金額・
   電話番号・メールアドレス。
2. 機密語辞書（org ごと。読みも登録）を Aho-Corasick 法で照合。

照合は `normalize.soft` / `normalize.compact` でそろえた後に行う。結果は「該当したか」だけで、
どの語・どの規則に該当したかは呼び出し元にも返さない（ログ・監査ログ・エラーに出さないため）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .ahocorasick import AhoCorasick
from .normalize import compact, percent_decode_fully, soft, url_parts_for_inspection

# 辞書の語は正規化後 2 文字以上のものだけ使う（1 文字だとほぼすべてに該当するため）
MIN_TERM_CHARS = 2

_ACCOUNT_CTX = r"(?:普通|当座|貯蓄|総合|預金|口座|振込|振り込み|支店|記号|a/c|acct|account|bank|iban)"
_CURRENCY_WORDS = r"(?:円|どる|ゆーろ|ぽんど|元|ういん|ふらん|yen|dollars?|euros?|pounds?)"
_CURRENCY_CODES = (
    r"(?:usd|jpy|eur|gbp|cny|rmb|sgd|hkd|krw|aud|cad|chf|nok|dkk|sek|inr|thb|php|idr|myr|vnd|twd)"
)


def _luhn_ok(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    in_query: bool = True
    in_url: bool = True
    validator: Callable[[str], bool] | None = None

    def hit(self, text: str) -> bool:
        for m in self.pattern.finditer(text):
            if self.validator is None or self.validator(m.group(0)):
                return True
        return False


def _r(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


_RULES: tuple[_Rule, ...] = (
    # --- 口座番号（6〜8桁で、前後に口座らしい文脈がある） ---
    _Rule(_r(_ACCOUNT_CTX + r"[^\d]{0,12}(?<!\d)\d{6,8}(?!\d)")),
    _Rule(_r(r"(?<!\d)\d{6,8}(?!\d)[^\d]{0,4}" + _ACCOUNT_CTX)),
    # 銀行コード-支店コード-口座番号
    _Rule(_r(r"(?<!\d)\d{4}[- ]\d{3}[- ]\d{7}(?!\d)")),
    # 口座番号らしい語のあとに区切り付きの数字（例: 口座番号: 123-4567）
    _Rule(_r(r"口座番号[^\d]{0,6}\d[\d -]{5,12}\d")),
    # IBAN
    _Rule(_r(r"(?<![a-z0-9])[a-z]{2}\d{2}[a-z0-9]{11,30}(?![a-z0-9])"), in_url=False),
    # --- カード番号（Luhn で確認）。URL では区切り付きの形だけ（数値 ID の誤検知を避ける） ---
    _Rule(_r(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), in_url=False, validator=_luhn_ok),
    _Rule(_r(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}(?!\d)"), validator=_luhn_ok),
    # --- 金額 ---
    _Rule(_r(r"[¥$€£₩₹]\s?\d")),  # 通貨記号（NFKC で ￥→¥、＄→$）
    _Rule(_r(r"(?<![a-z])" + _CURRENCY_CODES + r"\s?\d")),  # USD 1.2M
    _Rule(_r(r"\d(?:[\d,.]*\d)?\s?" + _CURRENCY_CODES + r"(?![a-z])")),  # 1.2M USD / 100 JPY
    _Rule(_r(r"\d(?:[\d,.]*\d)?\s?(?:千|万|百万|千万|億|兆)\s?" + _CURRENCY_WORDS)),  # 1234万円
    _Rule(_r(r"(?<!\d)\d{1,3}(?:,\d{3})+(?:\.\d+)?\s?" + _CURRENCY_WORDS)),  # 12,345,678円
    _Rule(_r(r"(?<!\d)\d{4,}(?:\.\d+)?\s?" + _CURRENCY_WORDS)),  # 5000円
    _Rule(_r(r"\d(?:[\d.]*\d)?\s?(?:m|mn|million|bn|billion|k)\s?" + _CURRENCY_CODES)),
    _Rule(_r(r"(?:金額|代金|費用|請求額|見積額|入金|送金)\s?[:：]?\s?\d[\d,]{3,}")),
    # --- 電話番号 ---
    _Rule(_r(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)")),
    _Rule(_r(r"(?<![\d-])0[5789]0-?\d{4}-?\d{4}(?!\d)")),
    _Rule(_r(r"(?<!\d)0\d{9,10}(?!\d)"), in_url=False),
    _Rule(_r(r"(?<![\w])\+\d{1,3}[ -]?\(?\d{1,4}\)?[ -]?\d{2,4}[ -]?\d{3,4}(?!\d)"), in_url=False),
    _Rule(_r(r"(?:tel|fax|電話|でんわ|携帯)[^\d]{0,6}\d[\d -]{7,14}\d")),
    # --- メールアドレス ---
    _Rule(_r(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}")),
)


@dataclass(frozen=True)
class DlpTerm:
    term: str
    readings: tuple[str, ...] = ()


class DlpEngine:
    """1つの org の辞書と共通の正規表現で検査する。"""

    def __init__(self, terms: Iterable[DlpTerm]) -> None:
        keys: set[str] = set()
        for t in terms:
            for raw in (t.term, *t.readings):
                key = compact(raw)
                if len(key) >= MIN_TERM_CHARS:
                    keys.add(key)
        self._ac = AhoCorasick(sorted(keys))

    @property
    def term_count(self) -> int:
        return len(self._ac)

    def _dictionary_hit(self, text: str) -> bool:
        return self._ac.contains_any(compact(text))

    @staticmethod
    def _rules_hit(text: str, *, url: bool) -> bool:
        s = soft(text)
        variants = [s] if url else [s, s.replace(" ", "")]
        for rule in _RULES:
            if (url and not rule.in_url) or (not url and not rule.in_query):
                continue
            if any(rule.hit(v) for v in variants):
                return True
        return False

    def query_blocked(self, query: str) -> bool:
        """検索語の検査。URL エンコードされた形で送られても、戻してから照合する。"""
        decoded = percent_decode_fully(query, plus_as_space=False)
        for text in {query, decoded}:
            if self._dictionary_hit(text) or self._rules_hit(text, url=False):
                return True
        return False

    def url_blocked(self, url: str) -> bool:
        """URL（ホスト・パス・クエリ・フラグメント）の検査。"""
        parts = url_parts_for_inspection(url)
        joined = " ".join(text for text, _ in parts)
        if self._dictionary_hit(joined):
            return True
        # クエリ・フラグメントは自由入力の文字列が入りやすいので、検索語と同じ厳しさで検査する
        return any(self._rules_hit(text, url=not free_text) for text, free_text in parts)
