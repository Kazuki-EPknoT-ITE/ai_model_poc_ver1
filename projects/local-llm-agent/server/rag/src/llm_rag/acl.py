"""フォルダ単位の ACL 対応表（パスの接頭辞 → acl_groups）。ADM-02 / SEC-07。

- 最も長く一致した接頭辞の規則を使う（下位フォルダで上書きできる）。
- どの規則にも当たらない文書は取り込まない（既定は拒否）。
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_rag.config import AclMap
from llm_rag.model import norm_path, under_prefix


@dataclass(frozen=True)
class AclDecision:
    acl_groups: list[str]
    doc_type: str | None


class AclResolver:
    def __init__(self, acl_map: AclMap) -> None:
        self._rules = sorted(acl_map.rules, key=lambda r: len(norm_path(r.prefix)), reverse=True)

    def resolve(self, path: str) -> AclDecision | None:
        for r in self._rules:
            if under_prefix(path, r.prefix):
                groups = sorted({g.strip() for g in r.acl_groups if g.strip()})
                return AclDecision(groups, r.doc_type) if groups else None
        return None
