"""Comic shapes, matching Comic / Source / ComicCreate / ComicUpdate in BeMeoLac/src/types/index.ts."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ComicStatus = Literal["reading", "completed", "plan_to_read", "on_hold", "dropped"]
SortOption = Literal["updated_at", "title", "rating", "progress", "relevance", "position"]

MAX_TAGS = 30
TAG_MAX = 60
MAX_SOURCES = 20
MAX_CHAPTERS = 100_000

HttpUrlStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000, pattern=r"^https?://\S+$")]
OptionalHttpUrlStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000, pattern=r"^(https?://\S+)?$")]
Chapters = Annotated[int, Field(ge=0, le=MAX_CHAPTERS)]


def normalize_tags(tags: list[str]) -> list[str]:
    """Trim, collapse spaces, drop empties and case-insensitive duplicates (first spelling wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags:
        name = re.sub(r"\s+", " ", raw).strip()
        if not name:
            continue
        if len(name) > TAG_MAX:
            raise ValueError(f"Thẻ tối đa {TAG_MAX} ký tự")
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    if len(out) > MAX_TAGS:
        raise ValueError(f"Tối đa {MAX_TAGS} thẻ cho một truyện")
    return out


class SourceIn(BaseModel):
    """A source as the client sends it. `id` is kept when it is an existing source of the comic."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    site_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    url: HttpUrlStr
    favicon_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    chapter_url: OptionalHttpUrlStr = ""
    latest_chapter: Chapters = 0
    is_alive: bool = True
    # set by the server's link checks; a value from the client is ignored
    last_checked_at: datetime | None = None


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    url: HttpUrlStr | None = None
    favicon_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    chapter_url: OptionalHttpUrlStr | None = None
    latest_chapter: Chapters | None = None
    is_alive: bool | None = None


class PrimarySourceIn(BaseModel):
    source_id: str


class _ComicFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "author", "description", "note", mode="before", check_fields=False)
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("tags", check_fields=False)
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_tags(v)


class ComicCreate(_ComicFields):
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    # an https:// URL, a data:image/…;base64 URL (stored as a WebP thumbnail), or ""
    cover_url: str = Field(default="", max_length=6_000_000)
    status: ComicStatus = "plan_to_read"
    current_chapter: Chapters = 0
    total_chapters: Chapters = 0
    rating: float = Field(default=0, ge=0, le=5)
    is_favorite: bool = False
    note: str = Field(default="", max_length=10_000)
    tags: list[str] = Field(default_factory=list)
    sources: list[SourceIn] = Field(default_factory=list, max_length=MAX_SOURCES)
    # may name one of `sources` by the id the client gave it
    primary_source_id: str | None = None
    # shelf ids; entries that aren't shelf ids (like the virtual "all") are ignored
    shelf_ids: list[str] = Field(default_factory=list)
    has_new_chapter: bool = False
    last_read_at: datetime | None = None
    # current_chapter may be above total_chapters: sites often list new chapters late


class ComicUpdate(_ComicFields):
    """PATCH: only the fields sent change. `sources`, `tags` and `shelf_ids` replace the whole list."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    cover_url: str | None = Field(default=None, max_length=6_000_000)
    status: ComicStatus | None = None
    current_chapter: Chapters | None = None
    total_chapters: Chapters | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    is_favorite: bool | None = None
    note: str | None = Field(default=None, max_length=10_000)
    tags: list[str] | None = None
    sources: list[SourceIn] | None = Field(default=None, max_length=MAX_SOURCES)
    primary_source_id: str | None = None
    shelf_ids: list[str] | None = None
    has_new_chapter: bool | None = None
    last_read_at: datetime | None = None


class ProgressIn(BaseModel):
    current_chapter: int = Field(ge=0, le=MAX_CHAPTERS)


# ── Responses ────────────────────────────────────────────────────────────


class SourceOut(BaseModel):
    id: str
    site_name: str
    url: str
    favicon_url: str
    chapter_url: str
    latest_chapter: int
    is_alive: bool
    last_checked_at: datetime | None


class ComicOut(BaseModel):
    id: str
    title: str
    author: str
    description: str
    cover_url: str
    status: ComicStatus
    current_chapter: int
    total_chapters: int
    rating: float
    is_favorite: bool
    note: str
    tags: list[str]
    sources: list[SourceOut]
    primary_source_id: str
    # always starts with the virtual "all" shelf
    shelf_ids: list[str]
    has_new_chapter: bool
    last_read_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ComicPage(BaseModel):
    items: list[ComicOut]
    total: int
    page: int
    page_size: int


class ComicSummary(BaseModel):
    total: int
    by_status: dict[ComicStatus, int]
    new_chapters: int


class ComicLookup(BaseModel):
    comic: ComicOut | None


class FacetOption(BaseModel):
    value: str
    label: str
    icon: str | None = None
    count: int


class SearchFacets(BaseModel):
    total: int
    statuses: list[FacetOption]
    genres: list[FacetOption]
    sources: list[FacetOption]
    shelves: list[FacetOption]


class TagCount(BaseModel):
    name: str
    count: int
