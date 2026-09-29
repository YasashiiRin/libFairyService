"""Search across the web (GET /api/discover/search): ask every provider at once, merge, mark what's saved.

Provider answers are cached for SEARCH_CACHE_TTL_HOURS in search_cache; nothing becomes a work
until a user saves it (POST /api/library).
"""

import asyncio
import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import LibraryItem, SearchCache, Work, WorkSource
from app.providers import PROVIDERS, ProviderResult, get_provider
from app.providers.base import dedupe, dedupe_links, dedupe_names, fold, name_key
from app.providers.http import new_client
from app.schemas.discover import DiscoverLink, DiscoverPage, DiscoverResult, ProviderCredit
from app.services.urls import identifies_work, normalize_url

logger = logging.getLogger(__name__)
INDEXED_IDS = ("mangadex", "anilist")  # external ids with a unique index on works


# ── Cache ────────────────────────────────────────────────────────────────


def cache_key(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip().lower()


async def cache_get(db: AsyncSession, key: str) -> Any | None:
    return await db.scalar(select(SearchCache.results).where(SearchCache.key == key, SearchCache.expires_at > func.now()))


async def cache_put(db: AsyncSession, key: str, provider: str, results: Any) -> None:
    expires = datetime.now(UTC) + timedelta(hours=get_settings().search_cache_ttl_hours)
    stmt = insert(SearchCache).values(key=key, provider=provider, results=results, expires_at=expires)
    await db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"results": stmt.excluded.results, "expires_at": expires, "created_at": func.now()}))


async def delete_expired_cache(db: AsyncSession) -> int:
    result = await db.execute(delete(SearchCache).where(SearchCache.expires_at <= func.now()))
    return result.rowcount or 0


# ── Merging duplicates ───────────────────────────────────────────────────


def _id_keys(r: ProviderResult) -> set[str]:
    return {f"{provider}:{value}" for provider, value in {**r.external_ids, r.provider: r.external_id}.items() if value}


def _titles(r: ProviderResult) -> set[str]:
    return {t for t in (fold(x) for x in [r.title, *r.alt_titles]) if t}


def _same_work(a: ProviderResult, b: ProviderResult) -> bool:
    """Same external id, or the same normalized title and a shared author (or no authors to compare)."""
    if _id_keys(a) & _id_keys(b):
        return True
    if not (_titles(a) & _titles(b)):
        return False
    authors_a, authors_b = {name_key(x) for x in a.authors}, {name_key(x) for x in b.authors}
    return not authors_a or not authors_b or bool(authors_a & authors_b)


def interleave(lists: list[list[ProviderResult]]) -> list[ProviderResult]:
    """Best first from every provider, then the second best, …"""
    out: list[ProviderResult] = []
    for rank in range(max((len(x) for x in lists), default=0)):
        out.extend(x[rank] for x in lists if rank < len(x))
    return out


def merge(results: list[ProviderResult]) -> list[tuple[ProviderResult, list[str]]]:
    """Group duplicates; each group becomes one result, at the rank of its best-ranked member.
    → [(merged result, providers that returned it)]"""
    groups: list[list[ProviderResult]] = []
    for r in results:
        group = next((g for g in groups if any(_same_work(r, other) for other in g)), None)
        if group is None:
            groups.append([r])
        else:
            group.append(r)
    return [(merge_group(g), dedupe([r.provider for r in g])) for g in groups]


def merge_group(group: list[ProviderResult]) -> ProviderResult:
    """One result from duplicates. The lead (whose title, id and cover are used) is the member from the
    first provider in PROVIDERS order: MangaDex, a reading site, before AniList, a catalogue."""
    order = list(PROVIDERS)
    lead = min(group, key=lambda r: order.index(r.provider) if r.provider in order else len(order))
    # the lead's links first (MangaDex's own page leads), then the others', one per site
    links = dedupe_links([link for r in [lead, *(r for r in group if r is not lead)] for link in r.links])
    chapters = [r.latest_chapter for r in group if r.latest_chapter is not None]
    external_ids: dict[str, str] = {}
    for r in group:
        for provider, value in {**r.external_ids, r.provider: r.external_id}.items():
            external_ids.setdefault(provider, value)
    return ProviderResult(
        provider=lead.provider,
        external_id=lead.external_id,
        title=lead.title,
        alt_titles=[t for t in dedupe([x for r in group for x in [r.title, *r.alt_titles]]) if t != lead.title],
        description=next((r.description for r in group if r.description), ""),
        cover_url=next((r.cover_url for r in group if r.cover_url), ""),
        authors=dedupe_names([a for r in [lead, *group] for a in r.authors]),
        genres=dedupe([g for r in group for g in r.genres]),
        status=next((r.status for r in group if r.status != "unknown"), "unknown"),
        latest_chapter=max(chapters) if chapters else None,
        links=links,
        external_ids=external_ids,
    )


# ── Ranking ──────────────────────────────────────────────────────────────

EXACT, NEAR_EXACT, OTHER = 2, 1, 0
_ARTICLES = ("the ", "a ", "an ")
# where a title's main part ends: "Frieren: Beyond Journey's End", "Solo Leveling - Ragnarok", "Dandadan (Official)"
_SUBTITLE = re.compile(r"\s*(?::|\s[-–—~]\s|\(|\[|【)")


def _typos_allowed(text: str) -> int:
    """Small typos a near-exact match may have: none in short titles, one from 5 letters, two from 10."""
    return 0 if len(text) < 5 else 1 if len(text) < 10 else 2


def _edits(a: str, b: str, limit: int) -> int:
    """Edit distance counting a swap of neighbours as one edit ("peice" / "piece"); stops above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _variants(title: str) -> set[str]:
    """A title as it is compared: folded, and its main part without a subtitle."""
    out = {fold(title)}
    main = _SUBTITLE.split(title, maxsplit=1)[0]
    if main.strip():
        out.add(fold(main))
    return out - {""}


def _without_article(text: str) -> str:
    return next((text.removeprefix(a) for a in _ARTICLES if text.startswith(a)), text)


def title_match(query: str, r: ProviderResult) -> int:
    """EXACT when a title (the main one or any alternative) is the query, NEAR_EXACT when it is the
    query up to spacing, a leading article, a subtitle or a small typo; OTHER otherwise."""
    q = fold(query)
    if not q:
        return OTHER
    titles = [t for t in [r.title, *r.alt_titles] if t]
    if any(fold(t) == q for t in titles):
        return EXACT
    q_bare = _without_article(q).replace(" ", "")
    typos = _typos_allowed(q_bare)
    for t in titles:
        for v in _variants(t):
            bare = _without_article(v).replace(" ", "")
            if bare == q_bare or (typos and _edits(bare, q_bare, typos) <= typos):
                return NEAR_EXACT
    return OTHER


def rank(query: str, merged: list[tuple[ProviderResult, list[str]]]) -> list[tuple[ProviderResult, list[str]]]:
    """Exact title matches first, then near-exact ones, then the rest; within each, the providers'
    own order (interleaved) is kept. So "frieren" puts Sousou no Frieren (alt title "Frieren: Beyond
    Journey's End") above an anthology the providers rank higher."""
    return sorted(merged, key=lambda pair: -title_match(query, pair[0]))  # sorted() is stable


# ── Search ───────────────────────────────────────────────────────────────


async def _ask(provider_name: str, client, query: str, page: int) -> list[ProviderResult]:
    provider = get_provider(provider_name, client)
    return await asyncio.wait_for(provider.search(query, page), timeout=get_settings().provider_timeout_seconds)


async def search_providers(db: AsyncSession, query: str, page: int) -> tuple[list[list[ProviderResult]], list[str]]:
    """Every provider's results (cached or fresh) → (lists in provider order, failed provider names)."""
    names = list(PROVIDERS)
    q = normalize_query(query)
    keys = {name: cache_key("search", name, q, page) for name in names}
    lists: dict[str, list[ProviderResult]] = {}
    rows = await db.execute(
        select(SearchCache.key, SearchCache.results).where(SearchCache.key.in_(keys.values()), SearchCache.expires_at > func.now())
    )  # every provider's cached answer in one round trip
    cached = dict(rows.all())
    for name in names:
        if keys[name] in cached:
            lists[name] = [ProviderResult.from_json(r) for r in cached[keys[name]]]

    missing = [n for n in names if n not in lists]
    failed: list[str] = []
    if missing:
        async with new_client() as client:
            answers = await asyncio.gather(*(_ask(n, client, query, page) for n in missing), return_exceptions=True)
        for name, answer in zip(missing, answers, strict=True):
            if isinstance(answer, BaseException):
                logger.warning("provider %s skipped: %r", name, answer)
                failed.append(name)
            else:
                lists[name] = answer
                await cache_put(db, keys[name], name, [r.to_json() for r in answer])
    return [lists.get(n, []) for n in names], failed


async def _library_matches(
    db: AsyncSession, user_id: uuid.UUID, results: list[ProviderResult]
) -> tuple[dict[str, str], dict[str, str]]:
    """The user's library items matching these results → ({"mangadex:<id>": item id}, {page url: item id})."""
    by_id: dict[str, str] = {}
    ids = {p: sorted({r.external_ids.get(p) for r in results} - {None}) for p in INDEXED_IDS}
    conds = [Work.external_ids[p].astext.in_(values) for p, values in ids.items() if values]
    if conds:
        rows = await db.execute(
            select(LibraryItem.id, Work.external_ids)
            .join(Work, Work.id == LibraryItem.work_id)
            .where(LibraryItem.user_id == user_id, or_(*conds))
        )
        for item_id, external_ids in rows.all():
            for p in INDEXED_IDS:
                if external_ids.get(p):
                    by_id[f"{p}:{external_ids[p]}"] = str(item_id)

    by_url: dict[str, str] = {}
    urls = sorted({normalize_url(link.url) for r in results for link in r.links if identifies_work(link.url)})
    if urls:
        rows = await db.execute(
            select(LibraryItem.id, WorkSource.url)
            .join(WorkSource, WorkSource.work_id == LibraryItem.work_id)
            .where(LibraryItem.user_id == user_id, WorkSource.url.in_(urls))
        )
        by_url = {url: str(item_id) for item_id, url in rows.all()}
    return by_id, by_url


async def search(db: AsyncSession, user_id: uuid.UUID, query: str, page: int) -> DiscoverPage:
    lists, failed = await search_providers(db, query, page)
    merged = rank(query, merge(interleave(lists)))
    by_id, by_url = await _library_matches(db, user_id, [r for r, _ in merged])

    results = []
    for r, providers in merged:
        item_id = next((by_id[k] for k in _id_keys(r) if k in by_id), None) or next(
            (by_url[normalize_url(link.url)] for link in r.links if normalize_url(link.url) in by_url), None
        )
        results.append(
            DiscoverResult(
                provider=r.provider,
                provider_name=PROVIDERS[r.provider].label,
                external_id=r.external_id,
                external_ids=r.external_ids,
                providers=providers,
                attribution=_credits(r, providers),
                title=r.title,
                alt_titles=r.alt_titles,
                description=r.description,
                cover_url=r.cover_url,
                authors=r.authors,
                genres=r.genres,
                status=r.status,  # type: ignore[arg-type]
                latest_chapter=r.latest_chapter,
                links=[DiscoverLink(site_name=link.site_name, url=link.url) for link in r.links],
                in_library=item_id is not None,
                library_item_id=item_id,
            )
        )
    return DiscoverPage(
        query=query,
        page=page,
        results=results,
        providers=list(PROVIDERS),
        provider_names={name: cls.label for name, cls in PROVIDERS.items()},
        providers_failed=failed,
    )


def _credits(r: ProviderResult, providers: list[str]) -> list[ProviderCredit]:
    """Every provider that returned the result, the lead first, each linking to the work's page there."""
    ordered = [r.provider, *(p for p in providers if p != r.provider)]
    return [
        ProviderCredit(provider=p, name=PROVIDERS[p].label, url=PROVIDERS[p].page_url(r.external_ids.get(p) or r.external_id))
        for p in ordered
        if p in PROVIDERS and (r.external_ids.get(p) or p == r.provider)
    ]
