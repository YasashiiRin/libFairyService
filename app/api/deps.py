import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ApiError
from app.core.security import TokenError, decode_access_token
from app.models import User
from app.services import auth as auth_service

_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}

Db = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Auth:
    user: User
    session_id: uuid.UUID


async def get_auth(db: Db, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Auth:
    """The signed-in user. 401 → the client refreshes once, then logs out (api-contract.md#authentication)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "Nàng cần đăng nhập để tiếp tục", "not_authenticated", _CHALLENGE)
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        if exc.expired:
            raise ApiError(401, "Phiên đăng nhập đã hết hạn", "token_expired", _CHALLENGE) from exc
        raise ApiError(401, "Nàng cần đăng nhập để tiếp tục", "not_authenticated", _CHALLENGE) from exc

    found = await auth_service.active_session(db, claims.session_id, claims.user_id)
    if found is None:  # signed out on this device, or from another one
        raise ApiError(401, "Phiên đăng nhập đã kết thúc, nàng đăng nhập lại nhé", "session_revoked", _CHALLENGE)
    _, user = found
    if not user.is_active:
        raise auth_service.account_locked()
    return Auth(user=user, session_id=claims.session_id)


CurrentAuth = Annotated[Auth, Depends(get_auth)]
