"""The home feed "Khu vườn hôm nay": a few dozen suggestions per user per day, kept for 3 days.

Suggestions are provider metadata cached for display (title, cover, authors, …), never a catalog:
a suggestion becomes a work only when the user saves it (POST /api/library).
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

FEED_SECTIONS = ("for_you", "new_releases", "trending")
FEED_BUILD_STATUSES = ("partial", "complete")


class FeedItem(Base):
    __tablename__ = "feed_items"
    __table_args__ = (
        UniqueConstraint("user_id", "feed_date", "provider", "external_id"),
        CheckConstraint(f"section IN {FEED_SECTIONS}", name="section"),
        Index("ix_feed_items_user_id_feed_date", "user_id", "feed_date", "section", "position"),
        Index("ix_feed_items_feed_date", "feed_date"),  # the daily cleanup
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feed_date: Mapped[date] = mapped_column(Date, nullable=False)
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    external_ids: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    # display data, as the provider gave it
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    cover_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    genres: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    latest_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    attribution: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class FeedDismissal(Base):
    """"Không hứng thú": never suggested again; its genres and authors count against the taste profile."""

    __tablename__ = "feed_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "provider", "external_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # the other ids of the same work (MangaDex knows the AniList id, …): dismissed under any of them
    external_ids: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    genres: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    created_at: Mapped[datetime] = created_at_column()


class FeedBuild(Base):
    """Where today's feed stands: "partial" when some providers didn't answer in time (the next home
    request, or the cron, fetches only what's missing; answers already fetched are cached)."""

    __tablename__ = "feed_builds"
    __table_args__ = (CheckConstraint(f"status IN {FEED_BUILD_STATUSES}", name="status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    feed_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    missing: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)  # sources still to fetch
    updated_at: Mapped[datetime] = updated_at_column()
