"""Shelf shapes, matching Shelf / ShelfInput in BeMeoLac/src/types/index.ts (api-contract.md#shelves)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# SHELF_COLORS in BeMeoLac/src/components/ShelfFormModal.tsx: palette identifiers, rendered per theme
SHELF_COLORS = ("#FEB2C0", "#A8C49A", "#D9C8F0", "#FFDF97", "#CFE8D5", "#FFE3D2")
NAME_MAX = 100
DESCRIPTION_MAX = 500
ICON_MAX = 16
MAX_ORDER = 5000  # ids in one reorder request


class ShelfOut(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    color: str
    position: int  # 1 is the virtual "all" shelf; the user's shelves follow
    comic_count: int
    cover_urls: list[str]  # up to 4, in the shelf's order


class ShelfDetailOut(ShelfOut):
    """GET /api/shelves/{id}: the shelf and its stats."""

    completed_count: int  # comics on it marked "Đã đọc xong"
    last_updated_at: datetime | None  # the most recent change to one of its comics; null when empty


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return v
    v = " ".join(v.split())
    if not v:
        raise ValueError("Tên kệ không được để trống")
    return v


def _known_color(v: str | None) -> str | None:
    if v is not None and v.upper() not in SHELF_COLORS:
        raise ValueError(f"Màu kệ chưa có trong bảng màu: {', '.join(SHELF_COLORS)}")
    return v.upper() if v else v


class ShelfIn(BaseModel):
    """POST /api/shelves (ShelfInput)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=NAME_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    icon: str | None = Field(default=None, max_length=ICON_MAX)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return _known_color(v)

    @field_validator("description")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ShelfUpdate(BaseModel):
    """PATCH /api/shelves/{id}: only the fields sent change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    icon: str | None = Field(default=None, min_length=1, max_length=ICON_MAX)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return _known_color(v)

    @field_validator("description")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else v


class ShelfComicsOrderIn(BaseModel):
    """PUT /api/shelves/{id}/order: the comics' full order after a drag-and-drop."""

    model_config = ConfigDict(extra="forbid")

    comic_ids: list[str] = Field(max_length=MAX_ORDER)


class ShelvesOrderIn(BaseModel):
    """PUT /api/shelves/order: every one of her shelves, in the new order ("all" may be sent first; it stays first)."""

    model_config = ConfigDict(extra="forbid")

    shelf_ids: list[str] = Field(max_length=MAX_ORDER)


class ShelfComicIn(BaseModel):
    """POST /api/shelves/{shelf_id}/comics."""

    model_config = ConfigDict(extra="forbid")

    comic_id: str = Field(min_length=1, max_length=64)
