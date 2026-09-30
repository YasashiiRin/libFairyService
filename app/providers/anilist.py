"""AniList, through its GraphQL API (https://docs.anilist.co/), for metadata enrichment.

Terms we follow: an identifying User-Agent, at most 90 requests per minute (we pace at one per
second, and pause after a 429), metadata and links only, no adult entries (isAdult: false).
"""

import json
from typing import Any

from app.providers.base import Link, Provider, ProviderError, ProviderResult, RateLimiter, clean_text, dedupe, dedupe_links, dedupe_names

API = "https://graphql.anilist.co"
SITE = "https://anilist.co/manga"
PAGE_SIZE = 20
STATUS = {"RELEASING": "ongoing", "FINISHED": "completed", "HIATUS": "hiatus", "CANCELLED": "cancelled"}
AUTHOR_ROLES = ("story", "art", "original creator")

FIELDS = """
  id idMal siteUrl status chapters genres synonyms popularity trending
  title { romaji english native }
  coverImage { large }
  startDate { year month day }
  description(asHtml: false)
  staff(perPage: 6, sort: RELEVANCE) { edges { role node { name { full } } } }
  externalLinks { site url type }
"""
# The home feed asks for several lists in one request (GraphQL aliases), so it keeps each lean:
# AniList limits a query's complexity, and allows about one request per second.
FEED_FIELDS = """
  id idMal type isAdult status chapters genres synonyms popularity trending
  title { romaji english native }
  coverImage { large }
  startDate { year month day }
  staff(perPage: 3, sort: RELEVANCE) { edges { role node { name { full } } } }
  tags { name rank }
"""
# AniList's genres (genre_in accepts only these); adult ones are always excluded.
GENRES = (
    "Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music",
    "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller",
)
ADULT_GENRES = ["Hentai", "Ecchi"]
# AniList has no Girls' Love / Boys' Love genre, only these tags: a work where one is central
# (rank ≥ MIN_TAG_RANK) gets it among its genres, under MangaDex's name, so both providers agree.
GENRE_TAGS = {"Yuri": "Girls' Love", "Boys' Love": "Boys' Love"}
MIN_TAG_RANK = 60
FEED_PAGE_SIZE = 15
RECOMMENDATIONS_PER_WORK = 6
SEARCH = f"""
query ($search: String, $page: Int, $perPage: Int) {{
  Page(page: $page, perPage: $perPage) {{
    media(search: $search, type: MANGA, sort: SEARCH_MATCH, isAdult: false) {{ {FIELDS} }}
  }}
}}"""
DETAILS = f"""
query ($id: Int) {{
  Media(id: $id, type: MANGA, isAdult: false) {{ {FIELDS} }}
}}"""


def _date(d: dict[str, Any] | None) -> str | None:
    """AniList's FuzzyDate → "YYYY-MM-DD", "YYYY-MM" or "YYYY" (as much as is known)."""
    if not d or not d.get("year"):
        return None
    out = f"{d['year']:04d}"
    if d.get("month"):
        out += f"-{d['month']:02d}"
        if d.get("day"):
            out += f"-{d['day']:02d}"
    return out


def parse_media(m: dict[str, Any]) -> ProviderResult:
    titles = m.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or titles.get("native") or "(không tên)"
    staff = (m.get("staff") or {}).get("edges") or []
    authors = dedupe_names([
        ((e.get("node") or {}).get("name") or {}).get("full", "")
        for e in staff
        if any(role in (e.get("role") or "").lower() for role in AUTHOR_ROLES)
    ])
    # reading sites only (AniList itself is a catalogue, not a place to read)
    links = [
        Link(site_name=link.get("site") or "Trang chính thức", url=link["url"])
        for link in m.get("externalLinks") or []
        if link.get("type") == "STREAMING" and str(link.get("url", "")).startswith(("http://", "https://"))
    ]
    external_ids = {"anilist": str(m["id"])}
    if m.get("idMal"):
        external_ids["mal"] = str(m["idMal"])
    return ProviderResult(
        provider="anilist",
        external_id=str(m["id"]),
        title=title,
        alt_titles=[t for t in dedupe([titles.get("romaji"), titles.get("english"), titles.get("native"), *(m.get("synonyms") or [])]) if t != title],
        description=clean_text(m.get("description")),
        cover_url=(m.get("coverImage") or {}).get("large") or "",
        authors=authors,
        genres=dedupe([*(m.get("genres") or []), *_genre_tags(m.get("tags"))]),
        status=STATUS.get(m.get("status") or "", "unknown"),
        latest_chapter=m.get("chapters") if isinstance(m.get("chapters"), int) else None,
        links=dedupe_links(links),
        external_ids=external_ids,
        popularity=m.get("popularity") if isinstance(m.get("popularity"), int) else None,
        trending=m.get("trending") if isinstance(m.get("trending"), int) else None,
        started_on=_date(m.get("startDate")),
    )


def _genre_tags(tags: list[dict[str, Any]] | None) -> list[str]:
    return [GENRE_TAGS[t["name"]] for t in tags or [] if t.get("name") in GENRE_TAGS and (t.get("rank") or 0) >= MIN_TAG_RANK]


class AniList(Provider):
    name = "anilist"
    label = "AniList"
    limiter = RateLimiter(per_second=1)

    @classmethod
    def page_url(cls, external_id: str) -> str:
        return f"{SITE}/{external_id}"

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        res = await self._send("POST", API, json={"query": query, "variables": variables})
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        if res.status_code == 404 or any((e.get("status") == 404) for e in body.get("errors") or []):
            return {}
        if res.status_code != 200 or body.get("errors"):
            raise ProviderError(f"anilist: HTTP {res.status_code}")
        return body.get("data") or {}

    async def search(self, query: str, page: int = 1) -> list[ProviderResult]:
        data = await self._query(SEARCH, {"search": query, "page": page, "perPage": PAGE_SIZE})
        return [parse_media(m) for m in ((data.get("Page") or {}).get("media") or [])]

    async def get_details(self, external_id: str) -> ProviderResult | None:
        if not external_id.isdigit():
            return None
        media = (await self._query(DETAILS, {"id": int(external_id)})).get("Media")
        return parse_media(media) if media else None

    # ── the home feed ─────────────────────────────────────────────────────

    async def browse(self, pages: dict[str, dict[str, Any]]) -> dict[str, list[ProviderResult]]:
        """Several lists in one request. pages = {alias: {"sort": "TRENDING_DESC", "genres": [...],
        "started_after": 20250929 (FuzzyDateInt), "releasing": True, "tag": "Yuri"}} → {alias: results}.
        One tag per list: AniList's tag_in asks for all of them at once."""
        if not pages:
            return {}
        parts = []
        for alias, spec in pages.items():
            args = ["type: MANGA", "isAdult: false", f"sort: [{spec['sort']}]", f"genre_not_in: {json.dumps(ADULT_GENRES)}"]
            genres = [g for g in spec.get("genres") or [] if g in GENRES]
            if genres:
                args.append(f"genre_in: {json.dumps(genres, ensure_ascii=False)}")
            if spec.get("tag") in GENRE_TAGS:
                args += [f"tag_in: {json.dumps([spec['tag']])}", f"minimumTagRank: {MIN_TAG_RANK}"]
            if spec.get("started_after"):
                args.append(f"startDate_greater: {int(spec['started_after'])}")
            if spec.get("releasing"):
                args.append("status: RELEASING")
            if spec.get("min_popularity"):
                args.append(f"popularity_greater: {int(spec['min_popularity'])}")
            parts.append(f"{alias}: Page(page: 1, perPage: {FEED_PAGE_SIZE}) {{ media({', '.join(args)}) {{ ...feed }} }}")
        data = await self._query("query { " + " ".join(parts) + " } fragment feed on Media { " + FEED_FIELDS + " }", {})
        return {alias: [parse_media(m) for m in (data.get(alias) or {}).get("media") or [] if _safe_manga(m)] for alias in pages}

    async def recommendations(self, external_ids: list[str]) -> dict[str, list[tuple[int, ProviderResult]]]:
        """Users' recommendations for each of these works, in one request → {id: [(rating, result)]}."""
        ids = [i for i in external_ids if i.isdigit()]
        if not ids:
            return {}
        parts = [
            f"r{i}: Media(id: {wid}, type: MANGA) {{ id recommendations(sort: [RATING_DESC], perPage: {RECOMMENDATIONS_PER_WORK}) "
            "{ nodes { rating mediaRecommendation { ...feed } } } }"
            for i, wid in enumerate(ids)
        ]
        data = await self._query("query { " + " ".join(parts) + " } fragment feed on Media { " + FEED_FIELDS + " }", {})
        out: dict[str, list[tuple[int, ProviderResult]]] = {}
        for i, wid in enumerate(ids):
            nodes = (((data.get(f"r{i}") or {}).get("recommendations") or {}).get("nodes")) or []
            out[wid] = [
                (int(n.get("rating") or 0), parse_media(n["mediaRecommendation"]))
                for n in nodes
                if n.get("mediaRecommendation") and _safe_manga(n["mediaRecommendation"]) and (n.get("rating") or 0) > 0
            ]
        return out


def _safe_manga(m: dict[str, Any]) -> bool:
    """Manga only, never adult (recommendations may point at anime or adult entries)."""
    return m.get("type", "MANGA") == "MANGA" and not m.get("isAdult") and not set(m.get("genres") or []) & set(ADULT_GENRES)
