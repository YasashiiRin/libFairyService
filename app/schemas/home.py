"""GET /api/home ("Khu vườn hôm nay") and POST /api/home/dismiss (api-contract.md#home)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.comic import ComicOut
from app.schemas.discover import ProviderCredit, PublicationStatus

SectionKey = Literal["continue_reading", "new_chapters", "for_you", "new_releases", "trending"]


class FeedItemOut(BaseModel):
    """A suggestion. Save it with POST /api/library { provider, external_id }."""

    provider: str
    provider_name: str
    external_id: str
    external_ids: dict[str, str]
    title: str
    cover_url: str
    authors: list[str]
    genres: list[str]
    latest_chapter: int | None
    status: PublicationStatus
    attribution: list[ProviderCredit]
    reason: str  # "Vì nàng thích Chữa lành"
    score: float


class HomeSection(BaseModel):
    key: SectionKey
    title: str
    kind: Literal["library", "feed"]  # library: comics (her own); feed: items (suggestions)
    comics: list[ComicOut] = []
    items: list[FeedItemOut] = []


class StarterOption(BaseModel):
    value: str  # what PUT /api/users/me/starter-tastes takes
    label: str
    icon: str


class HomeOut(BaseModel):
    date: date
    personalization_enabled: bool
    needs_starter_tastes: bool  # a small library and no starter tastes yet: ask her what she likes
    starter_tastes: list[str]
    priority_tastes: list[str]  # settings.priority_tastes: they lead for_you, new_releases and trending
    starter_options: list[StarterOption]
    # ready: today's feed is complete; building: some providers didn't answer in time, the next
    # request adds the rest; off: personalization is off (only non-personal sections)
    feed_status: Literal["ready", "building", "off"]
    sections: list[HomeSection]


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    external_id: str = Field(min_length=1, max_length=100)
