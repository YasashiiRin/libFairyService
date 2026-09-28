"""Seed the demo account with sample comics and shelves.

    uv run python -m app.seed           # create it (skips if the account exists)
    uv run python -m app.seed --reset   # delete that account and all its data, then create it again

Only touches the "bemeolac" account; other users are never changed.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import LibraryItem, LibraryItemTag, Notification, ReadingGoal, ReadingLog, Shelf, ShelfItem, Tag, User, Work, WorkSource
from app.models.user import default_settings
from app.services.urls import normalize_url

USERNAME = "bemeolac"
PASSWORD = "12012007"
DISPLAY_NAME = "Uyên"
EMAIL = "bemeolac@example.com"

SHELVES = [
    # key, name, description, icon, color (palette ids from the frontend's SHELF_COLORS)
    ("healing", "Truyện chữa lành", "Những câu chuyện dịu dàng cho buổi tối yên tĩnh.", "🌿", "#A8C49A"),
    ("fav-manga", "Manga yêu thích", "Những cuốn nàng muốn đọc đi đọc lại.", "✨", "#D9C8F0"),
    ("reread", "Đọc lại lần 2", "Đã xong nhưng vẫn muốn ghé thăm.", "📖", "#FFDF97"),
    ("webtoon", "Webtoon cuối tuần", "Truyện màu cuộn dọc cho sáng thứ Bảy.", "🎨", "#CFE8D5"),
]


@dataclass
class SeedSource:
    site_name: str
    url: str
    favicon_url: str
    latest_chapter: int
    is_alive: bool = True


@dataclass
class SeedComic:
    title: str
    author: str
    description: str
    cover_url: str
    status: str
    current_chapter: int
    total_chapters: int
    rating: float
    tags: list[str]
    shelves: list[str]
    sources: list[SeedSource]
    is_favorite: bool = False
    note: str = ""
    has_new_chapter: bool = False
    added_days_ago: int = 60
    read_days_ago: int | None = 1  # None = never opened
    reading_days: list[int] = field(default_factory=list)  # days ago with reading activity


def _unsplash(photo: str) -> str:
    return f"https://images.unsplash.com/{photo}?w=500&auto=format&fit=crop&q=80"


COMICS = [
    SeedComic(
        title="Tiệm Tạp Hóa Thời Gian",
        author="Hatori M.",
        description="Tại một con hẻm cổ kính phủ đầy rêu phong, có một tiệm tạp hóa kỳ lạ chỉ mở cửa vào những ngày nắng nhẹ.",
        cover_url=_unsplash("photo-1481627834876-b7833e8f5570"),
        status="reading",
        current_chapter=45,
        total_chapters=120,
        rating=4.9,
        tags=["Chữa lành", "Huyền bí", "Đời thường"],
        shelves=["healing", "fav-manga"],
        sources=[
            SeedSource("Cuutruyen", "https://cuutruyen.net/manga/tiem-tap-hoa-thoi-gian", "🌸", 47),
            SeedSource("BlogTruyen", "https://blogtruyen.vn/tiem-tap-hoa-thoi-gian", "📖", 46),
        ],
        is_favorite=True,
        note="Đọc vào buổi tối trước khi ngủ cực kỳ thư thái.",
        has_new_chapter=True,
        added_days_ago=200,
        reading_days=[0, 1, 2, 4, 5, 9, 16, 30, 45],
    ),
    SeedComic(
        title="Khu Vườn Phù Thủy Bí Mật",
        author="Solaria",
        description="Một phù thủy nhỏ thừa kế khu nhà kính bỏ hoang trăm năm ở rìa vương quốc elven.",
        cover_url=_unsplash("photo-1416879595882-3373a0480b5b"),
        status="completed",
        current_chapter=88,
        total_chapters=88,
        rating=5.0,
        tags=["Phép thuật", "Chữa lành", "Lãng mạn"],
        shelves=["healing", "reread"],
        sources=[SeedSource("Kakao", "https://page.kakao.com/content/khu-vuon-phu-thuy", "💛", 88)],
        is_favorite=True,
        note="Kết thúc viên mãn, khu vườn hoa hồng nở rộ.",
        added_days_ago=330,
        read_days_ago=12,
        reading_days=[12, 13, 15, 20, 26, 60, 75],
    ),
    SeedComic(
        title="Lữ Khách Phương Xa",
        author="Aoi Kaze",
        description="Hành trình của một lữ khách đi qua những cánh đồng nắng và các thị trấn chưa có trên bản đồ.",
        cover_url=_unsplash("photo-1500534314209-a25ddb2bd429"),
        status="reading",
        current_chapter=32,
        total_chapters=100,
        rating=4.6,
        tags=["Phiêu lưu", "Thiên nhiên", "Triết lý"],
        shelves=["fav-manga"],
        sources=[SeedSource("Bilibili", "https://manga.bilibili.com/detail/lu-khach-phuong-xa", "📺", 34)],
        has_new_chapter=True,
        added_days_ago=120,
        read_days_ago=3,
        reading_days=[3, 6, 7, 14, 21, 40],
    ),
    SeedComic(
        title="Mùa Hè Ở Làng Gió Biển",
        author="Nami Hoshino",
        description="Một mùa hè ở ngôi làng ven biển, nơi mỗi quán ăn nhỏ đều giữ một câu chuyện.",
        cover_url=_unsplash("photo-1507525428034-b723cf961d3e"),
        status="reading",
        current_chapter=18,
        total_chapters=60,
        rating=4.4,
        tags=["Đời thường", "Ẩm thực", "Chữa lành"],
        shelves=["healing", "webtoon"],
        sources=[SeedSource("Webtoon", "https://webtoons.com/episode/lang-gio-bien", "🌿", 18)],
        added_days_ago=45,
        read_days_ago=0,
        reading_days=[0, 2, 8, 10],
    ),
    SeedComic(
        title="Thư Viện Những Vì Sao Đêm",
        author="Lirien",
        description="Một thư viện chỉ hiện ra dưới bầu trời đầy sao, nơi mỗi cuốn sách là một chòm sao.",
        cover_url=_unsplash("photo-1519681393784-d120267933ba"),
        status="on_hold",
        current_chapter=40,
        total_chapters=140,
        rating=4.2,
        tags=["Kỳ ảo", "Lãng mạn", "Thư viện"],
        shelves=["fav-manga"],
        sources=[SeedSource("Hako", "https://ln.hako.vn/truyen/thu-vien-vi-sao", "📜", 64, is_alive=False)],
        added_days_ago=90,
        read_days_ago=50,
        reading_days=[50, 52, 58],
    ),
    SeedComic(
        title="Giai Điệu Của Suối Mây",
        author="Mira Sol",
        description="Cô bé thổi sáo và dòng suối biết hát, trong một khu rừng mây mù quanh năm.",
        cover_url=_unsplash("photo-1448375240586-882707db888b"),
        status="plan_to_read",
        current_chapter=0,
        total_chapters=72,
        rating=0,
        tags=["Âm nhạc", "Kỳ ảo"],
        shelves=[],
        sources=[SeedSource("Cuutruyen", "https://cuutruyen.net/manga/giai-dieu-suoi-may", "🌸", 72)],
        added_days_ago=5,
        read_days_ago=None,
    ),
]


async def _find_user(session: AsyncSession) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.username) == USERNAME))


async def seed(session: AsyncSession, *, reset: bool = False) -> str:
    existing = await _find_user(session)
    if existing and not reset:
        return f'Tài khoản "{USERNAME}" đã có sẵn, bỏ qua. Dùng --reset để tạo lại.'
    if existing:
        await session.execute(delete(User).where(User.id == existing.id))  # cascades to all their data
        await session.flush()

    now = datetime.now(UTC)
    today = now.date()
    user = User(
        username=USERNAME,
        email=EMAIL,
        password_hash=hash_password(PASSWORD),
        display_name=DISPLAY_NAME,
        bio="Người trông coi khu vườn chữ và những trang truyện thơm mùi thảo mộc. 🌸📖",
        role="user",
        settings=default_settings(),
        created_at=now - timedelta(days=365),
    )
    session.add(user)
    await session.flush()

    shelves: dict[str, Shelf] = {}
    for position, (key, name, description, icon, color) in enumerate(SHELVES, start=1):
        shelves[key] = Shelf(user_id=user.id, name=name, description=description, icon=icon, color=color, position=position)
    session.add_all(shelves.values())

    tags: dict[str, Tag] = {}
    for spec in COMICS:
        for name in spec.tags:
            tags.setdefault(name, Tag(user_id=user.id, name=name))
    session.add_all(tags.values())
    await session.flush()

    items: list[LibraryItem] = []
    for spec in COMICS:
        added = now - timedelta(days=spec.added_days_ago)
        last_read = None if spec.read_days_ago is None else now - timedelta(days=spec.read_days_ago, hours=2)
        # a seeded comic has links, so it is a shared work (like the migrated ones)
        work = await session.scalar(
            select(Work).join(WorkSource).where(WorkSource.url == normalize_url(spec.sources[0].url), WorkSource.private_owner_id.is_(None))
        )
        if work is None:
            work = Work(
                title=spec.title,
                authors=[spec.author],
                alt_titles=[],
                genres=[],
                external_ids={},
                description=spec.description,
                cover_url=spec.cover_url,
                latest_chapter=spec.total_chapters,
                created_at=added,
                sources=[
                    WorkSource(
                        position=i,
                        site_name=s.site_name,
                        url=normalize_url(s.url),
                        favicon_url=s.favicon_url,
                        chapter_url=normalize_url(s.url),
                        latest_chapter=s.latest_chapter,
                        is_alive=s.is_alive,
                        last_checked_at=now - timedelta(hours=6),
                        added_by_user_id=user.id,
                    )
                    for i, s in enumerate(spec.sources)
                ],
            )
            session.add(work)
            await session.flush()
        else:
            await session.refresh(work, ["sources"])
        item = LibraryItem(
            user_id=user.id,
            work=work,
            status=spec.status,
            current_chapter=spec.current_chapter,
            rating=spec.rating,
            is_favorite=spec.is_favorite,
            note=spec.note,
            has_new_chapter=spec.has_new_chapter,
            primary_source_id=work.sources[0].id,
            last_read_at=last_read,
            created_at=added,
            updated_at=last_read or added,
            tag_links=[LibraryItemTag(tag=tags[name], position=i) for i, name in enumerate(spec.tags)],
        )
        session.add(item)
        items.append(item)
    await session.flush()

    for spec, item in zip(COMICS, items, strict=True):
        for key in spec.shelves:
            session.add(ShelfItem(shelf_id=shelves[key].id, library_item_id=item.id, position=None))
        for n, days_ago in enumerate(spec.reading_days):
            day: date = today - timedelta(days=days_ago)
            session.add(ReadingLog(user_id=user.id, library_item_id=item.id, day=day, chapters=1 + (n * 7 + days_ago) % 4))

    session.add(ReadingGoal(user_id=user.id, year=today.year, target=12))

    by_title = {spec.title: item for spec, item in zip(COMICS, items, strict=True)}
    session.add_all(
        [
            Notification(
                user_id=user.id,
                library_item_id=by_title["Tiệm Tạp Hóa Thời Gian"].id,
                type="new_chapter",
                message="Chương 47 vừa nở hoa trên Cuutruyen 🌸",
                created_at=now - timedelta(hours=3),
            ),
            Notification(
                user_id=user.id,
                library_item_id=by_title["Lữ Khách Phương Xa"].id,
                type="new_chapter",
                message="Chương 34 đã có trên Bilibili",
                created_at=now - timedelta(days=1),
            ),
            Notification(
                user_id=user.id,
                library_item_id=by_title["Thư Viện Những Vì Sao Đêm"].id,
                type="broken_link",
                message="Liên kết Hako không mở được nữa, nàng xem lại giúp nhé 🍂",
                created_at=now - timedelta(days=2),
            ),
            Notification(
                user_id=user.id,
                library_item_id=by_title["Khu Vườn Phù Thủy Bí Mật"].id,
                type="new_chapter",
                message="Truyện đã có chương ngoại truyện",
                is_read=True,
                created_at=now - timedelta(days=8),
            ),
        ]
    )
    await session.commit()
    return f'Đã tạo tài khoản "{USERNAME}" với {len(shelves)} kệ, {len(items)} truyện, {len(tags)} thẻ.'


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help=f'delete the "{USERNAME}" account first')
    args = parser.parse_args()
    async with SessionLocal() as session:
        print(await seed(session, reset=args.reset))


if __name__ == "__main__":
    asyncio.run(main())
