from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.security import verify_password
from app.models import LibraryItem, Notification, ReadingLog, Shelf, ShelfItem, User, Work, WorkSource
from app.seed import PASSWORD, USERNAME, seed
from app.services.comics import delete_orphan_works
from tests.conftest import alembic

EXPECTED_TABLES = {
    "users", "works", "work_sources", "library_items", "library_item_tags", "tags", "shelves", "shelf_items",
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

    items = (await db_session.scalars(select(LibraryItem).where(LibraryItem.user_id == user.id))).all()
    assert len(items) >= 3
    assert all(i.primary_source_id is not None for i in items)
    assert await db_session.scalar(select(func.count()).select_from(Work)) == len(items)  # one work each
    assert await db_session.scalar(select(func.count()).select_from(Shelf).where(Shelf.user_id == user.id)) >= 2
    assert await db_session.scalar(select(func.count()).select_from(ShelfItem)) > 0


async def test_seed_is_idempotent_and_reset_rebuilds(db_session):
    await seed(db_session)
    first = await db_session.scalar(select(func.count()).select_from(LibraryItem))

    assert "bỏ qua" in await seed(db_session)
    assert await db_session.scalar(select(func.count()).select_from(LibraryItem)) == first

    await seed(db_session, reset=True)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LibraryItem)) == first
    assert await db_session.scalar(select(func.count()).select_from(Work)) == first  # works reused, not duplicated


async def test_deleting_a_user_cascades(db_session):
    await seed(db_session)
    user = await db_session.scalar(select(User).where(User.username == USERNAME))
    await db_session.delete(user)
    await db_session.commit()
    for model in (LibraryItem, Shelf, ShelfItem, ReadingLog, Notification):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0, model.__name__

    # shared works can't cascade from one user (others may have saved them); the cleanup removes orphans
    assert await db_session.scalar(select(func.count()).select_from(Work)) > 0
    assert await delete_orphan_works(db_session) > 0
    await db_session.commit()
    for model in (Work, WorkSource):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0, model.__name__


async def test_username_is_unique_case_insensitively(db_session):
    await seed(db_session)
    db_session.add(User(username=USERNAME.upper(), email="other@example.com", password_hash="x", display_name="X"))
    try:
        await db_session.commit()
        raise AssertionError("duplicate username was accepted")
    except IntegrityError:
        await db_session.rollback()


async def test_library_item_constraints(db_session):
    """current_chapter ≤ total_chapters is now enforced by the app (the total lives on the shared work);
    the API tests cover it. The database still refuses impossible per-user values."""
    await seed(db_session)
    for field, value in (("current_chapter", -1), ("rating", 6), ("status", "abandoned")):
        item = await db_session.scalar(select(LibraryItem).limit(1))
        setattr(item, field, value)
        try:
            await db_session.commit()
            raise AssertionError(f"{field}={value} was accepted")
        except IntegrityError:
            await db_session.rollback()


async def test_one_work_per_user(db_session):
    await seed(db_session)
    item = await db_session.scalar(select(LibraryItem).limit(1))
    db_session.add(LibraryItem(user_id=item.user_id, work_id=item.work_id))
    try:
        await db_session.commit()
        raise AssertionError("the same work was saved twice by one user")
    except IntegrityError:
        await db_session.rollback()


async def test_a_link_belongs_to_one_shared_work(db_session):
    await seed(db_session)
    src = await db_session.scalar(select(WorkSource).where(WorkSource.private_owner_id.is_(None)).limit(1))
    other = Work(title="Khác", alt_titles=[], authors=[], genres=[], external_ids={})
    db_session.add(other)
    await db_session.flush()
    db_session.add(WorkSource(work_id=other.id, site_name="X", url=src.url))
    try:
        await db_session.commit()
        raise AssertionError("one link on two shared works")
    except IntegrityError:
        await db_session.rollback()
