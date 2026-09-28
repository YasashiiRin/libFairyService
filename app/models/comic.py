import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

COMIC_STATUSES = ("reading", "completed", "plan_to_read", "on_hold", "dropped")


class Comic(Base):
    __tablename__ = "comics"
    __table_args__ = (
        CheckConstraint(f"status IN {COMIC_STATUSES}", name="status"),
        CheckConstraint("rating >= 0 AND rating <= 5", name="rating_range"),
        CheckConstraint("current_chapter >= 0 AND total_chapters >= 0", name="chapters_non_negative"),
        CheckConstraint("current_chapter <= total_chapters", name="progress_within_total"),
        Index("ix_comics_user_id_updated_at", "user_id", text("updated_at DESC")),
        # GET /api/comics/lookup?title= (exact title, case-insensitive)
        Index("ix_comics_user_id_title_lower", "user_id", text("lower(title)")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # External cover image URL. An uploaded cover is stored in `cover` instead (small WebP).
    cover_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cover: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="plan_to_read", server_default="plan_to_read")
    # Reading progress lives on the comic, not on its sources.
    current_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    primary_source_id: Mapped[uuid.UUID | None] = mapped_column(
        # sources.comic_id points back here, so this FK is added after both tables exist
        ForeignKey("sources.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    has_new_chapter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    sources: Mapped[list["Source"]] = relationship(
        back_populates="comic",
        foreign_keys="Source.comic_id",
        order_by="Source.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tag_links: Mapped[list["ComicTag"]] = relationship(
        back_populates="comic", order_by="ComicTag.position", cascade="all, delete-orphan", passive_deletes=True
    )


class Source(Base):
    """A site where the comic can be read. A comic has one or more; one is primary."""

    __tablename__ = "sources"
    __table_args__ = (Index("ix_sources_comic_id_position", "comic_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    comic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    site_name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    favicon_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    chapter_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    latest_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    comic: Mapped[Comic] = relationship(back_populates="sources", foreign_keys=[comic_id])


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (Index("uq_tags_user_id_name_lower", "user_id", text("lower(name)"), unique=True),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ComicTag(Base):
    __tablename__ = "comic_tags"
    __table_args__ = (Index("ix_comic_tags_tag_id", "tag_id"),)

    comic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    # keeps Comic.tags in the order the user entered them
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    comic: Mapped[Comic] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(lazy="joined")
