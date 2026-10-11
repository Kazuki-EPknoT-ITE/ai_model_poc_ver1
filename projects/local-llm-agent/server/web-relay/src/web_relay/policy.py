"""org ごとのポリシー（機密語辞書・禁止/信頼サイト・社内ドメイン）をまとめて保持する。

辞書の中身はここから外へ出さない（ログにも件数しか出さない）。
登録されていない org は、共通の正規表現・全社共通のサイト一覧・社内ドメインだけで判定する。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import PolicySettings, SiteSettings
from .dlp import DlpEngine, DlpTerm
from .netguard import SitePolicy, SiteRule, normalize_site_pattern


@dataclass(frozen=True)
class OrgPolicy:
    dlp: DlpEngine
    sites: SitePolicy


def _rules(sites: list[SiteSettings]) -> list[SiteRule]:
    return [SiteRule(pattern=normalize_site_pattern(s.pattern), kind=s.kind) for s in sites]


class PolicyStore:
    def __init__(self, settings: PolicySettings) -> None:
        self._lock = threading.Lock()
        self._orgs: dict[str, OrgPolicy] = {}
        self._default = OrgPolicy(
            DlpEngine(()), SitePolicy(_rules(settings.sites), settings.internal_domains)
        )
        self.load(settings)

    def load(self, settings: PolicySettings) -> None:
        """設定を読み直す（管理画面で辞書を更新したときなど）。"""
        orgs: dict[str, OrgPolicy] = {}
        for org_id, org in settings.orgs.items():
            terms = [DlpTerm(t.term, tuple(t.readings)) for t in org.dlp_terms]
            sites = SitePolicy(
                _rules(settings.sites) + _rules(org.sites),
                [*settings.internal_domains, *org.internal_domains],
            )
            orgs[org_id] = OrgPolicy(DlpEngine(terms), sites)
        default = OrgPolicy(DlpEngine(()), SitePolicy(_rules(settings.sites), settings.internal_domains))
        with self._lock:
            self._orgs = orgs
            self._default = default

    def get(self, org_id: str) -> OrgPolicy:
        with self._lock:
            return self._orgs.get(org_id, self._default)
