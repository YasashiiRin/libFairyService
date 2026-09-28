"""Comic providers: one module per site. To add one, subclass Provider and list it here."""

import re

import httpx

from app.providers.anilist import AniList
from app.providers.base import Link, Provider, ProviderError, ProviderResult, RateLimited
from app.providers.mangadex import MangaDex

PROVIDERS: dict[str, type[Provider]] = {cls.name: cls for cls in (MangaDex, AniList)}

# links a provider can open directly: POST /api/library { url }
_LINK_PATTERNS = [
    ("mangadex", re.compile(r"^https?://(?:www\.)?mangadex\.org/title/([0-9a-f-]{36})", re.I)),
    ("anilist", re.compile(r"^https?://(?:www\.)?anilist\.co/manga/(\d+)", re.I)),
]


def get_provider(name: str, client: httpx.AsyncClient) -> Provider | None:
    cls = PROVIDERS.get(name)
    return cls(client) if cls else None


def provider_for_link(url: str) -> tuple[str, str] | None:
    """(provider, external_id) when a link is a provider's own page, e.g. mangadex.org/title/…"""
    for provider, pattern in _LINK_PATTERNS:
        match = pattern.match(url.strip())
        if match:
            return provider, match.group(1).lower()
    return None


__all__ = ["PROVIDERS", "Link", "Provider", "ProviderError", "ProviderResult", "RateLimited", "get_provider", "provider_for_link"]
