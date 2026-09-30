"""The home page "Thế giới": new comics from across the web for her taste (never her own library)."""

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentAuth, Db
from app.core.config import get_settings
from app.models import LibraryItem
from app.providers import PROVIDERS
from app.schemas.home import DismissIn, FeedItemOut, HomeOut, HomeSection, StarterOption
from app.services import comics, feed, taste

router = APIRouter(prefix="/home", tags=["home"])

TITLES = {
    "for_you": "Dành cho nàng",
    "new_releases": "Mới ra mắt hợp gu",
    "trending": "Đang được yêu thích",
}


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

    feed_rows = await feed.today_items(db, user.id)
    starter = taste.starter_tastes_of(user)
    priority = taste.priority_tastes_of(user)
    # the library's size only matters while she has told us nothing about her taste
    needs_starter = personal and not starter and not priority
    if needs_starter:
        count = await db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.user_id == user.id)) or 0
        needs_starter = count < taste.STARTER_BELOW_ITEMS

    sections = [
        HomeSection(key=key, title=TITLES[key], items=[_item_out(r) for r in feed_rows[key]])
        for key in (feed.SECTIONS if personal else ("new_releases", "trending"))
    ]

    ready = record is not None and record.status == "complete"
    return HomeOut(
        date=comics.today(),
        personalization_enabled=personal,
        needs_starter_tastes=needs_starter,
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
