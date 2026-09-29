import os
import subprocess
import sys

# Tests never touch the real database: set this before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test?sslmode=disable"
os.environ["FRONTEND_URL"] = "https://tu-truyen-nho.example.app"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40

# Database tests need a throwaway Postgres; they are skipped without one. Never point this at Neon.
#   docker run -d --rm --name meolac-pg -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=meolac -p 55432:5432 postgres:18
#   TEST_DATABASE_URL=postgresql://postgres:pg@localhost:55432/meolac uv run pytest
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from fastapi import Request  # noqa: E402

from app.core.db import create_engine, get_session, uses_autocommit  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def alembic(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL_UNPOOLED": TEST_DATABASE_URL or ""}
    return subprocess.run([sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True, check=False)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """A test database rebuilt from the migrations once per test run."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    for step in (("downgrade", "base"), ("upgrade", "head")):
        result = alembic(*step)
        assert result.returncode == 0, result.stderr
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def db_engine(migrated_database):
    engine = create_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """A session whose tables are emptied after each test."""
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        yield session
    async with db_engine.begin() as conn:
        tables = (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"))).scalars().all()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))


@pytest.fixture
async def api(db_engine, db_session):
    """An HTTP client for the app, on the test database (tables are emptied after each test)."""
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    autocommit = async_sessionmaker(db_engine.execution_options(isolation_level="AUTOCOMMIT"), expire_on_commit=False)

    async def test_session(request: Request):  # the same rule as get_session
        async with (autocommit if uses_autocommit(request) else sessions)() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def fake_providers():
    """MangaDex and AniList answered by tests/fake_providers.py (no network), quick timeouts, no pacing."""
    import httpx

    from app.core.config import get_settings
    from app.providers import PROVIDERS, http
    from app.providers.base import RateLimiter
    from tests.fake_providers import FakeProviders

    fake = FakeProviders()
    settings = get_settings()
    old_timeout, old_limiters = settings.provider_timeout_seconds, {n: p.limiter for n, p in PROVIDERS.items()}
    settings.provider_timeout_seconds = 0.5
    for provider in PROVIDERS.values():
        provider.limiter = RateLimiter(per_second=1000)
    http.transport = httpx.MockTransport(fake.handler)
    yield fake
    http.transport = None
    settings.provider_timeout_seconds = old_timeout
    for name, provider in PROVIDERS.items():
        provider.limiter = old_limiters[name]
