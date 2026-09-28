"""/api/comics (api-contract.md#comics). Every query is scoped to the signed-in user."""

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.deps import CurrentAuth, Db
from app.schemas.comic import (
    ComicCreate,
    ComicLookup,
    ComicOut,
    ComicPage,
    ComicStatus,
    ComicSummary,
    ComicUpdate,
    PrimarySourceIn,
    ProgressIn,
    SearchFacets,
    SortOption,
    SourceIn,
    SourceUpdate,
)
from app.services import comics as svc
from app.services import images

router = APIRouter(prefix="/comics", tags=["comics"])


@router.get("")
async def list_comics(
    auth: CurrentAuth,
    db: Db,
    q: str | None = Query(default=None, max_length=200),
    status: list[ComicStatus] = Query(default=[]),
    tag: list[str] = Query(default=[]),
    source: list[str] = Query(default=[]),
    shelf: list[str] = Query(default=[]),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    progress_min: float | None = Query(default=None, ge=0, le=100),
    progress_max: float | None = Query(default=None, ge=0, le=100),
    has_new_chapter: bool | None = None,
    has_broken_link: bool | None = None,
    is_favorite: bool | None = None,
    sort: SortOption | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ComicPage:
    """GET /api/comics?q=&status=&tag=&source=&shelf=&min_rating=&progress_min=&progress_max=
    &has_new_chapter=&has_broken_link=&is_favorite=&sort=&page=&page_size= → Paginated<Comic>
    (array filters are repeated keys and mean "any of")"""
    filters = svc.ListFilters(
        q=q,
        status=status,
        tag=tag,
        source=source,
        shelf=shelf,
        min_rating=min_rating,
        progress_min=progress_min,
        progress_max=progress_max,
        has_new_chapter=has_new_chapter,
        has_broken_link=has_broken_link,
        is_favorite=is_favorite,
    )
    items, total = await svc.list_comics(db, auth.user.id, filters, sort, page, page_size)
    return ComicPage(items=[svc.comic_out(c) for c in items], total=total, page=page, page_size=page_size)


@router.get("/facets", response_model_exclude_none=True)  # FacetOption.icon is optional, not null
async def facets(auth: CurrentAuth, db: Db) -> SearchFacets:
    """GET /api/comics/facets → SearchFacets (filter options with counts over the whole library)"""
    return await svc.facets(db, auth.user.id)


@router.get("/summary")
async def summary(auth: CurrentAuth, db: Db) -> ComicSummary:
    """GET /api/comics/summary → { total, by_status, new_chapters }"""
    return ComicSummary(**await svc.summary(db, auth.user.id))


@router.get("/lookup")
async def lookup(auth: CurrentAuth, db: Db, title: str = Query(max_length=300)) -> ComicLookup:
    """GET /api/comics/lookup?title= → { comic: Comic | null } (exact title, case-insensitive)"""
    comic = await svc.lookup(db, auth.user.id, title)
    return ComicLookup(comic=svc.comic_out(comic) if comic else None)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_comic(data: ComicCreate, auth: CurrentAuth, db: Db, response: Response) -> ComicOut:
    """POST /api/comics body ComicCreate → 201 Comic
    (200 with the existing Comic when one of its links is a work the user already saved)
    404 shelf_not_found; 409 source_url_taken; 422 validation_error"""
    comic, created = await svc.create_comic(db, auth.user.id, data)
    await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return svc.comic_out(comic)


@router.get("/{comic_id}")
async def get_comic(comic_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """GET /api/comics/{id} → Comic; 404 comic_not_found"""
    return svc.comic_out(await svc.get_comic(db, auth.user.id, comic_id))


@router.patch("/{comic_id}")
async def update_comic(comic_id: str, data: ComicUpdate, auth: CurrentAuth, db: Db) -> ComicOut:
    """PATCH /api/comics/{id} body ComicUpdate → Comic (sources, tags, shelf_ids replace the whole list)
    404 comic_not_found | source_not_found | shelf_not_found; 409 shared_work | source_url_taken; 422 validation_error"""
    comic = await svc.update_comic(db, await svc.get_comic(db, auth.user.id, comic_id), data)
    await db.commit()
    return svc.comic_out(comic)


@router.delete("/{comic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comic(comic_id: str, auth: CurrentAuth, db: Db) -> Response:
    """DELETE /api/comics/{id} → 204; 404 comic_not_found"""
    await svc.delete_comic(db, await svc.get_comic(db, auth.user.id, comic_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{comic_id}/favorite")
async def mark_favorite(comic_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """PUT /api/comics/{id}/favorite → Comic (idempotent)"""
    comic = await svc.set_favorite(db, await svc.get_comic(db, auth.user.id, comic_id), True)
    await db.commit()
    return svc.comic_out(comic)


@router.delete("/{comic_id}/favorite")
async def unmark_favorite(comic_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """DELETE /api/comics/{id}/favorite → Comic (idempotent)"""
    comic = await svc.set_favorite(db, await svc.get_comic(db, auth.user.id, comic_id), False)
    await db.commit()
    return svc.comic_out(comic)


@router.put("/{comic_id}/progress")
async def update_progress(comic_id: str, data: ProgressIn, auth: CurrentAuth, db: Db) -> ComicOut:
    """PUT /api/comics/{id}/progress body { current_chapter } → Comic
    (clamped to 0..total_chapters; "completed" at the end; logs the chapters read today)"""
    comic = await svc.set_progress(db, await svc.get_comic(db, auth.user.id, comic_id), data.current_chapter)
    await db.commit()
    return svc.comic_out(comic)


# ── Sources ──────────────────────────────────────────────────────────────


@router.post("/{comic_id}/sources", status_code=status.HTTP_201_CREATED)
async def add_source(comic_id: str, data: SourceIn, auth: CurrentAuth, db: Db) -> ComicOut:
    """POST /api/comics/{id}/sources body Source → 201 Comic (the first source becomes primary)"""
    comic = await svc.add_source(db, await svc.get_comic(db, auth.user.id, comic_id), data)
    await db.commit()
    return svc.comic_out(comic)


@router.patch("/{comic_id}/sources/{source_id}")
async def update_source(comic_id: str, source_id: str, data: SourceUpdate, auth: CurrentAuth, db: Db) -> ComicOut:
    """PATCH /api/comics/{id}/sources/{source_id} body Partial<Source> → Comic; 404 source_not_found"""
    comic = await svc.update_source(db, await svc.get_comic(db, auth.user.id, comic_id), source_id, data)
    await db.commit()
    return svc.comic_out(comic)


@router.delete("/{comic_id}/sources/{source_id}")
async def delete_source(comic_id: str, source_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """DELETE /api/comics/{id}/sources/{source_id} → Comic (a removed primary passes to the first remaining source)"""
    comic = await svc.delete_source(db, await svc.get_comic(db, auth.user.id, comic_id), source_id)
    await db.commit()
    return svc.comic_out(comic)


@router.put("/{comic_id}/primary-source")
async def set_primary_source(comic_id: str, data: PrimarySourceIn, auth: CurrentAuth, db: Db) -> ComicOut:
    """PUT /api/comics/{id}/primary-source body { source_id } → Comic; 404 source_not_found"""
    comic = await svc.set_primary_source(db, await svc.get_comic(db, auth.user.id, comic_id), data.source_id)
    await db.commit()
    return svc.comic_out(comic)


# ── Cover ────────────────────────────────────────────────────────────────


@router.put("/{comic_id}/cover")
async def upload_cover(comic_id: str, auth: CurrentAuth, db: Db, file: UploadFile = File()) -> ComicOut:
    """PUT /api/comics/{id}/cover multipart { file } (image ≤ 4 MB) → Comic with the new cover_url
    400 invalid_file; 413 file_too_large. Stored as a WebP thumbnail (max 480×720)."""
    comic = await svc.get_comic(db, auth.user.id, comic_id)
    comic = await svc.upload_cover(db, comic, images.cover_thumbnail(await images.read_upload(file)))
    await db.commit()
    return svc.comic_out(comic)


@router.delete("/{comic_id}/cover")
async def delete_cover(comic_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """DELETE /api/comics/{id}/cover → Comic without a cover"""
    comic = await svc.update_comic(db, await svc.get_comic(db, auth.user.id, comic_id), ComicUpdate(cover_url=""))
    await db.commit()
    return svc.comic_out(comic)
