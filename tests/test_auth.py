"""POST /api/auth/* (api-contract.md#auth)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select, update

from app.core.security import hash_token
from app.models import PasswordResetToken, RefreshToken, User
from tests.helpers import IPHONE_UA, PASSWORD, bearer, expired_access_token, login, make_user

USER_FIELDS = {"id", "username", "email", "display_name", "bio", "avatar_url", "role", "created_at", "last_backup_at", "settings"}
SETTINGS_FIELDS = {
    "theme", "font_size", "sparkle_enabled", "notify_new_chapter", "notify_broken_link",
    "daily_reminder_enabled", "daily_reminder_time", "personalization_enabled", "starter_tastes", "priority_tastes",
}


# ── register ─────────────────────────────────────────────────────────────


async def test_register_signs_in(api, db_session):
    res = await api.post(
        "/api/auth/register",
        json={"username": "  TienNu ", "email": "TienNu@Example.com", "password": "hoa-hong-2026", "display_name": "Tiên Nữ"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["token_type"] == "bearer" and body["access_token"] and body["refresh_token"]
    assert set(body["user"]) == USER_FIELDS
    assert body["user"]["username"] == "tiennu"
    assert body["user"]["email"] == "tiennu@example.com"
    assert body["user"]["display_name"] == "Tiên Nữ"
    assert body["user"]["role"] == "user"

    me = await api.get("/api/users/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200 and me.json()["username"] == "tiennu"
    user = await db_session.scalar(select(User).where(User.username == "tiennu"))
    assert user.password_hash != "hoa-hong-2026" and user.password_hash.startswith("$argon2id$")


async def test_register_defaults_display_name_to_username(api):
    res = await api.post("/api/auth/register", json={"username": "meo", "email": "meo@example.com", "password": "12345678"})
    assert res.status_code == 201
    assert res.json()["user"]["display_name"] == "meo"


async def test_register_rejects_taken_username_and_email(api, db_session):
    await make_user(db_session, "bemeolac", email="uyen@example.com")
    res = await api.post("/api/auth/register", json={"username": "BeMeoLac", "email": "new@example.com", "password": "12345678"})
    assert res.status_code == 409 and res.json()["code"] == "username_taken"
    res = await api.post("/api/auth/register", json={"username": "other", "email": "UYEN@example.com", "password": "12345678"})
    assert res.status_code == 409 and res.json()["code"] == "email_taken"


async def test_register_validates_input(api):
    for body in (
        {"username": "ab", "email": "a@example.com", "password": "12345678"},  # too short
        {"username": "có dấu", "email": "a@example.com", "password": "12345678"},  # not allowed characters
        {"username": "valid", "email": "not-an-email", "password": "12345678"},
        {"username": "valid", "email": "a@example.com", "password": "1234567"},  # < 8 characters
    ):
        res = await api.post("/api/auth/register", json=body)
        assert res.status_code == 422, body
        assert res.json()["code"] == "validation_error"


# ── login ────────────────────────────────────────────────────────────────


async def test_login_with_username_or_email(api, db_session):
    await make_user(db_session, "bemeolac", email="uyen@example.com")
    for identifier in ("bemeolac", "BeMeoLac", "uyen@example.com", " UYEN@example.com "):
        body = await login(api, identifier)
        assert body["user"]["username"] == "bemeolac"
        assert set(body["user"]["settings"]) == SETTINGS_FIELDS


async def test_login_wrong_password_or_unknown_user(api, db_session):
    await make_user(db_session)
    for username, password in (("bemeolac", "wrong-password"), ("nobody", PASSWORD)):
        res = await api.post("/api/auth/login", json={"username": username, "password": password})
        assert res.status_code == 401
        assert res.json() == {"detail": "Tên đăng nhập hoặc mật khẩu chưa đúng", "code": "invalid_credentials"}


async def test_login_locked_account(api, db_session):
    await make_user(db_session, is_active=False)
    res = await api.post("/api/auth/login", json={"username": "bemeolac", "password": PASSWORD})
    assert res.status_code == 403
    assert res.json()["code"] == "account_locked"
    assert "khóa" in res.json()["detail"]
    # a wrong password on a locked account doesn't reveal the lock
    res = await api.post("/api/auth/login", json={"username": "bemeolac", "password": "wrong-password"})
    assert res.status_code == 401 and res.json()["code"] == "invalid_credentials"


async def test_login_records_the_device(api, db_session):
    await make_user(db_session)
    body = await login(api, ua=IPHONE_UA)
    sessions = (await api.get("/api/users/me/sessions", headers=bearer(body["access_token"]))).json()
    assert len(sessions) == 1
    assert sessions[0] | {"id": None, "last_active_at": None} == {
        "id": None,
        "device_name": "iPhone",
        "device_type": "phone",
        "browser": "Safari",
        "location": "",
        "last_active_at": None,
        "is_current": True,
    }
    row = await db_session.scalar(select(RefreshToken))
    assert row.token_hash == hash_token(body["refresh_token"])  # only the hash is stored


# ── access tokens ────────────────────────────────────────────────────────


async def test_protected_route_needs_a_valid_token(api, db_session):
    user = await make_user(db_session)
    body = await login(api)
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    session_id = uuid.UUID(claims["sid"])

    cases = {
        "not_authenticated": [{}, bearer("not-a-jwt"), {"Authorization": "Basic abc"}],
        "token_expired": [bearer(expired_access_token(user.id, session_id))],
    }
    forged = jwt.encode({"sub": str(user.id), "sid": str(session_id), "type": "access", "exp": 9999999999}, "x" * 40)
    cases["not_authenticated"].append(bearer(forged))
    for code, header_sets in cases.items():
        for headers in header_sets:
            res = await api.get("/api/users/me", headers=headers)
            assert res.status_code == 401, headers
            assert res.json()["code"] == code, headers
            assert res.headers["www-authenticate"] == "Bearer"


async def test_locked_account_is_refused_even_with_a_valid_token(api, db_session):
    user = await make_user(db_session)
    body = await login(api)
    await db_session.execute(update(User).where(User.id == user.id).values(is_active=False))
    await db_session.commit()

    res = await api.get("/api/users/me", headers=bearer(body["access_token"]))
    assert res.status_code == 403 and res.json()["code"] == "account_locked"
    res = await api.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert res.status_code == 403 and res.json()["code"] == "account_locked"


# ── refresh ──────────────────────────────────────────────────────────────


async def test_refresh_gives_a_new_access_token(api, db_session):
    await make_user(db_session)
    body = await login(api)
    before = (await db_session.scalar(select(RefreshToken))).expires_at

    res = await api.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert res.status_code == 200
    assert set(res.json()) == {"access_token", "token_type"}  # the client keeps its refresh token
    me = await api.get("/api/users/me", headers=bearer(res.json()["access_token"]))
    assert me.status_code == 200

    # the same refresh token keeps working (several tabs share it), and its expiry slides forward
    again = await api.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert again.status_code == 200
    db_session.expire_all()
    assert (await db_session.scalar(select(RefreshToken))).expires_at >= before


async def test_refresh_rejects_unknown_expired_or_revoked_tokens(api, db_session):
    await make_user(db_session)
    res = await api.post("/api/auth/refresh", json={"refresh_token": "nope"})
    assert res.status_code == 401 and res.json()["code"] == "invalid_refresh_token"

    expired = await login(api)
    await db_session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(expired["refresh_token"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    res = await api.post("/api/auth/refresh", json={"refresh_token": expired["refresh_token"]})
    assert res.status_code == 401 and res.json()["code"] == "invalid_refresh_token"


# ── logout ───────────────────────────────────────────────────────────────


async def test_logout_signs_out_this_device_only(api, db_session):
    await make_user(db_session)
    laptop = await login(api)
    phone = await login(api, ua=IPHONE_UA)

    res = await api.post("/api/auth/logout", json={"refresh_token": laptop["refresh_token"]}, headers=bearer(laptop["access_token"]))
    assert res.status_code == 204 and res.content == b""

    res = await api.get("/api/users/me", headers=bearer(laptop["access_token"]))
    assert res.status_code == 401 and res.json()["code"] == "session_revoked"
    res = await api.post("/api/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    assert res.status_code == 401
    # the phone is still signed in
    assert (await api.get("/api/users/me", headers=bearer(phone["access_token"]))).status_code == 200


async def test_logout_without_body_revokes_the_current_session(api, db_session):
    await make_user(db_session)
    body = await login(api)
    assert (await api.post("/api/auth/logout", headers=bearer(body["access_token"]))).status_code == 204
    assert (await api.get("/api/users/me", headers=bearer(body["access_token"]))).status_code == 401


async def test_logout_cannot_revoke_another_users_session(api, db_session):
    await make_user(db_session, "bemeolac")
    await make_user(db_session, "khach")
    mine = await login(api, "bemeolac")
    theirs = await login(api, "khach")
    await api.post("/api/auth/logout", json={"refresh_token": theirs["refresh_token"]}, headers=bearer(mine["access_token"]))
    assert (await api.get("/api/users/me", headers=bearer(theirs["access_token"]))).status_code == 200


async def test_logout_needs_auth(api):
    res = await api.post("/api/auth/logout", json={"refresh_token": "x"})
    assert res.status_code == 401 and res.json()["code"] == "not_authenticated"


# ── forgot / reset password ──────────────────────────────────────────────


def printed_reset_token(capsys) -> str:
    out = capsys.readouterr().out
    assert "[password-reset]" in out, out
    return out.split("token=")[1].split()[0]


async def test_forgot_password_prints_a_reset_link(api, db_session, capsys):
    await make_user(db_session, email="uyen@example.com")
    res = await api.post("/api/auth/forgot-password", json={"email": "Uyen@Example.com"})
    assert res.status_code == 202
    out = capsys.readouterr().out
    assert "uyen@example.com: https://tu-truyen-nho.example.app/reset-password?token=" in out
    assert await db_session.scalar(select(func.count()).select_from(PasswordResetToken)) == 1


async def test_forgot_password_is_silent_for_unknown_or_locked_accounts(api, db_session, capsys):
    await make_user(db_session, "khoa", is_active=False)
    for email in ("nobody@example.com", "khoa@example.com"):
        res = await api.post("/api/auth/forgot-password", json={"email": email})
        assert res.status_code == 202  # same answer: no account enumeration
    assert "[password-reset]" not in capsys.readouterr().out
    assert await db_session.scalar(select(func.count()).select_from(PasswordResetToken)) == 0


async def test_reset_password_flow(api, db_session, capsys):
    await make_user(db_session)
    old = await login(api)
    await api.post("/api/auth/forgot-password", json={"email": "bemeolac@example.com"})
    token = printed_reset_token(capsys)

    res = await api.post("/api/auth/reset-password", json={"token": token, "new_password": "mat-khau-moi"})
    assert res.status_code == 204

    # every device is signed out, the old password stops working, the new one works
    assert (await api.get("/api/users/me", headers=bearer(old["access_token"]))).status_code == 401
    res = await api.post("/api/auth/login", json={"username": "bemeolac", "password": PASSWORD})
    assert res.status_code == 401
    await login(api, password="mat-khau-moi")

    # single use
    res = await api.post("/api/auth/reset-password", json={"token": token, "new_password": "lan-thu-hai"})
    assert res.status_code == 400 and res.json()["code"] == "invalid_reset_token"


async def test_only_the_newest_reset_link_works(api, db_session, capsys):
    await make_user(db_session)
    await api.post("/api/auth/forgot-password", json={"email": "bemeolac@example.com"})
    first = printed_reset_token(capsys)
    await api.post("/api/auth/forgot-password", json={"email": "bemeolac@example.com"})
    second = printed_reset_token(capsys)

    res = await api.post("/api/auth/reset-password", json={"token": first, "new_password": "mat-khau-moi"})
    assert res.status_code == 400
    res = await api.post("/api/auth/reset-password", json={"token": second, "new_password": "mat-khau-moi"})
    assert res.status_code == 204


async def test_reset_password_rejects_bad_expired_or_weak(api, db_session, capsys):
    await make_user(db_session)
    res = await api.post("/api/auth/reset-password", json={"token": "nope", "new_password": "mat-khau-moi"})
    assert res.status_code == 400 and res.json()["code"] == "invalid_reset_token"

    await api.post("/api/auth/forgot-password", json={"email": "bemeolac@example.com"})
    token = printed_reset_token(capsys)
    res = await api.post("/api/auth/reset-password", json={"token": token, "new_password": "short"})
    assert res.status_code == 422 and res.json()["code"] == "validation_error"

    await db_session.execute(update(PasswordResetToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await db_session.commit()
    res = await api.post("/api/auth/reset-password", json={"token": token, "new_password": "mat-khau-moi"})
    assert res.status_code == 400 and res.json()["code"] == "invalid_reset_token"
