import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk

DEVICE_TYPES = ("desktop", "phone", "tablet")


class RefreshToken(Base):
    """One row per signed-in device (a DeviceSession in the API).

    Only a SHA-256 hash of the token is stored. Refreshing rotates token_hash in place,
    so the row id stays the session id that access tokens carry.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(f"device_type IN {DEVICE_TYPES}", name="device_type"),
        Index("ix_refresh_tokens_user_id_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    device_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    device_type: Mapped[str] = mapped_column(String(16), nullable=False, default="desktop", server_default="desktop")
    browser: Mapped[str] = mapped_column(String(50), nullable=False, default="", server_default="")
    location: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = created_at_column()
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    """Single-use, short-lived. Only a SHA-256 hash of the token is stored."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
