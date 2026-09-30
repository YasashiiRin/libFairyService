"""Fake MangaDex and AniList for tests: an httpx.MockTransport with their response shapes.

No test ever reaches the real network. `mode` switches a provider to slow / error / 429.
"""

import asyncio
import io
import json
import re
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


# ── The home feed's catalog ──────────────────────────────────────────────
# MangaDex tags by name → fake ids; works with tags, year and followers; AniList lists and recommendations.

MD_TAGS = {name: f"tag-{name.lower().replace(' ', '-')}" for name in (
    "Slice of Life", "Fantasy", "Magic", "Romance", "Adventure", "Comedy", "School Life", "Drama", "Supernatural", "Action",
    "Girls' Love", "Boys' Love",
)}


def _feed_manga(mid, title, author, tags, year, follows, created=None):
    m = _manga(mid, title, [], author, [(t, "genre") for t in tags], "ongoing", "", {})
    m["attributes"]["year"] = year
    m["attributes"]["createdAt"] = created or f"{year}-01-15T00:00:00+00:00"
    m["attributes"]["tags"] = [{"id": MD_TAGS[t], "attributes": {"name": {"en": t}, "group": "genre"}} for t in tags]
    return m, follows


FEED_MANGADEX = dict(
    (m["id"], (m, f))
    for m, f in [
        _feed_manga("feed-md-0001", "Quán Trà Dưới Gốc Sồi", "Hatori M.", ["Slice of Life"], 2026, 900, "2026-08-01T00:00:00+00:00"),
        _feed_manga("feed-md-0002", "Người Gác Vườn Mây", "Hatori M.", ["Slice of Life", "Fantasy"], 2019, 12000),
        _feed_manga("feed-md-0003", "Thư Viện Phép Màu", "Solaria", ["Fantasy", "Magic"], 2026, 3000, "2026-07-01T00:00:00+00:00"),
        _feed_manga("feed-md-0004", "Chuyến Tàu Hoàng Hôn", "Aoi Kaze", ["Adventure", "Drama"], 2021, 45000),
        _feed_manga("feed-md-0005", "Lớp Học Của Mèo", "Mira Sol", ["Comedy", "School Life"], 2026, 800, "2026-09-01T00:00:00+00:00"),
        _feed_manga("feed-md-0006", "Hiệp Sĩ Bão Tố", "Gore Master", ["Action"], 2025, 90000),
        _feed_manga("feed-md-0007", "Mùa Hoa Anh Đào Năm Ấy", "Hatori M.", ["Slice of Life", "Romance"], 2024, 20000),
        _feed_manga("feed-md-0008", "Tiệm Bánh Ở Góc Phố", "Hatori M.", ["Slice of Life"], 2025, 15000),
        _feed_manga("feed-md-0009", "Căn Bếp Của Bà", "Hatori M.", ["Slice of Life"], 2023, 7000),
        _feed_manga("feed-md-0010", "Góc Nhỏ Bình Yên", "Rin Aoba", ["Slice of Life", "Magic"], 2020, 50000),
        _feed_manga("feed-md-0011", "Nhật Ký Trà Chiều", "Rin Aoba", ["Slice of Life"], 2022, 30000),
        # Girls' Love / Boys' Love (priority tastes): five GL works by one author, to test variety
        *(_feed_manga(f"feed-md-gl0{i}", f"Hoa Bách Hợp {i}", "Yuri Sensei", ["Girls' Love", "Romance"], 2026, 400 + i, "2026-08-10T00:00:00+00:00")
          for i in range(1, 6)),
        _feed_manga("feed-md-gl10", "Hai Cô Gái Và Khu Vườn", "Hana Mori", ["Girls' Love", "Slice of Life"], 2020, 2500),
        _feed_manga("feed-md-bl01", "Chàng Thợ Gốm", "Kei Tanaka", ["Boys' Love", "Drama"], 2026, 600, "2026-09-05T00:00:00+00:00"),
        _feed_manga("feed-md-bl02", "Mùa Đông Ở Sapporo", "Kei Tanaka", ["Boys' Love", "Romance"], 2023, 5200),
    ]
)
MD_AUTHORS = {"Hatori M.": "author-hatori", "Solaria": "author-solaria", "Aoi Kaze": "author-aoi"}


def _feed_media(aid, title, author, genres, popularity, trending, start=(2020, 1, 1), status="RELEASING", tags=(), **extra):
    m = _media(aid, title, title, author, genres, status, None)
    m.update(popularity=popularity, trending=trending, type="MANGA", isAdult=False,
             startDate={"year": start[0], "month": start[1], "day": start[2]}, tags=[{"name": n, "rank": r} for n, r in tags], **extra)
    return m


ANILIST_FEED = {
    m["id"]: m
    for m in [
        _feed_media(501, "Khu Rừng Thì Thầm", "Lirien", ["Slice of Life", "Fantasy"], 60000, 120, (2018, 4, 1)),
        _feed_media(502, "Hạt Giống Sao Băng", "Lirien", ["Fantasy"], 8000, 90, (2026, 6, 1)),
        _feed_media(503, "Bản Tình Ca Mùa Hạ", "Min-seo Park", ["Romance", "Drama"], 30000, 140, (2024, 5, 1)),
        _feed_media(504, "Đao Kiếm Đẫm Máu", "Gore Master", ["Action", "Horror"], 70000, 200, (2025, 1, 1)),
        _feed_media(505, "Tiệm Tạp Hóa Thời Gian 2", "Hatori M.", ["Slice of Life", "Supernatural"], 25000, 60, (2026, 8, 1)),
        # AniList has no GL/BL genre: the tags Yuri and Boys' Love (rank = how central)
        _feed_media(601, "Nụ Hôn Dưới Mưa", "Sakura Ito", ["Romance"], 9000, 80, (2026, 7, 1), tags=[("Yuri", 90)]),
        _feed_media(602, "Lá Thư Gửi Senpai", "Mei Kato", ["Romance", "Drama"], 15000, 110, (2019, 3, 1), tags=[("Boys' Love", 85)]),
        _feed_media(603, "Chỉ Là Bạn Thôi", "Ai Ueda", ["Comedy"], 20000, 50, (2021, 1, 1), tags=[("Yuri", 20)]),  # not central
    ]
}
# AniList recommendations: for Time Grocery (TIEM_AL)
ANILIST_RECS = {int(TIEM_AL): [(88, 505), (40, 501), (3, 503)]}


def png(width=400, height=600) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 160, 200)).save(out, "PNG")
    return out.getvalue()


class FakeProviders:
    def __init__(self):
        self.mode = {"mangadex": "ok", "anilist": "ok"}
        self.calls: Counter = Counter()
        self.covers_ok = True
        self.latest: dict[str, str] = {}  # MangaDex id → the latest chapter its feed reports
        self.feed_queries: list[str] = []  # AniList feed queries received

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

    def _mangadex_feed(self, path: str, params: dict[str, list[str]]) -> httpx.Response | None:
        ok = lambda data: httpx.Response(200, json={"result": "ok", "data": data, "total": len(data)})  # noqa: E731
        if path == "/manga/tag":
            return ok([{"id": tid, "type": "tag", "attributes": {"name": {"en": name}, "group": "genre"}} for name, tid in MD_TAGS.items()])
        if path == "/author":
            name = params["name"][0]
            return ok([{"id": aid, "attributes": {"name": n}} for n, aid in MD_AUTHORS.items() if n == name])
        if path == "/statistics/manga":
            stats = {mid: {"follows": FEED_MANGADEX[mid][1]} for mid in params.get("manga[]", []) if mid in FEED_MANGADEX}
            return httpx.Response(200, json={"result": "ok", "statistics": stats})
        if path == "/manga" and "title" not in params:
            # the feed: never erotica or pornographic
            assert params["contentRating[]"] == ["safe", "suggestive"] and params["hasAvailableChapters"] == ["true"]
            works = [(m, f) for m, f in FEED_MANGADEX.values()]
            if "authorOrArtist" in params:
                name = next(n for n, aid in MD_AUTHORS.items() if aid == params["authorOrArtist"][0])
                works = [(m, f) for m, f in works if any(r["attributes"]["name"] == name for r in m["relationships"] if r["type"] == "author")]
            if "includedTags[]" in params:
                wanted = set(params["includedTags[]"])
                works = [(m, f) for m, f in works if wanted & {t["id"] for t in m["attributes"]["tags"]}]
            if "year" in params:
                works = [(m, f) for m, f in works if str(m["attributes"]["year"]) == params["year"][0]]
            works.sort(key=lambda mf: -mf[1])
            return ok([m for m, _ in works][: int(params.get("limit", ["20"])[0])])
        return None

    def _mangadex(self, request: httpx.Request, path: str) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        feed = self._mangadex_feed(path, params)
        if feed is not None:
            return feed
        if path == "/manga":
            assert params["contentRating[]"] == ["safe", "suggestive"]
            q = params["title"][0].lower()
            hits = [m for m in MANGADEX.values() if q in json.dumps(m, ensure_ascii=False).lower()]
            return httpx.Response(200, json={"result": "ok", "data": hits, "total": len(hits)})
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "manga":
            m = MANGADEX.get(parts[1]) or FEED_MANGADEX.get(parts[1], (None,))[0]
            return httpx.Response(200, json={"result": "ok", "data": m}) if m else httpx.Response(404, json={"result": "error"})
        if len(parts) == 3 and parts[2] == "feed":
            return httpx.Response(200, json={"result": "ok", "data": [{"attributes": {"chapter": self.latest.get(parts[1], "131.5")}}]})
        raise AssertionError(f"unexpected MangaDex path {path}")

    def _anilist_feed(self, query: str) -> httpx.Response:
        """The feed's aliased queries: Page lists (genre_in filters, sorted as asked) and recommendations."""
        self.feed_queries.append(query)
        assert "isAdult: false" in query or "recommendations" in query
        data = {}
        for alias, kind, args in re.findall(r"(\w+): (Page|Media)\(([^)]*)\)", query):
            if kind == "Page":
                media_args = re.search(rf"{alias}: Page\([^)]*\) \{{ media\(([^)]*)\)", query).group(1)
                genres = json.loads(g.group(1)) if (g := re.search(r"genre_in: (\[[^\]]*\])", media_args)) else None
                tags = json.loads(t.group(1)) if (t := re.search(r"tag_in: (\[[^\]]*\])", media_args)) else None
                min_rank = int(r.group(1)) if (r := re.search(r"minimumTagRank: (\d+)", media_args)) else 0
                hits = [m for m in ANILIST_FEED.values() if not genres or set(genres) <= set(m["genres"])]  # AniList: every one
                if tags:  # AniList: every one of the tags
                    hits = [m for m in hits if set(tags) <= {x["name"] for x in m["tags"] if x["rank"] >= min_rank}]
                sort = re.search(r"sort: \[(\w+)\]", media_args).group(1)
                key = {"TRENDING_DESC": "trending", "POPULARITY_DESC": "popularity"}.get(sort)
                if key:
                    hits.sort(key=lambda m: -m[key])
                else:  # START_DATE_DESC
                    hits = [m for m in hits if m["startDate"]["year"] >= 2025]
                    hits.sort(key=lambda m: tuple(m["startDate"].values()), reverse=True)
                data[alias] = {"media": hits}
            else:
                wid = int(re.search(r"id: (\d+)", args).group(1))
                nodes = [{"rating": r, "mediaRecommendation": ANILIST_FEED[mid]} for r, mid in ANILIST_RECS.get(wid, [])]
                data[alias] = {"id": wid, "recommendations": {"nodes": nodes}}
        return httpx.Response(200, json={"data": data})

    def _anilist(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "fragment feed" in body["query"]:
            return self._anilist_feed(body["query"])
        if "Page(" in body["query"]:
            q = body["variables"]["search"].lower()
            hits = [m for m in ANILIST.values() if q in json.dumps(m, ensure_ascii=False).lower()]
            return httpx.Response(200, json={"data": {"Page": {"media": hits}}})
        media = ANILIST.get(str(body["variables"]["id"]))
        if media is None:
            return httpx.Response(404, json={"data": {"Media": None}, "errors": [{"status": 404, "message": "Not Found."}]})
        return httpx.Response(200, json={"data": {"Media": media}})
