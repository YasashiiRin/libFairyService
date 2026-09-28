"""All models, imported here so Base.metadata (and Alembic autogenerate) sees every table."""

from app.models.auth import PasswordResetToken, RefreshToken
from app.models.base import Base
from app.models.comic import Comic, ComicTag, Source, Tag
from app.models.notification import Notification
from app.models.reading import ReadingGoal, ReadingLog
from app.models.shelf import Shelf, ShelfComic
from app.models.user import User

__all__ = [
    "Base",
    "Comic",
    "ComicTag",
    "Notification",
    "PasswordResetToken",
    "ReadingGoal",
    "ReadingLog",
    "RefreshToken",
    "Shelf",
    "ShelfComic",
    "Source",
    "Tag",
    "User",
]
