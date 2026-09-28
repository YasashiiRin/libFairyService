"""Passwords (argon2id), access tokens (JWT) and opaque tokens (refresh, password reset)."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# Verified when the user doesn't exist, so a login takes as long either way.
_DUMMY_HASH = _hasher.hash("not-a-real-password")

JWT_ALGORITHM = "HS256"


# ── Passwords ────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ── Opaque tokens (stored only as SHA-256) ───────────────────────────────


def new_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Access tokens ────────────────────────────────────────────────────────


class TokenError(Exception):
    def __init__(self, expired: bool = False):
        self.expired = expired


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None) -> str:
    settings = get_settings()
    now = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "sid", "exp", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError(expired=True) from exc
    except jwt.PyJWTError as exc:
        raise TokenError() from exc
    if payload.get("type") != "access":
        raise TokenError()
    try:
        return AccessClaims(user_id=uuid.UUID(payload["sub"]), session_id=uuid.UUID(payload["sid"]))
    except (ValueError, TypeError) as exc:
        raise TokenError() from exc
