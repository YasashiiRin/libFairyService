"""Fake MangaDex and AniList for tests: an httpx.MockTransport with their response shapes.

No test ever reaches the real network. `mode` switches a provider to slow / error / 429.
"""

import asyncio
import io
import json
from collections import Counter
from urllib.parse import parse_qs

import httpx
from PIL import Image

# 1. on both providers, linked by MangaDex's links.al → merged by external id
TIEM_MD = "0b4b5f6e-1111-4c1a-9f00-000000000001"
TIEM_AL = "101"
# 2. MangaDex only
VUON_MD = "0b4b5f6e-2222-4c1a-9f00-000000000002"
# 3. on both, but no shared id: same title + author → merged by title
LUKHACH_MD = "0b4b5f6e-3333-4c1a-9f00-000000000003"
LUKHACH_AL = "303"


def _manga(mid, title, alt, author, tags, status, last, links, cover="cover.jpg", description="Mô tả"):
    return {
        "id": mid,
        "type": "manga",
        "attributes": {
            "title": {"en": title},
            "altTitles": [{"vi": a} for a in alt],
            "description": {"en": description},
            "links": links,
            "lastChapter": last,
            "status": status,
            "tags": [{"attributes": {"name": {"en": name}, "group": group}} for name, group in tags],
        },
        "relationships": [
            {"type": "author", "attributes": {"name": author}},
            {"type": "cover_art", "attributes": {"fileName": cover}},
        ],
    }


MANGADEX = {
    TIEM_MD: _manga(
        TIEM_MD, "Time Grocery", ["Tiệm Tạp Hóa Thời Gian"], "Hatori M.",
        [("Slice of Life", "genre"), ("Magic", "theme"), ("Long Strip", "format")],
        "ongoing", "120", {"al": TIEM_AL, "mal": "201", "raw": "https://raw.example.jp/time-grocery", "mu": "abc"},
    ),
    VUON_MD: _manga(VUON_MD, "Secret Witch Garden", ["Khu Vườn Phù Thủy Bí Mật"], "Solaria", [("Fantasy", "genre")], "completed", "88", {}),
    LUKHACH_MD: _manga(LUKHACH_MD, "Faraway Traveler", ["Lữ Khách Phương Xa"], "Aoi Kaze", [("Adventure", "genre")], "hiatus", "", {}),
}


def _media(aid, english, romaji, author, genres, status, chapters, links=(), mal=None):
    return {
        "id": int(aid),
        "idMal": mal,
        "siteUrl": f"https://anilist.co/manga/{aid}",
        "status": status,
        "chapters": chapters,
        "genres": genres,
        "synonyms": [],
        "title": {"romaji": romaji, "english": english, "native": None},
        "coverImage": {"large": f"https://s4.anilist.co/cover/{aid}.jpg"},
        "description": "An <i>AniList</i> description<br>line 2",
        "staff": {"edges": [{"role": "Story & Art", "node": {"name": {"full": author}}}, {"role": "Translator", "node": {"name": {"full": "Someone Else"}}}]},
        "externalLinks": [{"site": site, "url": url, "type": kind} for site, url, kind in links],
    }


ANILIST = {
    TIEM_AL: _media(
        TIEM_AL, "Time Grocery", "Jikan no Zakkaya", "Hatori M.", ["Slice of Life", "Supernatural"], "RELEASING", None,
        links=[("Webtoon", "https://www.webtoons.com/en/time-grocery/list", "STREAMING"), ("Twitter", "https://x.com/tg", "SOCIAL")],
        mal=201,
    ),
    LUKHACH_AL: _media(LUKHACH_AL, "Faraway Traveler", "Tooi Tabibito", "Aoi Kaze", ["Adventure", "Drama"], "HIATUS", 64),
}


def png(width=400, height=600) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 160, 200)).save(out, "PNG")
    return out.getvalue()


class FakeProviders:
    def __init__(self):
        self.mode = {"mangadex": "ok", "anilist": "ok"}
        self.calls: Counter = Counter()
        self.covers_ok = True

    def _mode_response(self, provider: str):
        mode = self.mode[provider]
        if mode == "error":
            return httpx.Response(500, json={"result": "error"})
        if mode == "429":
            return httpx.Response(429, headers={"Retry-After": "120"}, json={"errors": [{"status": 429}]})
        return None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host in ("uploads.mangadex.org", "s4.anilist.co"):
            self.calls["cover"] += 1
            return httpx.Response(200, content=png()) if self.covers_ok else httpx.Response(404)

        provider = "mangadex" if host == "api.mangadex.org" else "anilist" if host == "graphql.anilist.co" else None
        if provider is None:
            raise AssertionError(f"unexpected request to {request.url}")
        self.calls[provider] += 1
        if self.mode[provider] == "slow":
            await asyncio.sleep(5)
        failure = self._mode_response(provider)
        if failure is not None:
            return failure
        assert "UyenThuCac/" in request.headers["user-agent"]
        return self._mangadex(request, path) if provider == "mangadex" else self._anilist(request)

    def _mangadex(self, request: httpx.Request, path: str) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        if path == "/manga":
            assert params["contentRating[]"] == ["safe", "suggestive"]
            q = params["title"][0].lower()
            hits = [m for m in MANGADEX.values() if q in json.dumps(m, ensure_ascii=False).lower()]
            return httpx.Response(200, json={"result": "ok", "data": hits, "total": len(hits)})
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "manga":
            m = MANGADEX.get(parts[1])
            return httpx.Response(200, json={"result": "ok", "data": m}) if m else httpx.Response(404, json={"result": "error"})
        if len(parts) == 3 and parts[2] == "feed":
            return httpx.Response(200, json={"result": "ok", "data": [{"attributes": {"chapter": "131.5"}}]})
        raise AssertionError(f"unexpected MangaDex path {path}")

    def _anilist(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "Page(" in body["query"]:
            q = body["variables"]["search"].lower()
            hits = [m for m in ANILIST.values() if q in json.dumps(m, ensure_ascii=False).lower()]
            return httpx.Response(200, json={"data": {"Page": {"media": hits}}})
        media = ANILIST.get(str(body["variables"]["id"]))
        if media is None:
            return httpx.Response(404, json={"data": {"Media": None}, "errors": [{"status": 404, "message": "Not Found."}]})
        return httpx.Response(200, json={"data": {"Media": media}})
