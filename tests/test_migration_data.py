"""The data conversion in migration 982606565e2e (comics → works + library items) and its downgrade,
on a throwaway database with the tricky cases the real data doesn't have."""

import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone

import asyncpg
import pytest

from tests.conftest import TEST_DATABASE_URL

BEFORE, AFTER = "921fe4ffa68e", "982606565e2e"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _server_url(db: str) -> str:
    base, _, _ = TEST_DATABASE_URL.rpartition("/")
    return f"{base}/{db}"


@pytest.fixture
async def migration_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    name = f"migration_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = _server_url(name)
    try:
        yield url
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


def migrate(url: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL_UNPOOLED": url}
    result = subprocess.run([sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


U1, U2 = uuid.uuid4(), uuid.uuid4()
S1, S2 = uuid.uuid4(), uuid.uuid4()
A1, A2, B1, C1, D1 = (uuid.uuid4() for _ in range(5))
SRC = {k: uuid.uuid4() for k in ("a1_page", "a1_bare", "a2_page", "b1_page", "d1_bare")}
TAG_U1, TAG_U2 = uuid.uuid4(), uuid.uuid4()
D = date(2026, 9, 1)


async def insert_old_data(conn: asyncpg.Connection) -> None:
    for uid, name in ((U1, "mot"), (U2, "hai")):
        await conn.execute(
            "INSERT INTO users (id, email, username, password_hash, display_name, settings) VALUES ($1,$2,$3,'x','X','{}')",
            uid, f"{name}@example.com", name,
        )
    for sid, uid in ((S1, U1), (S2, U1)):
        await conn.execute("INSERT INTO shelves (id, user_id, name) VALUES ($1,$2,'Kệ')", sid, uid)

    async def comic(cid, uid, title, current, total, note="", created=T0, primary=None):
        await conn.execute(
            "INSERT INTO comics (id, user_id, title, author, current_chapter, total_chapters, note, status, created_at, updated_at)"
            " VALUES ($1,$2,$3,'Tác giả',$4,$5,$6,'reading',$7,$7)",
            cid, uid, title, current, total, note, created,
        )

    async def source(sid, cid, url, position=0, latest=0):
        await conn.execute(
            "INSERT INTO sources (id, comic_id, position, site_name, url, latest_chapter) VALUES ($1,$2,$3,'Site',$4,$5)",
            sid, cid, position, url, latest,
        )

    await comic(A1, U1, "Truyện A", 10, 50, note="ghi chú 1", created=T0)
    await comic(A2, U1, "Truyện A (bản 2)", 20, 60, note="ghi chú 2", created=T0.replace(month=2))  # same page, same user
    await comic(B1, U2, "Truyện A theo cách gọi khác", 5, 50, created=T0.replace(month=3))  # same page, other user
    await comic(C1, U1, "Không có link", 1, 10)
    await comic(D1, U1, "Chỉ có trang chủ", 2, 10)
    await source(SRC["a1_page"], A1, "https://site.com/manga/x/", latest=48)
    await source(SRC["a1_bare"], A1, "https://cuutruyen.net", position=1)
    await source(SRC["a2_page"], A2, "https://site.com/manga/x#chapter-3", latest=55)
    await source(SRC["b1_page"], B1, "https://SITE.com/manga/x")
    await source(SRC["d1_bare"], D1, "https://cuutruyen.net")  # bare site: must not merge with A
    await conn.execute("UPDATE comics SET primary_source_id = $1 WHERE id = $2", SRC["a2_page"], A2)
    await conn.execute("UPDATE comics SET primary_source_id = $1 WHERE id = $2", SRC["b1_page"], B1)

    await conn.execute("INSERT INTO tags (id, user_id, name) VALUES ($1,$2,'Chữa lành'), ($3,$4,'Hài')", TAG_U1, U1, TAG_U2, U2)
    await conn.execute("INSERT INTO comic_tags (comic_id, tag_id, position) VALUES ($1,$2,0), ($3,$2,0), ($4,$5,0)", A1, TAG_U1, A2, B1, TAG_U2)
    await conn.execute("INSERT INTO shelf_comics (shelf_id, comic_id) VALUES ($1,$2), ($3,$4)", S1, A1, S2, A2)
    for cid, chapters in ((A1, 3), (A2, 2), (B1, 4)):
        await conn.execute(
            "INSERT INTO reading_logs (id, user_id, comic_id, day, chapters) SELECT $1, user_id, id, $2, $3 FROM comics WHERE id = $4",
            uuid.uuid4(), D, chapters, cid,
        )
    await conn.execute(
        "INSERT INTO notifications (id, user_id, comic_id, type, message) VALUES ($1,$2,$3,'new_chapter','Chương mới')",
        uuid.uuid4(), U1, A2,
    )


async def test_upgrade_converts_and_downgrade_rebuilds(migration_db):
    migrate(migration_db, "upgrade", BEFORE)
    conn = await asyncpg.connect(migration_db)
    try:
        await insert_old_data(conn)
        await conn.close()
        migrate(migration_db, "upgrade", AFTER)
        conn = await asyncpg.connect(migration_db)

        works = {r["title"]: r for r in await conn.fetch("SELECT * FROM works")}
        assert set(works) == {"Truyện A", "Không có link", "Chỉ có trang chủ"}  # A1, A2 and B1 share a page link
        a = works["Truyện A"]
        assert a["owner_user_id"] is None  # a real page link: shared
        assert set(a["alt_titles"]) == {"Truyện A (bản 2)", "Truyện A theo cách gọi khác"}
        assert a["latest_chapter"] == 60
        assert works["Không có link"]["owner_user_id"] == U1  # entered by hand: private
        assert works["Chỉ có trang chủ"]["owner_user_id"] == U1  # only a bare site: private, and not merged into A

        sources = await conn.fetch("SELECT * FROM work_sources WHERE work_id = $1 ORDER BY position", a["id"])
        assert [s["url"] for s in sources] == ["https://site.com/manga/x", "https://cuutruyen.net"]  # normalized, deduplicated
        assert sources[0]["id"] == SRC["a1_page"] and sources[0]["latest_chapter"] == 55  # oldest id kept, highest chapter

        items = {r["id"]: r for r in await conn.fetch("SELECT * FROM library_items")}
        assert set(items) == {A1, B1, C1, D1}  # A2 merged into U1's item for the same work (ids = comic ids)
        mine = items[A1]
        assert (mine["user_id"], mine["work_id"]) == (U1, a["id"])
        assert mine["current_chapter"] == 20  # the copy read furthest wins
        assert mine["note"] == "ghi chú 2\n\nghi chú 1"
        assert mine["primary_source_id"] == SRC["a1_page"]  # A2's primary pointed at the merged page link
        assert items[B1]["primary_source_id"] == SRC["a1_page"]

        assert await conn.fetchval("SELECT count(*) FROM library_item_tags WHERE library_item_id = $1", A1) == 1
        assert {r["shelf_id"] for r in await conn.fetch("SELECT shelf_id FROM shelf_items WHERE library_item_id = $1", A1)} == {S1, S2}
        logs = {r["library_item_id"]: r["chapters"] for r in await conn.fetch("SELECT * FROM reading_logs")}
        assert logs == {A1: 5, B1: 4}  # A1 3 + A2 2 on the same day
        assert await conn.fetchval("SELECT library_item_id FROM notifications") == A1
        await conn.close()

        migrate(migration_db, "downgrade", BEFORE)
        conn = await asyncpg.connect(migration_db)
        comics = {r["id"]: r for r in await conn.fetch("SELECT * FROM comics")}
        assert set(comics) == {A1, B1, C1, D1}  # the merged duplicate A2 doesn't come back
        assert comics[A1]["current_chapter"] == 20 and comics[A1]["total_chapters"] == 60
        assert comics[B1]["title"] == "Truyện A"
        a1_sources = await conn.fetch("SELECT id, url FROM sources WHERE comic_id = $1 ORDER BY position", A1)
        b1_sources = await conn.fetch("SELECT id, url FROM sources WHERE comic_id = $1 ORDER BY position", B1)
        assert [s["url"] for s in a1_sources] == [s["url"] for s in b1_sources]  # each holder gets its own copy
        assert a1_sources[0]["id"] == SRC["a1_page"] and b1_sources[0]["id"] != SRC["a1_page"]
        assert comics[B1]["primary_source_id"] == b1_sources[0]["id"]
        assert {r["comic_id"]: r["chapters"] for r in await conn.fetch("SELECT * FROM reading_logs")} == {A1: 5, B1: 4}
        assert await conn.fetchval("SELECT comic_id FROM notifications") == A1
        assert await conn.fetchval("SELECT count(*) FROM shelf_comics WHERE comic_id = $1", A1) == 2
    finally:
        await conn.close()
