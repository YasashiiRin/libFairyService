"""Shared works: one row per comic that at least one user has saved (not a catalog of the web)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk
from app.services.urls import WORK_LINK_SQL

PUBLICATION_STATUSES = ("ongoing", "completed", "hiatus", "cancelled", "unknown")
# providers whose ids are unique across works (one unique index each; add one per new provider)
EXTERNAL_ID_PROVIDERS = ("mangadex", "anilist")


class Work(Base):
    """Metadata shared by everyone who saved the comic.

    owner_user_id is set only for works entered by hand: those stay private to their creator.
    """

    __tablename__ = "works"
    __table_args__ = (
        CheckConstraint(f"publication_status IN {PUBLICATION_STATUSES}", name="publication_status"),
        CheckConstraint("latest_chapter >= 0", name="latest_chapter_non_negative"),
        *(
            Index(
                f"uq_works_external_id_{provider}",
                text(f"(external_ids ->> '{provider}')"),
                unique=True,
                postgresql_where=text(f"external_ids ? '{provider}'"),
            )
            for provider in EXTERNAL_ID_PROVIDERS
        ),
        Index("ix_works_owner_user_id", "owner_user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    alt_titles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    genres: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    # the API's total_chapters
    latest_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # external cover link; an uploaded cover is stored once in `cover` (small WebP) instead
    cover_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cover: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    cover_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {"mangadex": "…", "anilist": "…"}
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    sources: Mapped[list["WorkSource"]] = relationship(
        back_populates="work", order_by="WorkSource.position", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def author(self) -> str:
        """The API's single `author` string."""
        return ", ".join(self.authors)


class WorkSource(Base):
    """A site where the work can be read (a link and metadata only, never chapter content)."""

    __tablename__ = "work_sources"
    __table_args__ = (
        Index("ix_work_sources_work_id_position", "work_id", "position"),
        # A link belongs to one shared work. Private works are scoped to their owner, so two users'
        # hand-entered works may keep the same link. private_owner_id mirrors works.owner_user_id.
        # Only page links count: a bare site ("https://cuutruyen.net") may appear on many works.
        Index(
            "uq_work_sources_url_shared",
            "url",
            unique=True,
            postgresql_where=text(f"private_owner_id IS NULL AND {WORK_LINK_SQL}"),
        ),
        Index(
            "uq_work_sources_url_private",
            "private_owner_id",
            "url",
            unique=True,
            postgresql_where=text(f"private_owner_id IS NOT NULL AND {WORK_LINK_SQL}"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False)
    private_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    site_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # normalized (app/services/urls.py)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    favicon_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    chapter_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    latest_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    work: Mapped[Work] = relationship(back_populates="sources")
