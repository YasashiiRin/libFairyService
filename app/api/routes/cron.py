"""Scheduled jobs, called by Vercel Cron (vercel.json). Idempotent and quick."""

import hmac
import time

from fastapi import APIRouter, Header

from app.api.deps import Db
from app.core.config import get_settings
from app.core.errors import ApiError
from app.services import chapters, comics, discover, feed

router = APIRouter(prefix="/cron", tags=["cron"])


def _check_secret(authorization: str | None) -> None:
    secret = get_settings().cron_secret
    if not secret:
        raise ApiError(503, "CRON_SECRET chưa được cấu hình", "cron_not_configured")
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise ApiError(401, "Sai hoặc thiếu CRON_SECRET", "not_authenticated")


CHAPTER_SHARE = 0.3  # of the time budget, for the latest-chapter check; the rest builds feeds
PER_USER_SECONDS = 16.0  # a user's sources run at once; slow ones get up to 15 s


@router.get("/daily")
async def daily(db: Db, authorization: str | None = Header(default=None)) -> dict[str, int]:
    """GET /api/cron/daily (Authorization: Bearer <CRON_SECRET>) → what was done.
    Cleans up (expired search_cache rows, works nobody has any more, feed items older than 3 days),
    checks a small batch of saved works for new chapters, then builds today's home feed for active
    users, one at a time, until the time budget (CRON_BUDGET_SECONDS) runs low. Idempotent: users
    left over are built on their first home request."""
    _check_secret(authorization)
    started = time.monotonic()
    budget = get_settings().cron_budget_seconds
    expired = await discover.delete_expired_cache(db)
    orphans = await comics.delete_orphan_works(db)
    old_items = await feed.delete_old(db)
    await db.commit()
    report = await chapters.refresh_latest_chapters(db, budget * CHAPTER_SHARE)
    await db.commit()
    remaining = budget - (time.monotonic() - started)
    built = await feed.build_for_active_users(db, remaining, PER_USER_SECONDS)
    return {
        "search_cache_deleted": expired,
        "orphan_works_deleted": orphans,
        "feed_items_deleted": old_items,
        "chapters_checked": report.checked,
        "chapters_updated": report.updated,
        "new_chapter_notifications": report.notified,
        "feeds_built": built,
    }
