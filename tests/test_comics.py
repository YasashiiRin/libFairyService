"""/api/comics, sources, tags and covers (api-contract.md#comics, #tags)."""

import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models import ReadingLog, Shelf, ShelfItem, Tag, User
from tests.helpers import data_url, png_bytes, signed_in


async def make_shelf(db, username="bemeolac", name="Truyện chữa lành") -> str:
    user = await db.scalar(select(User).where(User.username == username))
    shelf = Shelf(user_id=user.id, name=name, position=1)
    db.add(shelf)
    await db.commit()
    return str(shelf.id)


def comic_payload(**overrides) -> dict:
    """Shaped like the frontend's ComicCreate (AddComicPage)."""
    body = {
        "title": "Tiệm Tạp Hóa Thời Gian",
        "author": "Hatori M.",
        "description": "Một tiệm tạp hóa kỳ lạ.",
        "cover_url": "https://images.example.com/cover.jpg",
        "status": "reading",
        "current_chapter": 10,
        "total_chapters": 120,
        "rating": 4.5,
        "is_favorite": False,
        "note": "",
        "tags": ["Chữa lành", "Huyền bí"],
        "sources": [
            {
                "id": "src_1727500000000",
                "site_name": "Cuutruyen",
                "url": "https://cuutruyen.net/manga/tiem",
                "favicon_url": "🌸",
                "chapter_url": "",
                "latest_chapter": 12,
                "is_alive": True,
                "last_checked_at": "2026-09-28T07:00:00Z",
            }
        ],
        "primary_source_id": "src_1727500000000",
        "shelf_ids": ["all"],
        "has_new_chapter": False,
        "last_read_at": "2026-09-28T07:00:00Z",
    }
    return body | overrides


async def create(api, headers, **overrides) -> dict:
    res = await api.post("/api/comics", json=comic_payload(**overrides), headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ── create / get ─────────────────────────────────────────────────────────


async def test_create_comic_like_the_frontend(api, db_session):
    headers = await signed_in(api, db_session)
    shelf_id = await make_shelf(db_session)
    body = await create(api, headers, shelf_ids=["all", shelf_id, "healing"], tags=[" Chữa  lành ", "huyền bí", "Chữa lành"])

    assert set(body) == {
        "id", "title", "author", "description", "cover_url", "status", "current_chapter", "total_chapters",
        "rating", "is_favorite", "note", "tags", "sources", "primary_source_id", "shelf_ids",
        "has_new_chapter", "last_read_at", "created_at", "updated_at",
    }
    assert body["tags"] == ["Chữa lành", "huyền bí"]  # trimmed, spaces collapsed, duplicates dropped
    assert body["shelf_ids"] == ["all", shelf_id]  # "all" first; unknown non-id "healing" ignored
    [source] = body["sources"]
    assert uuid.UUID(source["id"])  # server id, not the client's src_…
    assert body["primary_source_id"] == source["id"]  # mapped from the client id
    assert source["chapter_url"] == source["url"]  # empty chapter URL defaults to the URL
    assert source["last_checked_at"] is None  # only the server's link check sets it

    got = await api.get(f"/api/comics/{body['id']}", headers=headers)
    assert got.status_code == 200 and got.json() == body


async def test_same_page_link_is_the_same_comic(api, db_session):
    headers = await signed_in(api, db_session)
    first = await create(api, headers)
    res = await api.post("/api/comics", json=comic_payload(title="Tên khác", sources=[
        {"site_name": "Cuutruyen", "url": "https://CUUTRUYEN.net/manga/tiem/", "favicon_url": ""}
    ]), headers=headers)
    assert res.status_code == 200 and res.json()["id"] == first["id"]  # same page (normalized): the existing comic


async def test_bare_site_links_do_not_merge_comics(api, db_session):
    """AddComicPage fills in the site's home page when no link is given; that must not merge comics."""
    headers = await signed_in(api, db_session)
    bare = [{"site_name": "Cuutruyen", "url": "https://cuutruyen.net", "favicon_url": ""}]
    a = await create(api, headers, title="Truyện A", sources=bare)
    b = await create(api, headers, title="Truyện B", sources=bare)
    assert a["id"] != b["id"]
    assert (await api.get("/api/comics", headers=headers)).json()["total"] == 2


async def test_create_validation(api, db_session):
    headers = await signed_in(api, db_session)
    for overrides in (
        {"title": "   "},
        {"current_chapter": -1},
        {"status": "abandoned"},
        {"rating": 6},
        {"tags": ["x" * 61]},
        {"tags": [f"t{i}" for i in range(31)]},
        {"sources": [{"site_name": "X", "url": "javascript:alert(1)"}]},
        {"cover_url": "ftp://example.com/a.png"},
        {"unknown_field": 1},
    ):
        res = await api.post("/api/comics", json=comic_payload(**overrides), headers=headers)
        assert res.status_code == 422, overrides
        assert res.json()["code"] == "validation_error", overrides


async def test_create_rejects_someone_elses_shelf(api, db_session):
    headers = await signed_in(api, db_session)
    await signed_in(api, db_session, "khach")
    their_shelf = await make_shelf(db_session, "khach")
    res = await api.post("/api/comics", json=comic_payload(shelf_ids=[their_shelf]), headers=headers)
    assert res.status_code == 404 and res.json()["code"] == "shelf_not_found"


async def test_comics_are_private(api, db_session):
    mine = await signed_in(api, db_session)
    theirs = await signed_in(api, db_session, "khach")
    comic = await create(api, theirs, title="Truyện của khách")

    for method, path in (
        ("GET", f"/api/comics/{comic['id']}"),
        ("PATCH", f"/api/comics/{comic['id']}"),
        ("DELETE", f"/api/comics/{comic['id']}"),
        ("PUT", f"/api/comics/{comic['id']}/favorite"),
        ("PUT", f"/api/comics/{comic['id']}/progress"),
        ("GET", "/api/comics/not-a-uuid"),
    ):
        body = {"current_chapter": 1} if path.endswith("progress") else {}
        res = await api.request(method, path, json=body, headers=mine)
        assert res.status_code == 404, (method, path)
        assert res.json()["code"] == "comic_not_found"

    listing = (await api.get("/api/comics", headers=mine)).json()
    assert listing["total"] == 0 and listing["items"] == []
    assert (await api.get("/api/comics/summary", headers=mine)).json()["total"] == 0
    assert (await api.get("/api/tags", headers=mine)).json() == []


async def test_comics_need_auth(api):
    for method, path in (("GET", "/api/comics"), ("POST", "/api/comics"), ("GET", "/api/comics/summary"), ("GET", "/api/tags")):
        res = await api.request(method, path, json={})
        assert res.status_code == 401 and res.json()["code"] == "not_authenticated"


# ── list: filters, search, sort, pagination ──────────────────────────────


@pytest.fixture
async def library(api, db_session):
    headers = await signed_in(api, db_session)
    shelf_id = await make_shelf(db_session)
    specs = [
        dict(title="Đời Sống Ở Làng Gió", author="Nami", tags=["Đời thường"], status="reading", current_chapter=30, total_chapters=60, rating=4.0, note="đọc buổi tối", shelf_ids=[shelf_id]),
        dict(title="Ánh Trăng Nhà Kính", author="Lirien", tags=["Kỳ ảo", "Lãng mạn"], status="completed", current_chapter=88, total_chapters=88, rating=5.0, is_favorite=True, has_new_chapter=True),
        dict(title="Bức Thư Mùa Đông", author="Sora", tags=["Tình cảm"], status="plan_to_read", current_chapter=0, total_chapters=40, rating=3.0,
             sources=[{"site_name": "Hako", "url": "https://ln.hako.vn/x", "is_alive": False}], shelf_ids=[shelf_id]),
        dict(title="Anh Đào Tháng Tư", author="Đời Thường Studio", tags=["Học đường"], status="on_hold", current_chapter=10, total_chapters=100, rating=4.5),
    ]
    comics = []
    for n, spec in enumerate(specs):
        # each comic its own page link: one link = one work
        spec.setdefault("sources", [{"site_name": "Cuutruyen", "url": f"https://cuutruyen.net/manga/truyen-{n}", "favicon_url": "🌸"}])
        comics.append(await create(api, headers, **spec))
    return headers, shelf_id, {c["title"]: c for c in comics}


async def titles(api, headers, **params) -> list[str]:
    res = await api.get("/api/comics", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return [c["title"] for c in res.json()["items"]]


async def test_list_filters(api, library):
    headers, shelf_id, _ = library
    t = lambda **p: titles(api, headers, **p)  # noqa: E731
    assert set(await t(status=["reading", "completed"])) == {"Đời Sống Ở Làng Gió", "Ánh Trăng Nhà Kính"}
    assert await t(tag="KỲ ẢO") == ["Ánh Trăng Nhà Kính"]  # tag match ignores case
    assert set(await t(tag=["Tình cảm", "Học đường"])) == {"Bức Thư Mùa Đông", "Anh Đào Tháng Tư"}
    assert await t(source="Hako") == ["Bức Thư Mùa Đông"]
    assert set(await t(shelf=shelf_id)) == {"Đời Sống Ở Làng Gió", "Bức Thư Mùa Đông"}
    assert await t(shelf="healing") == []  # not a real shelf id
    assert len(await t(shelf="all")) == 4  # the virtual shelf = everything
    assert await t(has_new_chapter="true") == ["Ánh Trăng Nhà Kính"]
    assert await t(has_broken_link="true") == ["Bức Thư Mùa Đông"]
    assert await t(is_favorite="true") == ["Ánh Trăng Nhà Kính"]
    assert set(await t(min_rating=4, sort="rating")) == {"Ánh Trăng Nhà Kính", "Anh Đào Tháng Tư", "Đời Sống Ở Làng Gió"}
    assert set(await t(progress_min=40, progress_max=60)) == {"Đời Sống Ở Làng Gió"}


async def test_search_is_case_and_accent_insensitive(api, library):
    headers, _, _ = library
    # "doi song" finds "Đời Sống" (title), and the author/tag "Đời Thường"
    assert set(await titles(api, headers, q="doi")) == {"Đời Sống Ở Làng Gió", "Anh Đào Tháng Tư"}
    assert await titles(api, headers, q="ANH TRANG") == ["Ánh Trăng Nhà Kính"]
    assert await titles(api, headers, q="lang man") == ["Ánh Trăng Nhà Kính"]  # tag
    assert await titles(api, headers, q="buoi toi") == ["Đời Sống Ở Làng Gió"]  # note
    assert await titles(api, headers, q="100%") == []  # LIKE wildcards are literal


async def test_search_relevance_order(api, library):
    headers, _, _ = library
    # title prefix (6) beats author (3): "Đời Sống…" before "Anh Đào…" (author "Đời Thường Studio")
    assert await titles(api, headers, q="doi") == ["Đời Sống Ở Làng Gió", "Anh Đào Tháng Tư"]


async def test_sorting(api, library):
    headers, _, _ = library
    # Vietnamese order: Anh < Ánh < Bức < Đời
    assert await titles(api, headers, sort="title") == ["Anh Đào Tháng Tư", "Ánh Trăng Nhà Kính", "Bức Thư Mùa Đông", "Đời Sống Ở Làng Gió"]
    assert (await titles(api, headers, sort="rating"))[0] == "Ánh Trăng Nhà Kính"
    assert await titles(api, headers, sort="progress") == ["Ánh Trăng Nhà Kính", "Đời Sống Ở Làng Gió", "Anh Đào Tháng Tư", "Bức Thư Mùa Đông"]
    assert (await titles(api, headers))[0] == "Anh Đào Tháng Tư"  # default: most recently updated
    res = await api.get("/api/comics", params={"sort": "sideways"}, headers=headers)
    assert res.status_code == 422


async def test_sort_by_shelf_position(api, db_session, library):
    headers, shelf_id, comics = library
    res = await api.get("/api/comics", params={"sort": "position"}, headers=headers)
    assert res.status_code == 422  # needs exactly one shelf
    first, second = comics["Bức Thư Mùa Đông"]["id"], comics["Đời Sống Ở Làng Gió"]["id"]
    for position, cid in enumerate((first, second)):
        link = await db_session.get(ShelfItem, (uuid.UUID(shelf_id), uuid.UUID(cid)))
        link.position = position
    await db_session.commit()
    assert await titles(api, headers, shelf=shelf_id, sort="position") == ["Bức Thư Mùa Đông", "Đời Sống Ở Làng Gió"]


async def test_pagination(api, library):
    headers, _, _ = library
    res = (await api.get("/api/comics", params={"page_size": 3, "sort": "title"}, headers=headers)).json()
    assert (res["total"], res["page"], res["page_size"], len(res["items"])) == (4, 1, 3, 3)
    res2 = (await api.get("/api/comics", params={"page_size": 3, "page": 2, "sort": "title"}, headers=headers)).json()
    assert [c["title"] for c in res2["items"]] == ["Đời Sống Ở Làng Gió"] and res2["total"] == 4
    for params in ({"page": 0}, {"page_size": 101}, {"min_rating": 6}, {"status": "nope"}):
        assert (await api.get("/api/comics", params=params, headers=headers)).status_code == 422, params


# ── summary, facets, lookup, tags ────────────────────────────────────────


async def test_summary(api, library):
    headers, _, _ = library
    res = (await api.get("/api/comics/summary", headers=headers)).json()
    assert res == {
        "total": 4,
        "by_status": {"reading": 1, "completed": 1, "plan_to_read": 1, "on_hold": 1, "dropped": 0},
        "new_chapters": 1,
    }


async def test_facets(api, library):
    headers, shelf_id, _ = library
    res = (await api.get("/api/comics/facets", headers=headers)).json()
    assert res["total"] == 4
    assert [(s["value"], s["label"], s["count"]) for s in res["statuses"]][:2] == [("reading", "Đang đọc", 1), ("completed", "Đã đọc xong", 1)]
    assert {g["value"]: g["count"] for g in res["genres"]}["Kỳ ảo"] == 1
    assert {s["value"]: s["count"] for s in res["sources"]} == {"Cuutruyen": 3, "Hako": 1}
    assert [s["icon"] for s in res["sources"] if s["value"] == "Cuutruyen"] == ["🌸"]
    assert "icon" not in next(s for s in res["sources"] if s["value"] == "Hako")  # no favicon → no key
    assert res["shelves"] == [{"value": shelf_id, "label": "Truyện chữa lành", "icon": "🌸", "count": 2}]


async def test_lookup(api, library):
    headers, _, comics = library
    res = (await api.get("/api/comics/lookup", params={"title": "  ánh trăng nhà kính "}, headers=headers)).json()
    assert res["comic"]["id"] == comics["Ánh Trăng Nhà Kính"]["id"]
    assert (await api.get("/api/comics/lookup", params={"title": "Ánh Trăng"}, headers=headers)).json() == {"comic": None}


async def test_tags_list_and_cleanup(api, db_session, library):
    headers, _, comics = library
    res = (await api.get("/api/tags", headers=headers)).json()
    assert {t["name"]: t["count"] for t in res} == {"Đời thường": 1, "Kỳ ảo": 1, "Lãng mạn": 1, "Tình cảm": 1, "Học đường": 1}

    # a tag used twice comes first; a tag no comic uses any more disappears
    cid = comics["Bức Thư Mùa Đông"]["id"]
    await api.patch(f"/api/comics/{cid}", json={"tags": ["kỳ ảo"]}, headers=headers)  # reuses "Kỳ ảo"
    res = (await api.get("/api/tags", headers=headers)).json()
    assert res[0] == {"name": "Kỳ ảo", "count": 2}
    assert "Tình cảm" not in {t["name"] for t in res}
    assert await db_session.scalar(select(func.count()).select_from(Tag).where(Tag.name == "Tình cảm")) == 0


# ── update / delete ──────────────────────────────────────────────────────


async def test_update_fields(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers)
    res = await api.patch(f"/api/comics/{comic['id']}", json={"status": "on_hold", "rating": 3, "note": "  hay  "}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["rating"], body["note"]) == ("on_hold", 3, "hay")
    assert body["title"] == comic["title"] and body["tags"] == comic["tags"]  # untouched
    assert body["updated_at"] > comic["updated_at"]


async def test_update_total_below_progress_clamps(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, current_chapter=50, total_chapters=120)
    body = (await api.patch(f"/api/comics/{comic['id']}", json={"total_chapters": 40}, headers=headers)).json()
    assert (body["current_chapter"], body["total_chapters"]) == (40, 40)
    # progress above the total is kept (providers lag behind releases), not rejected
    res = await api.patch(f"/api/comics/{comic['id']}", json={"current_chapter": 41}, headers=headers)
    assert res.status_code == 200
    assert (res.json()["current_chapter"], res.json()["total_chapters"]) == (41, 40)


async def test_update_sources_like_the_detail_page(api, db_session):
    """ComicDetailPage sends the whole list: new sources carry a client id, and may become primary."""
    headers = await signed_in(api, db_session)
    comic = await create(api, headers)
    old = comic["sources"][0]
    new_source = {"id": "src_999", "site_name": "Kakao", "url": "https://page.kakao.com/x", "favicon_url": "", "chapter_url": "", "latest_chapter": 5, "is_alive": True, "last_checked_at": "2026-01-01T00:00:00Z"}

    body = (await api.patch(
        f"/api/comics/{comic['id']}",
        json={"sources": [old | {"site_name": "Cuu Truyen"}, new_source], "primary_source_id": "src_999"},
        headers=headers,
    )).json()
    assert [s["site_name"] for s in body["sources"]] == ["Cuu Truyen", "Kakao"]
    assert body["sources"][0]["id"] == old["id"]  # existing source kept its id
    kakao_id = body["sources"][1]["id"]
    assert body["primary_source_id"] == kakao_id

    # removing the primary (the page sends primary_source_id "") → the first remaining source
    body = (await api.patch(
        f"/api/comics/{comic['id']}",
        json={"sources": [s for s in body["sources"] if s["id"] != kakao_id], "primary_source_id": ""},
        headers=headers,
    )).json()
    assert [s["id"] for s in body["sources"]] == [old["id"]] and body["primary_source_id"] == old["id"]

    # set the primary by id only
    body = (await api.patch(f"/api/comics/{comic['id']}", json={"primary_source_id": old["id"]}, headers=headers)).json()
    assert body["primary_source_id"] == old["id"]
    res = await api.patch(f"/api/comics/{comic['id']}", json={"primary_source_id": str(uuid.uuid4())}, headers=headers)
    assert res.status_code == 404 and res.json()["code"] == "source_not_found"


async def test_update_shelves(api, db_session):
    headers = await signed_in(api, db_session)
    a, b = await make_shelf(db_session, name="A"), await make_shelf(db_session, name="B")
    comic = await create(api, headers, shelf_ids=[a])
    body = (await api.patch(f"/api/comics/{comic['id']}", json={"shelf_ids": ["all", b]}, headers=headers)).json()
    assert body["shelf_ids"] == ["all", b]


async def test_delete_comic(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, tags=["Chỉ mình tôi"])
    await api.put(f"/api/comics/{comic['id']}/progress", json={"current_chapter": 15}, headers=headers)

    res = await api.delete(f"/api/comics/{comic['id']}", headers=headers)
    assert res.status_code == 204 and res.content == b""
    assert (await api.get(f"/api/comics/{comic['id']}", headers=headers)).status_code == 404
    assert (await api.get("/api/tags", headers=headers)).json() == []  # its only tag is gone
    assert await db_session.scalar(select(func.count()).select_from(ReadingLog)) == 0


# ── favorite / progress ──────────────────────────────────────────────────


async def test_favorite_is_idempotent(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers)
    path = f"/api/comics/{comic['id']}/favorite"
    for _ in range(2):
        res = await api.put(path, headers=headers)
        assert res.status_code == 200 and res.json()["is_favorite"] is True
    for _ in range(2):
        res = await api.delete(path, headers=headers)
        assert res.status_code == 200 and res.json()["is_favorite"] is False


async def test_progress_logs_chapters_read(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, status="plan_to_read", current_chapter=10, total_chapters=20)
    path = f"/api/comics/{comic['id']}/progress"

    body = (await api.put(path, json={"current_chapter": 13}, headers=headers)).json()
    assert (body["current_chapter"], body["status"]) == (13, "reading")
    assert body["last_read_at"] > comic["last_read_at"]
    await api.put(path, json={"current_chapter": 15}, headers=headers)
    await api.put(path, json={"current_chapter": 14}, headers=headers)  # going back logs nothing

    logs = (await db_session.scalars(select(ReadingLog))).all()
    assert [(log.chapters, log.library_item_id) for log in logs] == [(5, uuid.UUID(comic["id"]))]  # 3 + 2, one row per day

    body = (await api.put(path, json={"current_chapter": 20}, headers=headers)).json()
    assert (body["current_chapter"], body["status"]) == (20, "completed")  # the known total: finished
    body = (await api.put(path, json={"current_chapter": 23}, headers=headers)).json()
    assert (body["current_chapter"], body["total_chapters"], body["status"]) == (23, 20, "reading")  # past it: more chapters exist
    body = (await api.put(path, json={"current_chapter": 0}, headers=headers)).json()
    assert (body["current_chapter"], body["status"]) == (0, "reading")
    assert (await api.put(path, json={"current_chapter": -1}, headers=headers)).status_code == 422


async def test_progress_clears_new_chapter_when_caught_up(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, current_chapter=10, total_chapters=120, has_new_chapter=True)  # source at 12
    path = f"/api/comics/{comic['id']}/progress"
    assert (await api.put(path, json={"current_chapter": 11}, headers=headers)).json()["has_new_chapter"] is True
    assert (await api.put(path, json={"current_chapter": 12}, headers=headers)).json()["has_new_chapter"] is False


# ── source endpoints ─────────────────────────────────────────────────────


async def test_source_endpoints(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, sources=[], primary_source_id=None)
    assert comic["sources"] == [] and comic["primary_source_id"] == ""
    base = f"/api/comics/{comic['id']}/sources"

    res = await api.post(base, json={"site_name": "Webtoon", "url": "https://webtoons.com/a", "latest_chapter": 30}, headers=headers)
    assert res.status_code == 201
    first = res.json()["sources"][0]
    assert res.json()["primary_source_id"] == first["id"]  # the first source becomes primary
    body = (await api.post(base, json={"site_name": "Bilibili", "url": "https://manga.bilibili.com/b"}, headers=headers)).json()
    second = body["sources"][1]
    assert body["primary_source_id"] == first["id"]

    body = (await api.patch(f"{base}/{second['id']}", json={"latest_chapter": 44, "is_alive": False}, headers=headers)).json()
    assert (body["sources"][1]["latest_chapter"], body["sources"][1]["is_alive"]) == (44, False)

    body = (await api.put(f"/api/comics/{comic['id']}/primary-source", json={"source_id": second["id"]}, headers=headers)).json()
    assert body["primary_source_id"] == second["id"]

    body = (await api.delete(f"{base}/{second['id']}", headers=headers)).json()
    assert [s["id"] for s in body["sources"]] == [first["id"]] and body["primary_source_id"] == first["id"]

    for method, path, payload in (
        ("PATCH", f"{base}/{uuid.uuid4()}", {"latest_chapter": 1}),
        ("DELETE", f"{base}/nope", None),
        ("PUT", f"/api/comics/{comic['id']}/primary-source", {"source_id": "nope"}),
    ):
        res = await api.request(method, path, json=payload, headers=headers)
        assert res.status_code == 404 and res.json()["code"] == "source_not_found", (method, path)


async def test_sources_of_someone_elses_comic(api, db_session):
    mine = await signed_in(api, db_session)
    theirs = await signed_in(api, db_session, "khach")
    comic = await create(api, theirs)
    sid = comic["sources"][0]["id"]
    for method, path, payload in (
        ("POST", f"/api/comics/{comic['id']}/sources", {"site_name": "X", "url": "https://x.example"}),
        ("PATCH", f"/api/comics/{comic['id']}/sources/{sid}", {"latest_chapter": 1}),
        ("DELETE", f"/api/comics/{comic['id']}/sources/{sid}", None),
    ):
        res = await api.request(method, path, json=payload, headers=mine)
        assert res.status_code == 404 and res.json()["code"] == "comic_not_found"


# ── covers ───────────────────────────────────────────────────────────────


def webp_size(raw: bytes) -> tuple[int, int]:
    img = Image.open(io.BytesIO(raw))
    assert img.format == "WEBP"
    return img.size


async def test_cover_from_data_url_and_serving(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers, cover_url=data_url(png_bytes(1200, 1800)))
    url = comic["cover_url"]
    assert url.startswith(f"/api/comics/{comic['id']}/cover?v=") and "&sig=" in url

    res = await api.get(url)  # public: <img> tags can't send a token
    assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert webp_size(res.content) == (480, 720)
    again = await api.get(url, headers={"If-None-Match": res.headers["etag"]})
    assert again.status_code == 304 and again.content == b""

    for bad in (url.replace("sig=", "sig=0"), url.split("&sig=")[0] + "&sig=" + "0" * 32, f"/api/comics/{comic['id']}/cover?v=1&sig=x"):
        assert (await api.get(bad)).status_code == 404, bad

    # sending the same cover_url back keeps the stored image; an external URL replaces it
    body = (await api.patch(f"/api/comics/{comic['id']}", json={"cover_url": url, "note": "x"}, headers=headers)).json()
    assert body["cover_url"] == url
    body = (await api.patch(f"/api/comics/{comic['id']}", json={"cover_url": "https://img.example/b.jpg"}, headers=headers)).json()
    assert body["cover_url"] == "https://img.example/b.jpg"
    assert (await api.get(url)).status_code == 404


async def test_cover_upload(api, db_session):
    headers = await signed_in(api, db_session)
    comic = await create(api, headers)
    path = f"/api/comics/{comic['id']}/cover"
    res = await api.put(path, files={"file": ("cover.png", png_bytes(300, 200), "image/png")}, headers=headers)
    assert res.status_code == 200
    served = await api.get(res.json()["cover_url"])
    assert webp_size(served.content) == (300, 200)  # small images are not upscaled

    res = await api.put(path, files={"file": ("x.png", b"not an image", "image/png")}, headers=headers)
    assert res.status_code == 400 and res.json()["code"] == "invalid_file"
    res = await api.put(path, files={"file": ("big.png", b"0" * (4 * 1024 * 1024 + 1), "image/png")}, headers=headers)
    assert res.status_code == 413 and res.json()["code"] == "file_too_large"

    body = (await api.delete(path, headers=headers)).json()
    assert body["cover_url"] == ""


# ── users/me: profile fields and avatar ──────────────────────────────────


async def test_patch_me_refuses_username(api, db_session):
    headers = await signed_in(api, db_session)
    res = await api.patch("/api/users/me", json={"username": "hacker", "display_name": "X"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == "username_not_editable"
    assert "Tên đăng nhập không thể thay đổi" in res.json()["detail"]
    me = (await api.get("/api/users/me", headers=headers)).json()
    assert (me["username"], me["display_name"]) == ("bemeolac", "Uyên")  # nothing changed

    for field in ("email", "role", "is_active", "password"):
        res = await api.patch("/api/users/me", json={field: "x"}, headers=headers)
        assert res.status_code == 400 and res.json()["code"] == "field_not_editable", field
    assert "Bảo mật" in res.json()["detail"]  # password → hint to the security page


async def test_patch_me_settings_and_avatar(api, db_session):
    headers = await signed_in(api, db_session)
    body = (await api.patch("/api/users/me", json={"settings": {"theme": "night"}, "avatar": data_url(png_bytes(800, 600))}, headers=headers)).json()
    assert body["settings"]["theme"] == "night"
    assert body["avatar_url"].startswith(f"/api/users/{body['id']}/avatar?v=")
    assert webp_size((await api.get(body["avatar_url"])).content) == (256, 256)  # centre-cropped square

    body = (await api.patch("/api/users/me", json={"avatar": None}, headers=headers)).json()
    assert body["avatar_url"] == ""
    res = await api.patch("/api/users/me", json={"avatar": "data:image/png;base64,@@@"}, headers=headers)
    assert res.status_code == 400 and res.json()["code"] == "invalid_file"


async def test_avatar_upload(api, db_session):
    headers = await signed_in(api, db_session)
    res = await api.put("/api/users/me/avatar", files={"file": ("me.png", png_bytes(500, 500), "image/png")}, headers=headers)
    assert res.status_code == 200
    served = await api.get(res.json()["avatar_url"])
    assert served.status_code == 200 and webp_size(served.content) == (256, 256)
    assert (await api.get("/api/users/me", headers=headers)).json()["avatar_url"] == res.json()["avatar_url"]
