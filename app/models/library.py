"""Per-user library: what each user saved, their progress, rating, note and own tags."""

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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk
from app.models.shelf import ShelfItem
from app.models.work import Work

LIBRARY_STATUSES = ("reading", "completed", "plan_to_read", "on_hold", "dropped")


class LibraryItem(Base):
    """One saved work for one user. Its id is the API's Comic.id."""

    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_library_items_user_id_work_id"),
        CheckConstraint(f"status IN {LIBRARY_STATUSES}", name="status"),
        CheckConstraint("rating >= 0 AND rating <= 5", name="rating_range"),
        CheckConstraint("current_chapter >= 0", name="current_chapter_non_negative"),
        Index("ix_library_items_user_id_updated_at", "user_id", text("updated_at DESC")),
        Index("ix_library_items_work_id", "work_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # RESTRICT: a work is deleted by the app once nobody has it (services/library.py)
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("works.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="plan_to_read", server_default="plan_to_read")
    # progress lives here, per user (≤ works.latest_chapter, enforced by the app)
    current_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # an unread new chapter, for this user
    has_new_chapter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # one of the work's sources; NULL = the first one
    primary_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_sources.id", ondelete="SET NULL"), nullable=True
    )
    last_chapter_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    work: Mapped[Work] = relationship()
    tag_links: Mapped[list["LibraryItemTag"]] = relationship(
        back_populates="item", order_by="LibraryItemTag.position", cascade="all, delete-orphan", passive_deletes=True
    )
    shelf_links: Mapped[list[ShelfItem]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class Tag(Base):
    """A user's own tag (work genres are separate, on works.genres)."""

    __tablename__ = "tags"
    __table_args__ = (Index("uq_tags_user_id_name_lower", "user_id", text("lower(name)"), unique=True),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class LibraryItemTag(Base):
    __tablename__ = "library_item_tags"
    __table_args__ = (Index("ix_library_item_tags_tag_id", "tag_id"),)

    library_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_items.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    # keeps the user's tags in the order they entered them
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    item: Mapped[LibraryItem] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(lazy="joined")
