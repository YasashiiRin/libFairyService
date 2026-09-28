from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.user import default_settings

PASSWORD = "12012007"
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
MAC_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


async def make_user(
    db: AsyncSession,
    username: str = "bemeolac",
    password: str = PASSWORD,
    email: str | None = None,
    is_active: bool = True,
    display_name: str = "Uyên",
) -> User:
    user = User(
        username=username,
        email=email or f"{username}@example.com",
        password_hash=hash_password(password),
        display_name=display_name,
        is_active=is_active,
        settings=default_settings(),
    )
    db.add(user)
    await db.commit()
    return user


async def login(api, username: str = "bemeolac", password: str = PASSWORD, ua: str = MAC_CHROME_UA) -> dict:
    res = await api.post("/api/auth/login", json={"username": username, "password": password}, headers={"User-Agent": ua})
    assert res.status_code == 200, res.text
    return res.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def expired_access_token(user_id, session_id) -> str:
    return create_access_token(user_id, session_id, now=datetime.now(UTC) - timedelta(hours=1))
