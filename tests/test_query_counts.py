"""Database round trips of the main endpoints: each statement is a trip to Neon, so these are kept low.

The limits are the current counts; a change that adds a query should be deliberate.
"""

import time
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.core import db as core_db
from tests.fake_providers import TIEM_MD, VUON_MD
from tests.helpers import login, make_user, signed_in
from tests.test_comics import create, make_shelf


@contextmanager
def statements(engine):
    """Every statement sent, and whether a transaction was opened (BEGIN is a round trip too)."""
    sent: list[str] = []

    def on_execute(conn, cursor, statement, params, context, executemany):
        sent.append(statement)

    def on_begin(conn):
        if conn.get_execution_options().get("isolation_level") != "AUTOCOMMIT":
            sent.append("BEGIN")

    sync = engine.sync_engine
    event.listen(sync, "before_cursor_execute", on_execute)
    event.listen(sync, "begin", on_begin)
    try:
        yield sent
    finally:
        event.remove(sync, "before_cursor_execute", on_execute)
        event.remove(sync, "begin", on_begin)


async def test_login_and_refresh(api, db_session, db_engine):
    await make_user(db_session)
    with statements(db_engine) as sent:
        tokens = await login(api)
    assert len(sent) == 2  # find the user, store the session (no transaction around them)
    with statements(db_engine) as sent:
        res = await api.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 200 and len(sent) == 2  # the session with its user, the update


async def test_reading_the_bookshelf(api, db_session, db_engine):
    headers = await signed_in(api, db_session)
    shelf = await make_shelf(db_session)
    for i in range(4):
        await create(api, headers, title=f"Truyện {i}", tags=["a", f"t{i}"], shelf_ids=[shelf],
                     sources=[{"site_name": "S", "url": f"https://s.example/truyen-{i}", "favicon_url": ""}])

    with statements(db_engine) as sent:
        page = (await api.get("/api/comics", headers=headers)).json()
    assert page["total"] == 4 and len(page["items"]) == 4
    # auth; items with their works and the total; sources; tags; shelves — whatever the number of comics
    assert len(sent) == 5 and "BEGIN" not in sent

    with statements(db_engine) as sent:
        empty = (await api.get("/api/comics", params={"page": 9}, headers=headers)).json()
    assert (empty["total"], empty["items"]) == (4, [])  # past the end: the total needs its own query
    assert len(sent) == 3

    comic_id = page["items"][0]["id"]
    with statements(db_engine) as sent:
        assert (await api.get(f"/api/comics/{comic_id}", headers=headers)).status_code == 200
    assert len(sent) == 5  # auth; item with its work; sources; tags; shelves


async def test_updating_progress(api, db_session, db_engine):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, current_chapter=3, total_chapters=40)
    with statements(db_engine) as sent:
        res = await api.put(f"/api/comics/{comic['id']}/progress", json={"current_chapter": 5}, headers=headers)
    assert res.status_code == 200 and res.json()["current_chapter"] == 5
    # BEGIN; auth; the comic (4 reads); today's reading log; the update — no reload afterwards
    assert len(sent) == 8, sent


async def test_saving_from_search(api, db_session, db_engine, fake_providers):
    headers = await signed_in(api, db_session)
    await api.get("/api/discover/search", params={"q": "witch garden"}, headers=headers)
    with statements(db_engine) as sent:
        res = await api.post("/api/library", json={"provider": "mangadex", "external_id": VUON_MD}, headers=headers)
    assert res.status_code == 201
    # BEGIN; auth; details cache read + write; one lookup for the work (ids and links together);
    # savepoint, work, its sources, release; the item; it leaves the home feed — the saved comic is not read back
    assert len(sent) == 11, sent

    with statements(db_engine) as sent:
        again = await api.post("/api/library", json={"provider": "mangadex", "external_id": VUON_MD}, headers=headers)
    assert again.status_code == 200 and again.json()["id"] == res.json()["id"]
    # BEGIN; auth; cache; the work and its sources; the item, its tags and shelves
    assert len(sent) == 8, sent


async def test_saving_a_work_with_several_links_is_one_lookup(api, db_session, db_engine, fake_providers):
    """TIEM has MangaDex, raw and Webtoon links (and an AniList id): still one query to find its work."""
    headers = await signed_in(api, db_session)
    with statements(db_engine) as sent:
        assert (await api.post("/api/library", json={"provider": "mangadex", "external_id": TIEM_MD}, headers=headers)).status_code == 201
    assert sum(1 for s in sent if s.lstrip().upper().startswith("SELECT WORKS.")) == 1
    assert not [s for s in sent if s.lstrip().upper().startswith("SELECT WORK_SOURCES")]  # a new work: no second look at links


# ── the connection pool ──────────────────────────────────────────────────


@pytest.fixture
async def counted_pings(migrated_database, monkeypatch):
    engine = core_db.create_engine(migrated_database)
    pings: list[int] = []
    real = core_db.ping
    monkeypatch.setattr(core_db, "ping", lambda conn: pings.append(1) or real(conn))
    yield engine, pings
    await engine.dispose()


async def test_only_idle_connections_are_pinged(counted_pings, monkeypatch):
    engine, pings = counted_pings
    for _ in range(3):  # back-to-back requests: no ping before each
        async with engine.connect() as conn:
            await conn.exec_driver_sql("select 1")
    assert pings == []

    now = time.monotonic()
    monkeypatch.setattr(core_db.time, "monotonic", lambda: now + core_db.PING_AFTER_IDLE_SECONDS + 1)
    async with engine.connect() as conn:  # after a quiet moment: pinged first
        await conn.exec_driver_sql("select 1")
    assert pings == [1]


async def test_a_dead_idle_connection_is_replaced(counted_pings, monkeypatch):
    engine, _ = counted_pings
    async with engine.connect() as conn:
        await conn.exec_driver_sql("select 1")
        dead = (await conn.get_raw_connection()).dbapi_connection
    await dead.driver_connection.close()  # e.g. Neon closed it while the compute slept

    now = time.monotonic()
    monkeypatch.setattr(core_db.time, "monotonic", lambda: now + core_db.PING_AFTER_IDLE_SECONDS + 1)
    async with engine.connect() as conn:  # the ping fails: a fresh connection, no error for the request
        assert (await conn.exec_driver_sql("select 1")).scalar() == 1
        assert (await conn.get_raw_connection()).dbapi_connection is not dead


async def test_reads_run_without_a_transaction(api, db_session, db_engine):
    """GET requests don't send BEGIN / ROLLBACK; writes still run in one transaction."""
    headers = await signed_in(api, db_session)
    with statements(db_engine) as sent:
        await api.get("/api/users/me", headers=headers)
        await api.get("/api/comics/summary", headers=headers)
    assert "BEGIN" not in sent
    with statements(db_engine) as sent:
        await create(api, headers)
    assert sent[0] == "BEGIN"
