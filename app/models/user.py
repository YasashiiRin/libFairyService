import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk

USER_ROLES = ("user", "admin")

# UserSettings in BeMeoLac/src/types/index.ts. Stored as JSONB so new settings need no migration.
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "day",
    "font_size": 16,
    "sparkle_enabled": True,
    "notify_new_chapter": True,
    "notify_broken_link": True,
    "daily_reminder_enabled": False,
    "daily_reminder_time": "20:00",
}


def default_settings() -> dict[str, Any]:
    return dict(DEFAULT_SETTINGS)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Login accepts username or email, case-insensitively, so uniqueness is case-insensitive too.
        Index("uq_users_username_lower", text("lower(username)"), unique=True),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint(f"role IN {USER_ROLES}", name="role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Small WebP thumbnail; served by the API, exposed to the frontend as avatar_url.
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=default_settings)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
