import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column, uuid_pk


class ReadingLog(Base):
    """Chapters read per library item per day: the source for /api/stats (calendar, streak, monthly chart)."""

    __tablename__ = "reading_logs"
    __table_args__ = (
        UniqueConstraint("library_item_id", "day", name="uq_reading_logs_library_item_id_day"),
        Index("ix_reading_logs_user_id_day", "user_id", "day"),
        CheckConstraint("chapters > 0", name="chapters_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    library_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False)
    # the user's local calendar day
    day: Mapped[date] = mapped_column(Date, nullable=False)
    chapters: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ReadingGoal(Base):
    """Comics to finish in a year (PUT /api/stats/goals/{year})."""

    __tablename__ = "reading_goals"
    __table_args__ = (CheckConstraint("target >= 1", name="target_positive"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = updated_at_column()
