"""GET /api/discover/search, POST /api/library, GET /api/cron/daily — with fake providers (no network)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.models import LibraryItem, SearchCache, Work, WorkSource
from app.providers import PROVIDERS
from tests.fake_providers import LUKHACH_AL, LUKHACH_MD, TIEM_AL, TIEM_MD, VUON_MD
from tests.helpers import signed_in
from tests.test_comics import create


async def search(api, headers, q, **params):
    res = await api.get("/api/discover/search", params={"q": q, **params}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ── search ───────────────────────────────────────────────────────────────


async def test_search_merges_providers(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    body = await search(api, headers, "time grocery")
    assert (body["query"], body["page"], body["providers"], body["providers_failed"]) == ("time grocery", 1, ["mangadex", "anilist"], [])
    [r] = body["results"]  # MangaDex's links.al = AniList's id: one result
    assert set(r) == {
        "provider", "external_id", "external_ids", "providers", "title", "alt_titles", "description", "cover_url",
        "authors", "genres", "status", "latest_chapter", "links", "in_library", "library_item_id",
    }
    assert (r["provider"], r["external_id"], r["providers"]) == ("mangadex", TIEM_MD, ["mangadex", "anilist"])
    assert r["external_ids"] == {"mangadex": TIEM_MD, "anilist": TIEM_AL, "mal": "201"}
    assert r["genres"] == ["Slice of Life", "Magic", "Supernatural"]  # union
    assert "Tiệm Tạp Hóa Thời Gian" in r["alt_titles"] and "Jikan no Zakkaya" in r["alt_titles"]
    assert r["cover_url"].startswith("https://uploads.mangadex.org/")
    assert [link["site_name"] for link in r["links"]] == ["MangaDex", "Raw", "Webtoon"]
    assert (r["status"], r["latest_chapter"], r["in_library"], r["library_item_id"]) == ("ongoing", 120, False, None)


async def test_search_merges_by_title_and_author(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    [r] = (await search(api, headers, "traveler"))["results"]  # no shared id, same title + author
    assert r["providers"] == ["mangadex", "anilist"]
    assert r["external_ids"] == {"mangadex": LUKHACH_MD, "anilist": LUKHACH_AL}
    assert r["latest_chapter"] == 64  # MangaDex has none; AniList knows it


async def test_search_keeps_different_works_apart(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    results = (await search(api, headers, "er"))["results"]
    assert sorted(r["external_id"] for r in results if r["provider"] == "mangadex") == sorted([TIEM_MD, VUON_MD, LUKHACH_MD])
    assert len(results) == 3


async def test_a_slow_or_failing_provider_is_skipped(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    for mode in ("slow", "error", "429"):
        fake_providers.mode["anilist"] = mode
        body = await search(api, headers, f"witch {mode}")
        assert body["providers_failed"] == ["anilist"], mode
        assert body["results"] == [] or all(r["providers"] == ["mangadex"] for r in body["results"])

    # after a 429 the provider rests: it isn't even asked until Retry-After passes
    calls = fake_providers.calls["anilist"]
    fake_providers.mode["anilist"] = "ok"
    assert (await search(api, headers, "witch again"))["providers_failed"] == ["anilist"]
    assert fake_providers.calls["anilist"] == calls


async def test_everything_failing_still_answers(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    fake_providers.mode = {"mangadex": "error", "anilist": "error"}
    body = await search(api, headers, "time grocery")
    assert body["results"] == [] and body["providers_failed"] == ["mangadex", "anilist"]


async def test_results_are_cached(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    first = await search(api, headers, "Time  Grocery")
    calls = dict(fake_providers.calls)
    again = await search(api, headers, "time grocery")  # same query after normalizing spaces and case
    assert fake_providers.calls == calls and again["results"] == first["results"]
    rows = (await db_session.scalars(select(SearchCache))).all()
    assert {r.provider for r in rows} == {"mangadex", "anilist"}
    assert all(timedelta(hours=5, minutes=59) < r.expires_at - r.created_at <= timedelta(hours=6, minutes=1) for r in rows)

    # a provider that failed isn't cached: it is asked again next time
    fake_providers.mode["anilist"] = "error"
    await search(api, headers, "garden")
    fake_providers.mode["anilist"] = "ok"
    before = fake_providers.calls["anilist"]
    assert (await search(api, headers, "garden"))["providers_failed"] == []
    assert fake_providers.calls["anilist"] == before + 1


async def test_search_validation_and_auth(api, db_session, fake_providers):
    assert (await api.get("/api/discover/search", params={"q": "time"})).status_code == 401
    headers = await signed_in(api, db_session)
    for params in ({"q": "a"}, {"q": "x" * 101}, {"q": "time", "page": 0}, {"q": "time", "page": 11}, {}):
        assert (await api.get("/api/discover/search", params=params, headers=headers)).status_code == 422, params


# ── POST /api/library ────────────────────────────────────────────────────


async def add(api, headers, body, expect=201):
    res = await api.post("/api/library", json=body, headers=headers)
    assert res.status_code == expect, res.text
    return res.json()


async def test_add_from_a_search_result(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    comic = await add(api, headers, {"provider": "mangadex", "external_id": TIEM_MD, "status": "reading", "current_chapter": 12})
    assert (comic["title"], comic["author"], comic["status"], comic["current_chapter"]) == ("Time Grocery", "Hatori M.", "reading", 12)
    assert comic["total_chapters"] == 120  # MangaDex's lastChapter (details also asked the feed only when it is missing)
    assert comic["tags"] == ["Slice of Life", "Magic", "Supernatural"]  # genres, enriched from AniList
    assert [s["site_name"] for s in comic["sources"]] == ["MangaDex", "Raw", "Webtoon"]
    assert comic["primary_source_id"] == comic["sources"][0]["id"]
    assert comic["cover_url"].startswith(f"/api/comics/{comic['id']}/cover?v=")  # fetched once, stored as WebP
    assert (await api.get(comic["cover_url"])).headers["content-type"] == "image/webp"

    work = await db_session.scalar(select(Work))
    assert work.owner_user_id is None  # from a provider: shared
    assert work.external_ids == {"mangadex": TIEM_MD, "anilist": TIEM_AL, "mal": "201"}
    assert work.publication_status == "ongoing"

    # it now shows as saved in searches
    [r] = (await search(api, headers, "time grocery"))["results"]
    assert (r["in_library"], r["library_item_id"]) == (True, comic["id"])


async def test_add_is_idempotent_and_the_work_is_shared(api, db_session, fake_providers):
    uyen = await signed_in(api, db_session)
    khach = await signed_in(api, db_session, "khach")
    mine = await add(api, uyen, {"provider": "mangadex", "external_id": TIEM_MD})
    covers = fake_providers.calls["cover"]
    again = await add(api, uyen, {"provider": "anilist", "external_id": TIEM_AL}, expect=200)  # same work via its AniList id
    assert again["id"] == mine["id"]
    theirs = await add(api, khach, {"url": f"https://mangadex.org/title/{TIEM_MD}/time-grocery"})
    assert theirs["id"] != mine["id"]
    assert fake_providers.calls["cover"] == covers  # the cover is stored once

    assert await db_session.scalar(select(func.count()).select_from(Work)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LibraryItem)) == 2
    [r] = (await search(api, khach, "time grocery"))["results"]
    assert r["library_item_id"] == theirs["id"]  # each user sees their own item


async def test_add_from_a_link(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    comic = await add(api, headers, {"url": f"https://mangadex.org/title/{VUON_MD}"})
    assert comic["title"] == "Secret Witch Garden" and comic["total_chapters"] == 88
    # a link the saved work already has: that work, no provider call
    calls = fake_providers.calls["mangadex"]
    assert (await add(api, headers, {"url": f"https://MANGADEX.org/title/{VUON_MD}/"}, expect=200))["id"] == comic["id"]
    assert fake_providers.calls["mangadex"] == calls

    res = await api.post("/api/library", json={"url": "https://cuutruyen.net/manga/khong-ro"}, headers=headers)
    assert res.status_code == 422 and res.json()["code"] == "link_not_supported"


async def test_add_attaches_to_a_saved_work_with_the_same_link(api, db_session, fake_providers):
    """A migrated/shared work that already has the provider's page link is reused, and gains the ids."""
    uyen = await signed_in(api, db_session)
    khach = await signed_in(api, db_session, "khach")
    old = await create(api, uyen, sources=[{"site_name": "MangaDex", "url": f"https://mangadex.org/title/{VUON_MD}", "favicon_url": ""}])
    item = await db_session.get(LibraryItem, uuid.UUID(old["id"]))
    await db_session.execute(update(Work).where(Work.id == item.work_id).values(owner_user_id=None))
    await db_session.execute(update(WorkSource).where(WorkSource.work_id == item.work_id).values(private_owner_id=None))
    await db_session.commit()

    theirs = await add(api, khach, {"provider": "mangadex", "external_id": VUON_MD})
    work = await db_session.get(Work, item.work_id)
    await db_session.refresh(work)
    assert await db_session.scalar(select(func.count()).select_from(Work)) == 1
    assert work.external_ids["mangadex"] == VUON_MD  # enriched, not replaced
    assert theirs["title"] == old["title"]  # the saved work's title is kept


async def test_add_manually(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    comic = await add(api, headers, {"manual": {"title": "Truyện Tự Nhập", "total_chapters": 10}, "status": "on_hold", "current_chapter": 3})
    assert (comic["title"], comic["status"], comic["current_chapter"]) == ("Truyện Tự Nhập", "on_hold", 3)
    work = await db_session.scalar(select(Work))
    assert work.owner_user_id is not None  # entered by hand: private
    assert sum(fake_providers.calls.values()) == 0


async def test_add_past_the_latest_chapter_notes_a_hint(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    comic = await add(api, headers, {"provider": "mangadex", "external_id": VUON_MD, "current_chapter": 90})
    assert (comic["current_chapter"], comic["total_chapters"], comic["status"]) == (90, 88, "reading")
    assert (await db_session.scalar(select(Work))).latest_chapter_hint == 90


async def test_add_errors(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    cases = [
        ({}, 422, "validation_error"),
        ({"provider": "mangadex"}, 422, "validation_error"),
        ({"provider": "mangadex", "external_id": TIEM_MD, "url": "https://x.example/a"}, 422, "validation_error"),
        ({"provider": "nowhere", "external_id": "1"}, 422, "unknown_provider"),
        ({"provider": "mangadex", "external_id": "00000000-0000-0000-0000-000000000000"}, 404, "work_not_found"),
        ({"provider": "anilist", "external_id": "999"}, 404, "work_not_found"),
    ]
    for body, status, code in cases:
        res = await api.post("/api/library", json=body, headers=headers)
        assert (res.status_code, res.json()["code"]) == (status, code), body

    fake_providers.mode["mangadex"] = "error"
    res = await api.post("/api/library", json={"provider": "mangadex", "external_id": LUKHACH_MD}, headers=headers)
    assert (res.status_code, res.json()["code"]) == (503, "provider_unavailable")
    assert await db_session.scalar(select(func.count()).select_from(Work)) == 0


async def test_enrichment_failure_does_not_block_the_save(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    fake_providers.mode["anilist"] = "error"
    comic = await add(api, headers, {"provider": "mangadex", "external_id": TIEM_MD})
    assert comic["tags"] == ["Slice of Life", "Magic"]  # MangaDex only


async def test_cover_that_cannot_be_fetched_stays_a_link(api, db_session, fake_providers):
    headers = await signed_in(api, db_session)
    fake_providers.covers_ok = False
    comic = await add(api, headers, {"provider": "mangadex", "external_id": VUON_MD})
    assert comic["cover_url"] == f"https://uploads.mangadex.org/covers/{VUON_MD}/cover.jpg.256.jpg"


# ── cron ─────────────────────────────────────────────────────────────────


async def test_daily_cron(api, db_session, fake_providers):
    settings = get_settings()
    old = settings.cron_secret
    try:
        settings.cron_secret = ""
        assert (await api.get("/api/cron/daily")).json()["code"] == "cron_not_configured"
        settings.cron_secret = "s3cret"
        assert (await api.get("/api/cron/daily", headers={"Authorization": "Bearer nope"})).status_code == 401

        headers = await signed_in(api, db_session)
        await search(api, headers, "time grocery")
        comic = await add(api, headers, {"provider": "mangadex", "external_id": VUON_MD})
        await db_session.execute(update(SearchCache).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db_session.commit()
        # an orphan: a work nobody has (e.g. after an account was deleted)
        db_session.add(Work(title="Mồ côi", alt_titles=[], authors=[], genres=[], external_ids={}))
        await db_session.commit()

        res = await api.get("/api/cron/daily", headers={"Authorization": "Bearer s3cret"})
        assert res.status_code == 200
        assert res.json()["orphan_works_deleted"] == 1 and res.json()["search_cache_deleted"] >= 2
        assert await db_session.scalar(select(func.count()).select_from(SearchCache).where(SearchCache.expires_at <= func.now())) == 0
        assert (await api.get(f"/api/comics/{comic['id']}", headers=headers)).status_code == 200  # saved works stay
        # idempotent
        again = (await api.get("/api/cron/daily", headers={"Authorization": "Bearer s3cret"})).json()
        assert again == {"search_cache_deleted": 0, "orphan_works_deleted": 0}
    finally:
        settings.cron_secret = old


def test_both_providers_are_registered():
    assert list(PROVIDERS) == ["mangadex", "anilist"]
