"""All models, imported here so Base.metadata (and Alembic autogenerate) sees every table."""

from app.models.auth import PasswordResetToken, RefreshToken
from app.models.base import Base
from app.models.cache import SearchCache
from app.models.library import LibraryItem, LibraryItemTag, Tag
from app.models.notification import Notification
from app.models.reading import ReadingGoal, ReadingLog
from app.models.shelf import Shelf, ShelfItem
from app.models.user import User
from app.models.work import Work, WorkSource

__all__ = [
    "Base",
    "LibraryItem",
    "LibraryItemTag",
    "Notification",
    "PasswordResetToken",
    "ReadingGoal",
    "ReadingLog",
    "RefreshToken",
    "SearchCache",
    "Shelf",
    "ShelfItem",
    "Tag",
    "User",
    "Work",
    "WorkSource",
]
