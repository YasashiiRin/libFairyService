"""Accounts, sessions and passwords.

A session is one row in refresh_tokens (one per signed-in device). The access token
carries its id (`sid`), so revoking the row signs that device out at once.
The refresh token is not rotated: several tabs share it, and rotation would sign
the other tabs out after one refresh. It expires after REFRESH_TOKEN_TTL_DAYS
without use, and it can be revoked per device.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models import PasswordResetToken, RefreshToken, User
from app.models.user import default_settings
from app.schemas.auth import RegisterIn
from app.services.devices import DeviceInfo

logger = logging.getLogger(__name__)

# ── Errors (detail is shown to the user) ────────────────────────────────


def invalid_credentials() -> ApiError:
    # 401 on login does not trigger the client's refresh flow (the login request sends no token)
    return ApiError(401, "Tên đăng nhập hoặc mật khẩu chưa đúng", "invalid_credentials")


def account_locked() -> ApiError:
    return ApiError(
        403,
        "Tài khoản của nàng đang tạm bị khóa. Nàng liên hệ quản trị viên để được mở lại nhé.",
        "account_locked",
    )


def invalid_refresh_token() -> ApiError:
    return ApiError(401, "Phiên đăng nhập đã hết hạn, nàng đăng nhập lại nhé", "invalid_refresh_token")


def invalid_reset_token() -> ApiError:
    return ApiError(400, "Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn", "invalid_reset_token")


def _now() -> datetime:
    return datetime.now(UTC)


# ── Accounts ─────────────────────────────────────────────────────────────


async def find_by_login(db: AsyncSession, identifier: str) -> User | None:
    ident = identifier.strip().lower()
    return await db.scalar(select(User).where(or_(func.lower(User.username) == ident, func.lower(User.email) == ident)))


async def register(db: AsyncSession, data: RegisterIn) -> User:
    if await db.scalar(select(User.id).where(func.lower(User.username) == data.username)):
        raise ApiError(409, "Tên đăng nhập này đã có người dùng", "username_taken")
    if await db.scalar(select(User.id).where(func.lower(User.email) == data.email)):
        raise ApiError(409, "Email này đã được đăng ký", "email_taken")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        settings=default_settings(),
    )
    db.add(user)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, identifier: str, password: str) -> User:
    user = await find_by_login(db, identifier)
    # verify even for unknown users, so response time doesn't reveal which usernames exist
    if not verify_password(user.password_hash if user else None, password) or user is None:
        raise invalid_credentials()
    # only reveal the lock to someone who knows the password
    if not user.is_active:
        raise account_locked()
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


# ── Sessions ─────────────────────────────────────────────────────────────


async def start_session(db: AsyncSession, user: User, device: DeviceInfo) -> tuple[str, str]:
    """New device session → (access_token, refresh_token)."""
    refresh = new_token()
    now = _now()
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh),
        device_name=device.device_name,
        device_type=device.device_type,
        browser=device.browser,
        location=device.location,
        user_agent=device.user_agent,
        created_at=now,
        last_active_at=now,
        expires_at=now + timedelta(days=get_settings().refresh_token_ttl_days),
    )
    db.add(row)
    await db.flush()
    return create_access_token(user.id, row.id, now), refresh


async def refresh_access_token(db: AsyncSession, refresh_token: str, device: DeviceInfo) -> str:
    now = _now()
    row = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(refresh_token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    if row is None:
        raise invalid_refresh_token()
    user = await db.get(User, row.user_id)
    if user is None:
        raise invalid_refresh_token()
    if not user.is_active:
        raise account_locked()
    row.last_active_at = now
    row.expires_at = now + timedelta(days=get_settings().refresh_token_ttl_days)
    if device.location:
        row.location = device.location
    return create_access_token(user.id, row.id, now)


async def active_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> tuple[RefreshToken, User] | None:
    """The session an access token belongs to, if it is still signed in."""
    row = await db.execute(
        select(RefreshToken, User)
        .join(User, User.id == RefreshToken.user_id)
        .where(
            RefreshToken.id == session_id,
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > _now(),
        )
    )
    found = row.first()
    return (found[0], found[1]) if found else None


async def logout(db: AsyncSession, user: User, current_session_id: uuid.UUID, refresh_token: str | None) -> None:
    """Revoke the session of `refresh_token` (this device), or the current one."""
    target = RefreshToken.id == current_session_id
    if refresh_token:
        target = or_(target, RefreshToken.token_hash == hash_token(refresh_token))
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None), target)
        .values(revoked_at=_now())
    )


async def list_sessions(db: AsyncSession, user: User) -> list[RefreshToken]:
    rows = await db.scalars(
        select(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > _now())
        .order_by(RefreshToken.last_active_at.desc())
    )
    return list(rows)


async def revoke_sessions(db: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None = None) -> int:
    """Sign out every device of the user, except `keep`. Returns how many were signed out."""
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if keep is not None:
        stmt = stmt.where(RefreshToken.id != keep)
    result = await db.execute(stmt.values(revoked_at=_now()))
    return result.rowcount or 0


# ── Passwords ────────────────────────────────────────────────────────────


async def change_password(db: AsyncSession, user: User, current_session_id: uuid.UUID, current: str, new: str) -> None:
    if not verify_password(user.password_hash, current):
        raise ApiError(400, "Mật khẩu hiện tại chưa đúng", "wrong_password")
    if current == new:
        raise ApiError(422, "Mật khẩu mới cần khác mật khẩu hiện tại", "validation_error")
    user.password_hash = hash_password(new)
    # anyone who knew the old password is signed out; this device stays in
    await revoke_sessions(db, user.id, keep=current_session_id)


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Create a reset token and "send" the link. Silent when the email is unknown (no account enumeration)."""
    user = await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None or not user.is_active:
        return
    now = _now()
    # only the newest link works
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_token()
    settings = get_settings()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    link = f"{settings.app_url}/reset-password?token={token}"
    # TODO(email): send `link` by email instead of printing it.
    print(f"[password-reset] {user.email}: {link}", flush=True)


async def reset_password(db: AsyncSession, token: str, new_password: str) -> None:
    now = _now()
    row = await db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hash_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .with_for_update()
    )
    if row is None:
        raise invalid_reset_token()
    user = await db.get(User, row.user_id)
    if user is None:
        raise invalid_reset_token()
    if not user.is_active:
        raise account_locked()
    row.used_at = now
    user.password_hash = hash_password(new_password)
    await revoke_sessions(db, user.id)
