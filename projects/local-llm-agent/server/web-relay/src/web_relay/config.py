"""Web Relay の設定（YAML）。

- 共有シークレット（Gateway だけが知る）は設定ファイルに書かず、環境変数かファイルで渡す。
- 機密語辞書（dlp_terms）・サイト一覧（net_sites）は `policy` に書くか、`policy_file` で別ファイルにする。
  本番では Gateway の DB（data_model §3 の net_sites / dlp_terms）から書き出したものを読み込む想定。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

NetLevel = Literal["off", "search", "browse"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_Strict):
    host: str = "0.0.0.0"  # noqa: S104 - 内部ネットワークで待ち受ける（Gateway からのみ到達できる配置にする）
    port: int = 8600
    # 共有シークレットを入れた環境変数名。`secret_file` があればそちらを優先する
    secret_env: str = "WEB_RELAY_SECRET"  # noqa: S105 - 環境変数の名前
    secret_file: str | None = None


class FetchSettings(_Strict):
    max_bytes: int = 10 * 1024 * 1024  # NET-06 / 設計書 §6.6: 10MB
    timeout_s: float = 20.0  # 1ホップ（接続〜本文受信まで）の上限
    connect_timeout_s: float = 5.0
    max_redirects: int = 5
    # スキームごとに許すポート（NET-08。既定は 80/443 のみ）
    allowed_ports: dict[str, list[int]] = Field(default_factory=lambda: {"http": [80], "https": [443]})
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["text/html", "application/xhtml+xml", "application/pdf", "text/plain"]
    )
    user_agent: str = "LocalLLMAgent-WebRelay/0.1 (+read-only)"
    max_chars: int = 8000  # NET-09: 1ページ最大 8,000 字
    max_pdf_pages: int = 50
    use_trafilatura: bool = True


class SearchSettings(_Strict):
    provider: Literal["fake", "searxng", "brave"] = "fake"
    base_url: str | None = None  # searxng: 自前で立てた SearXNG の URL。brave: 省略時は公式 API
    api_key_env: str | None = None  # 商用 API のキーを入れた環境変数名
    timeout_s: float = 10.0
    max_results: int = 10
    title_max_chars: int = 200
    snippet_max_chars: int = 400
    # 月ごとの検索回数の既定上限（Gateway がリクエストで上限を渡した場合はそちらを使う）。None は無制限
    default_monthly_limit: int | None = 300
    # 「月」の区切りに使う時差（JST = 9）
    quota_utc_offset_hours: int = 9


class AuditSettings(_Strict):
    kind: Literal["memory", "jsonl"] = "jsonl"
    path: str = "./data/web-relay-audit.jsonl"


class UsageSettings(_Strict):
    kind: Literal["memory", "sqlite"] = "sqlite"
    path: str = "./data/web-relay-usage.sqlite3"


class DlpTermSettings(_Strict):
    term: str
    category: str = "other"  # client | vessel | project | other（data_model §3 dlp_terms）
    readings: list[str] = Field(default_factory=list)  # 読み（ひらがな・カタカナ・ローマ字など）


class SiteSettings(_Strict):
    pattern: str  # '*.go.jp' / 'example.com'
    kind: Literal["block", "trusted"]


class OrgPolicySettings(_Strict):
    dlp_terms: list[DlpTermSettings] = Field(default_factory=list)
    sites: list[SiteSettings] = Field(default_factory=list)
    internal_domains: list[str] = Field(default_factory=list)


class PolicySettings(_Strict):
    # 社内ドメイン（サフィックス一致）。localhost とドットを含まない名前は常に拒否する
    internal_domains: list[str] = Field(
        default_factory=lambda: ["local", "localdomain", "internal", "intranet", "lan", "corp", "home.arpa"]
    )
    # 追加で拒否するネットワーク（社内の公開アドレス帯など）。CIDR 表記
    blocked_networks: list[str] = Field(default_factory=list)
    sites: list[SiteSettings] = Field(default_factory=list)  # 全社共通の net_sites
    orgs: dict[str, OrgPolicySettings] = Field(default_factory=dict)


class Settings(_Strict):
    server: ServerSettings = Field(default_factory=ServerSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    policy_file: str | None = None


def load_settings(path: str | Path | None = None) -> Settings:
    """YAML を読み込む。`policy_file` があればそれで `policy` を置き換える。"""
    data: object = {}
    if path is not None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    settings = Settings.model_validate(data)
    if settings.policy_file:
        pdata = yaml.safe_load(Path(settings.policy_file).read_text(encoding="utf-8")) or {}
        settings = settings.model_copy(update={"policy": PolicySettings.model_validate(pdata)})
    return settings


def load_secret(server: ServerSettings) -> str:
    """共有シークレットを読む。空や短すぎる値では起動させない。"""
    secret = ""
    if server.secret_file:
        secret = Path(server.secret_file).read_text(encoding="utf-8").strip()
    else:
        secret = os.environ.get(server.secret_env, "").strip()
    if len(secret) < 16:
        raise RuntimeError(
            f"共有シークレットが未設定か短すぎます（16文字以上。{server.secret_env} または secret_file）"
        )
    return secret
