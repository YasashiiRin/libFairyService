"""POST /api/library: save a comic from a search result, from a link, or entered by hand.

Finds the work (by external id, then by source link) or creates it, fetching details and the cover
once, attaches its sources and creates the user's library item. Idempotent: a work the user
already has returns their existing item.
"""

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import exists, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import LibraryItem, Work, WorkSource
from app.providers import PROVIDERS, ProviderError, ProviderResult, get_provider, provider_for_link
from app.providers.http import new_client
from app.schemas.comic import ComicCreate
from app.schemas.discover import LibraryAddIn
from app.services import comics, feed, images
from app.services.discover import INDEXED_IDS, cache_get, cache_key, cache_put, merge_group
from app.services.urls import identifies_work, normalize_url

logger = logging.getLogger(__name__)
COVER_MAX_BYTES = 5 * 1024 * 1024


def unknown_provider() -> ApiError:
    return ApiError(422, "Nguồn tìm kiếm này chưa được hỗ trợ", "unknown_provider")


def work_not_found() -> ApiError:
    return ApiError(404, "Không tìm thấy truyện này ở nguồn tìm kiếm", "work_not_found")


def provider_unavailable() -> ApiError:
    return ApiError(503, "Nguồn tìm kiếm đang bận, nàng thử lại sau một chút nhé", "provider_unavailable")


def link_not_supported() -> ApiError:
    return ApiError(
        422,
        "Chưa đọc được thông tin từ liên kết này. Nàng tìm theo tên hoặc nhập tay giúp nhé.",
        "link_not_supported",
    )


# ── Details (cached like searches) ───────────────────────────────────────


async def _details(db: AsyncSession, client: httpx.AsyncClient, provider_name: str, external_id: str) -> ProviderResult | None:
    key = cache_key("details", provider_name, external_id)
    cached = await cache_get(db, key)
    if cached is not None:
        return ProviderResult.from_json(cached) if cached else None
    provider = get_provider(provider_name, client)
    result = await asyncio.wait_for(provider.get_details(external_id), timeout=get_settings().provider_timeout_seconds * 2)
    await cache_put(db, key, provider_name, result.to_json() if result else {})
    return result


async def fetch_work_details(db: AsyncSession, provider_name: str, external_id: str) -> ProviderResult:
    """The provider's details, enriched by the other providers that know the same work."""
    if provider_name not in PROVIDERS:
        raise unknown_provider()
    async with new_client() as client:
        try:
            lead = await _details(db, client, provider_name, external_id)
        except (ProviderError, asyncio.TimeoutError) as exc:
            logger.warning("details from %s failed: %r", provider_name, exc)
            raise provider_unavailable() from exc
        if lead is None:
            raise work_not_found()
        group = [lead]
        for other, other_id in lead.external_ids.items():
            if other != provider_name and other in PROVIDERS:
                try:  # enrichment only: a failure here doesn't stop the save
                    extra = await _details(db, client, other, other_id)
                except (ProviderError, asyncio.TimeoutError) as exc:
                    logger.info("enrichment from %s skipped: %r", other, exc)
                    continue
                if extra is not None:
                    group.append(extra)
        details = merge_group(group)
        details.cover_url = lead.cover_url or details.cover_url
        return details


async def _download_cover(url: str) -> bytes | None:
    """The cover, fetched once and stored as our own WebP thumbnail; None if it can't be fetched."""
    if not url:
        return None
    try:
        async with new_client() as client, client.stream("GET", url) as res:
            if res.status_code != 200:
                return None
            data = bytearray()
            async for chunk in res.aiter_bytes():
                data.extend(chunk)
                if len(data) > COVER_MAX_BYTES:
                    return None
        return images.cover_thumbnail(bytes(data))
    except (httpx.HTTPError, ApiError) as exc:
        logger.info("cover not stored (%s): %r", url, exc)
        return None


# ── Finding or creating the work ─────────────────────────────────────────


def _id_match(work: Work, details: ProviderResult) -> bool:
    return any(details.external_ids.get(p) and (work.external_ids or {}).get(p) == details.external_ids[p] for p in INDEXED_IDS)


async def _find_work(db: AsyncSession, details: ProviderResult) -> Work | None:
    """The shared work with one of these external ids, else one that has one of these page links.
    One query for every candidate (and one for their sources), whatever the number of links."""
    ids = [Work.external_ids[p].astext == details.external_ids[p] for p in INDEXED_IDS if details.external_ids.get(p)]
    links = [u for u in dict.fromkeys(normalize_url(link.url) for link in details.links) if identifies_work(u)]
    has_link = (
        exists().where(WorkSource.work_id == Work.id, WorkSource.url.in_(links), WorkSource.private_owner_id.is_(None))
        if links
        else false()
    )
    if not ids and not links:
        return None
    candidates = list(
        await db.scalars(select(Work).where(Work.owner_user_id.is_(None), or_(*ids, has_link)).options(selectinload(Work.sources)))
    )
    by_id = next((w for w in candidates if _id_match(w, details)), None)
    if by_id is not None:
        return by_id
    for url in links:  # in the provider's link order
        work = next((w for w in candidates if any(s.url == url for s in w.sources)), None)
        if work is not None:
            return work
    return None


async def _add_links(db: AsyncSession, work: Work, details: ProviderResult, user_id: uuid.UUID, *, checked: bool = False) -> None:
    """Attach the provider's links the work doesn't have (links owned by another work are skipped).
    `checked`: _find_work just found no shared work with these links, so none is taken."""
    have = {s.url for s in work.sources}
    wanted = [normalize_url(link.url) for link in details.links]
    missing = [u for u in wanted if u not in have and identifies_work(u)]
    taken: set[str] = set()
    if missing and not checked:  # page links another shared work already has, in one query
        taken = set(await db.scalars(select(WorkSource.url).where(WorkSource.url.in_(missing), WorkSource.private_owner_id.is_(None))))
    for link, url in zip(details.links, wanted, strict=True):
        if url in have or url in taken:
            continue
        src = comics.new_source(work, user_id)
        src.site_name, src.url, src.chapter_url, src.position = link.site_name[:100], url, url, len(work.sources)
        work.sources.append(src)
        have.add(url)


def _enrich(work: Work, details: ProviderResult) -> None:
    """A work others saved: add what it lacks (ids, genres, alt titles, a newer chapter), never overwrite."""
    ids = dict(work.external_ids or {})
    for provider, value in details.external_ids.items():
        ids.setdefault(provider, value)
    work.external_ids = ids
    work.alt_titles = [t for t in dict.fromkeys([*work.alt_titles, *details.alt_titles, details.title]) if t != work.title]
    work.genres = list(dict.fromkeys([*work.genres, *details.genres]))
    work.authors = work.authors or details.authors
    work.description = work.description or details.description
    if work.publication_status == "unknown":
        work.publication_status = details.status
    work.latest_chapter = max(work.latest_chapter, details.latest_chapter or 0)


async def work_from_details(db: AsyncSession, details: ProviderResult, user_id: uuid.UUID) -> tuple[Work, bool]:
    """→ (work with its sources loaded, created)."""
    work = await _find_work(db, details)
    if work is not None:
        _enrich(work, details)
        await _add_links(db, work, details, user_id)
        return work, False

    cover = await _download_cover(details.cover_url)
    work = Work(
        title=details.title[:300],
        alt_titles=details.alt_titles,
        description=details.description,
        authors=details.authors,
        genres=details.genres,
        publication_status=details.status,
        latest_chapter=details.latest_chapter or 0,
        external_ids=details.external_ids,
        owner_user_id=None,  # from a provider: shared
        sources=[],
    )
    comics.set_work_cover(work, cover if cover else details.cover_url)
    await _add_links(db, work, details, user_id, checked=True)
    try:
        async with db.begin_nested():  # someone saving the same work at the same moment
            db.add(work)
            await db.flush()  # the work and its sources, in one savepoint
    except IntegrityError:
        found = await _find_work(db, details)
        if found is None:
            raise
        _enrich(found, details)
        await _add_links(db, found, details, user_id)
        return found, False
    return work, True


# ── The library item ─────────────────────────────────────────────────────


async def _new_item(db: AsyncSession, user_id: uuid.UUID, work: Work, data: LibraryAddIn) -> LibraryItem:
    """A library item for a work whose sources are loaded (and flushed, so they have ids)."""
    await db.flush()
    item = LibraryItem(
        user_id=user_id,
        work=work,
        status=data.status or ("reading" if data.current_chapter else "plan_to_read"),
        current_chapter=data.current_chapter or 0,
        primary_source_id=min(work.sources, key=lambda s: s.position).id if work.sources else None,
        hidden_source_ids=[],
        source_overrides={},
        tag_links=[],
        shelf_links=[],
    )
    db.add(item)
    await db.flush()
    comics.note_progress(item)
    await comics.set_shelves(db, item, data.shelf_ids or [])
    return await comics.reload(db, item)


async def add(db: AsyncSession, user_id: uuid.UUID, data: LibraryAddIn) -> tuple[LibraryItem, bool]:
    """→ (library item, created)."""
    if data.manual is not None:
        manual = data.manual.model_copy(
            update={k: v for k, v in (("status", data.status), ("current_chapter", data.current_chapter), ("shelf_ids", data.shelf_ids)) if v is not None}
        )
        return await comics.create_comic(db, user_id, ComicCreate.model_validate(manual.model_dump()))

    if data.url is not None:
        url = normalize_url(data.url)
        work = await comics.work_with_links(db, user_id, [url])
        if work is not None:
            existing = await comics.item_for_work(db, user_id, work)
            return (existing, False) if existing else (await _new_item(db, user_id, work, data), True)
        known = provider_for_link(url)
        if known is None:
            raise link_not_supported()
        provider_name, external_id = known
    else:
        provider_name, external_id = data.provider, data.external_id  # type: ignore[assignment]

    details = await fetch_work_details(db, provider_name, external_id)
    work, created = await work_from_details(db, details, user_id)
    if not created:  # a brand-new work has no library items yet
        existing = await comics.item_for_work(db, user_id, work)
        if existing is not None:
            return existing, False
    item = await _new_item(db, user_id, work, data)
    await feed.forget_saved(db, user_id, {**(work.external_ids or {}), provider_name: external_id})
    return item, True
