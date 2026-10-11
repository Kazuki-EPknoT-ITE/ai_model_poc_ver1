"""Relay のエラー。形式は API 仕様 §1.1 に合わせる（Gateway がそのまま中継できるように）。

メッセージには検索語・URL・辞書の語を入れない（dev_guide §3.1）。
"""

from __future__ import annotations

from typing import Any


class RelayError(Exception):
    """利用者（＝AI）に返すエラー。`code` は API 仕様のエラーコード。"""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}


def unauthenticated() -> RelayError:
    return RelayError(401, "UNAUTHENTICATED", "内部 API の認証に失敗しました")


def invalid_request(message: str = "リクエストの形式が正しくありません") -> RelayError:
    return RelayError(400, "INVALID_REQUEST", message)


def net_disabled() -> RelayError:
    return RelayError(403, "NET_DISABLED", "この操作はネットの設定で許可されていません")


def dlp_blocked() -> RelayError:
    # どの語・どの規則に該当したかは返さない（辞書の漏えい防止。API §7）
    return RelayError(
        403,
        "DLP_BLOCKED",
        "機密情報の可能性がある語（社外秘の名称・口座番号・金額・連絡先など）を含むため送信しませんでした。"
        "固有名詞や数値を除いた一般的な言葉に言い換えて検索してください。",
    )


def domain_blocked() -> RelayError:
    return RelayError(
        403,
        "DOMAIN_BLOCKED",
        "このアドレスへのアクセスは許可されていません（社内アドレス・禁止サイト・許可されないスキームやポート）",
    )


def quota_exceeded() -> RelayError:
    return RelayError(
        429,
        "QUOTA_EXCEEDED",
        "今月の検索回数の上限に達しました。ネットを使わずに作業を続けてください。",
        retryable=False,
    )


def policy_unavailable() -> RelayError:
    # Gateway からポリシーを取り込めていない（安全側に倒して断る）。時間をおけば直るので再試行可
    return RelayError(
        503, "UPSTREAM_UNAVAILABLE", "ネットのポリシーを読み込めていないため、今は使えません", retryable=True
    )


def upstream(code: str, message: str, *, retryable: bool = False) -> RelayError:
    """取得先・検索 API 側の問題（502）。code は UPSTREAM_* / CONTENT_TYPE_NOT_ALLOWED など。"""
    return RelayError(502, code, message, retryable=retryable)
