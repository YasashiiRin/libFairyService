"""The taste profile and the feed's ranking, on objects in memory (no database, no network)."""

from datetime import UTC, date, datetime, timedelta

from app.models import FeedDismissal, LibraryItem, LibraryItemTag, Tag, Work
from app.providers.base import ProviderResult, name_key
from app.services.feed import Candidate, MAX_RUN, diversify, freshness, score, taste_match
from app.services.taste import MOODS, build_taste, canonical_genre, item_weight

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def item(title, genres=(), authors=(), tags=(), status="reading", rating=0.0, favorite=False, days_ago=200, ids=None):
    work = Work(title=title, alt_titles=[], genres=list(genres), authors=list(authors), external_ids=ids or {})
    return LibraryItem(
        work=work, status=status, rating=rating, is_favorite=favorite, current_chapter=1,
        last_read_at=NOW - timedelta(days=days_ago), updated_at=NOW - timedelta(days=days_ago),
        tag_links=[LibraryItemTag(tag=Tag(name=t), position=i) for i, t in enumerate(tags)],
    )


def result(eid, title, genres=(), authors=(), popularity=None, started_on=None, provider="mangadex"):
    return ProviderResult(provider=provider, external_id=eid, title=title, genres=list(genres), authors=list(authors),
                          popularity=popularity, started_on=started_on)


def test_vietnamese_tags_and_provider_genres_are_the_same_taste():
    assert canonical_genre("Đời thường") == canonical_genre("slice of life") == canonical_genre("Chữa lành") == "Slice of Life"
    assert canonical_genre("Học đường") == "School Life" and canonical_genre("Nấu ăn kiểu Huế") == "Nấu ăn kiểu Huế"


def test_what_counts_more_and_what_counts_against():
    loved = item("A", status="completed", rating=5, favorite=True, days_ago=5)
    liked = item("B", status="reading", days_ago=200)
    dropped = item("C", status="dropped", days_ago=5)
    assert item_weight(loved, NOW) > item_weight(liked, NOW) > 0 > item_weight(dropped, NOW)
    # recent activity counts more
    assert item_weight(item("D", days_ago=3), NOW) > item_weight(item("D", days_ago=120), NOW)


def test_taste_profile():
    taste = build_taste(
        [
            item("Frieren", genres=["Fantasy", "Adventure"], authors=["Yamada Kanehito"], status="completed", rating=5, days_ago=3,
                 ids={"anilist": "118586", "mangadex": "md-frieren"}),
            item("Quán trà", tags=["Chữa lành"], authors=["Hatori M."], rating=4, days_ago=10),
            item("Kiếm sĩ", genres=["Action"], status="dropped"),
        ],
        [FeedDismissal(provider="mangadex", external_id="md-x", external_ids={"anilist": "9"}, genres=["Romance"], authors=["Someone"])],
        starter_tastes=["Hài hước"],
        now=NOW,
    )
    assert taste.top_genres(2) == ["Fantasy", "Adventure"]
    assert taste.genres["Action"] < 0 and taste.genres["Romance"] < 0  # dropped, dismissed
    assert taste.label("Slice of Life") == "Chữa lành"  # her own tag's wording
    assert taste.label("Fantasy") == "Kỳ ảo"
    assert taste.top_authors(1) == ["Yamada Kanehito"] and taste.authors[name_key("Someone")] < 0
    assert [s.reason for s in taste.seeds] == ["Giống Frieren mà nàng chấm 5 tim"]  # "Quán trà" has no provider ids
    assert {"anilist:118586", "mangadex:md-frieren"} <= taste.library_ids and "frieren" in taste.library_titles
    assert {"mangadex:md-x", "anilist:9"} <= taste.dismissed_ids
    # 3 items < 5: the starter taste counts, labelled as she chose it
    assert taste.uses_starter and taste.genres["Comedy"] > 0 and taste.label("Comedy") == "Hài hước"
    assert not taste.needs_starter


def test_starter_tastes_are_only_for_a_small_library():
    big = [item(f"T{i}", genres=["Drama"]) for i in range(5)]
    taste = build_taste(big, [], ["Lãng mạn"], NOW)
    assert not taste.uses_starter and "Romance" not in taste.genres
    empty = build_taste([], [], [], NOW)
    assert empty.needs_starter and not empty.genres
    assert build_taste([], [], ["Không có"], NOW).starter_tastes == []  # unknown moods are ignored
    assert set(MOODS) == {
        "Chữa lành", "Cổ tích", "Lãng mạn", "Phiêu lưu", "Hài hước", "Học đường", "Kỳ ảo", "Đời thường", "Bách hợp (GL)", "Đam mỹ (BL)",
    }


def test_scores_and_reasons():
    taste = build_taste([item("Quán trà", tags=["Chữa lành"], rating=5, days_ago=2)], [], [], NOW)
    today = date(2026, 9, 29)
    cozy = Candidate(result=result("1", "Cozy", genres=["Slice of Life"], popularity=5000, started_on="2026-08"), members=[], origins={"genre"})
    loud = Candidate(result=result("2", "Loud", genres=["Action"], popularity=5000, started_on="2026-08"), members=[], origins={"genre"})
    for c in (cozy, loud):
        score(c, taste, today)
    assert cozy.score > loud.score and cozy.reason == "Vì nàng thích Chữa lành"
    fresh = Candidate(result=result("3", "New", genres=["Slice of Life"], started_on="2026-09-01"), members=[], origins={"fresh"})
    score(fresh, taste, today)
    assert fresh.section == "new_releases" and fresh.reason == "Mới ra mắt, đúng gu Chữa lành của nàng"
    trending = Candidate(result=result("4", "Hot", genres=["Slice of Life"], popularity=90000), members=[], origins={"trending"})
    score(trending, taste, today)
    assert trending.section == "trending" and trending.reason == "Đang được yêu thích trong Chữa lành"
    # without personalization: no taste, general reasons
    score(trending, None, today)
    assert trending.reason == "Đang được nhiều người yêu thích"
    assert taste_match(taste, result("5", "?", genres=["Horror"])) == (0.0, None)
    assert freshness(result("6", "old", started_on="1999"), today) < freshness(result("7", "new", started_on="2026-09-01"), today)


def test_variety_no_long_runs_of_one_author_or_genre():
    def c(i, author, genre, s):
        cand = Candidate(result=result(str(i), f"W{i}", genres=[genre], authors=[author]), members=[], origins={"genre"}, score=s)
        cand.main_genre = canonical_genre(genre)
        return cand

    ranked = [c(i, "Hatori M.", "Slice of Life", 10 - i) for i in range(6)] + [c(10, "Solaria", "Fantasy", 1), c(11, "Aoi", "Drama", 0.5)]
    out = diversify(ranked, 8)
    assert len(out) == 8
    for i in range(len(out) - MAX_RUN):
        window = out[i : i + MAX_RUN + 1]
        assert len({w.result.authors[0] for w in window}) > 1  # never 4 in a row by the same author
    # the best-scored still leads
    assert out[0].result.external_id == "0"


def test_reasons_name_different_genres_she_likes():
    from app.services.feed import SAME_LABEL, vary_reasons

    taste = build_taste([item("Quán trà", tags=["Chữa lành", "Lãng mạn"], rating=5, days_ago=2)], [], [], NOW)
    items = []
    for i in range(6):
        c = Candidate(result=result(str(i), f"W{i}", genres=["Slice of Life", "Romance"]), members=[], origins={"genre"})
        score(c, taste, date(2026, 9, 29), "for_you")
        items.append(c)
    assert {c.reason for c in items} == {"Vì nàng thích Chữa lành"}
    vary_reasons(items, taste)
    reasons = [c.reason for c in items]
    assert reasons.count("Vì nàng thích Chữa lành") == SAME_LABEL and reasons.count("Vì nàng thích Lãng mạn") == SAME_LABEL


# ── Priority tastes (settings.priority_tastes) ───────────────────────────


def test_girls_love_and_boys_love_under_every_name():
    for name in ("Girls' Love", "Yuri", "Shoujo Ai", "Bách hợp", "Bách hợp (GL)"):
        assert canonical_genre(name) == "Girls' Love", name
    for name in ("Boys' Love", "Yaoi", "Shounen Ai", "Đam mỹ", "Đam mỹ (BL)"):
        assert canonical_genre(name) == "Boys' Love", name


def test_anilist_tags_count_as_genres_when_central():
    from app.providers.anilist import parse_media

    media = {"id": 1, "title": {"romaji": "X"}, "genres": ["Romance"], "tags": [{"name": "Yuri", "rank": 80}, {"name": "Boys' Love", "rank": 30}]}
    assert parse_media(media).genres == ["Romance", "Girls' Love"]  # MangaDex's name; a minor tag doesn't count
    assert parse_media({"id": 2, "title": {"romaji": "Y"}, "genres": ["Drama"]}).genres == ["Drama"]  # search has no tags


def priority_taste(**kw):
    return build_taste([item("Quán trà", tags=["Chữa lành"], rating=5, days_ago=2)], [], [], NOW, ["Bách hợp (GL)", "Đam mỹ (BL)", "Không có"], **kw)


def test_priority_tastes_in_the_profile():
    taste = priority_taste()
    assert taste.priority == {"Girls' Love": "Bách hợp", "Boys' Love": "Đam mỹ"}  # her order; unknown moods ignored
    assert taste.priority_of(["Romance", "Yuri"]) == "Girls' Love" and taste.priority_of(["Romance"]) is None
    assert "Girls' Love" not in taste.top_genres(4)  # asked for on their own
    assert not build_taste([], [], [], NOW, ["Đam mỹ (BL)"]).needs_starter  # she told us what she likes


def test_a_priority_taste_outranks_every_other_signal():
    taste = priority_taste()
    today = date(2026, 9, 29)
    best_other = Candidate(result=result("1", "Cozy", genres=["Slice of Life"], popularity=10**6, started_on="2026-09-01"),
                           members=[], origins={"rec"}, rec=(50, "x"), author="Hatori M.")
    gl = Candidate(result=result("2", "GL", genres=["Horror", "Girls' Love"]), members=[], origins={"genre"})
    for c in (best_other, gl):
        score(c, taste, today, "for_you")
    assert gl.score > best_other.score and gl.priority == "Girls' Love" and gl.reason == "Vì nàng mê Bách hợp"

    reasons = {}
    for section, origin in (("new_releases", "fresh"), ("trending", "trending")):
        bl = Candidate(result=result("3", "BL", genres=["Yaoi"]), members=[], origins={origin})
        score(bl, taste, today, section)
        reasons[section] = bl.reason
    assert reasons == {"new_releases": "Đam mỹ mới ra mắt", "trending": "Đam mỹ đang được yêu thích"}


def test_priority_works_lead_with_variety_by_author_only():
    taste = priority_taste()
    today = date(2026, 9, 29)

    def c(i, author, genres, s=0.0):
        cand = Candidate(result=result(str(i), f"W{i}", genres=genres, authors=[author]), members=[], origins={"genre"})
        score(cand, taste, today, "for_you")
        cand.score += s
        return cand

    others = [c(i, f"Other {i}", ["Slice of Life"], 1.0) for i in range(3)]  # good matches, but not a priority taste
    gl = [c(10 + i, "Yuri Sensei", ["Girls' Love"], -i / 100) for i in range(5)] + [c(20, "Hana", ["Girls' Love"], -0.5)]
    out = diversify(sorted(others + gl, key=lambda x: -x.score), 9)
    assert [x.priority is not None for x in out] == [True] * 6 + [False] * 3  # priority works lead, the others fill
    assert [x.result.authors[0] for x in out[:6]] == ["Yuri Sensei"] * 3 + ["Hana"] + ["Yuri Sensei"] * 2
    # the main-genre rule doesn't split them up: all six are Girls' Love and still come first


def test_collect_merges_like_same_work_without_comparing_everything():
    import time

    from app.services.discover import _same_work
    from app.services.feed import collect

    def e(provider, eid, title, authors=(), alt=(), ids=None):
        r = ProviderResult(provider=provider, external_id=eid, title=title, alt_titles=list(alt), authors=list(authors),
                           external_ids=ids or {})
        return {"origin": "genre", "result": r.to_json()}

    entries = [
        e("mangadex", "m1", "Yagate Kimi ni Naru", ["Nakatani Nio"], ids={"anilist": "1"}),
        e("anilist", "1", "Bloom Into You", ["Nio Nakatani"]),  # the same work by id
        e("anilist", "2", "Yagate Kimi ni Naru", ["Nakatani Nio"], alt=["Bloom"]),  # by title and author
        e("anilist", "3", "Yagate Kimi ni Naru", ["Someone Else"]),  # same title, another author: another work
        e("mangadex", "m4", "Untitled", []),
        e("anilist", "4", "untitled!", ["Anyone"]),  # no authors on one side: merged by title
    ]
    groups = collect(entries)
    assert [[m.external_id for m in c.members] for c in groups] == [["m1", "1", "2"], ["3"], ["m4", "4"]]
    results = [ProviderResult.from_json(x["result"]) for x in entries]
    assert _same_work(results[0], results[2]) and not _same_work(results[0], results[3]) and _same_work(results[4], results[5])

    many = [e("mangadex", f"id{i}", f"Title {i}", [f"Author {i}"], alt=[f"Alt {i} {j}" for j in range(8)]) for i in range(1500)]
    start = time.monotonic()
    assert len(collect(many)) == 1500
    assert time.monotonic() - start < 1.0  # was quadratic: seconds for a few hundred
