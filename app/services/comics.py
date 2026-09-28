"""The API's comics (api-contract.md#comics), built from shared works + per-user library items.

A Comic is one LibraryItem (its id is the Comic id) joined with its Work:
- per user (library_items): status, current_chapter, rating, is_favorite, note, has_new_chapter,
  primary_source_id, last_read_at, shelf_ids, and the user's own tags;
- shared (works / work_sources): title, author, description, cover, total_chapters (= latest_chapter),
  genres (added to `tags`) and sources.

Shared fields can be changed only while nobody else relies on them: the work is private (entered
by hand) or this user is the only one who saved it. Otherwise → 409 shared_work. Adding a source
is always allowed (it only adds information). Every query is scoped to one user.
"""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, bindparam, case, delete, exists, false, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import Text

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import LibraryItem, LibraryItemTag, ReadingLog, Shelf, ShelfItem, Tag, Work, WorkSource
from app.schemas.comic import (
    ComicCreate,
    ComicOut,
    ComicStatus,
    ComicUpdate,
    FacetOption,
    SearchFacets,
    SortOption,
    SourceIn,
    SourceOut,
    SourceUpdate,
)
from app.services import images
from app.services.urls import identifies_work, normalize_url

ALL_SHELF = "all"  # the virtual shelf every comic is on (never stored)
VI_COLLATION = "vi-x-icu"
STATUS_LABELS: dict[str, str] = {
    "reading": "Đang đọc",
    "completed": "Đã đọc xong",
    "plan_to_read": "Muốn đọc",
    "on_hold": "Tạm dừng",
    "dropped": "Bỏ dở",
}


def comic_not_found() -> ApiError:
    return ApiError(404, "Không tìm thấy truyện", "comic_not_found")


def source_not_found() -> ApiError:
    return ApiError(404, "Không tìm thấy nguồn đọc này", "source_not_found")


def shelf_not_found() -> ApiError:
    return ApiError(404, "Không tìm thấy kệ sách", "shelf_not_found")


def shared_work() -> ApiError:
    return ApiError(
        409,
        "Truyện này đang được nhiều người lưu, nên chưa sửa được thông tin chung (tên, tác giả, ảnh bìa, nguồn đọc). "
        "Nàng vẫn đổi được trạng thái, tiến độ, đánh giá, ghi chú và thẻ của riêng mình nhé.",
        "shared_work",
    )


def source_url_taken() -> ApiError:
    return ApiError(409, "Liên kết này đã gắn với một truyện khác", "source_url_taken")


def parse_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def split_authors(author: str) -> list[str]:
    return [a.strip() for a in author.split(",") if a.strip()]


# ── Loading and serialising ──────────────────────────────────────────────


def _with_relations(stmt: Select) -> Select:
    return stmt.options(
        selectinload(LibraryItem.work).selectinload(Work.sources),
        selectinload(LibraryItem.tag_links).joinedload(LibraryItemTag.tag),
        selectinload(LibraryItem.shelf_links),
    )


async def get_comic(db: AsyncSession, user_id: uuid.UUID, comic_id: str) -> LibraryItem:
    item_id = parse_uuid(comic_id)
    item = item_id and await db.scalar(
        _with_relations(select(LibraryItem).where(LibraryItem.id == item_id, LibraryItem.user_id == user_id))
    )
    if not item:
        raise comic_not_found()
    return item


async def reload(db: AsyncSession, item: LibraryItem) -> LibraryItem:
    """Fresh copy with relations, after changes (server defaults and orderings applied)."""
    await db.flush()
    return await db.scalar(
        _with_relations(select(LibraryItem).where(LibraryItem.id == item.id)).execution_options(populate_existing=True)
    )


def merge_tags(own: list[str], genres: list[str]) -> list[str]:
    """The API's tags: the user's own (in their order), then the work's genres not already there."""
    out, seen = list(own), {t.lower() for t in own}
    for genre in genres:
        if genre.lower() not in seen:
            seen.add(genre.lower())
            out.append(genre)
    return out


def source_out(s: WorkSource) -> SourceOut:
    return SourceOut(
        id=str(s.id),
        site_name=s.site_name,
        url=s.url,
        favicon_url=s.favicon_url,
        chapter_url=s.chapter_url,
        latest_chapter=s.latest_chapter,
        is_alive=s.is_alive,
        last_checked_at=s.last_checked_at,
    )


def cover_url_of(item: LibraryItem) -> str:
    work = item.work
    # stored once on the work, served under the item's own signed URL
    return images.media_url("cover", item.id, work.cover_updated_at) if work.cover_updated_at else work.cover_url


def comic_out(item: LibraryItem) -> ComicOut:
    work = item.work
    primary = item.primary_source_id or (work.sources[0].id if work.sources else None)
    return ComicOut(
        id=str(item.id),
        title=work.title,
        author=work.author,
        description=work.description,
        cover_url=cover_url_of(item),
        status=item.status,  # type: ignore[arg-type]
        current_chapter=min(item.current_chapter, work.latest_chapter),
        total_chapters=work.latest_chapter,
        rating=item.rating,
        is_favorite=item.is_favorite,
        note=item.note,
        tags=merge_tags([link.tag.name for link in item.tag_links], work.genres),
        sources=[source_out(s) for s in work.sources],
        primary_source_id=str(primary) if primary else "",
        shelf_ids=[ALL_SHELF, *(str(link.shelf_id) for link in sorted(item.shelf_links, key=lambda sl: sl.added_at))],
        has_new_chapter=item.has_new_chapter,
        last_read_at=item.last_read_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ── Listing and search ───────────────────────────────────────────────────


def _fold(expr) -> ColumnElement:
    """Case- and accent-insensitive form: "Đời Sống" → "doi song"."""
    return func.lower(func.unaccent(expr))


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _progress_pct() -> ColumnElement:
    return case(
        (Work.latest_chapter > 0, func.least(LibraryItem.current_chapter, Work.latest_chapter) * 100.0 / Work.latest_chapter),
        else_=0.0,
    )


def _joined(arr) -> ColumnElement:
    return func.array_to_string(arr, " | ")


class ListFilters:
    def __init__(
        self,
        *,
        q: str | None = None,
        status: list[ComicStatus] | None = None,
        tag: list[str] | None = None,
        source: list[str] | None = None,
        shelf: list[str] | None = None,
        min_rating: int | None = None,
        progress_min: float | None = None,
        progress_max: float | None = None,
        has_new_chapter: bool | None = None,
        has_broken_link: bool | None = None,
        is_favorite: bool | None = None,
    ):
        self.q = (q or "").strip()
        self.status = status or []
        self.tag = [t.strip() for t in tag or [] if t.strip()]
        self.source = [s.strip() for s in source or [] if s.strip()]
        self.shelf = [s for s in shelf or [] if s and s != ALL_SHELF]
        self.min_rating = min_rating
        self.progress_min = progress_min
        self.progress_max = progress_max
        self.has_new_chapter = has_new_chapter
        self.has_broken_link = has_broken_link
        self.is_favorite = is_favorite


def _own_tag_exists(*conds: ColumnElement) -> ColumnElement:
    return exists(
        select(1)
        .select_from(LibraryItemTag)
        .join(Tag, Tag.id == LibraryItemTag.tag_id)
        .where(LibraryItemTag.library_item_id == LibraryItem.id, *conds)
    )


def _text_match(q: str) -> tuple[ColumnElement, ColumnElement]:
    """(where clause, relevance score). Weights as in the mock: title prefix 6, title 4, author 3, tag 2, note 1."""
    folded = _fold(literal(_like_escape(q)))
    contains = func.concat("%", folded, "%")
    prefix = func.concat(folded, "%")
    title = _fold(Work.title)
    in_title = or_(title.like(contains, escape="\\"), _fold(_joined(Work.alt_titles)).like(contains, escape="\\"))
    in_author = _fold(_joined(Work.authors)).like(contains, escape="\\")
    in_note = _fold(LibraryItem.note).like(contains, escape="\\")
    in_tags = or_(
        _own_tag_exists(_fold(Tag.name).like(contains, escape="\\")),
        _fold(_joined(Work.genres)).like(contains, escape="\\"),
    )
    score = (
        case((title.like(prefix, escape="\\"), 6), (in_title, 4), else_=0)
        + case((in_author, 3), else_=0)
        + case((in_tags, 2), else_=0)
        + case((in_note, 1), else_=0)
    )
    return or_(in_title, in_author, in_tags, in_note), score


def _conditions(user_id: uuid.UUID, f: ListFilters) -> list[ColumnElement]:
    conds: list[ColumnElement] = [LibraryItem.user_id == user_id]
    if f.q:
        conds.append(_text_match(f.q)[0])
    if f.status:
        conds.append(LibraryItem.status.in_(f.status))
    if f.tag:
        lowered = [t.lower() for t in f.tag]
        genre_match = text("EXISTS (SELECT 1 FROM unnest(works.genres) AS g WHERE lower(g) = ANY(:tag_names))").bindparams(
            bindparam("tag_names", lowered, type_=ARRAY(Text))
        )
        conds.append(or_(_own_tag_exists(func.lower(Tag.name).in_(lowered)), genre_match))
    if f.source:
        conds.append(exists(select(1).where(WorkSource.work_id == LibraryItem.work_id, WorkSource.site_name.in_(f.source))))
    if f.shelf:
        shelf_ids = [sid for sid in (parse_uuid(s) for s in f.shelf) if sid]
        conds.append(
            exists(select(1).where(ShelfItem.library_item_id == LibraryItem.id, ShelfItem.shelf_id.in_(shelf_ids)))
            if shelf_ids
            else false()  # only unknown shelf ids: nothing matches
        )
    if f.min_rating:
        conds.append(LibraryItem.rating >= f.min_rating)
    if f.progress_min is not None and f.progress_min > 0:
        conds.append(_progress_pct() >= f.progress_min)
    if f.progress_max is not None and f.progress_max < 100:
        conds.append(_progress_pct() <= f.progress_max)
    if f.has_new_chapter:
        conds.append(LibraryItem.has_new_chapter.is_(True))
    if f.has_broken_link:
        conds.append(exists(select(1).where(WorkSource.work_id == LibraryItem.work_id, WorkSource.is_alive.is_(False))))
    if f.is_favorite:
        conds.append(LibraryItem.is_favorite.is_(True))
    return conds


async def list_comics(
    db: AsyncSession, user_id: uuid.UUID, f: ListFilters, sort: SortOption | None, page: int, page_size: int
) -> tuple[list[LibraryItem], int]:
    base = select(LibraryItem).join(Work, Work.id == LibraryItem.work_id).where(*_conditions(user_id, f))
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0

    sort = sort or ("relevance" if f.q else "updated_at")
    stmt = base
    newest = LibraryItem.updated_at.desc()
    if sort == "relevance":
        order = [_text_match(f.q)[1].desc(), newest] if f.q else [newest]
    elif sort == "title":
        order = [Work.title.collate(VI_COLLATION).asc()]
    elif sort == "rating":
        order = [LibraryItem.rating.desc(), newest]
    elif sort == "progress":
        order = [_progress_pct().desc(), newest]
    elif sort == "position":
        if len(f.shelf) != 1:
            raise ApiError(422, "Sắp xếp theo vị trí cần đúng một kệ (shelf)", "validation_error")
        # an alias: the shelf filter's EXISTS (shelf_items …) must not correlate to this join
        placed = aliased(ShelfItem)
        stmt = stmt.outerjoin(placed, and_(placed.library_item_id == LibraryItem.id, placed.shelf_id == parse_uuid(f.shelf[0])))
        order = [placed.position.asc().nulls_last(), newest]
    else:
        order = [newest]

    rows = await db.scalars(
        _with_relations(stmt.order_by(*order, LibraryItem.id).limit(page_size).offset((page - 1) * page_size))
    )
    return list(rows), total


async def summary(db: AsyncSession, user_id: uuid.UUID) -> dict:
    rows = (
        await db.execute(
            select(LibraryItem.status, func.count()).where(LibraryItem.user_id == user_id).group_by(LibraryItem.status)
        )
    ).all()
    by_status = {status: 0 for status in STATUS_LABELS} | {status: n for status, n in rows}
    new = await db.scalar(select(func.count()).where(LibraryItem.user_id == user_id, LibraryItem.has_new_chapter.is_(True)))
    return {"total": sum(by_status.values()), "by_status": by_status, "new_chapters": new or 0}


# the user's own tags and their works' genres, counted once per library item (case-insensitive)
_TAG_COUNTS = text("""
    SELECT min(name) AS name, count(DISTINCT item_id) AS n
    FROM (
        SELECT lit.library_item_id AS item_id, t.name
        FROM library_item_tags lit JOIN tags t ON t.id = lit.tag_id
        WHERE t.user_id = :user_id
        UNION ALL
        SELECT li.id, g
        FROM library_items li JOIN works w ON w.id = li.work_id, unnest(w.genres) AS g
        WHERE li.user_id = :user_id
    ) AS x
    GROUP BY lower(name)
    ORDER BY n DESC, min(name) COLLATE "vi-x-icu"
""")


async def list_tags(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[str, int]]:
    """Every tag in the library (the user's own ∪ genres), most used first."""
    return [(name, n) for name, n in (await db.execute(_TAG_COUNTS, {"user_id": user_id})).all()]


async def facets(db: AsyncSession, user_id: uuid.UUID) -> SearchFacets:
    status_counts = dict(
        (
            await db.execute(
                select(LibraryItem.status, func.count()).where(LibraryItem.user_id == user_id).group_by(LibraryItem.status)
            )
        ).all()
    )
    genres = await list_tags(db, user_id)
    sources = (
        await db.execute(
            select(WorkSource.site_name, func.count(func.distinct(LibraryItem.id)), func.max(WorkSource.favicon_url))
            .join(LibraryItem, LibraryItem.work_id == WorkSource.work_id)
            .where(LibraryItem.user_id == user_id)
            .group_by(WorkSource.site_name)
            .order_by(func.count(func.distinct(LibraryItem.id)).desc(), WorkSource.site_name)
        )
    ).all()
    shelves = (
        await db.execute(
            select(Shelf.id, Shelf.name, Shelf.icon, func.count(ShelfItem.library_item_id))
            .outerjoin(ShelfItem, ShelfItem.shelf_id == Shelf.id)
            .where(Shelf.user_id == user_id)
            .group_by(Shelf.id)
            .order_by(Shelf.position, Shelf.created_at)
        )
    ).all()
    return SearchFacets(
        total=sum(status_counts.values()),
        statuses=[FacetOption(value=s, label=label, count=status_counts.get(s, 0)) for s, label in STATUS_LABELS.items()],
        genres=[FacetOption(value=name, label=name, count=n) for name, n in genres],
        sources=[FacetOption(value=name, label=name, icon=icon or None, count=n) for name, n, icon in sources],
        shelves=[FacetOption(value=str(sid), label=name, icon=icon, count=n) for sid, name, icon, n in shelves],
    )


async def lookup(db: AsyncSession, user_id: uuid.UUID, title: str) -> LibraryItem | None:
    t = title.strip()
    if not t:
        return None
    return await db.scalar(
        _with_relations(
            select(LibraryItem)
            .join(Work, Work.id == LibraryItem.work_id)
            .where(LibraryItem.user_id == user_id, func.lower(func.btrim(Work.title)) == func.lower(t))
            .order_by(LibraryItem.created_at)
            .limit(1)
        )
    )


# ── Tags (the user's own) ────────────────────────────────────────────────


async def _set_tags(db: AsyncSession, item: LibraryItem, names: list[str]) -> None:
    """Replace the user's own tags. Names that are genres of the work are not stored twice."""
    genres = {g.lower() for g in item.work.genres}
    names = [n for n in names if n.lower() not in genres]
    tags: list[Tag] = []
    if names:
        existing = await db.scalars(
            select(Tag).where(Tag.user_id == item.user_id, func.lower(Tag.name).in_([n.lower() for n in names]))
        )
        by_name = {t.name.lower(): t for t in existing}
        for name in names:
            tag = by_name.get(name.lower())
            if tag is None:
                tag = by_name[name.lower()] = Tag(user_id=item.user_id, name=name)
                db.add(tag)
            tags.append(tag)
        await db.flush()

    # reuse link rows: deleting and re-inserting the same (item, tag) in one flush would collide
    links = {link.tag_id: link for link in item.tag_links}
    kept = []
    for position, tag in enumerate(tags):
        link = links.get(tag.id) or LibraryItemTag(tag=tag, tag_id=tag.id)
        link.position = position
        kept.append(link)
    item.tag_links = kept


async def delete_unused_tags(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.flush()
    await db.execute(
        delete(Tag).where(Tag.user_id == user_id, ~exists(select(1).where(LibraryItemTag.tag_id == Tag.id)))
    )


# ── Shelves (membership only; shelves themselves are managed in /api/shelves) ─


async def _set_shelves(db: AsyncSession, item: LibraryItem, shelf_ids: list[str]) -> None:
    wanted = list(dict.fromkeys(sid for sid in (parse_uuid(s) for s in shelf_ids) if sid))
    if wanted:
        owned = set(await db.scalars(select(Shelf.id).where(Shelf.user_id == item.user_id, Shelf.id.in_(wanted))))
        if len(owned) != len(wanted):
            raise shelf_not_found()
    links = {link.shelf_id: link for link in item.shelf_links}
    item.shelf_links = [links.get(sid) or ShelfItem(shelf_id=sid, position=None) for sid in wanted]


# ── Shared-work rules ────────────────────────────────────────────────────


async def holders(db: AsyncSession, work_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.work_id == work_id)) or 0


async def _require_editable(db: AsyncSession, item: LibraryItem) -> None:
    """Shared fields may change only if the work is this user's private work, or nobody else saved it."""
    if item.work.owner_user_id == item.user_id:
        return
    if await holders(db, item.work_id) > 1:
        raise shared_work()


async def _find_source_by_url(db: AsyncSession, url: str, owner_id: uuid.UUID | None) -> WorkSource | None:
    """The source with this (normalized) page link in one scope: shared works, or one user's private works.
    A bare site link never matches (it doesn't identify a work)."""
    if not identifies_work(url):
        return None
    scope = WorkSource.private_owner_id.is_(None) if owner_id is None else WorkSource.private_owner_id == owner_id
    return await db.scalar(select(WorkSource).where(WorkSource.url == url, scope))


async def _check_url_free(db: AsyncSession, work: Work, url: str, except_id: uuid.UUID | None = None) -> None:
    other = await _find_source_by_url(db, url, work.owner_user_id)
    if other is not None and other.id != except_id:
        raise source_url_taken()


def _apply_source(src: WorkSource, data: SourceIn | SourceUpdate) -> None:
    for field in ("site_name", "favicon_url", "chapter_url", "latest_chapter", "is_alive"):
        value = getattr(data, field)
        if value is not None:
            setattr(src, field, value)
    if data.url is not None:
        src.url = normalize_url(data.url)
    if not src.chapter_url:
        src.chapter_url = src.url


def _new_source(work: Work, user_id: uuid.UUID) -> WorkSource:
    return WorkSource(
        work_id=work.id,
        private_owner_id=work.owner_user_id,
        added_by_user_id=user_id,
        favicon_url="",
        chapter_url="",
        latest_chapter=0,
        is_alive=True,
    )


def _as_input(src: WorkSource, client_id: str | None = None) -> SourceIn:
    """A source as it would be sent back unchanged (optionally under the client's id)."""
    return SourceIn(
        id=client_id or str(src.id),
        site_name=src.site_name,
        url=src.url,
        favicon_url=src.favicon_url,
        chapter_url=src.chapter_url,
        latest_chapter=src.latest_chapter,
        is_alive=src.is_alive,
    )


def _source_changed(src: WorkSource, data: SourceIn) -> bool:
    chapter_url = normalize_url(data.chapter_url) if data.chapter_url else normalize_url(data.url)
    return (
        normalize_url(data.url) != src.url
        or data.site_name != src.site_name
        or chapter_url != normalize_url(src.chapter_url)
        or data.latest_chapter != src.latest_chapter
        or data.is_alive != src.is_alive
        or data.favicon_url != src.favicon_url
    )


async def _set_sources(db: AsyncSession, item: LibraryItem, entries: list[SourceIn], primary: str | None) -> None:
    """Replace the work's source list, then point this user's primary source.

    Existing sources are matched by id (or by link); new ones get server ids. `primary` may be an
    existing source id or the client id of a new one. Removing or editing sources of a work other
    people saved is refused (409); adding is fine.
    """
    work = item.work
    by_id = {str(s.id): s for s in work.sources}
    by_url = {s.url: s for s in work.sources}
    current_primary = str(item.primary_source_id) if item.primary_source_id else None

    resolved = [(e, by_id.get(e.id or "") or by_url.get(normalize_url(e.url))) for e in entries]
    kept = {m.id for _, m in resolved if m is not None}
    if any(s.id not in kept for s in work.sources) or any(m is not None and _source_changed(m, e) for e, m in resolved):
        await _require_editable(db, item)

    item.primary_source_id = None
    await db.flush()

    by_client_id: dict[str, WorkSource] = {}
    new_list: list[WorkSource] = []
    for entry, src in resolved:
        url = normalize_url(entry.url)
        if src is None:
            await _check_url_free(db, work, url)
            src = _new_source(work, item.user_id)
        elif url != src.url:
            await _check_url_free(db, work, url, except_id=src.id)
        _apply_source(src, entry)
        if src not in new_list:
            new_list.append(src)
        if entry.id:
            by_client_id[entry.id] = src
    for position, src in enumerate(new_list):
        src.position = position
    work.sources = new_list
    await db.flush()

    def pick() -> WorkSource | None:
        # the requested primary, else the current one if it was kept, else the first source
        for key in (primary, current_primary):
            if key:
                match = by_client_id.get(key) or next((s for s in new_list if str(s.id) == key), None)
                if match is not None:
                    return match
        return new_list[0] if new_list else None

    chosen = pick()
    item.primary_source_id = chosen.id if chosen else None
    work.updated_at = _now()


def _find_source(item: LibraryItem, source_id: str) -> WorkSource:
    src = next((s for s in item.work.sources if str(s.id) == source_id), None)
    if src is None:
        raise source_not_found()
    return src


async def add_source(db: AsyncSession, item: LibraryItem, data: SourceIn) -> LibraryItem:
    work = item.work
    url = normalize_url(data.url)
    if not any(s.url == url for s in work.sources):  # already there: nothing to add
        await _check_url_free(db, work, url)
        src = _new_source(work, item.user_id)
        src.position = len(work.sources)
        _apply_source(src, data)
        work.sources.append(src)
        work.updated_at = _now()
        await db.flush()
    item.updated_at = _now()
    return await reload(db, item)


async def update_source(db: AsyncSession, item: LibraryItem, source_id: str, data: SourceUpdate) -> LibraryItem:
    src = _find_source(item, source_id)
    await _require_editable(db, item)
    if data.url is not None and normalize_url(data.url) != src.url:
        await _check_url_free(db, item.work, normalize_url(data.url), except_id=src.id)
    _apply_source(src, data)
    item.work.updated_at = item.updated_at = _now()
    return await reload(db, item)


async def delete_source(db: AsyncSession, item: LibraryItem, source_id: str) -> LibraryItem:
    src = _find_source(item, source_id)
    await _require_editable(db, item)
    work = item.work
    remaining = [s for s in work.sources if s is not src]
    if item.primary_source_id == src.id:
        item.primary_source_id = None
        await db.flush()
    work.sources = remaining
    for position, s in enumerate(remaining):
        s.position = position
    await db.flush()
    if item.primary_source_id is None and remaining:
        item.primary_source_id = remaining[0].id
    work.updated_at = item.updated_at = _now()
    return await reload(db, item)


async def set_primary_source(db: AsyncSession, item: LibraryItem, source_id: str) -> LibraryItem:
    item.primary_source_id = _find_source(item, source_id).id  # per user: always allowed
    item.updated_at = _now()
    return await reload(db, item)


# ── Cover ────────────────────────────────────────────────────────────────


def set_cover_image(work: Work, webp: bytes) -> None:
    work.cover = webp
    work.cover_url = ""
    work.cover_updated_at = _now()


def _cover_change(value: str, current: str | None) -> bytes | str | None:
    """What a cover_url from the client means: WebP bytes to store, an external link, or None (unchanged)."""
    value = value.strip()
    if images.is_data_url(value):
        return images.cover_thumbnail(images.decode_data_url(value))
    if value == current:
        return None
    if value and not value.startswith(("http://", "https://")):
        raise ApiError(422, "Ảnh bìa cần là liên kết http(s) hoặc ảnh tải lên", "validation_error")
    return value


def _apply_cover(work: Work, change: bytes | str) -> None:
    if isinstance(change, bytes):
        set_cover_image(work, change)
    else:
        work.cover_url = change
        work.cover = None
        work.cover_updated_at = None


async def upload_cover(db: AsyncSession, item: LibraryItem, webp: bytes) -> LibraryItem:
    await _require_editable(db, item)
    set_cover_image(item.work, webp)
    item.updated_at = _now()
    return await reload(db, item)


# ── Create / update / delete ─────────────────────────────────────────────


async def _work_with_links(db: AsyncSession, user_id: uuid.UUID, urls: list[str]) -> Work | None:
    """A shared work (or one of the user's own private works) that already has one of these links."""
    work_ids = set()
    for url in urls:
        src = await _find_source_by_url(db, url, None) or await _find_source_by_url(db, url, user_id)
        if src is not None:
            work_ids.add(src.work_id)
    if len(work_ids) > 1:
        raise source_url_taken()  # the links belong to different works
    return await db.get(Work, work_ids.pop()) if work_ids else None


async def create_comic(db: AsyncSession, user_id: uuid.UUID, data: ComicCreate) -> tuple[LibraryItem, bool]:
    """Save a comic entered by hand → (item, created).

    A link that a saved work already has attaches the comic to that work; if the user already has
    that work, their existing item comes back unchanged. Otherwise a new private work is created.
    """
    work = await _work_with_links(db, user_id, [normalize_url(s.url) for s in data.sources])
    if work is not None:
        mine = await db.scalar(
            _with_relations(select(LibraryItem).where(LibraryItem.user_id == user_id, LibraryItem.work_id == work.id))
        )
        if mine is not None:
            return mine, False
        # joining a work others saved: add what it lacks, never overwrite what it has
        if data.title != work.title and data.title not in work.alt_titles:
            work.alt_titles = [*work.alt_titles, data.title]
        work.latest_chapter = max(work.latest_chapter, data.total_chapters)
        await db.refresh(work, ["sources"])
    else:
        work = Work(
            title=data.title,
            description=data.description,
            authors=split_authors(data.author),
            alt_titles=[],
            genres=[],
            external_ids={},
            latest_chapter=data.total_chapters,
            owner_user_id=user_id,  # entered by hand: private to its creator
            sources=[],
        )
        db.add(work)
        await db.flush()
        change = _cover_change(data.cover_url, None)
        if change:
            _apply_cover(work, change)

    item = LibraryItem(
        user_id=user_id,
        work=work,
        status=data.status,
        current_chapter=min(data.current_chapter, work.latest_chapter),
        rating=data.rating,
        is_favorite=data.is_favorite,
        note=data.note,
        has_new_chapter=data.has_new_chapter,
        last_read_at=data.last_read_at,
        tag_links=[],
        shelf_links=[],
    )
    db.add(item)
    await db.flush()
    await _set_tags(db, item, data.tags)
    await _set_shelves(db, item, data.shelf_ids)

    # keep the work's sources as they are; links it already has map onto them (under the client's id,
    # so primary_source_id still resolves); the rest are added
    by_url = {s.url: s for s in work.sources}
    entries = [_as_input(s) for s in work.sources]
    for entry in data.sources:
        known = by_url.get(normalize_url(entry.url))
        entries.append(_as_input(known, entry.id) if known else entry)
    await _set_sources(db, item, entries, data.primary_source_id)
    return await reload(db, item), True


async def update_comic(db: AsyncSession, item: LibraryItem, data: ComicUpdate) -> LibraryItem:
    fields = data.model_fields_set
    work = item.work

    # shared fields: only real changes count (the edit form sends unchanged values too)
    shared: dict[str, object] = {}
    if data.title is not None and data.title != work.title:
        shared["title"] = data.title
    if data.author is not None and split_authors(data.author) != work.authors:
        shared["authors"] = split_authors(data.author)
    if data.description is not None and data.description != work.description:
        shared["description"] = data.description
    if data.total_chapters is not None and data.total_chapters != work.latest_chapter:
        shared["latest_chapter"] = data.total_chapters
    cover = _cover_change(data.cover_url, cover_url_of(item)) if data.cover_url is not None else None
    if shared or cover is not None:
        await _require_editable(db, item)
        for name, value in shared.items():
            setattr(work, name, value)
        if cover is not None:
            _apply_cover(work, cover)
        work.updated_at = _now()

    # per-user fields
    for name in ("status", "rating", "is_favorite", "note", "has_new_chapter", "last_read_at"):
        if name in fields and (getattr(data, name) is not None or name == "last_read_at"):
            setattr(item, name, getattr(data, name))
    if data.current_chapter is not None:
        if data.current_chapter > work.latest_chapter:
            raise ApiError(422, "Chương đang đọc không thể lớn hơn tổng số chương", "validation_error")
        item.current_chapter = data.current_chapter
    elif item.current_chapter > work.latest_chapter:
        item.current_chapter = work.latest_chapter  # lowering the total pulls the progress down with it

    if data.tags is not None:
        await _set_tags(db, item, data.tags)
    if data.shelf_ids is not None:
        await _set_shelves(db, item, data.shelf_ids)
    if data.sources is not None:
        await _set_sources(db, item, data.sources, data.primary_source_id)
    elif "primary_source_id" in fields:
        if data.primary_source_id:
            item.primary_source_id = _find_source(item, data.primary_source_id).id
        else:
            item.primary_source_id = work.sources[0].id if work.sources else None
    item.updated_at = _now()
    if data.tags is not None:
        await delete_unused_tags(db, item.user_id)
    return await reload(db, item)


async def delete_orphan_works(db: AsyncSession) -> int:
    """Delete works nobody has in their library (e.g. after an account was deleted). → how many."""
    result = await db.execute(delete(Work).where(~exists(select(1).where(LibraryItem.work_id == Work.id))))
    return result.rowcount or 0


async def delete_comic(db: AsyncSession, item: LibraryItem) -> None:
    """Remove from the library. A work nobody has any more is deleted (sources and cover with it)."""
    user_id, work_id = item.user_id, item.work_id
    await db.delete(item)  # tag links, shelf links, reading logs, notifications cascade
    await db.flush()
    if await holders(db, work_id) == 0:
        await db.execute(delete(Work).where(Work.id == work_id))
    await delete_unused_tags(db, user_id)


async def set_favorite(db: AsyncSession, item: LibraryItem, value: bool) -> LibraryItem:
    if item.is_favorite != value:
        item.is_favorite = value
        item.updated_at = _now()
    return await reload(db, item)


async def set_progress(db: AsyncSession, item: LibraryItem, chapter: int) -> LibraryItem:
    """Clamp to 0..total, finish at the end, log the chapters read today (api-contract.md, PUT progress)."""
    work = item.work
    total = work.latest_chapter
    new = min(max(0, chapter), total)
    read = new - min(item.current_chapter, total)
    now = _now()
    item.current_chapter = new
    item.status = "completed" if total > 0 and new >= total else "reading"
    item.last_read_at = now
    item.updated_at = now
    latest = max((s.latest_chapter for s in work.sources), default=0)
    if item.has_new_chapter and latest and new >= latest:
        item.has_new_chapter = False  # caught up with the newest chapter

    if read > 0:
        stmt = insert(ReadingLog).values(
            id=uuid.uuid4(), user_id=item.user_id, library_item_id=item.id, day=today(), chapters=read
        )
        await db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_reading_logs_library_item_id_day",
                set_={"chapters": ReadingLog.chapters + stmt.excluded.chapters, "updated_at": func.now()},
            )
        )
    return await reload(db, item)
