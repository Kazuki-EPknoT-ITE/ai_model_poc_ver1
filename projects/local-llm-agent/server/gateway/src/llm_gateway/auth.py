"""認証（ADR-008）。

- パスワード: argon2id（m=64MiB, t=3, p=1）。5回続けて失敗したら15分ロック。
- アクセストークン: JWT（EdDSA / Ed25519）15分。`sid` にセッション系列（family）を入れる。
- リフレッシュトークン: 不透明な乱数。DB には sha256 だけを置く。使うたびに交換し、
  交換済み（失効済み）のトークンが再び使われたら盗用とみなして系列をすべて失効させる。
- `AuthProvider` で抽象化し、OIDC・LDAP を後から追加できるようにする。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import AuthSettings
from .db import Database, Department, Session, User
from .util import ApiError, Clock, new_ulid, sha256_hex

log = logging.getLogger("llm_gateway.auth")

# 系列ごと無効にする失効理由（rotated は「交換済み」で系列は生きている）
FAMILY_DEAD_REASONS = ("logout", "reuse", "password", "admin")


def make_hasher(s: AuthSettings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=s.argon2.time_cost, memory_cost=s.argon2.memory_kib, parallelism=s.argon2.parallelism
    )


@dataclass
class Principal:
    """認証済みの利用者（リクエストごと）。"""

    user_id: str
    login: str
    role: str
    org_id: str
    department_id: str | None
    family_id: str
    max_concurrent: int
    net_level_cap: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthProvider(Protocol):
    """認証方式の抽象（ADR-008）。local の後に OIDC・LDAP を追加する。"""

    name: str

    async def authenticate(self, s: AsyncSession, login: str, password: str) -> User | None:
        """成功なら User、失敗なら None。ロック中などは ApiError。"""
        ...

    async def resolve_groups(self, s: AsyncSession, user: User) -> list[str]:
        """RAG の検索範囲などに使うグループ（部門の acl_groups ＋ 個人の追加分）。"""
        ...


class LocalAuthProvider:
    name = "local"

    def __init__(self, settings: AuthSettings, clock: Clock) -> None:
        self.s = settings
        self.clock = clock
        self.hasher = make_hasher(settings)
        # 存在しない利用者でも同じだけ時間をかける（利用者の有無を推測させない）
        self._dummy = self.hasher.hash(secrets.token_hex(16))

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def _verify(self, hashed: str, password: str) -> bool:
        try:
            return self.hasher.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    async def authenticate(self, s: AsyncSession, login: str, password: str) -> User | None:
        user = (await s.execute(select(User).where(User.login == login))).scalar_one_or_none()
        if user is None or user.auth_provider != "local" or user.password_hash is None:
            self._verify(self._dummy, password)
            return None
        now = self.clock.now()
        if user.locked_until is not None and user.locked_until > now:
            # ロック中は正しいパスワードでも通さない（回数も増やさない）
            raise ApiError(401, "UNAUTHENTICATED", "ログインIDまたはパスワードが違います")
        if user.locked_until is not None:
            user.locked_until = None
            user.failed_logins = 0
        if not self._verify(user.password_hash, password):
            user.failed_logins += 1
            if user.failed_logins >= self.s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=self.s.lockout_minutes)
                user.failed_logins = 0
                log.warning("account locked user_id=%s", user.id)
            return None
        if user.disabled:
            return None
        user.failed_logins = 0
        if self.hasher.check_needs_rehash(user.password_hash):
            user.password_hash = self.hasher.hash(password)
        return user

    async def resolve_groups(self, s: AsyncSession, user: User) -> list[str]:
        groups = [f"user:{user.id}"]
        if user.department_id is not None:
            dept = await s.get(Department, user.department_id)
            groups.append(f"department:{user.department_id}")
            if dept is not None:
                groups.extend(dept.acl_groups)
        groups.extend(user.extra_acl_groups)
        return list(dict.fromkeys(groups))


class TokenService:
    def __init__(self, key: Ed25519PrivateKey, settings: AuthSettings, clock: Clock) -> None:
        self.key = key
        self.pub = key.public_key()
        self.s = settings
        self.clock = clock

    def issue_access(self, user: User, family_id: str) -> str:
        now = self.clock.now()
        claims: dict[str, Any] = {
            "iss": self.s.issuer,
            "sub": user.id,
            "role": user.role,
            "sid": family_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.s.access_ttl_s)).timestamp()),
            "jti": new_ulid(),
        }
        return jwt.encode(claims, self.key, algorithm="EdDSA")

    def verify_access(self, token: str) -> dict[str, Any]:
        try:
            # 期限は差し替え可能な時計で判定するため、ライブラリの exp 判定は切る
            claims: dict[str, Any] = jwt.decode(
                token,
                self.pub,
                algorithms=["EdDSA"],
                issuer=self.s.issuer,
                options={"verify_exp": False, "require": ["exp", "sub", "sid", "iss"]},
            )
        except jwt.PyJWTError:
            raise ApiError(401, "UNAUTHENTICATED", "認証情報が無効です") from None
        if int(claims["exp"]) <= int(self.clock.now().timestamp()):
            raise ApiError(401, "TOKEN_EXPIRED", "アクセストークンの有効期限が切れました")
        return claims


class AuthService:
    def __init__(
        self,
        db: Database,
        provider: LocalAuthProvider,
        tokens: TokenService,
        settings: AuthSettings,
        clock: Clock,
    ) -> None:
        self.db = db
        self.provider = provider
        self.tokens = tokens
        self.s = settings
        self.clock = clock

    async def _new_session(
        self, s: AsyncSession, user: User, family_id: str, device: str | None
    ) -> tuple[str, Session]:
        raw = secrets.token_urlsafe(32)
        now = self.clock.now()
        row = Session(
            id=new_ulid(),
            user_id=user.id,
            family_id=family_id,
            refresh_hash=sha256_hex(raw),
            device_name=device,
            created_at=now,
            expires_at=now + timedelta(days=self.s.refresh_ttl_days),
        )
        s.add(row)
        return raw, row

    async def _token_response(
        self, s: AsyncSession, user: User, family_id: str, refresh: str
    ) -> dict[str, Any]:
        dept = await s.get(Department, user.department_id) if user.department_id else None
        return {
            "access_token": self.tokens.issue_access(user, family_id),
            "refresh_token": refresh,
            "expires_in": self.s.access_ttl_s,
            "user": {
                "id": user.id,
                "display_name": user.display_name,
                "role": user.role,
                "department": dept.name if dept else None,
            },
        }

    async def login(self, login: str, password: str, device: str | None) -> tuple[dict[str, Any], str | None]:
        """(応答, user_id)。失敗は ApiError(401)。"""
        async with self.db.tx() as s:
            user = await self.provider.authenticate(s, login, password)
            if user is not None:
                family = new_ulid()
                raw, _ = await self._new_session(s, user, family, device)
                return await self._token_response(s, user, family, raw), user.id
        # 失敗回数の書き込みをコミットしてから 401 を返す
        raise ApiError(401, "UNAUTHENTICATED", "ログインIDまたはパスワードが違います")

    async def refresh(self, raw: str) -> dict[str, Any]:
        now = self.clock.now()
        reuse_family: str | None = None
        async with self.db.tx() as s:
            row = (
                await s.execute(select(Session).where(Session.refresh_hash == sha256_hex(raw)))
            ).scalar_one_or_none()
            if row is None:
                raise ApiError(401, "UNAUTHENTICATED", "リフレッシュトークンが無効です")
            if row.revoked_at is not None:
                if row.revoked_reason == "rotated":
                    reuse_family = row.family_id
                    await self._revoke_family(s, row.family_id, "reuse")
                    log.warning(
                        "refresh token reuse detected user_id=%s family=%s", row.user_id, row.family_id
                    )
            elif row.expires_at <= now:
                raise ApiError(401, "UNAUTHENTICATED", "リフレッシュトークンの有効期限が切れました")
            else:
                user = await s.get(User, row.user_id)
                if user is None or user.disabled or await self._family_dead(s, row.family_id):
                    raise ApiError(401, "UNAUTHENTICATED", "リフレッシュトークンが無効です")
                row.revoked_at = now
                row.revoked_reason = "rotated"
                new_raw, _ = await self._new_session(s, user, row.family_id, row.device_name)
                return await self._token_response(s, user, row.family_id, new_raw)
        # 失効の書き込みはコミットしてから 401 を返す
        if reuse_family is not None:
            raise ApiError(401, "UNAUTHENTICATED", "リフレッシュトークンが無効です（再ログインしてください）")
        raise ApiError(401, "UNAUTHENTICATED", "リフレッシュトークンが無効です")

    async def logout(self, raw: str) -> None:
        async with self.db.tx() as s:
            row = (
                await s.execute(select(Session).where(Session.refresh_hash == sha256_hex(raw)))
            ).scalar_one_or_none()
            if row is not None:
                await self._revoke_family(s, row.family_id, "logout")

    async def change_password(self, p: Principal, current: str, new: str) -> None:
        if len(new) < self.s.min_password_length:
            raise ApiError(
                400, "INVALID_REQUEST", f"パスワードは{self.s.min_password_length}文字以上にしてください"
            )
        async with self.db.tx() as s:
            user = await s.get(User, p.user_id)
            if (
                user is None
                or user.password_hash is None
                or not self.provider._verify(user.password_hash, current)
            ):
                raise ApiError(403, "FORBIDDEN", "現在のパスワードが違います")
            user.password_hash = self.provider.hash(new)
            # ほかの端末のセッションは失効させる（今の系列は残す）
            await self.revoke_user_sessions(s, user.id, "password", keep_family=p.family_id)

    async def _revoke_family(self, s: AsyncSession, family_id: str, reason: str) -> None:
        now = self.clock.now()
        await s.execute(
            update(Session)
            .where(Session.family_id == family_id, Session.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )
        # 交換済みの行にも「系列が死んだ」印を残す（アクセストークンの判定用）
        await s.execute(
            update(Session)
            .where(Session.family_id == family_id, Session.revoked_reason == "rotated")
            .values(revoked_reason=reason)
        )

    async def revoke_user_sessions(
        self, s: AsyncSession, user_id: str, reason: str, keep_family: str | None = None
    ) -> None:
        q = select(Session.family_id).where(Session.user_id == user_id).distinct()
        for fam in (await s.execute(q)).scalars().all():
            if fam != keep_family:
                await self._revoke_family(s, fam, reason)

    async def _family_dead(self, s: AsyncSession, family_id: str) -> bool:
        q = (
            select(Session.id)
            .where(Session.family_id == family_id, Session.revoked_reason.in_(FAMILY_DEAD_REASONS))
            .limit(1)
        )
        return (await s.execute(q)).first() is not None

    async def principal(self, token: str) -> Principal:
        claims = self.tokens.verify_access(token)
        async with self.db.tx() as s:
            user = await s.get(User, str(claims["sub"]))
            if user is None or user.disabled:
                raise ApiError(401, "UNAUTHENTICATED", "認証情報が無効です")
            family = str(claims["sid"])
            if await self._family_dead(s, family):
                raise ApiError(401, "UNAUTHENTICATED", "セッションは失効しています")
            return Principal(
                user_id=user.id,
                login=user.login,
                role=user.role,
                org_id=user.org_id,
                department_id=user.department_id,
                family_id=family,
                max_concurrent=user.max_concurrent,
                net_level_cap=user.net_level_cap,
            )
