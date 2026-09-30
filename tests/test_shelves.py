"""/api/shelves (api-contract.md#shelves): list, detail, create, update, delete, reorder, membership."""

import pytest

from tests.helpers import signed_in
from tests.test_comics import create


async def new_shelf(api, headers, **body) -> dict:
    res = await api.post("/api/shelves", json={"name": "Truyện chữa lành", **body}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def shelves(api, headers) -> list[dict]:
    res = await api.get("/api/shelves", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def comic(title: str, **kw) -> dict:
    slug = title.lower().replace(" ", "-")
    return {"title": title, "cover_url": f"https://img.example/{slug}.jpg",
            "sources": [{"site_name": "S", "url": f"https://s.example/{slug}", "favicon_url": ""}]} | kw


async def test_create_and_list(api, db_session):
    headers = await signed_in(api, db_session)
    assert [s["id"] for s in await shelves(api, headers)] == ["all"]  # the virtual shelf, always there

    first = await new_shelf(api, headers, name="  Truyện   chữa lành ", description=" Đọc buổi tối ")
    assert first == {
        "id": first["id"], "name": "Truyện chữa lành", "description": "Đọc buổi tối", "icon": "🌸", "color": "#FEB2C0",
        "position": 2, "comic_count": 0, "cover_urls": [],
    }
    second = await new_shelf(api, headers, name="Kỳ ảo", icon="✨", color="#a8c49a")
    assert (second["position"], second["icon"], second["color"]) == (3, "✨", "#A8C49A")

    await create(api, headers, **comic("Mèo Con", shelf_ids=[first["id"]]))
    await create(api, headers, **comic("Không Bìa", cover_url="", shelf_ids=[first["id"]]))
    listed = await shelves(api, headers)
    assert [(s["id"], s["position"], s["comic_count"]) for s in listed] == [("all", 1, 2), (first["id"], 2, 2), (second["id"], 3, 0)]
    assert listed[0]["name"] == "Tất cả truyện"
    assert listed[1]["cover_urls"] == ["https://img.example/mèo-con.jpg"]  # comics without a cover are skipped

    for bad in ({"name": "   "}, {"name": "x", "color": "#000000"}, {"name": "x", "position": 9}, {}):
        res = await api.post("/api/shelves", json=bad, headers=headers)
        assert res.status_code == 422 and res.json()["code"] == "validation_error", bad


async def test_covers_follow_the_shelf_order_up_to_four(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    made = [await create(api, headers, **comic(f"Truyện {i}", shelf_ids=[shelf["id"]])) for i in range(6)]
    order = [c["id"] for c in reversed(made)]
    assert (await api.put(f"/api/shelves/{shelf['id']}/order", json={"comic_ids": order}, headers=headers)).status_code == 204
    listed = {s["id"]: s for s in await shelves(api, headers)}
    assert listed[shelf["id"]]["cover_urls"] == [f"https://img.example/truyện-{i}.jpg" for i in (5, 4, 3, 2)]
    assert listed["all"]["comic_count"] == 6 and len(listed["all"]["cover_urls"]) == 4


async def test_detail_with_stats(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    await create(api, headers, **comic("Xong Rồi", status="completed", shelf_ids=[shelf["id"]]))
    last = await create(api, headers, **comic("Đang Đọc", status="reading", shelf_ids=[shelf["id"]]))
    await create(api, headers, **comic("Không Trên Kệ", status="completed"))

    res = await api.get(f"/api/shelves/{shelf['id']}", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["comic_count"], body["completed_count"], body["position"]) == (2, 1, 2)
    assert body["last_updated_at"] == last["updated_at"]
    assert len(body["cover_urls"]) == 2

    everything = (await api.get("/api/shelves/all", headers=headers)).json()
    assert (everything["id"], everything["comic_count"], everything["completed_count"]) == ("all", 3, 2)

    empty = await new_shelf(api, headers, name="Trống")
    assert (await api.get(f"/api/shelves/{empty['id']}", headers=headers)).json()["last_updated_at"] is None
    for missing in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        res = await api.get(f"/api/shelves/{missing}", headers=headers)
        assert res.status_code == 404 and res.json()["code"] == "shelf_not_found"


async def test_update(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    await create(api, headers, **comic("Mèo", shelf_ids=[shelf["id"]]))
    res = await api.patch(f"/api/shelves/{shelf['id']}", json={"name": "Góc bình yên", "icon": "🌙", "color": "#D9C8F0"}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["name"], body["icon"], body["color"], body["description"], body["comic_count"]) == ("Góc bình yên", "🌙", "#D9C8F0", "", 1)

    for bad in ({"name": ""}, {"color": "red"}, {"comic_count": 3}):
        assert (await api.patch(f"/api/shelves/{shelf['id']}", json=bad, headers=headers)).status_code == 422, bad
    res = await api.patch("/api/shelves/all", json={"name": "Của tôi"}, headers=headers)
    assert res.status_code == 422 and res.json()["code"] == "validation_error"


async def test_delete_keeps_the_comics(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    other = await new_shelf(api, headers, name="Khác")
    kept = await create(api, headers, **comic("Mèo", shelf_ids=[shelf["id"], other["id"]]))

    assert (await api.delete(f"/api/shelves/{shelf['id']}", headers=headers)).status_code == 204
    assert [s["id"] for s in await shelves(api, headers)] == ["all", other["id"]]
    again = (await api.get(f"/api/comics/{kept['id']}", headers=headers)).json()
    assert again["shelf_ids"] == ["all", other["id"]]  # the comic stays, off the deleted shelf only
    listed = (await api.get("/api/comics", params={"shelf": shelf["id"]}, headers=headers)).json()
    assert listed["total"] == 0

    assert (await api.delete(f"/api/shelves/{shelf['id']}", headers=headers)).status_code == 404
    assert (await api.delete("/api/shelves/all", headers=headers)).status_code == 422


async def test_reorder_shelves(api, db_session):
    headers = await signed_in(api, db_session)
    a, b, c = [await new_shelf(api, headers, name=n) for n in ("A", "B", "C")]
    res = await api.put("/api/shelves/order", json={"shelf_ids": ["all", c["id"], a["id"], b["id"]]}, headers=headers)
    assert res.status_code == 200, res.text
    assert [(s["name"], s["position"]) for s in res.json()] == [("Tất cả truyện", 1), ("C", 2), ("A", 3), ("B", 4)]
    assert [s["name"] for s in await shelves(api, headers)] == ["Tất cả truyện", "C", "A", "B"]
    assert (await new_shelf(api, headers, name="D"))["position"] == 5  # a new shelf goes last

    for bad in ([a["id"], b["id"]], [a["id"], a["id"], b["id"], c["id"]], ["x", a["id"], b["id"], c["id"]]):
        res = await api.put("/api/shelves/order", json={"shelf_ids": bad}, headers=headers)
        assert res.status_code == 422 and res.json()["code"] == "validation_error", bad


async def test_reorder_comics_on_a_shelf(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    x, _, z = [await create(api, headers, **comic(t, shelf_ids=[shelf["id"]])) for t in ("X", "Y", "Z")]
    outside = await create(api, headers, **comic("Ngoài"))

    res = await api.put(f"/api/shelves/{shelf['id']}/order", json={"comic_ids": [z["id"], x["id"]]}, headers=headers)
    assert res.status_code == 204
    page = (await api.get("/api/comics", params={"shelf": shelf["id"], "sort": "position"}, headers=headers)).json()
    assert [c["title"] for c in page["items"]] == ["Z", "X", "Y"]  # not in the order: after the ordered ones

    for bad in ([outside["id"]], [x["id"], x["id"]], ["nope"]):
        res = await api.put(f"/api/shelves/{shelf['id']}/order", json={"comic_ids": bad}, headers=headers)
        assert res.status_code == 422, bad
    assert (await api.put("/api/shelves/all/order", json={"comic_ids": []}, headers=headers)).status_code == 422


async def test_add_and_remove_a_comic(api, db_session):
    headers = await signed_in(api, db_session)
    shelf = await new_shelf(api, headers)
    item = await create(api, headers, **comic("Mèo"))

    for _ in range(2):  # idempotent
        res = await api.post(f"/api/shelves/{shelf['id']}/comics", json={"comic_id": item["id"]}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["shelf_ids"] == ["all", shelf["id"]]
    listed = {s["id"]: s for s in await shelves(api, headers)}
    assert listed[shelf["id"]]["comic_count"] == 1 and listed[shelf["id"]]["cover_urls"] == [item["cover_url"]]

    for _ in range(2):
        res = await api.delete(f"/api/shelves/{shelf['id']}/comics/{item['id']}", headers=headers)
        assert res.status_code == 200 and res.json()["shelf_ids"] == ["all"]
    assert (await api.get(f"/api/comics/{item['id']}", headers=headers)).status_code == 200  # still in the library

    res = await api.post(f"/api/shelves/{shelf['id']}/comics", json={"comic_id": "00000000-0000-0000-0000-000000000000"}, headers=headers)
    assert res.status_code == 404 and res.json()["code"] == "comic_not_found"
    res = await api.post("/api/shelves/00000000-0000-0000-0000-000000000000/comics", json={"comic_id": item["id"]}, headers=headers)
    assert res.status_code == 404 and res.json()["code"] == "shelf_not_found"


@pytest.mark.parametrize("action", ["get", "patch", "delete", "order", "add", "remove"])
async def test_someone_elses_shelf_is_not_found(api, db_session, action):
    theirs_headers = await signed_in(api, db_session, "khach")
    theirs = await new_shelf(api, theirs_headers, name="Của khách")
    their_comic = await create(api, theirs_headers, **comic("Của Khách", shelf_ids=[theirs["id"]]))
    headers = await signed_in(api, db_session)
    mine = await create(api, headers, **comic("Của Tôi"))
    sid = theirs["id"]
    call = {
        "get": lambda: api.get(f"/api/shelves/{sid}", headers=headers),
        "patch": lambda: api.patch(f"/api/shelves/{sid}", json={"name": "Của tôi"}, headers=headers),
        "delete": lambda: api.delete(f"/api/shelves/{sid}", headers=headers),
        "order": lambda: api.put(f"/api/shelves/{sid}/order", json={"comic_ids": []}, headers=headers),
        "add": lambda: api.post(f"/api/shelves/{sid}/comics", json={"comic_id": mine["id"]}, headers=headers),
        "remove": lambda: api.delete(f"/api/shelves/{sid}/comics/{mine['id']}", headers=headers),
    }[action]
    res = await call()
    assert res.status_code == 404 and res.json()["code"] == "shelf_not_found"

    # and nothing of hers shows up in mine, nor can her comic go on my shelf
    assert [s["id"] for s in await shelves(api, headers)] == ["all"]
    my_shelf = await new_shelf(api, headers)
    res = await api.post(f"/api/shelves/{my_shelf['id']}/comics", json={"comic_id": their_comic["id"]}, headers=headers)
    assert res.status_code == 404 and res.json()["code"] == "comic_not_found"
    assert (await shelves(api, theirs_headers))[1]["comic_count"] == 1  # hers is untouched


async def test_shelves_need_a_login(api):
    for method, path in (("get", "/api/shelves"), ("post", "/api/shelves"), ("put", "/api/shelves/order"), ("get", "/api/shelves/all")):
        assert (await getattr(api, method)(path)).status_code == 401
