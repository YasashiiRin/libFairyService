"""Shared works: personal overrides instead of editing what others see, and progress past the known total."""

import uuid

from sqlalchemy import select

from app.models import LibraryItem, Work, WorkSource
from tests.helpers import data_url, png_bytes, signed_in
from tests.test_comics import comic_payload, create

LINK = "https://cuutruyen.net/manga/tiem"


async def make_shared(db_session, comic) -> None:
    """Hand-entered works are private; shared ones come from the migration or (Part B) from search results."""
    item = await db_session.get(LibraryItem, uuid.UUID(comic["id"]))
    work = await db_session.get(Work, item.work_id)
    work.owner_user_id = None
    for src in (await db_session.scalars(select(WorkSource).where(WorkSource.work_id == work.id))).all():
        src.private_owner_id = None
    await db_session.commit()


async def two_holders(api, db_session):
    """uyen has a shared work; khach saves the same page link → one work, two library items."""
    uyen = await signed_in(api, db_session)
    khach = await signed_in(api, db_session, "khach")
    mine = await create(api, uyen, total_chapters=120)
    await make_shared(db_session, mine)
    res = await api.post("/api/comics", json=comic_payload(title="Tên khách đặt", total_chapters=100), headers=khach)
    assert res.status_code == 201
    theirs = res.json()
    work_id = await db_session.scalar(select(LibraryItem.work_id).where(LibraryItem.id == uuid.UUID(mine["id"])))
    assert work_id == await db_session.scalar(select(LibraryItem.work_id).where(LibraryItem.id == uuid.UUID(theirs["id"])))
    return uyen, khach, mine, theirs, work_id


async def get(api, headers, comic):
    res = await api.get(f"/api/comics/{comic['id']}", headers=headers)
    assert res.status_code == 200
    return res.json()


async def test_joining_a_saved_work(api, db_session):
    uyen, khach, mine, theirs, work_id = await two_holders(api, db_session)
    assert theirs["title"] == mine["title"]  # the shared title; theirs is kept as an alternative title
    work = await db_session.get(Work, work_id)
    await db_session.refresh(work)
    assert "Tên khách đặt" in work.alt_titles
    assert theirs["sources"][0]["id"] == mine["sources"][0]["id"]  # one shared source row
    assert theirs["total_chapters"] == 120  # the shared count (their 100 is lower, so nothing to keep)


async def test_a_private_work_is_never_joined(api, db_session):
    uyen = await signed_in(api, db_session)
    khach = await signed_in(api, db_session, "khach")
    mine = await create(api, uyen)  # entered by hand: private
    theirs = await create(api, khach)  # same link, but they get their own private work
    item_a = await db_session.get(LibraryItem, uuid.UUID(mine["id"]))
    item_b = await db_session.get(LibraryItem, uuid.UUID(theirs["id"]))
    assert item_a.work_id != item_b.work_id


async def test_shared_edits_become_personal_overrides(api, db_session):
    uyen, khach, mine, theirs, work_id = await two_holders(api, db_session)
    res = await api.patch(
        f"/api/comics/{theirs['id']}",
        json={"title": "Tiệm Của Khách", "author": "Ai Đó", "description": "Mô tả riêng", "total_chapters": 130},
        headers=khach,
    )
    assert res.status_code == 200  # never 409
    body = res.json()
    assert (body["title"], body["author"], body["description"], body["total_chapters"]) == ("Tiệm Của Khách", "Ai Đó", "Mô tả riêng", 130)

    other = await get(api, uyen, mine)  # the other holder sees the shared work, unchanged
    assert (other["title"], other["author"], other["description"], other["total_chapters"]) == (
        mine["title"], mine["author"], mine["description"], 120,
    )
    db_session.expire_all()
    work = await db_session.get(Work, work_id)
    assert (work.title, work.latest_chapter) == (mine["title"], 120)

    # search and sort see each user's own title
    found = (await api.get("/api/comics", params={"q": "tiem cua khach"}, headers=khach)).json()
    assert [c["id"] for c in found["items"]] == [theirs["id"]]
    assert (await api.get("/api/comics", params={"q": "tiem cua khach"}, headers=uyen)).json()["total"] == 0
    assert (await api.get("/api/comics/lookup", params={"title": "tiệm của khách"}, headers=khach)).json()["comic"]["id"] == theirs["id"]

    # setting the shared value again removes the override
    body = (await api.patch(f"/api/comics/{theirs['id']}", json={"title": mine["title"]}, headers=khach)).json()
    assert body["title"] == mine["title"]
    item = await db_session.get(LibraryItem, uuid.UUID(theirs["id"]))
    await db_session.refresh(item)
    assert item.custom_title is None


async def test_sole_holder_edits_the_work(api, db_session):
    uyen = await signed_in(api, db_session)
    mine = await create(api, uyen)
    await api.patch(f"/api/comics/{mine['id']}", json={"title": "Tên Mới"}, headers=uyen)
    item = await db_session.scalar(select(LibraryItem).where(LibraryItem.id == uuid.UUID(mine["id"])))
    work = await db_session.get(Work, item.work_id)
    await db_session.refresh(work)
    assert work.title == "Tên Mới" and item.custom_title is None


async def test_personal_cover_on_a_shared_work(api, db_session):
    uyen, khach, mine, theirs, _ = await two_holders(api, db_session)
    body = (await api.patch(f"/api/comics/{theirs['id']}", json={"cover_url": data_url(png_bytes(600, 900))}, headers=khach)).json()
    assert body["cover_url"].startswith(f"/api/comics/{theirs['id']}/cover?v=")
    served = await api.get(body["cover_url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    assert (await get(api, uyen, mine))["cover_url"] == mine["cover_url"]  # the shared cover link, unchanged

    upload = await api.put(f"/api/comics/{theirs['id']}/cover", files={"file": ("c.png", png_bytes(300, 450), "image/png")}, headers=khach)
    assert upload.status_code == 200 and upload.json()["cover_url"] != body["cover_url"]
    assert (await get(api, uyen, mine))["cover_url"] == mine["cover_url"]

    back = (await api.patch(f"/api/comics/{theirs['id']}", json={"cover_url": mine["cover_url"]}, headers=khach)).json()
    assert back["cover_url"] == mine["cover_url"]  # the shared cover again


async def test_personal_source_changes_on_a_shared_work(api, db_session):
    uyen, khach, mine, theirs, work_id = await two_holders(api, db_session)
    shared = theirs["sources"][0]

    # rename the site for me, and add a second link (added to the shared work)
    kakao = {"id": "src_new", "site_name": "Kakao", "url": "https://page.kakao.com/tiem", "favicon_url": ""}
    body = (await api.patch(
        f"/api/comics/{theirs['id']}",
        json={"sources": [shared | {"site_name": "Cuu Truyện"}, kakao], "primary_source_id": "src_new"},
        headers=khach,
    )).json()
    assert [s["site_name"] for s in body["sources"]] == ["Cuu Truyện", "Kakao"]
    kakao_id = body["sources"][1]["id"]
    assert body["primary_source_id"] == kakao_id

    theirs_view = await get(api, uyen, mine)
    assert [s["site_name"] for s in theirs_view["sources"]] == ["Cuutruyen", "Kakao"]  # the rename is personal, the new link shared
    assert theirs_view["primary_source_id"] == shared["id"]  # primary is per user

    # remove the original link: hidden for me only
    body = (await api.delete(f"/api/comics/{theirs['id']}/sources/{shared['id']}", headers=khach)).json()
    assert [s["id"] for s in body["sources"]] == [kakao_id]
    assert len((await get(api, uyen, mine))["sources"]) == 2
    assert await db_session.scalar(select(WorkSource).where(WorkSource.id == uuid.UUID(shared["id"]))) is not None
    res = await api.patch(f"/api/comics/{theirs['id']}/sources/{shared['id']}", json={"site_name": "x"}, headers=khach)
    assert res.status_code == 404  # hidden sources are gone for this user

    # editing a link on a shared work: the new link is added, the old one hidden for me
    body = (await api.patch(
        f"/api/comics/{theirs['id']}/sources/{kakao_id}", json={"url": "https://page.kakao.com/tiem-v2"}, headers=khach
    )).json()
    assert [s["url"] for s in body["sources"]] == ["https://page.kakao.com/tiem-v2"]
    assert body["primary_source_id"] == body["sources"][0]["id"]
    assert {s["url"] for s in (await get(api, uyen, mine))["sources"]} == {LINK, "https://page.kakao.com/tiem", "https://page.kakao.com/tiem-v2"}

    # filters see only what each user sees
    assert (await api.get("/api/comics", params={"source": "Cuutruyen"}, headers=khach)).json()["total"] == 0
    assert (await api.get("/api/comics", params={"source": "Cuutruyen"}, headers=uyen)).json()["total"] == 1


async def test_a_later_leaver_makes_the_work_editable_again(api, db_session):
    uyen, khach, mine, theirs, work_id = await two_holders(api, db_session)
    await api.delete(f"/api/comics/{theirs['id']}", headers=khach)
    await api.patch(f"/api/comics/{mine['id']}", json={"title": "Chỉ Còn Mình Tôi"}, headers=uyen)
    db_session.expire_all()
    assert (await db_session.get(Work, work_id)).title == "Chỉ Còn Mình Tôi"


# ── progress past the known total ────────────────────────────────────────


async def test_progress_past_the_latest_chapter_is_kept_as_a_hint(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, current_chapter=10, total_chapters=20)
    body = (await api.put(f"/api/comics/{comic['id']}/progress", json={"current_chapter": 25}, headers=headers)).json()
    assert (body["current_chapter"], body["total_chapters"], body["status"]) == (25, 20, "reading")

    item = await db_session.get(LibraryItem, uuid.UUID(comic["id"]))
    work = await db_session.get(Work, item.work_id)
    await db_session.refresh(work)
    assert (work.latest_chapter, work.latest_chapter_hint) == (20, 25)  # the daily check refreshes works like this

    # the hint only grows
    await api.put(f"/api/comics/{comic['id']}/progress", json={"current_chapter": 22}, headers=headers)
    await db_session.refresh(work)
    assert work.latest_chapter_hint == 25
    # progress bars stay within 0–100 %
    assert (await api.get("/api/comics", params={"progress_min": 100}, headers=headers)).json()["total"] == 1


async def test_create_with_progress_past_the_total(api, db_session):
    headers = await signed_in(api, db_session)
    body = await create(api, headers, current_chapter=35, total_chapters=30)
    assert (body["current_chapter"], body["total_chapters"]) == (35, 30)
    item = await db_session.get(LibraryItem, uuid.UUID(body["id"]))
    assert (await db_session.get(Work, item.work_id)).latest_chapter_hint == 35


async def test_ongoing_work_is_not_finished_at_its_latest_chapter(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, current_chapter=10, total_chapters=20)
    item = await db_session.get(LibraryItem, uuid.UUID(comic["id"]))
    work = await db_session.get(Work, item.work_id)
    work.publication_status = "ongoing"
    await db_session.commit()
    body = (await api.put(f"/api/comics/{comic['id']}/progress", json={"current_chapter": 20}, headers=headers)).json()
    assert body["status"] == "reading"  # caught up, not finished: more chapters are coming
