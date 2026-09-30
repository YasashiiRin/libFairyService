"""User shapes, matching User / UserSettings / DeviceSession in BeMeoLac/src/types/index.ts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.user import DEFAULT_SETTINGS, User
from app.services.images import media_url
from app.services.taste import MAX_PRIORITY, MOODS

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
    personalization_enabled: bool
    starter_tastes: list[str]
    priority_tastes: list[str]


def _known_moods(v: list[str]) -> list[str]:
    unknown = [t for t in v if t not in MOODS]
    if unknown:
        raise ValueError(f"Chưa có gu “{unknown[0]}”. Nàng chọn trong: {', '.join(MOODS)}")
    return list(dict.fromkeys(v))


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
    personalization_enabled: bool | None = None
    # moods (GET /api/home starter_options) that lead every personal section; [] clears them
    priority_tastes: list[str] | None = Field(default=None, max_length=MAX_PRIORITY)
    # starter_tastes: PUT /api/users/me/starter-tastes (checked against the list of moods)

    @field_validator("priority_tastes")
    @classmethod
    def _priority_known(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _known_moods(v)


class StarterTastesIn(BaseModel):
    """PUT /api/users/me/starter-tastes: the moods she likes (from GET /api/home starter_options)."""

    model_config = ConfigDict(extra="forbid")

    starter_tastes: list[str] = Field(max_length=len(MOODS))

    @field_validator("starter_tastes")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        return _known_moods(v)


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
            avatar_url=media_url("avatar", user.id, user.avatar_updated_at) if user.avatar_updated_at else "",
            role=user.role,  # type: ignore[arg-type]
            created_at=user.created_at,
            last_backup_at=user.last_backup_at,
            # stored settings over the defaults, so settings added later always have a value
            settings=UserSettings(**{**DEFAULT_SETTINGS, **(user.settings or {})}),
        )


def _trimmed(value: str) -> str:
    return value.strip()


class ProfileUpdate(BaseModel):
    """PATCH /api/users/me: display_name, bio, avatar and settings only. Other fields (username, email,
    role, …) are collected rather than dropped, so the route can refuse them with a clear error."""

    model_config = ConfigDict(extra="allow")

    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)
    bio: str | None = Field(default=None, max_length=BIO_MAX)
    # a data:image/…;base64 URL to upload a new picture, or null to remove it
    avatar: str | None = Field(default=None, max_length=6_000_000)
    settings: UserSettingsUpdate | None = None

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
