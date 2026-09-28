from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column


class SearchCache(Base):
    """Provider answers, cached for a few hours (no permanent catalog). The daily cron deletes expired rows."""

    __tablename__ = "search_cache"
    __table_args__ = (Index("ix_search_cache_expires_at", "expires_at"),)

    # sha256 of "search:<provider>:<normalized query>:<page>" or "details:<provider>:<id>"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    results: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
