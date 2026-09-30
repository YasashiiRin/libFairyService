"""GET /api/home ("Khu vườn hôm nay"), POST /api/home/dismiss, PUT /api/users/me/starter-tastes and the
daily cron's feed and chapter work — with fake providers (tests/fake_providers.py, no network)."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import FeedBuild, FeedDismissal, FeedItem, Notification, User
from app.services import comics
from tests.fake_providers import TIEM_MD
from tests.helpers import signed_in
from tests.test_comics import create
from tests.test_query_counts import statements

SECTION_KEYS = ["for_you", "new_releases", "trending"]


async def home(api, headers) -> dict:
    res = await api.get("/api/home", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def section(body, key) -> dict:
    return next(s for s in body["sections"] if s["key"] == key)


def ids(body, key) -> list[str]:
    return [i["external_id"] for i in section(body, key)["items"]]


def all_items(body) -> list[dict]:
    return [i for s in body["sections"] for i in s["items"]]


async def saved_library(api, headers) -> dict:
    """Time Grocery from MangaDex (AniList id 101 → recommendations), 5 hearts; a dropped action comic."""
    res = await api.post("/api/library", json={"provider": "mangadex", "external_id": TIEM_MD, "current_chapter": 12}, headers=headers)
    assert res.status_code == 201, res.text
    tiem = res.json()
    assert (await api.patch(f"/api/comics/{tiem['id']}", json={"rating": 5}, headers=headers)).status_code == 200
    await create(api, headers, title="Kiếm Khách Bóng Đêm", author="Ai Đó", tags=["Hành động"], status="dropped",
                 sources=[{"site_name": "S", "url": "https://s.example/kiem-khach", "favicon_url": ""}])
    return tiem


@pytest.fixture
def quick_feed():
    settings = get_settings()
    old = settings.feed_build_seconds
    settings.feed_build_seconds = 3.0
    yield settings
    settings.feed_build_seconds = old


async def test_home_for_her_taste(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    tiem = await saved_library(api, headers)
    body = await home(api, headers)

    assert [s["key"] for s in body["sections"]] == SECTION_KEYS
    assert [s["title"] for s in body["sections"]] == ["Dành cho nàng", "Mới ra mắt hợp gu", "Đang được yêu thích"]
    assert all(set(s) == {"key", "title", "items"} for s in body["sections"])  # suggestions only, never her own comics
    assert body["feed_status"] == "ready" and body["personalization_enabled"] is True
    assert body["needs_starter_tastes"] is True  # 2 comics, no starter tastes yet
    assert [o["value"] for o in body["starter_options"]][:3] == ["Chữa lành", "Cổ tích", "Lãng mạn"]
    assert tiem["id"] not in str(body)

    for_you = {i["external_id"]: i for i in section(body, "for_you")["items"]}
    # an AniList recommendation for Time Grocery, which she rated 5 hearts
    assert for_you["505"]["reason"] == "Giống Time Grocery mà nàng chấm 5 tim"
    assert for_you["505"]["attribution"] == [{"provider": "anilist", "name": "AniList", "url": "https://anilist.co/manga/505"}]
    assert for_you["505"]["provider_name"] == "AniList"
    # the same author on MangaDex
    assert for_you["feed-md-0002"]["reason"] == "Cùng tác giả Hatori M. với Time Grocery"
    assert set(for_you["feed-md-0002"]) == {
        "provider", "provider_name", "external_id", "external_ids", "title", "cover_url", "authors", "genres",
        "latest_chapter", "status", "attribution", "reason", "score",
    }

    everything = all_items(body)
    shown = [i["external_id"] for i in everything]
    assert len(shown) == len(set(shown)) <= 40  # each work once, about 40 at most
    assert TIEM_MD not in shown and "101" not in shown  # already in her library
    assert "feed-md-0006" not in shown and "504" not in shown  # action, which she dropped
    assert all(i["reason"] and i["attribution"] for i in everything)
    assert section(body, "new_releases")["items"] and section(body, "trending")["items"]
    assert all("Mới ra mắt" in i["reason"] for i in section(body, "new_releases")["items"])

    # variety: never more than 3 in a row by the same author
    for s in body["sections"]:
        authors = [tuple(i["authors"]) for i in s["items"]]
        assert not any(len(set(authors[k : k + 4])) == 1 for k in range(len(authors) - 3)), s["key"]

    # the second visit reads what was built (no provider is asked again)
    calls = dict(fake_providers.calls)
    again = await home(api, headers)
    assert all_items(again) == everything and dict(fake_providers.calls) == calls


async def test_starter_tastes(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    body = await home(api, headers)
    assert body["needs_starter_tastes"] is True and body["starter_tastes"] == []
    assert section(body, "for_you")["items"] == []  # nothing to go on yet: everyone's favourites only
    assert section(body, "trending")["items"] and all(i["reason"] == "Đang được nhiều người yêu thích" for i in section(body, "trending")["items"])

    res = await api.put("/api/users/me/starter-tastes", json={"starter_tastes": ["Chữa lành", "Chữa lành", "Hài hước"]}, headers=headers)
    assert res.status_code == 200 and res.json()["settings"]["starter_tastes"] == ["Chữa lành", "Hài hước"]

    body = await home(api, headers)  # built again with her moods
    assert body["needs_starter_tastes"] is False and body["starter_tastes"] == ["Chữa lành", "Hài hước"]
    reasons = {i["reason"] for i in section(body, "for_you")["items"]}
    assert "Vì nàng thích Chữa lành" in reasons
    assert all("Hành động" not in r for r in reasons)

    for bad in ({"starter_tastes": ["Kinh dị máu me"]}, {"starter_tastes": ["Chữa lành"] * 11}, {"tastes": []}):
        res = await api.put("/api/users/me/starter-tastes", json=bad, headers=headers)
        assert res.status_code == 422, bad
    assert (await api.put("/api/users/me/starter-tastes", json={"starter_tastes": []}, headers=headers)).json()["settings"]["starter_tastes"] == []


async def test_not_interested(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    body = await home(api, headers)
    assert "feed-md-0002" in ids(body, "for_you")

    for _ in range(2):  # idempotent
        res = await api.post("/api/home/dismiss", json={"provider": "mangadex", "external_id": "feed-md-0002"}, headers=headers)
        assert res.status_code == 204
    assert "feed-md-0002" not in [i["external_id"] for i in all_items(await home(api, headers))]
    dismissal = await db_session.scalar(select(FeedDismissal))
    assert dismissal.title == "Người Gác Vườn Mây" and dismissal.genres == ["Slice of Life", "Fantasy"] and dismissal.authors == ["Hatori M."]

    # a rebuild (e.g. tomorrow) never brings it back
    await api.put("/api/users/me/starter-tastes", json={"starter_tastes": ["Kỳ ảo"]}, headers=headers)
    assert "feed-md-0002" not in [i["external_id"] for i in all_items(await home(api, headers))]

    res = await api.post("/api/home/dismiss", json={"provider": "mangadex"}, headers=headers)
    assert res.status_code == 422
    assert (await api.post("/api/home/dismiss", json={"provider": "x", "external_id": "y"})).status_code == 401


async def test_personalization_off(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    await home(api, headers)
    res = await api.patch("/api/users/me/settings", json={"personalization_enabled": False}, headers=headers)
    assert res.status_code == 200 and res.json()["settings"]["personalization_enabled"] is False

    body = await home(api, headers)
    assert [s["key"] for s in body["sections"]] == ["new_releases", "trending"]
    assert body["feed_status"] == "off" and body["needs_starter_tastes"] is False
    reasons = {i["reason"] for i in all_items(body)}
    assert reasons <= {"Mới ra mắt gần đây", "Đang được nhiều người yêu thích"}
    assert fake_providers.feed_queries and "recommendations" not in fake_providers.feed_queries[-1]
    assert TIEM_MD not in [i["external_id"] for i in all_items(body)]  # still never what she has


async def test_saving_a_suggestion_takes_it_off_the_feed(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    assert "feed-md-0002" in ids(await home(api, headers), "for_you")
    res = await api.post("/api/library", json={"provider": "mangadex", "external_id": "feed-md-0002"}, headers=headers)
    assert res.status_code == 201
    assert "feed-md-0002" not in [i["external_id"] for i in all_items(await home(api, headers))]


async def test_slow_providers_finish_on_the_next_request(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    quick_feed.feed_build_seconds = 1.0
    fake_providers.mode["anilist"] = "slow"  # answers after 5 s
    body = await home(api, headers)
    assert body["feed_status"] == "building"
    assert {i["provider"] for i in all_items(body)} == {"mangadex"}  # what was ready
    build = await db_session.scalar(select(FeedBuild))
    assert build.status == "partial" and build.attempts == 1 and len(build.missing) == 4  # AniList's 4 requests

    fake_providers.mode["anilist"] = "ok"
    mangadex_calls = fake_providers.calls["mangadex"]
    body = await home(api, headers)
    assert body["feed_status"] == "ready" and "505" in ids(body, "for_you")
    assert fake_providers.calls["mangadex"] == mangadex_calls  # MangaDex's answers were cached: only AniList is asked


async def test_a_feed_missing_sources_stops_retrying(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    fake_providers.mode = {"mangadex": "error", "anilist": "error"}
    for status in ("building", "building", "ready"):  # after 3 attempts it is left as it is
        assert (await home(api, headers))["feed_status"] == status
    calls = dict(fake_providers.calls)
    assert (await home(api, headers))["feed_status"] == "ready" and dict(fake_providers.calls) == calls


async def test_home_round_trips_once_built(api, db_session, db_engine, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    await home(api, headers)
    with statements(db_engine) as sent:
        await home(api, headers)
    # BEGIN; auth; today's build; today's feed; library size (no starter tastes yet)
    assert len(sent) == 5, sent
    await api.put("/api/users/me/starter-tastes", json={"starter_tastes": ["Chữa lành"]}, headers=headers)
    await home(api, headers)
    with statements(db_engine) as sent:
        await home(api, headers)
    assert len(sent) == 4, sent  # she told us her taste: the library's size isn't needed


async def test_daily_cron_builds_feeds_and_finds_new_chapters(api, db_session, fake_providers):
    settings = get_settings()
    old = settings.cron_secret
    settings.cron_secret = "s3cret"
    try:
        headers = await signed_in(api, db_session)
        res = await api.post("/api/library", json={"provider": "mangadex", "external_id": TIEM_MD}, headers=headers)
        tiem = res.json()
        assert tiem["total_chapters"] == 120
        await api.put(f"/api/comics/{tiem['id']}/progress", json={"current_chapter": 120}, headers=headers)  # caught up
        user = await db_session.scalar(select(User))
        db_session.add(FeedItem(user_id=user.id, feed_date=comics.today() - timedelta(days=5), section="trending", position=0,
                                provider="mangadex", external_id="old", title="Cũ", score=0, reason="-"))
        await db_session.commit()

        fake_providers.latest[TIEM_MD] = "125"
        res = await api.get("/api/cron/daily", headers={"Authorization": "Bearer s3cret"})
        assert res.status_code == 200, res.text
        report = res.json()
        assert report["feed_items_deleted"] == 1
        assert (report["chapters_checked"], report["chapters_updated"], report["new_chapter_notifications"]) == (1, 1, 1)
        assert report["feeds_built"] == 1

        note = await db_session.scalar(select(Notification))
        assert note.type == "new_chapter" and note.message == "“Time Grocery” đã có chương 125 🌸"
        body = await home(api, headers)  # already built by the cron: no provider asked
        assert body["feed_status"] == "ready"
        saved = (await api.get(f"/api/comics/{tiem['id']}", headers=headers)).json()
        assert saved["has_new_chapter"] and saved["total_chapters"] == 125

        # idempotent: nothing new the second time
        again = (await api.get("/api/cron/daily", headers={"Authorization": "Bearer s3cret"})).json()
        assert (again["chapters_updated"], again["new_chapter_notifications"], again["feeds_built"]) == (0, 0, 0)
        assert await db_session.scalar(select(func.count()).select_from(Notification)) == 1
    finally:
        settings.cron_secret = old


async def test_an_empty_anilist_list_is_asked_again(api, db_session, fake_providers, quick_feed, monkeypatch):
    """AniList sometimes answers an empty list when overloaded: that isn't cached as "nothing there"."""
    from app.models import SearchCache
    from tests import fake_providers as fp

    monkeypatch.setattr(fp, "ANILIST_FEED", {})
    headers = await signed_in(api, db_session)
    assert (await home(api, headers))["feed_status"] == "building"
    assert not await db_session.scalar(select(func.count()).select_from(SearchCache).where(SearchCache.provider == "feed:anilist"))
    monkeypatch.undo()
    body = await home(api, headers)
    assert body["feed_status"] == "ready" and any(i["provider"] == "anilist" for i in all_items(body))


PRIORITY = ["Bách hợp (GL)", "Đam mỹ (BL)"]
GL_BL = {"Girls' Love", "Boys' Love"}


async def test_priority_tastes_lead_every_personal_section(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    await saved_library(api, headers)
    res = await api.patch("/api/users/me/settings", json={"priority_tastes": PRIORITY}, headers=headers)
    assert res.status_code == 200 and res.json()["settings"]["priority_tastes"] == PRIORITY
    body = await home(api, headers)
    assert body["feed_status"] == "ready" and body["priority_tastes"] == PRIORITY
    assert "Bách hợp (GL)" in [o["value"] for o in body["starter_options"]]

    for key in ("for_you", "new_releases", "trending"):
        items = section(body, key)["items"]
        flags = [bool(set(i["genres"]) & GL_BL) for i in items]
        assert flags[0] and flags == sorted(flags, reverse=True), (key, flags)  # led by them, then the others
        assert not all(flags), key  # filled with her other good matches
        for i in items:
            if set(i["genres"]) & GL_BL:
                label = "Bách hợp" if "Girls' Love" in i["genres"] else "Đam mỹ"
                assert label in i["reason"], i
        # variety holds inside the priority works too (five GL works share one author)
        authors = [tuple(i["authors"]) for i in items]
        assert not any(len(set(authors[k : k + 4])) == 1 for k in range(len(authors) - 3)), key
    assert all(i["reason"].endswith("mới ra mắt") for i in section(body, "new_releases")["items"] if set(i["genres"]) & GL_BL)
    assert all(i["reason"].endswith("đang được yêu thích") for i in section(body, "trending")["items"] if set(i["genres"]) & GL_BL)

    shown = {i["external_id"]: i for i in all_items(body)}
    assert shown["601"]["genres"] == ["Romance", "Girls' Love"]  # AniList's Yuri tag
    assert "Girls' Love" not in shown.get("603", {"genres": []})["genres"]  # Yuri is only a minor tag there
    # asked for directly: one AniList request per priority tag (its tag_in means "all of these")
    tagged = [q for q in fake_providers.feed_queries if "tag_in" in q]
    assert len(tagged) == 2 and all("isAdult: false" in q and "minimumTagRank: 60" in q for q in tagged)
    assert any('tag_in: ["Yuri"]' in q for q in tagged) and any('tag_in: ["Boys\' Love"]' in q for q in tagged)
    assert all("isAdult: false" in q for q in fake_providers.feed_queries if "Page(" in q)
    # AniList's genre_in means "all of these": never more than one genre per list
    import re
    assert all(len(json.loads(g)) == 1 for q in fake_providers.feed_queries for g in re.findall(r"genre_in: (\[[^\]]*\])", q))


async def test_priority_tastes_setting(api, db_session, fake_providers, quick_feed):
    headers = await signed_in(api, db_session)
    body = await home(api, headers)
    assert body["priority_tastes"] == [] and body["needs_starter_tastes"] is True
    assert not any(set(i["genres"]) & GL_BL for i in section(body, "for_you")["items"])

    for bad in (["Kinh dị"], PRIORITY * 3):  # an unknown mood; more than 4
        res = await api.patch("/api/users/me/settings", json={"priority_tastes": bad}, headers=headers)
        assert res.status_code == 422, bad
    res = await api.patch("/api/users/me/settings", json={"priority_tastes": ["Đam mỹ (BL)", "Đam mỹ (BL)"]}, headers=headers)
    assert res.json()["settings"]["priority_tastes"] == ["Đam mỹ (BL)"]

    body = await home(api, headers)  # today's feed was built again, led by BL even with an empty library
    assert body["needs_starter_tastes"] is False
    for key in ("for_you", "new_releases", "trending"):
        items = section(body, key)["items"]
        assert items and "Boys' Love" in items[0]["genres"] and "Đam mỹ" in items[0]["reason"], key
    assert not any("Girls' Love" in i["genres"] and "Bách hợp" in i["reason"] for i in all_items(body))

    # personalization off: no personal sections, so no priority either
    await api.patch("/api/users/me/settings", json={"personalization_enabled": False}, headers=headers)
    body = await home(api, headers)
    assert {i["reason"] for i in all_items(body)} <= {"Mới ra mắt gần đây", "Đang được nhiều người yêu thích"}


async def test_the_seed_account_puts_gl_and_bl_first(db_session):
    from app.seed import PRIORITY_TASTES, seed

    await seed(db_session)
    user = await db_session.scalar(select(User))
    assert user.settings["priority_tastes"] == PRIORITY_TASTES == PRIORITY
