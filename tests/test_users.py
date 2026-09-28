"""/api/users/me: profile, settings, password, sessions (api-contract.md#users)."""

from tests.helpers import IPHONE_UA, PASSWORD, bearer, login, make_user


async def test_get_me(api, db_session):
    await make_user(db_session)
    body = await login(api)
    res = await api.get("/api/users/me", headers=bearer(body["access_token"]))
    assert res.status_code == 200
    me = res.json()
    assert me["username"] == "bemeolac" and me["display_name"] == "Uyên"
    assert me["avatar_url"] == "" and me["last_backup_at"] is None
    assert me["settings"] == {
        "theme": "day",
        "font_size": 16,
        "sparkle_enabled": True,
        "notify_new_chapter": True,
        "notify_broken_link": True,
        "daily_reminder_enabled": False,
        "daily_reminder_time": "20:00",
    }
    assert "password_hash" not in me and "is_active" not in me


async def test_update_profile(api, db_session):
    await make_user(db_session)
    token = (await login(api))["access_token"]
    res = await api.patch("/api/users/me", json={"display_name": "  Uyên Nhỏ  ", "bio": "Thích truyện chữa lành 🌿"}, headers=bearer(token))
    assert res.status_code == 200
    assert res.json()["display_name"] == "Uyên Nhỏ"
    assert res.json()["bio"] == "Thích truyện chữa lành 🌿"

    # only the fields sent change
    res = await api.patch("/api/users/me", json={"bio": ""}, headers=bearer(token))
    assert res.json()["display_name"] == "Uyên Nhỏ" and res.json()["bio"] == ""
    assert (await api.get("/api/users/me", headers=bearer(token))).json()["display_name"] == "Uyên Nhỏ"


async def test_update_profile_validation(api, db_session):
    await make_user(db_session)
    token = (await login(api))["access_token"]
    for body in ({"display_name": "   "}, {"display_name": "x" * 41}, {"bio": "x" * 161}, {"username": "hacker"}):
        res = await api.patch("/api/users/me", json=body, headers=bearer(token))
        assert res.status_code == 422, body
        assert res.json()["code"] == "validation_error"


async def test_update_settings_merges(api, db_session):
    await make_user(db_session)
    token = (await login(api))["access_token"]
    res = await api.patch("/api/users/me/settings", json={"theme": "night", "font_size": 18}, headers=bearer(token))
    assert res.status_code == 200
    res = await api.patch("/api/users/me/settings", json={"daily_reminder_time": "07:45"}, headers=bearer(token))
    settings = res.json()["settings"]
    assert settings["theme"] == "night" and settings["font_size"] == 18 and settings["daily_reminder_time"] == "07:45"
    assert settings["sparkle_enabled"] is True  # untouched
    assert (await api.get("/api/users/me", headers=bearer(token))).json()["settings"] == settings


async def test_update_settings_validation(api, db_session):
    await make_user(db_session)
    token = (await login(api))["access_token"]
    for body in (
        {"theme": "dark"},
        {"font_size": 12},
        {"font_size": 23},
        {"daily_reminder_time": "25:00"},
        {"unknown_setting": True},
    ):
        res = await api.patch("/api/users/me/settings", json=body, headers=bearer(token))
        assert res.status_code == 422, body
        assert res.json()["code"] == "validation_error"


async def test_change_password(api, db_session):
    await make_user(db_session)
    here = await login(api)
    other = await login(api, ua=IPHONE_UA)

    res = await api.post(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "mat-khau-moi"},
        headers=bearer(here["access_token"]),
    )
    assert res.status_code == 204

    # this device stays signed in, other devices are signed out
    assert (await api.get("/api/users/me", headers=bearer(here["access_token"]))).status_code == 200
    assert (await api.get("/api/users/me", headers=bearer(other["access_token"]))).status_code == 401
    assert (await api.post("/api/auth/login", json={"username": "bemeolac", "password": PASSWORD})).status_code == 401
    await login(api, password="mat-khau-moi")


async def test_change_password_errors(api, db_session):
    await make_user(db_session)
    token = (await login(api))["access_token"]
    cases = [
        ({"current_password": "wrong-password", "new_password": "mat-khau-moi"}, 400, "wrong_password"),
        ({"current_password": PASSWORD, "new_password": "short"}, 422, "validation_error"),
        ({"current_password": PASSWORD, "new_password": PASSWORD}, 422, "validation_error"),
    ]
    for body, status, code in cases:
        res = await api.post("/api/users/me/password", json=body, headers=bearer(token))
        assert res.status_code == status, body
        assert res.json()["code"] == code, body
    assert (await api.post("/api/auth/login", json={"username": "bemeolac", "password": PASSWORD})).status_code == 200


async def test_sessions_list_and_logout_other_devices(api, db_session):
    await make_user(db_session)
    await make_user(db_session, "khach")
    first = await login(api, ua=IPHONE_UA)
    await login(api)
    here = await login(api)
    stranger = await login(api, "khach")

    sessions = (await api.get("/api/users/me/sessions", headers=bearer(here["access_token"]))).json()
    assert len(sessions) == 3  # not the other user's session
    assert sessions[0]["is_current"] is True
    assert [s["is_current"] for s in sessions[1:]] == [False, False]

    res = await api.delete("/api/users/me/sessions", headers=bearer(here["access_token"]))
    assert res.status_code == 200 and res.json() == {"revoked": 2}

    sessions = (await api.get("/api/users/me/sessions", headers=bearer(here["access_token"]))).json()
    assert [s["is_current"] for s in sessions] == [True]
    assert (await api.get("/api/users/me", headers=bearer(first["access_token"]))).status_code == 401
    assert (await api.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await api.get("/api/users/me", headers=bearer(stranger["access_token"]))).status_code == 200


async def test_users_me_routes_need_auth(api):
    for method, path in (
        ("GET", "/api/users/me"),
        ("PATCH", "/api/users/me"),
        ("PATCH", "/api/users/me/settings"),
        ("POST", "/api/users/me/password"),
        ("GET", "/api/users/me/sessions"),
        ("DELETE", "/api/users/me/sessions"),
    ):
        res = await api.request(method, path, json={})
        assert res.status_code == 401, (method, path)
        assert res.json()["code"] == "not_authenticated"
