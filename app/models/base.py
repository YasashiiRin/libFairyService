import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, event, func, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names, so Alembic autogenerate produces clean, repeatable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Server-generated values (created_at, updated_at, …) come back with the INSERT/UPDATE itself
    # (RETURNING), so objects stay usable after a flush without reloading them.
    __mapper_args__ = {"eager_defaults": True}


@event.listens_for(Base, "init", propagate=True)
def _unset_nullable_columns_are_none(target, args, kwargs) -> None:
    """A new object starts with None in the nullable columns it isn't given: they are NULL once
    inserted, and being known in memory the object needs no reload before it is shown."""
    for attr in inspect(type(target)).column_attrs:
        column = attr.columns[0]
        if attr.key not in kwargs and column.nullable and column.default is None and column.server_default is None:
            setattr(target, attr.key, None)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
