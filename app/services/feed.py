"""The home feed "Khu vườn hôm nay": what is new each day, for this user's taste.

Built once a day per user (by the daily cron, or on the first home request), from the official APIs
(MangaDex, AniList; safe content only). Each provider request is a *source*: its answer is cached in
search_cache for the day, shared by every user with the same tastes, so a build that ran out of time
picks up where it stopped. Nothing becomes a work until the user saves it (POST /api/library).

Sections (feed_items.section):
  for_you       AniList recommendations for works she loved, her authors, her top genres
  new_releases  recently started series in her genres
  trending      what readers love right now in her genres
With personalization off, only new_releases and trending are built, from everyone's popularity.

Her priority tastes (settings.priority_tastes, e.g. Bách hợp / Đam mỹ) come first: they are asked for
on their own (new, trending and loved works in each), score above any other signal, and lead every
personal section; the rest of a section is her other good matches.
"""

import asyncio
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import FeedBuild, FeedDismissal, FeedItem, RefreshToken, SearchCache, User
from app.providers import PROVIDERS, ProviderError, ProviderResult
from app.providers.anilist import GENRE_TAGS, GENRES, AniList
from app.providers.base import fold, name_key
from app.providers.http import new_client
from app.providers.mangadex import MangaDex
from app.services import comics, taste as taste_service
from app.services.discover import cache_key, merge_group
from app.services.taste import Taste, canonical_genre

logger = logging.getLogger(__name__)

SECTIONS = ("for_you", "new_releases", "trending")
SECTION_SIZES = {"for_you": 16, "new_releases": 12, "trending": 12}  # about 40 a day
KEEP_DAYS = 3  # feed_items older than this are deleted by the daily cron
SOURCE_TTL_HOURS = 20  # a day's provider answers (shared by users with the same tastes)
TAGS_TTL_HOURS = 24 * 7  # MangaDex's tag list rarely changes
MAX_ATTEMPTS = 3  # a feed still missing sources after this many builds is left as it is
MAX_RUN = 3  # variety: no more than this many in a row by the same author or main genre
TOP_GENRES = 4
TOP_AUTHORS = 2
SEEDS = 3  # works she loved, asked for recommendations
MIN_TASTE = -0.15  # below this, a candidate looks like what she dropped or dismissed
PRIORITY_BOOST = 2.0  # more than any score without it can reach (at most about 1.8)
ANILIST_TAGS = {genre: tag for tag, genre in GENRE_TAGS.items()}  # "Girls' Love" → "Yuri"


# ── Sources: one provider request each, cached ───────────────────────────


@dataclass
class Source:
    key: str  # search_cache key
    provider: str
    fetch: Callable[[httpx.AsyncClient], Awaitable[list[dict[str, Any]]]]
    ttl_hours: int = SOURCE_TTL_HOURS


def _entry(origin: str, r: ProviderResult, **extra: Any) -> dict[str, Any]:
    """What a source keeps: where the candidate comes from and its data (no user-specific text:
    sources are shared between users)."""
    return {"origin": origin, "result": r.to_json(), **extra}


def _fuzzy_date(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _anilist_pages(today: date, **only: Any) -> dict[str, dict[str, Any]]:
    """The feed's lists: new series, trending, and (when filtered) the best-loved; `only` is
    genres=[…] or tag="Yuri"."""
    started_after = _fuzzy_date(today - timedelta(days=365))
    pages: dict[str, dict[str, Any]] = {
        "fresh": {"sort": "START_DATE_DESC", "started_after": started_after, "releasing": True, "min_popularity": 50, **only},
        "trending": {"sort": "TRENDING_DESC", **only},
    }
    if any(only.values()):
        pages["genre"] = {"sort": "POPULARITY_DESC", **only}
    return pages


def anilist_browse(genres: list[str], today: date) -> list[Source]:
    """One request per list: AniList answers a genre-filtered list in a few seconds, and aliases in one
    request are answered one after another, so separate requests (paced 1 s apart) finish sooner.
    AniList's genre_in means "every one of these", so its lists are in her top genre only (MangaDex's
    take any of her genres; the ranking weighs the rest)."""
    genres = [g for g in genres if g in GENRES][:1]
    pages = _anilist_pages(today, genres=genres)

    def source(origin: str, spec: dict[str, Any]) -> Source:
        async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
            answer = await AniList(client).browse({origin: spec})
            if not answer.get(origin):
                # AniList sometimes answers an empty list when it is overloaded: not cached, asked again
                raise ProviderError("anilist: empty list")
            return [_entry(origin, r) for r in answer[origin]]

        return Source(cache_key("feed", "anilist", origin, today, sorted(genres)), "anilist", fetch)

    return [source(origin, spec) for origin, spec in pages.items()]


def anilist_priority(genre: str, today: date) -> Source | None:
    """A priority taste's three lists in one request (a tag-filtered list is quick, and her priority
    tastes shouldn't wait behind each other at one request a second)."""
    if genre in GENRES:
        pages = _anilist_pages(today, genres=[genre])
    elif genre in ANILIST_TAGS:
        pages = _anilist_pages(today, tag=ANILIST_TAGS[genre])
    else:
        return None  # not something AniList can filter by (MangaDex may still have it)

    async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        answer = await AniList(client).browse(pages)
        if not any(answer.values()):
            raise ProviderError("anilist: empty lists")  # overloaded (see anilist_browse): asked again
        return [_entry(origin, r) for origin, results in answer.items() for r in results]

    return Source(cache_key("feed", "anilist", "priority", today, genre), "anilist", fetch)


def anilist_recommendations(seed_ids: list[str], today: date) -> Source:
    async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        answer = await AniList(client).recommendations(seed_ids)
        return [_entry("rec", r, seed=f"anilist:{wid}", rating=rating) for wid, recs in answer.items() for rating, r in recs]

    return Source(cache_key("feed", "anilist", "recs", today, sorted(seed_ids)), "anilist", fetch)


def mangadex_tags() -> Source:
    async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        return [{"tags": await MangaDex(client).tag_ids()}]

    return Source(cache_key("feed", "mangadex", "tags"), "mangadex", fetch, ttl_hours=TAGS_TTL_HOURS)


def mangadex_browse(genres: list[str], tag_map: dict[str, str], today: date) -> Source:
    tag_ids = sorted({tag_map[g] for g in genres if g in tag_map})

    async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        md = MangaDex(client)
        if genres and not tag_ids:
            return []  # none of her genres is a MangaDex tag
        fresh = await md.browse(tag_ids=tag_ids, order="followedCount", year=today.year)
        if len(fresh) < 8:  # early in the year: last year's new series too
            fresh += await md.browse(tag_ids=tag_ids, order="followedCount", year=today.year - 1)
        out = [_entry("fresh", r) for r in fresh]
        out += [_entry("trending", r) for r in await md.browse(tag_ids=tag_ids, order="followedCount", updated_within_days=7)]
        if tag_ids:
            out += [_entry("genre", r) for r in await md.browse(tag_ids=tag_ids, order="rating")]
        return await _with_follows(md, out)

    return Source(cache_key("feed", "mangadex", "browse", today, tag_ids), "mangadex", fetch)


def mangadex_author(author: str, today: date) -> Source:
    async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        md = MangaDex(client)
        author_id = await md.find_author(author)
        if not author_id:
            return []
        return await _with_follows(md, [_entry("author", r, author=author) for r in await md.browse(author_id=author_id, order="followedCount", limit=10)])

    return Source(cache_key("feed", "mangadex", "author", today, name_key(author)), "mangadex", fetch)


async def _with_follows(md: MangaDex, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MangaDex's lists don't say how popular a work is: one statistics request for all of them."""
    ids = list(dict.fromkeys(e["result"]["external_id"] for e in entries))
    follows = await md.statistics(ids) if ids else {}
    for e in entries:
        e["result"]["popularity"] = follows.get(e["result"]["external_id"])
    return entries


@dataclass
class Fetched:
    answers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # key → entries
    missing: list[str] = field(default_factory=list)  # not answered in time: fetched on the next build
    failed: list[str] = field(default_factory=list)  # errors (retried on the next build too)


async def run_sources(
    db: AsyncSession, client: httpx.AsyncClient, sources: list[Source], deadline: float, into: Fetched, per_source: float | None = None
) -> None:
    """Cached answers in one query; the rest from the providers at once, until the deadline.
    Provider calls never touch the session (one session can't be used by concurrent tasks)."""
    if not sources:
        return
    rows = await db.execute(
        select(SearchCache.key, SearchCache.results).where(
            SearchCache.key.in_([s.key for s in sources]), SearchCache.expires_at > func.now()
        )
    )
    cached = dict(rows.all())
    todo = []
    for s in sources:
        if s.key in cached:
            into.answers[s.key] = cached[s.key]
        else:
            todo.append(s)
    remaining = deadline - time.monotonic()
    if not todo:
        return
    if remaining <= 0.2:
        into.missing += [s.key for s in todo]
        return
    per_source = min(remaining, per_source or get_settings().provider_timeout_seconds * 2)
    tasks = {asyncio.create_task(asyncio.wait_for(s.fetch(client), timeout=per_source)): s for s in todo}
    done, pending = await asyncio.wait(tasks, timeout=remaining)
    answered: list[dict[str, Any]] = []
    for task in pending:
        task.cancel()
        into.missing.append(tasks[task].key)
    for task in done:
        s = tasks[task]
        if task.cancelled() or task.exception() is not None:
            logger.info("feed source %s skipped: %r", s.provider, None if task.cancelled() else task.exception())
            into.failed.append(s.key)
            continue
        entries = task.result()
        into.answers[s.key] = entries
        expires = datetime.now(UTC) + timedelta(hours=s.ttl_hours)
        answered.append({"key": s.key, "provider": f"feed:{s.provider}", "results": entries, "expires_at": expires})
    if answered:  # one statement for all of them: the deadline has passed, the page is waiting
        stmt = insert(SearchCache).values(answered)
        await db.execute(
            stmt.on_conflict_do_update(index_elements=["key"], set_={"results": stmt.excluded.results, "expires_at": stmt.excluded.expires_at})
        )


# ── Candidates and scores ────────────────────────────────────────────────


@dataclass
class Candidate:
    result: ProviderResult
    members: list[ProviderResult]
    origins: set[str] = field(default_factory=set)  # rec, author, genre, fresh, trending
    rec: tuple[int, str] | None = None  # (rating, seed id) of the best recommendation
    author: str | None = None
    priority: str | None = None  # the priority taste it is in (canonical genre)
    score: float = 0.0
    match: float = 0.0  # taste match alone
    section: str = ""
    reason: str = ""
    main_genre: str = ""
    liked: list[str] = field(default_factory=list)  # her liked genres among the work's, best first


def _ids(r: ProviderResult) -> set[str]:
    return {f"{p}:{v}" for p, v in {**r.external_ids, r.provider: r.external_id}.items() if v}


def collect(entries: list[dict[str, Any]]) -> list[Candidate]:
    """Merge the same work from different sources and providers: as discover._same_work (a shared id,
    or a shared title and a shared author, or no authors to compare), but through indexes of ids and
    folded titles, since a feed has hundreds of entries (comparing each to all is seconds of CPU)."""
    groups: list[Candidate] = []
    by_id: dict[str, int] = {}  # id → group index
    by_title: dict[str, list[tuple[int, set[str]]]] = {}  # folded title → (group index, that member's authors)
    for e in entries:
        if "result" not in e:
            continue
        r = ProviderResult.from_json(e["result"])
        ids = _ids(r)
        titles = {t for t in (fold(x) for x in [r.title, *r.alt_titles]) if t}
        authors = {name_key(a) for a in r.authors}
        matches = {by_id[i] for i in ids if i in by_id} | {
            g for t in titles for g, other in by_title.get(t, ()) if not authors or not other or authors & other
        }
        if matches:
            index = min(matches)  # the first group it matches, as before
            c = groups[index]
            c.members.append(r)
        else:
            index = len(groups)
            c = Candidate(result=r, members=[r])
            groups.append(c)
        for i in ids:
            by_id.setdefault(i, index)
        for t in titles:
            by_title.setdefault(t, []).append((index, authors))
        c.origins.add(e["origin"])
        if e["origin"] == "rec" and (c.rec is None or e.get("rating", 0) > c.rec[0]):
            c.rec = (int(e.get("rating", 0)), e.get("seed", ""))
        if e["origin"] == "author":
            c.author = c.author or e.get("author")
    for c in groups:
        lead = merge_group(c.members)
        lead.popularity = max((m.popularity or 0 for m in c.members), default=0) or None
        lead.trending = max((m.trending or 0 for m in c.members), default=0) or None
        lead.started_on = next((m.started_on for m in c.members if m.started_on and len(m.started_on) >= 7), None) or next(
            (m.started_on for m in c.members if m.started_on), None
        )
        c.result = lead
    return groups


def excluded(c: Candidate, taste: Taste) -> bool:
    """Already in her library (by any id, or by title: works entered by hand have no ids), or dismissed."""
    ids = set().union(*(_ids(m) for m in c.members))
    if ids & taste.library_ids or ids & taste.dismissed_ids:
        return True
    titles = {fold(t) for m in c.members for t in [m.title, *m.alt_titles]}
    return bool(titles & taste.library_titles)


def taste_match(taste: Taste, r: ProviderResult) -> tuple[float, str | None]:
    """→ (-1..1.5, her best-liked genre among the work's genres)."""
    genres = {canonical_genre(g) for g in r.genres}
    weights = {g: taste.genres.get(g, 0.0) for g in genres}
    author = sum(taste.authors.get(name_key(a), 0.0) for a in r.authors)
    top = max((w for w in taste.genres.values() if w > 0), default=0.0)
    if top <= 0 and author <= 0:
        return 0.0, None
    liked = liked_genres(taste, r)
    raw = (sum(weights.values()) + author) / (max(top, 1.0) * 2)
    return max(-1.0, min(1.5, raw)), (liked[0] if liked else None)


def liked_genres(taste: Taste, r: ProviderResult) -> list[str]:
    """Her liked genres among the work's, best first (ties keep the provider's order)."""
    genres = list(dict.fromkeys(canonical_genre(g) for g in r.genres))
    return [g for g in sorted(genres, key=lambda g: -taste.genres.get(g, 0.0)) if taste.genres.get(g, 0.0) > 0]


def popularity(r: ProviderResult) -> float:
    pop = min(1.0, math.log10(1 + (r.popularity or 0)) / 5)  # 100k readers → 1
    trend = min(1.0, (r.trending or 0) / 150)
    return max(pop, trend)


def freshness(r: ProviderResult, today: date) -> float:
    if not r.started_on:
        return 0.2
    parts = [int(p) for p in r.started_on.split("-")]
    started = date(parts[0], parts[1] if len(parts) > 1 else 7, parts[2] if len(parts) > 2 else 1)
    days = (today - started).days
    return 1.0 if days <= 120 else 0.7 if days <= 365 else 0.35 if days <= 3 * 365 else 0.1


def eligible(c: Candidate, personal: bool) -> list[str]:
    """The sections a candidate fits, by where it came from (in the order preferred on a tie)."""
    out = ["for_you"] if personal and c.origins & {"rec", "author"} else []  # its reason is about her: first choice
    if "fresh" in c.origins:
        out.append("new_releases")
    if "trending" in c.origins:
        out.append("trending")
    if personal and "genre" in c.origins:
        out.append("for_you")
    return out or (["for_you"] if personal else ["trending"])


def score(c: Candidate, taste: Taste | None, today: date, section: str | None = None) -> None:
    """Taste match + provider popularity + freshness, weighted for the section; sets the reason."""
    r = c.result
    match, genre = taste_match(taste, r) if taste else (0.0, None)
    pop, fresh = popularity(r), freshness(r, today)
    c.section = section or eligible(c, taste is not None)[0]
    c.main_genre = canonical_genre(r.genres[0]) if r.genres else ""
    c.liked = liked_genres(taste, r) if taste else []
    c.priority = taste.priority_of(r.genres) if taste else None
    label = taste.label(genre) if taste and genre else None
    seeds = {sid: s for s in (taste.seeds if taste else []) for sid in (f"{p}:{v}" for p, v in s.external_ids.items())}

    if c.section == "for_you":
        rec_bonus = 0.35 * min(c.rec[0], 50) / 50 if c.rec else 0.0
        author_bonus = 0.25 if c.author else 0.0
        c.score = 0.55 * match + 0.2 * pop + 0.1 * fresh + rec_bonus + author_bonus
        seed = seeds.get(c.rec[1]) if c.rec else None
        if seed:
            c.reason = seed.reason
        elif c.author:
            title = taste.author_titles.get(name_key(c.author)) if taste else None
            c.reason = f"Cùng tác giả {c.author} với {title}" if title else f"Của {c.author}, tác giả nàng mê"
        else:
            c.reason = f"Vì nàng thích {label}" if label else "Có thể nàng sẽ thích"
    elif c.section == "new_releases":
        c.score = 0.4 * match + 0.2 * pop + 0.4 * fresh
        c.reason = f"Mới ra mắt, đúng gu {label} của nàng" if label else "Mới ra mắt gần đây"
    else:
        c.score = 0.35 * match + 0.5 * pop + 0.15 * fresh
        c.reason = f"Đang được yêu thích trong {label}" if label else "Đang được nhiều người yêu thích"
    if c.priority and taste:
        c.score += PRIORITY_BOOST
        c.reason = priority_reason(c, taste, seeds.get(c.rec[1]) if c.rec else None)
    c.score = round(c.score, 4)
    c.match = match


def priority_reason(c: Candidate, taste: Taste, seed: taste_service.Seed | None) -> str:
    """"Bách hợp mới ra mắt", "Đam mỹ đang được yêu thích", "Bách hợp, giống Frieren mà nàng chấm 5 tim"."""
    label = taste.priority[c.priority]
    if c.section == "new_releases":
        return f"{label} mới ra mắt"
    if c.section == "trending":
        return f"{label} đang được yêu thích"
    if seed:
        return f"{label}, {seed.reason[0].lower()}{seed.reason[1:]}"
    if c.author:
        return f"{label} của {c.author}, tác giả nàng mê"
    return f"Vì nàng mê {label}"


def diversify(ranked: list[Candidate], limit: int) -> list[Candidate]:
    """Best first, but never more than MAX_RUN in a row by the same author or with the same main genre.
    Works in her priority tastes all come before the others; among them only the author rule holds
    (they share her favourite genre on purpose)."""
    out: list[Candidate] = []
    pool = sorted(ranked, key=lambda c: c.priority is None)  # stable: best first within each

    def breaks_run(c: Candidate) -> bool:
        last = out[-MAX_RUN:]
        if len(last) < MAX_RUN:
            return False
        same_genre = not c.priority and c.main_genre and all(x.main_genre == c.main_genre for x in last)
        authors = {name_key(a) for a in c.result.authors}
        same_author = bool(authors) and all(authors & {name_key(a) for a in x.result.authors} for x in last)
        return bool(same_genre or same_author)

    while pool and len(out) < limit:
        tier = [c for c in pool if (c.priority is None) == (pool[0].priority is None)]  # never skip ahead of a priority work
        pick = next((c for c in tier if not breaks_run(c)), tier[0])
        out.append(pick)
        pool.remove(pick)
    return out


def arrange(candidates: list[Candidate], taste: Taste | None, today: date) -> dict[str, list[Candidate]]:
    """Each work goes to one section: the one it fits that is least full so far (the best candidates
    choose first), so a popular new series in her genres doesn't leave "trending" empty."""
    personal = taste is not None
    for c in candidates:
        score(c, taste, today)  # a first score, to decide who chooses first
    kept = sorted((c for c in candidates if not personal or c.match >= MIN_TASTE or c.priority), key=lambda c: -c.score)
    pools: dict[str, list[Candidate]] = {s: [] for s in SECTIONS}
    for c in kept:
        options = eligible(c, personal)
        open_ = [s for s in options if len(pools[s]) < SECTION_SIZES[s]] or options
        if open_[0] == "for_you" and c.origins & {"rec", "author"}:
            section = "for_you"  # a recommendation or her author stays "for her" while there is room
        else:
            section = min(open_, key=lambda s: (len(pools[s]) / SECTION_SIZES[s], options.index(s)))
        score(c, taste, today, section)
        pools[section].append(c)
    sections = {s: diversify(sorted(pool, key=lambda c: -c.score), SECTION_SIZES[s]) for s, pool in pools.items()}
    if taste:
        for items in sections.values():
            vary_reasons(items, taste)
    return sections


GENRE_REASONS = ("Vì nàng thích {}", "Mới ra mắt, đúng gu {} của nàng", "Đang được yêu thích trong {}")
SAME_LABEL = 3  # after this many reasons naming one genre, name the work's next liked genre


def vary_reasons(items: list[Candidate], taste: Taste) -> None:
    """"Vì nàng thích Chữa lành" sixteen times says little: once a genre was named SAME_LABEL times in
    a section, a work that matches another of her genres is explained by that one."""
    used: dict[str, int] = {}
    for c in items:
        template = next((t for t in GENRE_REASONS if c.liked and c.reason == t.format(taste.label(c.liked[0]))), None)
        if template is None:
            continue
        genre = next((g for g in c.liked if used.get(taste.label(g), 0) < SAME_LABEL), c.liked[0])
        label = taste.label(genre)
        used[label] = used.get(label, 0) + 1
        c.reason = template.format(label)


# ── Building today's feed ────────────────────────────────────────────────


def _credits(r: ProviderResult, members: list[ProviderResult]) -> list[dict[str, str]]:
    providers = [r.provider, *(m.provider for m in members if m.provider != r.provider)]
    ids = {**r.external_ids, r.provider: r.external_id}
    out = []
    for p in dict.fromkeys(providers):
        if p in PROVIDERS and ids.get(p):
            out.append({"provider": p, "name": PROVIDERS[p].label, "url": PROVIDERS[p].page_url(ids[p])})
    return out


async def _try_lock(db: AsyncSession, user_id: uuid.UUID, day: date) -> bool:
    """One build per user and day at a time (two tabs opening the home page at once)."""
    return bool(await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"feed:{user_id}:{day}"}))


def plan(taste: Taste | None, today: date) -> tuple[list[Source], Callable[[dict[str, str]], list[Source]]]:
    """→ (sources to ask now, sources that need MangaDex's tag list)."""
    genres = taste.top_genres(TOP_GENRES) if taste else []
    priority = list(taste.priority) if taste else []
    # her priority tastes first: AniList answers one request a second, in the order they are made
    first = [s for s in (anilist_priority(g, today) for g in priority) if s]
    first += anilist_browse(genres, today)
    if taste:
        seed_ids = [s.external_ids["anilist"] for s in sorted(taste.seeds, key=lambda s: -s.weight) if s.external_ids.get("anilist")][:SEEDS]
        if seed_ids:
            first.append(anilist_recommendations(seed_ids, today))
        first += [mangadex_author(a, today) for a in taste.top_authors(TOP_AUTHORS)]
    return first, lambda tag_map: [*(mangadex_browse([g], tag_map, today) for g in priority if g in tag_map), mangadex_browse(genres, tag_map, today)]


async def build(db: AsyncSession, user: User, day: date, seconds: float, per_source: float | None = None) -> FeedBuild:
    """Build (or finish) the user's feed for `day` within `seconds` (each provider request gets at most
    `per_source`). The caller commits."""
    deadline = time.monotonic() + seconds
    record = await db.get(FeedBuild, (user.id, day)) or FeedBuild(user_id=user.id, feed_date=day, status="partial", attempts=0, missing=[])
    db.add(record)
    library = await taste_service.load_taste(db, user)  # also what she has and dismissed: never suggested
    taste: Taste | None = library if taste_service.personalization_enabled(user) else None
    if taste and not any(w > 0 for w in taste.genres.values()) and not taste.seeds and not taste.priority:
        taste = None  # nothing to go on yet (no library, no starter tastes): everyone's favourites

    fetched = Fetched()
    sources, needs_tags = plan(taste, day)
    tags = mangadex_tags()
    async with new_client() as client:
        # MangaDex filters by tag ids: the tag list first (cached for a week, so usually instant),
        # then every source at once, so a slow provider never holds up the others
        await run_sources(db, client, [tags], min(deadline, time.monotonic() + get_settings().provider_timeout_seconds), fetched)
        tag_map = (fetched.answers.get(tags.key) or [{}])[0].get("tags") or {}
        await run_sources(db, client, sources + needs_tags(tag_map), deadline, fetched, per_source)
    entries = [e for key, answer in fetched.answers.items() if key != tags.key for e in answer]

    candidates = [c for c in collect(entries) if not excluded(c, library)]
    sections = arrange(candidates, taste, day)

    await db.execute(delete(FeedItem).where(FeedItem.user_id == user.id, FeedItem.feed_date == day))
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": user.id,
            "feed_date": day,
            "section": section,
            "position": i,
            "provider": c.result.provider,
            "external_id": c.result.external_id,
            "external_ids": {**c.result.external_ids, c.result.provider: c.result.external_id},
            "title": c.result.title[:300],
            "cover_url": c.result.cover_url,
            "authors": c.result.authors[:6],
            "genres": c.result.genres[:8],
            "latest_chapter": c.result.latest_chapter,
            "publication_status": c.result.status,
            "attribution": _credits(c.result, c.members),
            "score": c.score,
            "reason": c.reason,
        }
        for section, items in sections.items()
        for i, c in enumerate(items)
    ]
    if rows:
        await db.execute(insert(FeedItem).values(rows).on_conflict_do_nothing())

    record.attempts += 1
    record.missing = sorted(set(fetched.missing) | set(fetched.failed))
    record.status = "complete" if not record.missing or record.attempts >= MAX_ATTEMPTS else "partial"
    logger.info("feed for %s on %s: %s, %d items, missing %d", user.id, day, record.status, len(rows), len(record.missing))
    return record


async def ensure_today(db: AsyncSession, user: User, seconds: float, retry_seconds: float) -> tuple[FeedBuild | None, bool]:
    """Today's feed, built or finished now if needed → (its build record, built now). The first build
    of the day may take `seconds`; finishing a partial one only `retry_seconds` (the page shouldn't be
    slow again: the cron, or the next visit, adds the rest). The record is None when another request is
    building it at this moment (its items show next time)."""
    day = comics.today()
    record = await db.get(FeedBuild, (user.id, day))
    if record is not None and record.status == "complete":
        return record, False
    if not await _try_lock(db, user.id, day):
        return record, False
    return await build(db, user, day, retry_seconds if record is not None else seconds), True


async def today_items(db: AsyncSession, user_id: uuid.UUID) -> dict[str, list[FeedItem]]:
    rows = await db.scalars(
        select(FeedItem)
        .where(FeedItem.user_id == user_id, FeedItem.feed_date == comics.today())
        .order_by(FeedItem.section, FeedItem.position)
    )
    out: dict[str, list[FeedItem]] = {s: [] for s in SECTIONS}
    for row in rows:
        out[row.section].append(row)
    return out


async def reset_today(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Tastes changed: today's feed is built again on the next home request."""
    day = comics.today()
    await db.execute(delete(FeedItem).where(FeedItem.user_id == user_id, FeedItem.feed_date == day))
    await db.execute(delete(FeedBuild).where(FeedBuild.user_id == user_id, FeedBuild.feed_date == day))


def _matching(user_id: uuid.UUID, ids: dict[str, str]):
    pairs = [and_(FeedItem.provider == p, FeedItem.external_id == v) for p, v in ids.items() if v]
    return and_(FeedItem.user_id == user_id, or_(*pairs)) if pairs else None


async def dismiss(db: AsyncSession, user_id: uuid.UUID, provider: str, external_id: str) -> None:
    """"Không hứng thú": never suggested again, and its genres and authors count less (idempotent)."""
    shown = await db.scalar(
        select(FeedItem)
        .where(FeedItem.user_id == user_id, FeedItem.provider == provider, FeedItem.external_id == external_id)
        .order_by(FeedItem.feed_date.desc())
        .limit(1)
    )
    ids = {**(shown.external_ids if shown else {}), provider: external_id}
    await db.execute(
        insert(FeedDismissal)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=provider,
            external_id=external_id,
            external_ids=ids,
            title=shown.title if shown else "",
            genres=shown.genres if shown else [],
            authors=shown.authors if shown else [],
        )
        .on_conflict_do_nothing()
    )
    await db.execute(delete(FeedItem).where(_matching(user_id, ids)))


async def forget_saved(db: AsyncSession, user_id: uuid.UUID, external_ids: dict[str, str]) -> None:
    """A suggestion she just saved leaves the feed (it is in her library now)."""
    cond = _matching(user_id, external_ids)
    if cond is not None:
        await db.execute(delete(FeedItem).where(cond))


# ── The daily cron ───────────────────────────────────────────────────────

ACTIVE_WITHIN_DAYS = 30


async def delete_old(db: AsyncSession) -> int:
    oldest = comics.today() - timedelta(days=KEEP_DAYS - 1)
    items = await db.execute(delete(FeedItem).where(FeedItem.feed_date < oldest))
    await db.execute(delete(FeedBuild).where(FeedBuild.feed_date < oldest))
    return items.rowcount or 0


async def users_to_build(db: AsyncSession, limit: int) -> list[User]:
    """Active users (signed in within 30 days) whose feed for today isn't complete, most recent first."""
    day = comics.today()
    since = datetime.now(UTC) - timedelta(days=ACTIVE_WITHIN_DAYS)
    last_active = (
        select(func.max(RefreshToken.last_active_at)).where(RefreshToken.user_id == User.id).correlate(User).scalar_subquery()
    )
    done = exists().where(FeedBuild.user_id == User.id, FeedBuild.feed_date == day, FeedBuild.status == "complete")
    return list(
        await db.scalars(
            select(User)
            .where(User.is_active.is_(True), last_active > since, ~done)
            .order_by(last_active.desc())
            .limit(limit)
        )
    )


CRON_PER_SOURCE_SECONDS = 15.0  # nobody is waiting: slow providers get more time


async def build_for_active_users(db: AsyncSession, budget_seconds: float, per_user_seconds: float) -> int:
    """Builds feeds in a small batch, committing after each user, until the time budget runs low."""
    start = time.monotonic()
    built = 0
    for user in await users_to_build(db, limit=50):
        remaining = budget_seconds - (time.monotonic() - start)
        if remaining < per_user_seconds:
            break
        day = comics.today()
        if await _try_lock(db, user.id, day):
            await build(db, user, day, per_user_seconds, CRON_PER_SOURCE_SECONDS)
            built += 1
        await db.commit()
    return built
