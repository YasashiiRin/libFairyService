"""MangaDex, through its official public API (https://api.mangadex.org/docs/).

Terms we follow: an identifying User-Agent, at most 5 requests per second, metadata and links
only (never chapter pages or images), credit to MangaDex (results carry provider "mangadex"),
and no adult content (content ratings safe and suggestive only).
"""

from typing import Any
from urllib.parse import urlsplit

from app.providers.base import (
    Link,
    Provider,
    ProviderError,
    ProviderResult,
    RateLimiter,
    chapter_number,
    clean_text,
    dedupe,
    dedupe_links,
    dedupe_names,
)

API = "https://api.mangadex.org"
COVERS = "https://uploads.mangadex.org/covers"
SITE = "https://mangadex.org/title"
PAGE_SIZE = 20
CONTENT_RATINGS = ["safe", "suggestive"]
LANG_ORDER = ("vi", "en", "ja-ro", "ko-ro", "zh-ro", "ja", "ko", "zh")
STATUS = {"ongoing": "ongoing", "completed": "completed", "hiatus": "hiatus", "cancelled": "cancelled"}
# MangaDex `links`: official reading sites (sources) and other catalogues (external ids)
OFFICIAL_LINKS = {"raw": "Bản gốc", "engtl": "Bản dịch chính thức"}
CROSS_IDS = {"al": "anilist", "mal": "mal"}


def _pick(texts: dict[str, str] | None) -> str:
    if not texts:
        return ""
    for lang in LANG_ORDER:
        if texts.get(lang):
            return texts[lang]
    return next(iter(texts.values()), "")


def _site_name(url: str) -> str:
    host = (urlsplit(url).hostname or url).removeprefix("www.")
    return host.split(".")[0].capitalize() if host else "Trang chính thức"


def parse_manga(data: dict[str, Any]) -> ProviderResult:
    attrs = data.get("attributes") or {}
    rels = data.get("relationships") or []
    manga_id = data["id"]

    titles = attrs.get("title") or {}
    title = _pick(titles) or "(không tên)"
    alt = [v for t in attrs.get("altTitles") or [] for v in t.values()] + list(titles.values())

    cover_file = next((r.get("attributes", {}).get("fileName") for r in rels if r.get("type") == "cover_art"), None)
    authors = dedupe_names([r.get("attributes", {}).get("name", "") for r in rels if r.get("type") in ("author", "artist")])
    genres = dedupe([
        _pick((t.get("attributes") or {}).get("name"))
        for t in attrs.get("tags") or []
        if (t.get("attributes") or {}).get("group") in ("genre", "theme")
    ])

    links = attrs.get("links") or {}
    external_ids = {"mangadex": manga_id}
    for key, provider in CROSS_IDS.items():
        if links.get(key):
            external_ids[provider] = str(links[key])
    sources = [Link(site_name="MangaDex", url=f"{SITE}/{manga_id}")]
    for key in OFFICIAL_LINKS:
        url = links.get(key)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            sources.append(Link(site_name=_site_name(url), url=url))

    return ProviderResult(
        provider="mangadex",
        external_id=manga_id,
        title=title,
        alt_titles=[t for t in dedupe(alt) if t != title],
        description=clean_text(_pick(attrs.get("description"))),
        # MangaDex's own 256 px thumbnail
        cover_url=f"{COVERS}/{manga_id}/{cover_file}.256.jpg" if cover_file else "",
        authors=authors,
        genres=genres,
        status=STATUS.get(attrs.get("status") or "", "unknown"),
        latest_chapter=chapter_number(attrs.get("lastChapter")),
        links=dedupe_links(sources),
        external_ids=external_ids,
    )


class MangaDex(Provider):
    name = "mangadex"
    label = "MangaDex"
    limiter = RateLimiter(per_second=5)

    @classmethod
    def page_url(cls, external_id: str) -> str:
        return f"{SITE}/{external_id}"

    async def _get(self, path: str, params: list[tuple[str, Any]]) -> dict[str, Any]:
        res = await self._send("GET", f"{API}{path}", params=params)
        if res.status_code == 404:
            return {}
        if res.status_code != 200:
            raise ProviderError(f"mangadex: HTTP {res.status_code}")
        body = res.json()
        if body.get("result") != "ok":
            raise ProviderError("mangadex: result is not ok")
        return body

    async def search(self, query: str, page: int = 1) -> list[ProviderResult]:
        params: list[tuple[str, Any]] = [
            ("title", query),
            ("limit", PAGE_SIZE),
            ("offset", (page - 1) * PAGE_SIZE),
            ("order[relevance]", "desc"),
            ("includes[]", "cover_art"),
            ("includes[]", "author"),
            ("includes[]", "artist"),
            *(("contentRating[]", r) for r in CONTENT_RATINGS),
        ]
        body = await self._get("/manga", params)
        return [parse_manga(m) for m in body.get("data") or []]

    async def get_details(self, external_id: str) -> ProviderResult | None:
        body = await self._get(
            f"/manga/{external_id}", [("includes[]", "cover_art"), ("includes[]", "author"), ("includes[]", "artist")]
        )
        if not body.get("data"):
            return None
        result = parse_manga(body["data"])
        if result.latest_chapter is None:
            result.latest_chapter = await self.latest_chapter(external_id)
        return result

    async def latest_chapter(self, external_id: str) -> int | None:
        """The highest chapter number in the feed (any language): one request, one row."""
        body = await self._get(
            f"/manga/{external_id}/feed",
            [("limit", 1), ("order[chapter]", "desc"), *(("contentRating[]", r) for r in CONTENT_RATINGS)],
        )
        rows = body.get("data") or []
        return chapter_number((rows[0].get("attributes") or {}).get("chapter")) if rows else None
