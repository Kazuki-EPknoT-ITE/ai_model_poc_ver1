"""ネットアクセスの上限（NET-02）: 利用者・部門・全社・クライアントの設定（X-Net-Level）の最小を採用する。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Principal
from .config import Profile
from .db import NetPolicy
from .util import net_min


@dataclass
class NetDecision:
    cap: str  # 管理者側の上限（利用者・部門・全社の最小）
    effective: str  # クライアントの設定との最小
    monthly_search_limit: int | None


async def admin_cap(s: AsyncSession, p: Principal, profile: Profile) -> tuple[str, int | None]:
    org = await s.get(NetPolicy, "org")
    org_cap = org.level_cap if org is not None else profile.net.level_cap
    limits: list[int] = []
    org_limit = org.monthly_search_limit if org is not None else profile.net.monthly_search_limit
    if org_limit is not None:
        limits.append(org_limit)
    dept_cap: str | None = None
    if p.department_id is not None:
        dept = await s.get(NetPolicy, f"department:{p.department_id}")
        if dept is not None:
            dept_cap = dept.level_cap
            if dept.monthly_search_limit is not None:
                limits.append(dept.monthly_search_limit)
    return net_min(p.net_level_cap, dept_cap, org_cap), (min(limits) if limits else None)


async def decide(s: AsyncSession, p: Principal, profile: Profile, client_level: str | None) -> NetDecision:
    cap, limit = await admin_cap(s, p, profile)
    # クライアントが送ってこなければ off とみなす（二重の確認。local_llm_api.md §7）
    return NetDecision(cap=cap, effective=net_min(cap, client_level or "off"), monthly_search_limit=limit)
