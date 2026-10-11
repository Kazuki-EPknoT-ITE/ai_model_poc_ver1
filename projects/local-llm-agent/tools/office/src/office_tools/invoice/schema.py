"""invoice.v1（protocol §3.5）の抽出スキーマ。LLM の出力（guided_json）をこの形に縛る。"""

from __future__ import annotations

from typing import Any

SCHEMA_ID = "invoice.v1"

TEXT_FIELDS = ("issuer_name", "issuer_reg_no", "bill_to", "vessel_name", "voyage_no", "currency")
DATE_FIELDS = ("issue_date", "due_date")
NUMBER_FIELDS = ("subtotal", "tax", "total")
BANK_FIELDS = ("name", "branch", "account_type", "account_no", "swift")
LINE_FIELDS = ("description", "qty", "unit_price", "amount")
LINE_NUMBERS = ("qty", "unit_price", "amount")
MAX_LINES = 200

_PAGE = {"type": ["integer", "null"], "minimum": 1, "maximum": 10000}
_NUM = {"type": ["number", "string", "null"]}
_STR = {"type": ["string", "null"], "maxLength": 500}


def _field(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "page"],
        "properties": {"value": value, "page": _PAGE},
    }


def response_schema() -> dict[str, Any]:
    """LLM に返させる JSON の形（各項目に値とページ番号）。"""
    props: dict[str, Any] = {}
    for name in DATE_FIELDS:
        props[name] = _field({"type": ["string", "null"], "maxLength": 40})
    for name in TEXT_FIELDS:
        props[name] = _field(_STR)
    for name in NUMBER_FIELDS:
        props[name] = _field(_NUM)
    props["bank"] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(BANK_FIELDS),
        "properties": {name: _field(_STR) for name in BANK_FIELDS},
    }
    props["lines"] = {
        "type": "array",
        "maxItems": MAX_LINES,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": [*LINE_FIELDS, "page"],
            "properties": {
                "description": _STR,
                "qty": _NUM,
                "unit_price": _NUM,
                "amount": _NUM,
                "page": _PAGE,
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": SCHEMA_ID,
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "properties": props,
    }


PROMPT = """次は請求書から読み取ったテキストです。スキーマに従って項目を抜き出してください。
- テキストに書かれていない項目は null にします。推測で埋めません。
- 金額は数字だけを書きます（カンマや通貨記号は除く）。通貨は ISO コード（JPY, USD など）で書きます。
- 日付は YYYY-MM-DD 形式にします。
- 各項目に、その値があったページ番号を付けます。
<untrusted source="{source}" ref="{ref}">
{text}
</untrusted>"""

# ISO 4217（有効な通貨コード）
ISO_4217 = frozenset(
    """AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD
    CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS
    GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
    LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD
    OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP
    SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XCD XCG XOF
    XPF YER ZAR ZMW ZWG""".split()
)
ZERO_DECIMAL = frozenset(
    {
        "JPY",
        "KRW",
        "VND",
        "CLP",
        "ISK",
        "PYG",
        "UGX",
        "XAF",
        "XOF",
        "XPF",
        "BIF",
        "DJF",
        "GNF",
        "KMF",
        "RWF",
        "VUV",
    }
)
