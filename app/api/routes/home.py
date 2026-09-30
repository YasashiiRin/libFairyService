"""The home page "Khu vườn hôm nay": her own comics to continue, then suggestions for her taste."""

from fastapi import APIRouter, Response, status
from sqlalchemy import and_, func, or_, select

from app.api.deps import CurrentAuth, Db
from app.core.config import get_settings
from app.models import LibraryItem
from app.providers import PROVIDERS
from app.schemas.home import DismissIn, FeedItemOut, HomeOut, HomeSection, StarterOption
from app.services import comics, feed, taste

router = APIRouter(prefix="/home", tags=["home"])

LIBRARY_SECTION_SIZE = 10
TITLES = {
    "continue_reading": "Đọc tiếp nhé",
    "new_chapters": "Có chương mới",
    "for_you": "Dành cho nàng",
    "new_releases": "Mới ra mắt hợp gu",
    "trending": "Đang được yêu thích",
}


async def _library_sections(db: Db, user_id) -> tuple[list[LibraryItem], list[LibraryItem]]:
    """In-progress and new-chapter comics, in one query (plus their sources, tags and shelves)."""
    reading = and_(LibraryItem.status == "reading", LibraryItem.current_chapter > 0)
    rows = list(
        await db.scalars(
            comics.with_relations(
                select(LibraryItem)
                .where(LibraryItem.user_id == user_id, or_(reading, LibraryItem.has_new_chapter.is_(True)))
                .order_by(LibraryItem.last_read_at.desc().nulls_last(), LibraryItem.updated_at.desc())
                .limit(LIBRARY_SECTION_SIZE * 3)
            )
        )
    )
    new = [i for i in rows if i.has_new_chapter][:LIBRARY_SECTION_SIZE]
    shown = {i.id for i in new}
    cont = [i for i in rows if i.status == "reading" and i.current_chapter > 0 and i.id not in shown][:LIBRARY_SECTION_SIZE]
    return cont, new


def _item_out(row) -> FeedItemOut:
    return FeedItemOut(
        provider=row.provider,
        provider_name=PROVIDERS[row.provider].label if row.provider in PROVIDERS else row.provider,
        external_id=row.external_id,
        external_ids=row.external_ids,
        title=row.title,
        cover_url=row.cover_url,
        authors=row.authors,
        genres=row.genres,
        latest_chapter=row.latest_chapter,
        status=row.publication_status,
        attribution=row.attribution,
        reason=row.reason,
        score=row.score,
    )


@router.get("")
async def home(auth: CurrentAuth, db: Db) -> HomeOut:
    """GET /api/home → HomeOut. Builds today's feed on the first request of the day (within
    FEED_BUILD_SECONDS); if providers are slow, feed_status is "building" and the next request adds the rest."""
    user = auth.user
    personal = taste.personalization_enabled(user)
    settings = get_settings()
    record, built = await feed.ensure_today(db, user, settings.feed_build_seconds, settings.feed_retry_seconds)
    if built:
        await db.commit()

    cont, new = await _library_sections(db, user.id)
    feed_rows = await feed.today_items(db, user.id)
    count = await db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.user_id == user.id)) or 0
    starter = taste.starter_tastes_of(user)
    priority = taste.priority_tastes_of(user)

    sections = [
        HomeSection(key="continue_reading", title=TITLES["continue_reading"], kind="library", comics=[comics.comic_out(i) for i in cont]),
        HomeSection(key="new_chapters", title=TITLES["new_chapters"], kind="library", comics=[comics.comic_out(i) for i in new]),
    ]
    for key in feed.SECTIONS if personal else ("new_releases", "trending"):
        sections.append(HomeSection(key=key, title=TITLES[key], kind="feed", items=[_item_out(r) for r in feed_rows[key]]))

    ready = record is not None and record.status == "complete"
    return HomeOut(
        date=comics.today(),
        personalization_enabled=personal,
        needs_starter_tastes=personal and count < taste.STARTER_BELOW_ITEMS and not starter and not priority,
        starter_tastes=starter,
        priority_tastes=priority,
        starter_options=[StarterOption(value=name, label=name, icon=m.icon) for name, m in taste.MOODS.items()],
        feed_status="off" if not personal and ready else "ready" if ready else "building",
        sections=sections,
    )


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(data: DismissIn, auth: CurrentAuth, db: Db) -> Response:
    """POST /api/home/dismiss body { provider, external_id } → 204 ("Không hứng thú"; idempotent)"""
    await feed.dismiss(db, auth.user.id, data.provider, data.external_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
