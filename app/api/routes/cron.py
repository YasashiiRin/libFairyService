"""Scheduled jobs, called by Vercel Cron (vercel.json). Idempotent and quick."""

import hmac

from fastapi import APIRouter, Header

from app.api.deps import Db
from app.core.config import get_settings
from app.core.errors import ApiError
from app.services import comics, discover

router = APIRouter(prefix="/cron", tags=["cron"])


def _check_secret(authorization: str | None) -> None:
    secret = get_settings().cron_secret
    if not secret:
        raise ApiError(503, "CRON_SECRET chưa được cấu hình", "cron_not_configured")
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise ApiError(401, "Sai hoặc thiếu CRON_SECRET", "not_authenticated")


@router.get("/daily")
async def daily(db: Db, authorization: str | None = Header(default=None)) -> dict[str, int]:
    """GET /api/cron/daily (Authorization: Bearer <CRON_SECRET>) → what was cleaned up.
    Deletes expired search_cache rows and works nobody has in their library any more."""
    _check_secret(authorization)
    expired = await discover.delete_expired_cache(db)
    orphans = await comics.delete_orphan_works(db)
    await db.commit()
    return {"search_cache_deleted": expired, "orphan_works_deleted": orphans}
