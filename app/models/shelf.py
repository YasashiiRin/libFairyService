import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

# The virtual "all" shelf (api-contract.md#shelves) is computed by the API, never stored.
DEFAULT_SHELF_ICON = "🌸"
DEFAULT_SHELF_COLOR = "#FEB2C0"


class Shelf(Base):
    __tablename__ = "shelves"
    __table_args__ = (Index("ix_shelves_user_id_position", "user_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default=DEFAULT_SHELF_ICON, server_default=DEFAULT_SHELF_ICON)
    # palette identifier from the frontend's SHELF_COLORS (stored as data, rendered per theme)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default=DEFAULT_SHELF_COLOR, server_default=DEFAULT_SHELF_COLOR)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ShelfComic(Base):
    __tablename__ = "shelf_comics"
    __table_args__ = (Index("ix_shelf_comics_comic_id", "comic_id"),)

    shelf_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shelves.id", ondelete="CASCADE"), primary_key=True)
    comic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), primary_key=True)
    # PUT /api/shelves/{id}/order; NULL = not in the saved order (listed after the ordered ones)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
