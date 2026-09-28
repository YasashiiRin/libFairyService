"""What every comic provider offers, and the pieces they share.

A provider only reads metadata and links (title, cover, authors, genres, status, latest chapter,
where to read). It never fetches chapter content. Add a site by writing one module with a
Provider subclass and registering it in app/providers/__init__.py.
"""

import asyncio
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

PUBLICATION_STATUSES = ("ongoing", "completed", "hiatus", "cancelled", "unknown")


@dataclass
class Link:
    site_name: str
    url: str


@dataclass
class ProviderResult:
    """One work as a provider describes it (search results and details share this shape)."""

    provider: str
    external_id: str
    title: str
    alt_titles: list[str] = field(default_factory=list)
    description: str = ""
    cover_url: str = ""
    authors: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    status: str = "unknown"  # one of PUBLICATION_STATUSES
    latest_chapter: int | None = None
    links: list[Link] = field(default_factory=list)  # where to read (reading sites, official pages)
    # every id this provider knows for the work, its own included: {"mangadex": …, "anilist": …, "mal": …}
    external_ids: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ProviderResult":
        return cls(**{**data, "links": [Link(**link) for link in data.get("links", [])]})


class ProviderError(Exception):
    """The provider answered badly or not at all. The search goes on without it."""


class RateLimited(ProviderError):
    pass


class RateLimiter:
    """At most one request per `interval` seconds, and a pause after a 429.

    Per server instance: on serverless each instance keeps its own pace. The search cache keeps the
    total far below the providers' limits.
    """

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._paused_until = 0.0

    async def wait(self) -> None:
        if time.monotonic() < self._paused_until:
            raise RateLimited("paused after a 429")
        async with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.interval

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)


class Provider:
    name: str = ""
    label: str = ""  # shown to users, e.g. "MangaDex"
    limiter: RateLimiter

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, query: str, page: int = 1) -> list[ProviderResult]:
        raise NotImplementedError

    async def get_details(self, external_id: str) -> ProviderResult | None:
        raise NotImplementedError

    async def latest_chapter(self, external_id: str) -> int | None:
        details = await self.get_details(external_id)
        return details.latest_chapter if details else None

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        await self.limiter.wait()
        try:
            res = await self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc.__class__.__name__}") from exc
        if res.status_code == 429:
            self.limiter.pause(_retry_after(res))
            raise RateLimited(f"{self.name}: 429")
        return res


def _retry_after(res: httpx.Response) -> float:
    try:
        return max(1.0, float(res.headers.get("retry-after", "60")))
    except ValueError:
        return 60.0


def chapter_number(value: Any) -> int | None:
    """ "120", "12.5", 88 → 120, 12, 88; "", None, "Oneshot" → None."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number >= 0 else None


def clean_text(value: str | None, limit: int = 5000) -> str:
    """Provider descriptions: drop HTML tags and markdown links, collapse blank lines."""
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def fold(text: str) -> str:
    """For comparing titles and names: no accents, no case, letters and digits only."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[^\W_]+", text))


def name_key(name: str) -> str:
    """People's names regardless of order: "Yamada Kanehito" (MangaDex) = "Kanehito Yamada" (AniList)."""
    return " ".join(sorted(fold(name).split()))


def dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        key = name_key(n or "")
        if key and key not in seen:
            seen.add(key)
            out.append(n.strip())
    return out


def site_key(url: str) -> str:
    return (urlsplit(url).hostname or url).lower().removeprefix("www.")


def dedupe_links(links: list["Link"]) -> list["Link"]:
    """One link per site (two providers often point at different pages of the same site)."""
    seen: set[str] = set()
    out = []
    for link in links:
        key = site_key(link.url)
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for v in values:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out
