import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk

NOTIFICATION_TYPES = ("new_chapter", "broken_link", "achievement")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(f"type IN {NOTIFICATION_TYPES}", name="type"),
        Index("ix_notifications_user_id_created_at", "user_id", text("created_at DESC")),
        # the bell badge: GET /api/notifications/unread-count
        Index("ix_notifications_user_id_unread", "user_id", "type", postgresql_where=text("NOT is_read")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # the API's comic_id / comic_title / comic_cover_url come from this library item (and its work);
    # removing the item from the library deletes its notifications
    library_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("library_items.id", ondelete="CASCADE"), nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_column()
