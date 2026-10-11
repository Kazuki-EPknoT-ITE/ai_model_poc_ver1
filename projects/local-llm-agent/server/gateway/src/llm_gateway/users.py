"""利用者の管理（管理 API と CLI で共用。ADM-01/02/04）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AuthService, LocalAuthProvider
from .config import AuthSettings, Profile
from .db import Department, Org, User
from .util import ApiError, Clock, new_ulid


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    role: Literal["user", "admin"] = "user"
    department: str | None = Field(default=None, max_length=128)
    max_concurrent: int = Field(default=2, ge=1, le=64)
    net_level_cap: Literal["off", "search", "browse"] = "off"


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    role: Literal["user", "admin"] | None = None
    department: str | None = Field(default=None, max_length=128)
    max_concurrent: int | None = Field(default=None, ge=1, le=64)
    net_level_cap: Literal["off", "search", "browse"] | None = None
    disabled: bool | None = None


async def ensure_org(s: AsyncSession, profile: Profile) -> Org:
    org = await s.get(Org, profile.org.id)
    data = profile.model_dump(exclude={"instructions_body", "base_dir"})
    if org is None:
        org = Org(id=profile.org.id, name=profile.org.name, profile=data)
        s.add(org)
    else:
        org.name = profile.org.name
        org.profile = data
    await s.flush()
    return org


async def ensure_department(s: AsyncSession, org_id: str, dept: str) -> Department:
    row = await s.get(Department, dept)
    if row is None:
        row = Department(id=dept, org_id=org_id, name=dept, acl_groups=[])
        s.add(row)
        await s.flush()
    return row


def user_view(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "login": u.login,
        "display_name": u.display_name,
        "role": u.role,
        "department": u.department_id,
        "auth_provider": u.auth_provider,
        "max_concurrent": u.max_concurrent,
        "net_level_cap": u.net_level_cap,
        "disabled": u.disabled,
        "locked_until": u.locked_until.isoformat() if u.locked_until else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


class UserAdmin:
    def __init__(
        self, provider: LocalAuthProvider, settings: AuthSettings, profile: Profile, clock: Clock
    ) -> None:
        self.provider = provider
        self.s = settings
        self.profile = profile
        self.clock = clock

    def _check_password(self, pw: str) -> None:
        if len(pw) < self.s.min_password_length:
            raise ApiError(
                400, "INVALID_REQUEST", f"パスワードは{self.s.min_password_length}文字以上にしてください"
            )

    async def create(self, s: AsyncSession, body: UserCreate) -> User:
        self._check_password(body.password)
        if (await s.execute(select(User.id).where(User.login == body.login))).first() is not None:
            raise ApiError(409, "CONFLICT", "そのログインIDは使われています")
        org = await ensure_org(s, self.profile)
        dept = await ensure_department(s, org.id, body.department) if body.department else None
        user = User(
            id=new_ulid(),
            org_id=org.id,
            department_id=dept.id if dept else None,
            login=body.login,
            display_name=body.display_name,
            role=body.role,
            auth_provider="local",
            password_hash=self.provider.hash(body.password),
            extra_acl_groups=[],
            max_concurrent=body.max_concurrent,
            net_level_cap=body.net_level_cap,
            disabled=False,
            failed_logins=0,
            created_at=self.clock.now(),
        )
        s.add(user)
        await s.flush()
        return user

    async def patch(self, s: AsyncSession, auth: AuthService, user_id: str, body: UserPatch) -> User:
        user = await s.get(User, user_id)
        if user is None:
            raise ApiError(404, "NOT_FOUND", "利用者がいません")
        if body.display_name is not None:
            user.display_name = body.display_name
        if body.role is not None:
            user.role = body.role
        if body.department is not None:
            if body.department == "":
                user.department_id = None
            else:
                user.department_id = (await ensure_department(s, user.org_id, body.department)).id
        if body.max_concurrent is not None:
            user.max_concurrent = body.max_concurrent
        if body.net_level_cap is not None:
            user.net_level_cap = body.net_level_cap
        if body.password is not None:
            self._check_password(body.password)
            user.password_hash = self.provider.hash(body.password)
            user.failed_logins = 0
            user.locked_until = None
            await auth.revoke_user_sessions(s, user.id, "admin")
        if body.disabled is not None:
            user.disabled = body.disabled
            if body.disabled:
                await auth.revoke_user_sessions(s, user.id, "admin")
        await s.flush()
        return user
