"""GET /api/discover/search and POST /api/library (api-contract.md#discover)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.comic import Chapters, ComicCreate, ComicStatus

PublicationStatus = Literal["ongoing", "completed", "hiatus", "cancelled", "unknown"]


class DiscoverLink(BaseModel):
    site_name: str
    url: str


class ProviderCredit(BaseModel):
    """Where a result's data comes from, for an attribution badge ("MangaDex") linking back."""

    provider: str  # "mangadex"
    name: str  # "MangaDex"
    url: str  # the work's page on the provider


class DiscoverResult(BaseModel):
    provider: str  # the provider this result is added from (POST /api/library)
    provider_name: str  # its display name, e.g. "MangaDex"
    external_id: str
    external_ids: dict[str, str]  # every id known for the work: {"mangadex": …, "anilist": …, "mal": …}
    providers: list[str]  # every provider that returned it (after merging duplicates)
    attribution: list[ProviderCredit]  # the same providers, with names and links (the lead first)
    title: str
    alt_titles: list[str]
    description: str
    cover_url: str  # the provider's image (a saved work keeps its own copy)
    authors: list[str]
    genres: list[str]
    status: PublicationStatus
    latest_chapter: int | None
    links: list[DiscoverLink]  # where to read
    in_library: bool
    library_item_id: str | None  # the Comic id when in_library


class DiscoverPage(BaseModel):
    query: str
    page: int
    results: list[DiscoverResult]
    providers: list[str]  # the providers asked
    provider_names: dict[str, str]  # display names: {"mangadex": "MangaDex", "anilist": "AniList"}
    providers_failed: list[str]  # skipped: too slow, down or rate-limited (the search still answers)


class LibraryAddIn(BaseModel):
    """Exactly one of: {provider, external_id} (a search result), {url} (a link), {manual} (entered by hand).
    status / current_chapter / shelf_ids apply to the new library item."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=32)
    external_id: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://\S+$")
    manual: ComicCreate | None = None

    status: ComicStatus | None = None
    current_chapter: Chapters | None = None
    shelf_ids: list[str] | None = None

    @model_validator(mode="after")
    def _one_way(self) -> "LibraryAddIn":
        ways = [bool(self.provider or self.external_id), bool(self.url), self.manual is not None]
        if sum(ways) != 1:
            raise ValueError("Cần đúng một cách thêm: {provider, external_id}, {url} hoặc {manual}")
        if ways[0] and not (self.provider and self.external_id):
            raise ValueError("Thêm từ kết quả tìm kiếm cần cả provider và external_id")
        return self
