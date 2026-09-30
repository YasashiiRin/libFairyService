"""A user's taste, from their own data only: what they saved, how they rated it, what they dismissed.

Genres are compared under one canonical name (the providers' English genre, e.g. "Slice of Life"), so
a user's own Vietnamese tag "Đời thường" and MangaDex's "Slice of Life" count as the same taste.
Reasons shown to the user use a Vietnamese label ("Chữa lành" when that is what they chose or tagged).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import FeedDismissal, LibraryItem, LibraryItemTag, User
from app.providers.base import fold, name_key
from app.services import comics

# ── Moods (starter tastes) and genre names ───────────────────────────────


@dataclass(frozen=True)
class Mood:
    genres: tuple[str, ...]  # canonical genres (MangaDex tags / AniList genres)
    icon: str
    short: str = ""  # its name in reasons, when the mood's own is too long ("Bách hợp mới ra mắt")


# Friendly moods a new user can pick (settings.starter_tastes), in display order.
MOODS: dict[str, Mood] = {
    "Chữa lành": Mood(("Slice of Life",), "🌿"),
    "Cổ tích": Mood(("Fantasy", "Magic"), "🧚"),
    "Lãng mạn": Mood(("Romance",), "💗"),
    "Phiêu lưu": Mood(("Adventure",), "🧭"),
    "Hài hước": Mood(("Comedy",), "😄"),
    "Học đường": Mood(("School Life",), "🎒"),
    "Kỳ ảo": Mood(("Fantasy", "Supernatural"), "✨"),
    "Đời thường": Mood(("Slice of Life",), "☕"),
    "Bách hợp (GL)": Mood(("Girls' Love",), "🌷", "Bách hợp"),
    "Đam mỹ (BL)": Mood(("Boys' Love",), "🌹", "Đam mỹ"),
}

# Vietnamese labels for the providers' genres (reasons, and the user's own Vietnamese tags).
GENRE_LABELS: dict[str, str] = {
    "Action": "Hành động",
    "Adventure": "Phiêu lưu",
    "Comedy": "Hài hước",
    "Cooking": "Ẩm thực",
    "Drama": "Chính kịch",
    "Fantasy": "Kỳ ảo",
    "Girls' Love": "Bách hợp",
    "Boys' Love": "Đam mỹ",
    "Historical": "Lịch sử",
    "Horror": "Kinh dị",
    "Isekai": "Isekai",
    "Magic": "Phép thuật",
    "Mecha": "Robot",
    "Music": "Âm nhạc",
    "Mystery": "Bí ẩn",
    "Psychological": "Tâm lý",
    "Romance": "Lãng mạn",
    "School Life": "Học đường",
    "Sci-Fi": "Khoa học viễn tưởng",
    "Slice of Life": "Đời thường",
    "Sports": "Thể thao",
    "Supernatural": "Siêu nhiên",
    "Thriller": "Giật gân",
}

# folded name → canonical genre: English names, their Vietnamese labels, and the moods
_CANONICAL: dict[str, str] = {
    **{fold(en): en for en in GENRE_LABELS},
    **{fold(vi): en for en, vi in GENRE_LABELS.items()},
    **{fold(mood): m.genres[0] for mood, m in MOODS.items()},
    fold("Iyashikei"): "Slice of Life",
    fold("School"): "School Life",
    # common Vietnamese tag wordings
    fold("Huyền bí"): "Supernatural",
    fold("Tình cảm"): "Romance",
    fold("Nhẹ nhàng"): "Slice of Life",
    fold("Ấm áp"): "Slice of Life",
    fold("Thần tiên"): "Fantasy",
    fold("Hài"): "Comedy",
    fold("Hành trình"): "Adventure",
    # AniList's tag and older names (MangaDex's name is the canonical one)
    fold("Yuri"): "Girls' Love",
    fold("Shoujo Ai"): "Girls' Love",
    fold("GL"): "Girls' Love",
    fold("Yaoi"): "Boys' Love",
    fold("Shounen Ai"): "Boys' Love",
    fold("BL"): "Boys' Love",
}


def canonical_genre(name: str) -> str:
    """"đời thường", "Slice of Life", "Chữa lành" → "Slice of Life"; unknown names stay as they are."""
    return _CANONICAL.get(fold(name), name.strip())


def genre_label(genre: str) -> str:
    return GENRE_LABELS.get(genre, genre)


# ── Weights ──────────────────────────────────────────────────────────────

STATUS_WEIGHT = {"completed": 1.5, "reading": 1.0, "on_hold": 0.4, "plan_to_read": 0.3, "dropped": -1.5}
FAVORITE_BONUS = 1.0
RATING_STEP = 0.5  # per heart above (or below) 3
AUTHOR_FACTOR = 1.2
DISMISSED_GENRE = -0.6
DISMISSED_AUTHOR = -1.2
STARTER_WEIGHT = 2.0
STARTER_BELOW_ITEMS = 5  # starter tastes count while the library is this small
MAX_PRIORITY = 4


def recency_factor(last_activity: datetime | None, now: datetime) -> float:
    if last_activity is None:
        return 1.0
    days = (now - last_activity).days
    return 1.5 if days <= 30 else 1.2 if days <= 90 else 1.0


def item_weight(item: LibraryItem, now: datetime) -> float:
    """How much one saved comic says about the user's taste (negative: dropped)."""
    w = STATUS_WEIGHT.get(item.status, 0.3)
    if item.rating and item.rating > 0:
        w += (item.rating - 3) * RATING_STEP
    if item.is_favorite:
        w += FAVORITE_BONUS
    return w * recency_factor(item.last_read_at or item.updated_at, now)


@dataclass
class Seed:
    """A work the user loved: its recommendations and authors are good suggestions."""

    title: str
    weight: float
    reason: str  # "Giống Frieren mà nàng chấm 5 tim"
    external_ids: dict[str, str]


@dataclass
class Taste:
    genres: dict[str, float] = field(default_factory=dict)  # canonical genre → weight
    labels: dict[str, str] = field(default_factory=dict)  # canonical genre → label shown in reasons
    authors: dict[str, float] = field(default_factory=dict)  # name_key → weight
    author_names: dict[str, str] = field(default_factory=dict)  # name_key → display name
    author_titles: dict[str, str] = field(default_factory=dict)  # name_key → a saved title by them
    seeds: list[Seed] = field(default_factory=list)
    library_ids: set[str] = field(default_factory=set)  # "mangadex:<id>" of saved works
    library_titles: set[str] = field(default_factory=set)  # folded titles of saved works
    dismissed_ids: set[str] = field(default_factory=set)
    item_count: int = 0
    starter_tastes: list[str] = field(default_factory=list)
    # settings.priority_tastes: canonical genre → its name in reasons, in her order. Above every
    # other signal: each personal section is led by works in one of them (app/services/feed.py).
    priority: dict[str, str] = field(default_factory=dict)

    @property
    def uses_starter(self) -> bool:
        return self.item_count < STARTER_BELOW_ITEMS and bool(self.starter_tastes)

    @property
    def needs_starter(self) -> bool:
        return self.item_count < STARTER_BELOW_ITEMS and not self.starter_tastes and not self.priority

    def top_genres(self, n: int) -> list[str]:
        """Her best-liked genres, besides her priority tastes (those are asked for on their own)."""
        return [g for g, w in sorted(self.genres.items(), key=lambda kv: -kv[1]) if w > 0 and g not in self.priority][:n]

    def priority_of(self, genres: list[str]) -> str | None:
        """The first of her priority tastes (in her order) among these provider genres."""
        canonical = {canonical_genre(g) for g in genres}
        return next((g for g in self.priority if g in canonical), None)

    def top_authors(self, n: int) -> list[str]:
        """Display names of the best-liked authors."""
        return [self.author_names[a] for a, w in sorted(self.authors.items(), key=lambda kv: -kv[1]) if w > 0][:n]

    def label(self, genre: str) -> str:
        return self.labels.get(genre) or genre_label(genre)

    def _add_genre(self, genre: str, w: float, label: str | None = None) -> None:
        self.genres[genre] = self.genres.get(genre, 0.0) + w
        if label and (w > 0) and genre not in self.labels:
            self.labels[genre] = label

    def _add_author(self, name: str, w: float, title: str | None = None) -> None:
        key = name_key(name)
        if not key:
            return
        self.authors[key] = self.authors.get(key, 0.0) + w
        self.author_names.setdefault(key, name.strip())
        if title and w > 0:
            self.author_titles.setdefault(key, title)


def _seed_reason(item: LibraryItem, title: str) -> str | None:
    if item.status == "dropped":
        return None
    if item.rating and item.rating >= 4:
        return f"Giống {title} mà nàng chấm {round(item.rating)} tim"
    if item.is_favorite:
        return f"Giống {title} nàng yêu thích"
    if item.status == "completed":
        return f"Giống {title} nàng đã đọc xong"
    return None


def build_taste(
    items: list[LibraryItem], dismissals: list[FeedDismissal], starter_tastes: list[str], now: datetime, priority_tastes: list[str] = ()
) -> Taste:
    taste = Taste(item_count=len(items), starter_tastes=[t for t in starter_tastes if t in MOODS])
    for mood in (t for t in priority_tastes if t in MOODS):
        for genre in MOODS[mood].genres:
            taste.priority.setdefault(genre, MOODS[mood].short or mood)
    # the heaviest items first, so labels and author titles come from what she loves most
    weighted = sorted(((item_weight(i, now), i) for i in items), key=lambda p: -p[0])
    for w, item in weighted:
        work = item.work
        title = comics.view_title(item)
        own_tags = [link.tag.name for link in item.tag_links]
        genres: dict[str, str | None] = {}
        for tag in own_tags:  # her own tags: their wording is the label
            genres.setdefault(canonical_genre(tag), tag.strip())
        for g in work.genres:
            genres.setdefault(canonical_genre(g), None)
        for genre, label in genres.items():
            taste._add_genre(genre, w, label)
        for author in comics.split_authors(comics.view_author(item)):
            taste._add_author(author, w * AUTHOR_FACTOR, title)

        for provider, value in (work.external_ids or {}).items():
            taste.library_ids.add(f"{provider}:{value}")
        taste.library_titles.update(t for t in (fold(x) for x in [title, work.title, *work.alt_titles]) if t)

        reason = _seed_reason(item, title)
        if reason and w > 0 and work.external_ids:
            taste.seeds.append(Seed(title=title, weight=w, reason=reason, external_ids=dict(work.external_ids)))

    for d in dismissals:
        for g in {canonical_genre(g) for g in d.genres}:
            taste._add_genre(g, DISMISSED_GENRE)
        for a in d.authors:
            taste._add_author(a, DISMISSED_AUTHOR)
        taste.dismissed_ids.update(f"{p}:{v}" for p, v in {**d.external_ids, d.provider: d.external_id}.items())

    if taste.uses_starter:
        for mood in taste.starter_tastes:
            for genre in MOODS[mood].genres:
                taste._add_genre(genre, STARTER_WEIGHT, mood)
                taste.labels.setdefault(genre, mood)
    return taste


async def load_taste(db: AsyncSession, user: User) -> Taste:
    """Two queries: the library (with works and tags) and the dismissals."""
    items = list(
        await db.scalars(
            select(LibraryItem)
            .where(LibraryItem.user_id == user.id)
            .options(joinedload(LibraryItem.work, innerjoin=True), selectinload(LibraryItem.tag_links).joinedload(LibraryItemTag.tag))
        )
    )
    dismissals = list(await db.scalars(select(FeedDismissal).where(FeedDismissal.user_id == user.id)))
    return build_taste(items, dismissals, starter_tastes_of(user), datetime.now(UTC), priority_tastes_of(user))


def starter_tastes_of(user: User) -> list[str]:
    return [t for t in (user.settings or {}).get("starter_tastes") or [] if t in MOODS]


def priority_tastes_of(user: User) -> list[str]:
    return [t for t in (user.settings or {}).get("priority_tastes") or [] if t in MOODS]


def personalization_enabled(user: User) -> bool:
    return bool((user.settings or {}).get("personalization_enabled", True))
