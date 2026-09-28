from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.security import verify_password
from app.models import Comic, Notification, ReadingLog, Shelf, ShelfComic, Source, User
from app.seed import PASSWORD, USERNAME, seed
from tests.conftest import alembic

EXPECTED_TABLES = {
    "users", "comics", "sources", "tags", "comic_tags", "shelves", "shelf_comics",
    "reading_logs", "reading_goals", "notifications", "refresh_tokens", "password_reset_tokens",
}


def test_migrations_match_models(migrated_database):
    result = alembic("check")
    assert result.returncode == 0, result.stdout + result.stderr


async def test_all_tables_exist(db_session):
    names = set((await db_session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))).scalars())
    assert EXPECTED_TABLES <= names


async def test_seed_creates_demo_account(db_session):
    message = await seed(db_session)
    assert USERNAME in message

    user = await db_session.scalar(select(User).where(User.username == USERNAME))
    assert user is not None
    assert user.display_name == "Uyên"
    assert user.is_active and user.role == "user"
    assert verify_password(user.password_hash, PASSWORD)
    assert not verify_password(user.password_hash, "wrong")

    comics = (await db_session.scalars(select(Comic).where(Comic.user_id == user.id))).all()
    assert len(comics) >= 3
    assert all(c.primary_source_id is not None for c in comics)
    assert await db_session.scalar(select(func.count()).select_from(Shelf).where(Shelf.user_id == user.id)) >= 2
    assert await db_session.scalar(select(func.count()).select_from(ShelfComic)) > 0


async def test_seed_is_idempotent_and_reset_rebuilds(db_session):
    await seed(db_session)
    first = await db_session.scalar(select(func.count()).select_from(Comic))

    assert "bỏ qua" in await seed(db_session)
    assert await db_session.scalar(select(func.count()).select_from(Comic)) == first

    await seed(db_session, reset=True)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Comic)) == first


async def test_deleting_a_user_cascades(db_session):
    await seed(db_session)
    user = await db_session.scalar(select(User).where(User.username == USERNAME))
    await db_session.delete(user)
    await db_session.commit()
    for model in (Comic, Source, Shelf, ShelfComic, ReadingLog, Notification):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0, model.__name__


async def test_username_is_unique_case_insensitively(db_session):
    await seed(db_session)
    db_session.add(User(username=USERNAME.upper(), email="other@example.com", password_hash="x", display_name="X"))
    try:
        await db_session.commit()
        raise AssertionError("duplicate username was accepted")
    except IntegrityError:
        await db_session.rollback()


async def test_progress_cannot_exceed_total(db_session):
    await seed(db_session)
    comic = await db_session.scalar(select(Comic).limit(1))
    comic.current_chapter = comic.total_chapters + 1
    try:
        await db_session.commit()
        raise AssertionError("current_chapter > total_chapters was accepted")
    except IntegrityError:
        await db_session.rollback()
