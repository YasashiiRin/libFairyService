"""The user's shelves (api-contract.md#shelves). Every query is scoped to the user.

Positions: the virtual "all" shelf is always first (position 1) and her shelves follow in their saved
order, so a shelf's position is its rank (stored position, then creation time) + 1, whatever numbers
are stored. Reordering stores 2, 3, … again.

Covers: up to 4 per shelf, in the shelf's order (as GET /api/comics?shelf=…&sort=position lists it;
"all" by most recently updated), read from the columns alone: one query for every shelf at once.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, case, delete, func, literal, null, select, union_all, update
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import set_committed_value

from app.core.errors import ApiError
from app.models import LibraryItem, Shelf, ShelfItem, Work
from app.models.shelf import DEFAULT_SHELF_COLOR, DEFAULT_SHELF_ICON
from app.schemas.shelf import ShelfDetailOut, ShelfIn, ShelfOut, ShelfUpdate
from app.services import comics
from app.services.comics import ALL_SHELF, COVER_COLUMNS, cover_url_from, parse_uuid, shelf_not_found

COVERS = 4
COVER_CANDIDATES = 8  # comics looked at per shelf: some have no cover
ALL_NAME = "Tất cả truyện"
ALL_DESCRIPTION = "Mọi cuốn truyện trong tủ của nàng"
ALL_ICON = "📚"


def all_not_editable() -> ApiError:
    return ApiError(422, "Kệ “Tất cả truyện” luôn có mọi truyện, nàng không cần sửa nó đâu", "validation_error")


def _invalid(detail: str) -> ApiError:
    return ApiError(422, detail, "validation_error")


# ── Reading ──────────────────────────────────────────────────────────────


def _ranked(user_id: uuid.UUID):
    """Her shelves with their position (rank + 1: "all" is 1)."""
    rank = func.row_number().over(order_by=(Shelf.position, Shelf.created_at, Shelf.id)) + 1
    sub = select(Shelf, rank.label("rank")).where(Shelf.user_id == user_id).subquery()
    return aliased(Shelf, sub), sub.c.rank


def _item_count(shelf_id) -> Select:
    return select(func.count()).where(ShelfItem.shelf_id == shelf_id).scalar_subquery()


async def _covers(db: AsyncSession, user_id: uuid.UUID, shelf_ids: list[uuid.UUID], with_all: bool) -> tuple[dict, list[str], int]:
    """→ ({shelf id: cover urls}, the "all" shelf's cover urls, the library's size) in one query."""
    parts = []
    if shelf_ids:
        rn = func.row_number().over(
            partition_by=ShelfItem.shelf_id,
            order_by=(ShelfItem.position.asc().nulls_last(), LibraryItem.updated_at.desc(), LibraryItem.id),
        )
        ranked = (
            select(ShelfItem.shelf_id.label("shelf_id"), *COVER_COLUMNS, rn.label("rn"), literal(0).label("total"))
            .join(LibraryItem, LibraryItem.id == ShelfItem.library_item_id)
            .join(Work, Work.id == LibraryItem.work_id)
            .where(ShelfItem.shelf_id.in_(shelf_ids), LibraryItem.user_id == user_id)
            .subquery()
        )
        parts.append(select(ranked).where(ranked.c.rn <= COVER_CANDIDATES))
    if with_all:
        rn = func.row_number().over(order_by=(LibraryItem.updated_at.desc(), LibraryItem.id))
        ranked = (
            select(null().cast(UUID(as_uuid=True)).label("shelf_id"), *COVER_COLUMNS, rn.label("rn"), func.count().over().label("total"))
            .join(Work, Work.id == LibraryItem.work_id)
            .where(LibraryItem.user_id == user_id)
            .subquery()
        )
        parts.append(select(ranked).where(ranked.c.rn <= COVER_CANDIDATES))
    if not parts:
        return {}, [], 0

    by_shelf: dict[uuid.UUID, list[str]] = {}
    all_covers: list[str] = []
    total = 0
    rows = (await db.execute(union_all(*parts) if len(parts) > 1 else parts[0])).all()
    for shelf_id, item_id, custom_at, custom_url, work_at, work_url, rn, count in sorted(rows, key=lambda r: r.rn):
        url = cover_url_from(item_id, custom_at, custom_url, work_at, work_url)
        target = all_covers if shelf_id is None else by_shelf.setdefault(shelf_id, [])
        if shelf_id is None:
            total = count
        if url and len(target) < COVERS:
            target.append(url)
    return by_shelf, all_covers, total


def _out(shelf: Shelf, position: int, count: int, covers: list[str]) -> ShelfOut:
    return ShelfOut(
        id=str(shelf.id),
        name=shelf.name,
        description=shelf.description,
        icon=shelf.icon,
        color=shelf.color,
        position=position,
        comic_count=count,
        cover_urls=covers,
    )


def _all_out(count: int, covers: list[str]) -> ShelfOut:
    return ShelfOut(
        id=ALL_SHELF, name=ALL_NAME, description=ALL_DESCRIPTION, icon=ALL_ICON, color=DEFAULT_SHELF_COLOR,
        position=1, comic_count=count, cover_urls=covers,
    )


async def list_shelves(db: AsyncSession, user_id: uuid.UUID) -> list[ShelfOut]:
    """"all" first, then her shelves by position, with counts and covers: two queries."""
    s, rank = _ranked(user_id)
    rows = (await db.execute(select(s, rank, _item_count(s.id)).order_by(rank))).all()
    by_shelf, all_covers, total = await _covers(db, user_id, [shelf.id for shelf, _, _ in rows], with_all=True)
    return [_all_out(total, all_covers), *(_out(shelf, pos, n, by_shelf.get(shelf.id, [])) for shelf, pos, n in rows)]


async def get_owned(db: AsyncSession, user_id: uuid.UUID, shelf_id: str) -> Shelf:
    """Her shelf, for a change; "all" can't be changed."""
    if shelf_id == ALL_SHELF:
        raise all_not_editable()
    sid = parse_uuid(shelf_id)
    shelf = sid and await db.scalar(select(Shelf).where(Shelf.id == sid, Shelf.user_id == user_id))
    if not shelf:
        raise shelf_not_found()
    return shelf


async def shelf_detail(db: AsyncSession, user_id: uuid.UUID, shelf_id: str) -> ShelfDetailOut:
    """The shelf with its stats (comic count, completed count, last updated) and covers: two queries."""
    completed = func.count().filter(LibraryItem.status == "completed")
    if shelf_id == ALL_SHELF:
        count, done, last = (
            await db.execute(select(func.count(), completed, func.max(LibraryItem.updated_at)).where(LibraryItem.user_id == user_id))
        ).one()
        _, covers, _ = await _covers(db, user_id, [], with_all=True)
        return ShelfDetailOut(**_all_out(count, covers).model_dump(), completed_count=done, last_updated_at=last)

    sid = parse_uuid(shelf_id)
    if sid is None:
        raise shelf_not_found()
    s, rank = _ranked(user_id)
    stats = (
        select(func.count(), completed, func.max(LibraryItem.updated_at))
        .select_from(ShelfItem)
        .join(LibraryItem, LibraryItem.id == ShelfItem.library_item_id)
        .where(ShelfItem.shelf_id == s.id)
        .lateral()
    )
    row = (await db.execute(select(s, rank, stats).join(stats, literal(True)).where(s.id == sid))).first()
    if row is None:
        raise shelf_not_found()
    shelf, position, count, done, last = row
    by_shelf, _, _ = await _covers(db, user_id, [shelf.id], with_all=False)
    return ShelfDetailOut(
        **_out(shelf, position, count, by_shelf.get(shelf.id, [])).model_dump(), completed_count=done, last_updated_at=last
    )


# ── Changing ─────────────────────────────────────────────────────────────


async def create(db: AsyncSession, user_id: uuid.UUID, data: ShelfIn) -> ShelfOut:
    """A new shelf, last: its position is after every other (stored, and as the API counts it)."""
    last, n = (await db.execute(select(func.max(Shelf.position), func.count()).where(Shelf.user_id == user_id))).one()
    shelf = Shelf(
        user_id=user_id,
        name=data.name,
        description=data.description,
        icon=data.icon or DEFAULT_SHELF_ICON,
        color=data.color or DEFAULT_SHELF_COLOR,
        position=max(last or 1, 1) + 1,
    )
    db.add(shelf)
    await db.flush()
    return _out(shelf, n + 2, 0, [])


async def update_shelf(db: AsyncSession, user_id: uuid.UUID, shelf_id: str, data: ShelfUpdate) -> ShelfDetailOut:
    shelf = await get_owned(db, user_id, shelf_id)
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(shelf, field, value)
    await db.flush()
    return await shelf_detail(db, user_id, str(shelf.id))


async def delete_shelf(db: AsyncSession, user_id: uuid.UUID, shelf_id: str) -> None:
    """The shelf goes, and its shelf_items with it (ON DELETE CASCADE); the comics stay in the library."""
    if shelf_id == ALL_SHELF:
        raise all_not_editable()
    sid = parse_uuid(shelf_id)
    deleted = sid and await db.scalar(delete(Shelf).where(Shelf.id == sid, Shelf.user_id == user_id).returning(Shelf.id))
    if not deleted:
        raise shelf_not_found()


async def reorder_shelves(db: AsyncSession, user_id: uuid.UUID, shelf_ids: list[str]) -> list[ShelfOut]:
    """Every one of her shelves, in the new order ("all" may be listed; it stays first)."""
    wanted = [s for s in shelf_ids if s != ALL_SHELF]
    ids = [parse_uuid(s) for s in wanted]
    if None in ids or len(set(ids)) != len(ids):
        raise _invalid("Danh sách kệ có mã không hợp lệ hoặc bị lặp")
    owned = set(await db.scalars(select(Shelf.id).where(Shelf.user_id == user_id)))
    if set(ids) != owned:
        raise _invalid("Nàng gửi lại đủ mọi kệ của mình theo thứ tự mới nhé")
    if ids:
        await db.execute(
            update(Shelf)
            .where(Shelf.user_id == user_id, Shelf.id.in_(ids))
            .values(position=case({sid: i + 2 for i, sid in enumerate(ids)}, value=Shelf.id))
        )
    return await list_shelves(db, user_id)


async def reorder_comics(db: AsyncSession, user_id: uuid.UUID, shelf_id: str, comic_ids: list[str]) -> None:
    """The comics' order on the shelf. Its comics not listed keep no position (they come after)."""
    shelf = await get_owned(db, user_id, shelf_id)
    ids = [parse_uuid(c) for c in comic_ids]
    if None in ids or len(set(ids)) != len(ids):
        raise _invalid("Danh sách truyện có mã không hợp lệ hoặc bị lặp")
    on_shelf = set(await db.scalars(select(ShelfItem.library_item_id).where(ShelfItem.shelf_id == shelf.id)))
    if not set(ids) <= on_shelf:
        raise _invalid("Có truyện không nằm trên kệ này")
    await db.execute(
        update(ShelfItem)
        .where(ShelfItem.shelf_id == shelf.id)
        .values(position=case({cid: i for i, cid in enumerate(ids)}, value=ShelfItem.library_item_id, else_=None) if ids else None)
    )


@dataclass
class Membership:
    shelf: Shelf
    item: LibraryItem


async def _membership(db: AsyncSession, user_id: uuid.UUID, shelf_id: str, comic_id: str) -> Membership:
    shelf = await get_owned(db, user_id, shelf_id)
    return Membership(shelf, await comics.get_comic(db, user_id, comic_id))


async def add_comic(db: AsyncSession, user_id: uuid.UUID, shelf_id: str, comic_id: str) -> LibraryItem:
    """Idempotent: a comic already on the shelf stays where it is."""
    m = await _membership(db, user_id, shelf_id, comic_id)
    if not any(link.shelf_id == m.shelf.id for link in m.item.shelf_links):
        await db.execute(
            insert(ShelfItem).values(shelf_id=m.shelf.id, library_item_id=m.item.id, position=None).on_conflict_do_nothing()
        )
        await _refresh_links(db, m.item)
    return m.item


async def remove_comic(db: AsyncSession, user_id: uuid.UUID, shelf_id: str, comic_id: str) -> LibraryItem:
    """Idempotent: removing a comic that isn't on the shelf is fine."""
    m = await _membership(db, user_id, shelf_id, comic_id)
    if any(link.shelf_id == m.shelf.id for link in m.item.shelf_links):
        await db.execute(delete(ShelfItem).where(ShelfItem.shelf_id == m.shelf.id, ShelfItem.library_item_id == m.item.id))
        await _refresh_links(db, m.item)
    return m.item


async def _refresh_links(db: AsyncSession, item: LibraryItem) -> None:
    """The item's shelf_ids after a change made by statement (not through the relationship)."""
    links = list(await db.scalars(select(ShelfItem).where(ShelfItem.library_item_id == item.id).execution_options(populate_existing=True)))
    set_committed_value(item, "shelf_links", links)

