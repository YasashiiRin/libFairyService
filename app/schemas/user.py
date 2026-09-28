"""User shapes, matching User / UserSettings / DeviceSession in BeMeoLac/src/types/index.ts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.user import DEFAULT_SETTINGS, User

DISPLAY_NAME_MAX = 40  # ProfileSection NAME_MAX
BIO_MAX = 160  # ProfileSection BIO_MAX
FONT_SIZE_MIN, FONT_SIZE_MAX = 13, 22  # ThemeContext FONT_SIZE_MIN / FONT_SIZE_MAX

ReminderTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class UserSettings(BaseModel):
    theme: Literal["day", "night"]
    font_size: int = Field(ge=FONT_SIZE_MIN, le=FONT_SIZE_MAX)
    sparkle_enabled: bool
    notify_new_chapter: bool
    notify_broken_link: bool
    daily_reminder_enabled: bool
    daily_reminder_time: ReminderTime


class UserSettingsUpdate(BaseModel):
    """PATCH /api/users/me/settings: only the fields that changed."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["day", "night"] | None = None
    font_size: int | None = Field(default=None, ge=FONT_SIZE_MIN, le=FONT_SIZE_MAX)
    sparkle_enabled: bool | None = None
    notify_new_chapter: bool | None = None
    notify_broken_link: bool | None = None
    daily_reminder_enabled: bool | None = None
    daily_reminder_time: ReminderTime | None = None


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    display_name: str
    bio: str
    avatar_url: str
    role: Literal["user", "admin"]
    created_at: datetime
    last_backup_at: datetime | None
    settings: UserSettings

    @classmethod
    def from_model(cls, user: User) -> "UserOut":
        return cls(
            id=str(user.id),
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            bio=user.bio,
            # Avatar upload/serving (PUT /api/users/me/avatar) comes with the profile picture work.
            avatar_url="",
            role=user.role,  # type: ignore[arg-type]
            created_at=user.created_at,
            last_backup_at=user.last_backup_at,
            # stored settings over the defaults, so settings added later always have a value
            settings=UserSettings(**{**DEFAULT_SETTINGS, **(user.settings or {})}),
        )


def _trimmed(value: str) -> str:
    return value.strip()


class ProfileUpdate(BaseModel):
    """PATCH /api/users/me"""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)
    bio: str | None = Field(default=None, max_length=BIO_MAX)

    @field_validator("display_name", "bio", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return _trimmed(v) if isinstance(v, str) else v

    @field_validator("display_name")
    @classmethod
    def _not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v:
            raise ValueError("Bút danh không được để trống")
        return v


class DeviceSessionOut(BaseModel):
    id: str
    device_name: str
    device_type: Literal["desktop", "phone", "tablet"]
    browser: str
    location: str
    last_active_at: datetime
    is_current: bool


class RevokedOut(BaseModel):
    revoked: int
