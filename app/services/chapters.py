"""The daily check of saved works' latest chapters (from MangaDex's feed, else AniList's count).

Works whose readers are past the known latest chapter (latest_chapter_hint) go first, then the ones
checked longest ago, in a small batch. A higher chapter marks has_new_chapter on the library items of
readers who had caught up, and sends them a "new_chapter" notification (if notify_new_chapter is on).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models import LibraryItem, Notification, User, Work
from app.providers.anilist import AniList
from app.providers.http import new_client
from app.providers.mangadex import MangaDex

logger = logging.getLogger(__name__)
BATCH = 15


@dataclass
class ChapterReport:
    checked: int = 0
    updated: int = 0
    notified: int = 0


async def _latest(client, work: Work) -> int | None:
    ids = work.external_ids or {}
    if ids.get("mangadex"):
        return await MangaDex(client).latest_chapter(ids["mangadex"])
    if ids.get("anilist"):
        details = await AniList(client).get_details(ids["anilist"])
        return details.latest_chapter if details else None
    return None


def works_to_check():
    """Shared works someone has saved, known to a provider: hinted ones first, then the oldest checks."""
    hinted = Work.latest_chapter_hint > Work.latest_chapter
    return (
        select(Work)
        .where(
            Work.owner_user_id.is_(None),
            or_(Work.external_ids.has_key("mangadex"), Work.external_ids.has_key("anilist")),
            exists().where(LibraryItem.work_id == Work.id),
        )
        .order_by(hinted.desc().nulls_last(), Work.chapters_checked_at.asc().nulls_first())
        .options(selectinload(Work.sources))
    )


async def refresh_latest_chapters(db: AsyncSession, budget_seconds: float, batch: int = BATCH) -> ChapterReport:
    report = ChapterReport()
    works = list(await db.scalars(works_to_check().limit(batch)))
    if not works:
        return report
    timeout = min(budget_seconds, get_settings().provider_timeout_seconds * 2)
    started = time.monotonic()
    async with new_client() as client:
        answers = await asyncio.gather(
            *(asyncio.wait_for(_latest(client, w), timeout=timeout) for w in works), return_exceptions=True
        )
    now = datetime.now(UTC)
    for work, latest in zip(works, answers, strict=True):
        if isinstance(latest, BaseException):
            logger.info("latest chapter of %s skipped: %r", work.id, latest)
            continue
        report.checked += 1
        work.chapters_checked_at = now
        if latest is None or latest <= work.latest_chapter:
            if work.latest_chapter_hint and latest is not None and latest >= work.latest_chapter_hint:
                work.latest_chapter_hint = None
            continue
        report.updated += 1
        report.notified += await _new_chapter(db, work, latest)
    logger.info("chapter check: %s in %.1fs", report, time.monotonic() - started)
    return report


async def _new_chapter(db: AsyncSession, work: Work, latest: int) -> int:
    previous = work.latest_chapter
    work.latest_chapter = latest
    if work.latest_chapter_hint and latest >= work.latest_chapter_hint:
        work.latest_chapter_hint = None  # the providers caught up with the readers
    for src in work.sources:  # sources that followed the work's count follow it on
        if src.latest_chapter <= previous:
            src.latest_chapter = latest
    rows = await db.execute(
        select(LibraryItem, User).join(User, User.id == LibraryItem.user_id).where(LibraryItem.work_id == work.id)
    )
    sent = 0
    for item, user in rows.all():
        # new for her if she had read up to the previous latest (or further), and not this one yet
        if item.current_chapter >= latest or item.current_chapter < previous or item.has_new_chapter:
            continue
        item.has_new_chapter = True
        if (user.settings or {}).get("notify_new_chapter", True):
            title = item.custom_title if item.custom_title is not None else work.title
            db.add(Notification(user_id=user.id, library_item_id=item.id, type="new_chapter", message=f"“{title}” đã có chương {latest} 🌸"))
            sent += 1
    return sent

