"""Async SQLAlchemy engine and session for Neon Postgres."""

from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

# libpq options that asyncpg rejects as URL query parameters.
_LIBPQ_ONLY = {"sslmode", "channel_binding"}


def to_asyncpg_url(url: str) -> str:
    """Turn a libpq URL (as Neon gives it) into one SQLAlchemy + asyncpg accepts.

    postgres://… or postgresql://…?sslmode=require&channel_binding=require
      → postgresql+asyncpg://…?ssl=require
    """
    parts = urlsplit(url)
    scheme = parts.scheme.split("+")[0]
    if scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported database URL scheme: {parts.scheme!r}")

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    for key in _LIBPQ_ONLY:
        query.pop(key, None)
    if "ssl" not in query and sslmode and sslmode != "disable":
        # verify-ca / verify-full need certificates asyncpg doesn't get from the URL; require is what Neon needs.
        query["ssl"] = "require" if sslmode in ("require", "prefer", "allow") else sslmode

    return urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), parts.fragment))


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        to_asyncpg_url(url),
        # Serverless: each function instance keeps only a couple of connections.
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,  # Neon closes idle connections when the compute scales to zero
        pool_recycle=300,
        connect_args={
            # The pooled endpoint is PgBouncer in transaction mode: no prepared-statement caches.
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )


engine = create_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with SessionLocal() as session:
        yield session
