"""AniList, through its GraphQL API (https://docs.anilist.co/), for metadata enrichment.

Terms we follow: an identifying User-Agent, at most 90 requests per minute (we pace at one per
second, and pause after a 429), metadata and links only, no adult entries (isAdult: false).
"""

from typing import Any

from app.providers.base import Link, Provider, ProviderError, ProviderResult, RateLimiter, clean_text, dedupe, dedupe_links, dedupe_names

API = "https://graphql.anilist.co"
PAGE_SIZE = 20
STATUS = {"RELEASING": "ongoing", "FINISHED": "completed", "HIATUS": "hiatus", "CANCELLED": "cancelled"}
AUTHOR_ROLES = ("story", "art", "original creator")

FIELDS = """
  id idMal siteUrl status chapters genres synonyms
  title { romaji english native }
  coverImage { large }
  description(asHtml: false)
  staff(perPage: 6, sort: RELEVANCE) { edges { role node { name { full } } } }
  externalLinks { site url type }
"""
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
        genres=dedupe(m.get("genres") or []),
        status=STATUS.get(m.get("status") or "", "unknown"),
        latest_chapter=m.get("chapters") if isinstance(m.get("chapters"), int) else None,
        links=dedupe_links(links),
        external_ids=external_ids,
    )


class AniList(Provider):
    name = "anilist"
    label = "AniList"
    limiter = RateLimiter(per_second=1)

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
