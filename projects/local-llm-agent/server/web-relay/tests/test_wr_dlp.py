"""DLP の表記ゆれ（test_spec §5.3 DX-01〜08）と正規化・Aho-Corasick の単体試験。"""

from __future__ import annotations

import pytest

from web_relay.ahocorasick import AhoCorasick
from web_relay.dlp import DlpEngine, DlpTerm
from web_relay.normalize import compact, decode_host, percent_decode_fully, soft


@pytest.fixture(scope="module")
def dlp() -> DlpEngine:
    return DlpEngine(
        [
            DlpTerm("平井丸", ("ひらいまる",)),
            DlpTerm("HIRAI MARU"),
            DlpTerm("三井商事", ("ミツイショウジ",)),
        ]
    )


# ---------------------------------------------------------------- 正規化


def test_normalize_width_kana_case() -> None:
    assert compact("Ｈｉｒａｉ　Ｍａｒｕ") == "hiraimaru"
    assert compact("hirai-maru") == "hiraimaru"
    assert compact("ﾋﾗｲﾏﾙ") == "ひらいまる"
    assert compact("ヒライマル") == "ひらいまる"
    assert compact("ﾊﾞｽ") == "ばす"  # 半角の濁点も合成される
    assert compact("平井​丸") == "平井丸"  # ゼロ幅スペース
    assert soft("１２３４５６７") == "1234567"
    assert soft("￥12,345") == "¥12,345"


def test_percent_decode_multi_and_punycode() -> None:
    once = "%E5%B9%B3%E4%BA%95%E4%B8%B8"
    twice = once.replace("%", "%25")
    assert percent_decode_fully(once) == "平井丸"
    assert percent_decode_fully(twice) == "平井丸"
    assert percent_decode_fully("%u5E73%u4E95%u4E38") == "平井丸"
    assert decode_host("xn--qiqri784b.example") == "平井丸.example"


def test_ahocorasick_basic() -> None:
    ac = AhoCorasick(["he", "she", "his", "hers"])
    assert ac.contains_any("ushers")
    assert ac.contains_any("ahishe")
    assert not ac.contains_any("abcdef")
    assert AhoCorasick([]).contains_any("anything") is False
    # 長い語の途中にある短い語（失敗リンクからの出力の引き継ぎ）
    ac2 = AhoCorasick(["abcd", "bc"])
    assert ac2.contains_any("xabcx")
    assert len(ac2) == 2


# ---------------------------------------------------------------- DX ケース


@pytest.mark.parametrize(
    "query",
    [
        "平井丸 入渠 費用",  # DX-01
        "ﾋﾗｲﾏﾙ",  # DX-02 半角カナ（読みを登録）
        "ひらいまる 修繕",  # DX-02 ひらがな
        "ヒライマル",
        "Ｈｉｒａｉ　Ｍａｒｕ",  # DX-03 全角英字・全角空白
        "hirai-maru dry dock",  # DX-03
        "HIRAI_MARU",
        "平 井 丸",
        "平・井・丸",
        "平井​丸",
        "三井商事 取引",
        "みついしょうじ",
        "%E5%B9%B3%E4%BA%95%E4%B8%B8 費用",  # DX-07 を検索語に入れた場合
        "普通 1234567",  # DX-04
        "口座番号 1234567",
        "当座預金 0012345",
        "1234567 普通預金",
        "0001-234-1234567",
        "¥12,345,678",  # DX-05
        "￥１２，３４５，６７８",
        "USD 1.2M",
        "1.2M USD",
        "1234万円",
        "１２３４万円",
        "12,345,678円",
        "5億円の案件",
        "$5,000",
        "請求額 1,200,000",
        "4111 1111 1111 1111",  # カード番号（Luhn）
        "4111-1111-1111-1111",
        "03-1234-5678",  # 電話番号
        "090-1234-5678",
        "09012345678",
        "+81 3 1234 5678",
        "tel 0312345678",
        "taro.yamada@example.co.jp",  # メール
    ],
)
def test_query_blocked(dlp: DlpEngine, query: str) -> None:
    assert dlp.query_blocked(query), query


@pytest.mark.parametrize(
    "query",
    [
        "傭船契約 一般的な条項",  # DX-08
        "国際海事機関 2026年 規則改正",
        "MARPOL Annex VI 2020",
        "SOLAS 第II-2章",
        "船級協会 検査 周期",
        "平井 一般的な姓",  # 辞書の語の一部だけ
        "丸の内",
        "ISO 9001",
        "1234567",  # 文脈のない7桁は口座とみなさない
        "4111 1111 1111 1112",  # Luhn が合わない
    ],
)
def test_query_not_blocked(dlp: DlpEngine, query: str) -> None:
    assert not dlp.query_blocked(query), query


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/?q=平井丸",  # DX-06
        "https://example.com/?q=%E5%B9%B3%E4%BA%95%E4%B8%B8",  # DX-07
        "https://example.com/?q=%25E5%25B9%25B3%25E4%25BA%2595%25E4%25B8%25B8",  # 二重エンコード
        "https://example.com/search?q=hirai+maru",
        "https://example.com/HIRAI-MARU/report",  # パス
        "https://example.com/page#平井丸",  # フラグメント
        "https://hirai-maru.example.com/",  # ホスト
        "https://xn--qiqri784b.example/",  # punycode のホスト（平井丸）
        "https://example.com/?amount=%C2%A512%2C345%2C678",  # ¥12,345,678
        "https://example.com/?acct=%E6%99%AE%E9%80%9A%201234567",  # 普通 1234567
        "https://example.com/?mail=taro%40example.co.jp",
        "https://example.com/?tel=03-1234-5678",
        "https://example.com/ﾋﾗｲﾏﾙ",
    ],
)
def test_url_blocked(dlp: DlpEngine, url: str) -> None:
    assert dlp.url_blocked(url), url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mlit.go.jp/maritime/index.html",
        "https://www.imo.org/en/MediaCentre/PressBriefings/Pages/2026.aspx",
        "https://example.com/articles/1234567890123456789",  # 数値 ID（カード番号とはみなさない）
        "https://example.com/?page=2&sort=desc",
    ],
)
def test_url_not_blocked(dlp: DlpEngine, url: str) -> None:
    assert not dlp.url_blocked(url), url


def test_short_terms_are_ignored() -> None:
    # 1文字の語は登録しない（すべてに該当してしまうため）
    engine = DlpEngine([DlpTerm("丸")])
    assert engine.term_count == 0
    assert not engine.query_blocked("丸の内")
