"""Provider adapters: parsing, links, rate limiting (no network)."""

import pytest

from app.providers import provider_for_link
from app.providers.anilist import parse_media
from app.providers.base import RateLimited, RateLimiter, chapter_number, clean_text
from app.providers.http import new_client, user_agent
from app.providers.mangadex import parse_manga
from tests.fake_providers import ANILIST, MANGADEX, TIEM_AL, TIEM_MD, VUON_MD


def test_mangadex_parsing():
    r = parse_manga(MANGADEX[TIEM_MD])
    assert (r.provider, r.external_id, r.title) == ("mangadex", TIEM_MD, "Time Grocery")
    assert r.alt_titles == ["Tiệm Tạp Hóa Thời Gian"]
    assert r.cover_url == f"https://uploads.mangadex.org/covers/{TIEM_MD}/cover.jpg.256.jpg"
    assert r.authors == ["Hatori M."]
    assert r.genres == ["Slice of Life", "Magic"]  # genre and theme tags, not formats
    assert (r.status, r.latest_chapter) == ("ongoing", 120)
    assert r.external_ids == {"mangadex": TIEM_MD, "anilist": TIEM_AL, "mal": "201"}  # cross ids from links
    assert [(link.site_name, link.url) for link in r.links] == [
        ("MangaDex", f"https://mangadex.org/title/{TIEM_MD}"),
        ("Raw", "https://raw.example.jp/time-grocery"),  # the official raw link; trackers like "mu" are not sources
    ]
    assert parse_manga(MANGADEX[VUON_MD]).status == "completed"


def test_anilist_parsing():
    r = parse_media(ANILIST[TIEM_AL])
    assert (r.provider, r.external_id, r.title) == ("anilist", "101", "Time Grocery")
    assert r.alt_titles == ["Jikan no Zakkaya"]
    assert r.authors == ["Hatori M."]  # story/art roles only
    assert (r.status, r.latest_chapter) == ("ongoing", None)
    assert r.description == "An AniList description\nline 2"  # HTML removed
    assert [link.url for link in r.links] == ["https://www.webtoons.com/en/time-grocery/list"]  # reading sites only
    assert r.external_ids == {"anilist": "101", "mal": "201"}


@pytest.mark.parametrize(("value", "expected"), [("120", 120), ("12.5", 12), (88, 88), ("", None), (None, None), ("Oneshot", None)])
def test_chapter_number(value, expected):
    assert chapter_number(value) == expected


def test_clean_text():
    assert clean_text("a<br/>b <b>c</b> [link](https://x)\n\n\n\nd") == "a\nb c link\n\nd"


def test_provider_for_link():
    assert provider_for_link(f"https://mangadex.org/title/{TIEM_MD}/time-grocery") == ("mangadex", TIEM_MD)
    assert provider_for_link("https://anilist.co/manga/101/Time-Grocery/") == ("anilist", "101")
    assert provider_for_link("https://cuutruyen.net/manga/abc") is None


async def test_rate_limiter_pauses_after_429():
    limiter = RateLimiter(per_second=1000)
    await limiter.wait()
    limiter.pause(60)
    with pytest.raises(RateLimited):
        await limiter.wait()


async def test_user_agent_identifies_the_app():
    from app.core.config import get_settings

    settings = get_settings()
    old = settings.provider_contact
    settings.provider_contact = "https://tu-truyen-nho.example.app"
    try:
        assert user_agent() == "UyenThuCac/0.1.0 (+https://tu-truyen-nho.example.app)"
        async with new_client() as client:
            assert client.headers["user-agent"] == user_agent()
    finally:
        settings.provider_contact = old


def test_names_and_links_are_deduplicated():
    from app.providers.base import Link, dedupe_links, dedupe_names

    # MangaDex writes Japanese name order, AniList Western order
    assert dedupe_names(["Yamada Kanehito", "Abe Tsukasa", "Kanehito Yamada", "Tsukasa ABE"]) == ["Yamada Kanehito", "Abe Tsukasa"]
    links = [Link("Viz", "https://www.viz.com/shonensunday/a"), Link("VIZ", "https://viz.com/read/b"), Link("Bilibili", "https://manga.bilibili.com/x")]
    assert [link.site_name for link in dedupe_links(links)] == ["Viz", "Bilibili"]  # one link per site
