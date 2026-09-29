"""Async SQLAlchemy engine and session for Neon Postgres."""

import time
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from sqlalchemy import event, exc
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.util import await_

from app.core.config import get_settings

# libpq options that asyncpg rejects as URL query parameters.
_LIBPQ_ONLY = {"sslmode", "channel_binding"}
# A pooled connection idle longer than this is pinged before use (Neon may have closed it).
# Busier connections skip the ping: one round trip less per request.
PING_AFTER_IDLE_SECONDS = 10
# Requests that run without a transaction: reads, and endpoints whose writes are single statements.
# Saves BEGIN and COMMIT/ROLLBACK (two round trips).
AUTOCOMMIT_PATHS = {"/api/auth/login", "/api/auth/refresh"}


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


def _ping_idle_connections(engine: AsyncEngine) -> None:
    """Like pool_pre_ping, but only for connections that sat idle (a stale one is replaced)."""
    sync = engine.sync_engine

    @event.listens_for(sync, "checkin")
    def _checked_in(dbapi_connection, record) -> None:
        record.info["idle_since"] = time.monotonic()

    @event.listens_for(sync, "checkout")
    def _checked_out(dbapi_connection, record, proxy) -> None:
        idle_since = record.info.pop("idle_since", None)
        if idle_since is not None and time.monotonic() - idle_since > PING_AFTER_IDLE_SECONDS:
            try:
                ping(dbapi_connection)
            except Exception as error:
                raise exc.DisconnectionError() from error  # the pool opens a fresh connection instead


def ping(dbapi_connection) -> None:
    """One round trip: SELECT 1 over the simple query protocol, outside any transaction.
    (SQLAlchemy's own asyncpg ping costs four: BEGIN, prepare, execute, ROLLBACK.)"""
    await_(dbapi_connection.driver_connection.execute("SELECT 1"))


def create_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(
        to_asyncpg_url(url),
        # Serverless: each function instance keeps only a couple of connections.
        pool_size=2,
        max_overflow=3,
        # the most recently used connection first: it is warm (statements prepared, no ping needed)
        pool_use_lifo=True,
        # Neon closes idle connections when the compute scales to zero (after 5 minutes): older
        # connections are replaced, idle ones pinged (_ping_idle_connections).
        pool_recycle=300,
        connect_args={
            # asyncpg's own cache stays off; SQLAlchemy prepares every statement itself, and keeping
            # those prepared (protocol-level, which Neon's PgBouncer supports) saves a round trip each.
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 100 if get_settings().database_prepared_statements else 0,
        },
    )
    _ping_idle_connections(engine)
    return engine


def uses_autocommit(request: Request) -> bool:
    return request.method in ("GET", "HEAD") or request.url.path in AUTOCOMMIT_PATHS


engine = create_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
AutocommitSession = async_sessionmaker(engine.execution_options(isolation_level="AUTOCOMMIT"), expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Reads (and a few single-statement writes) run
    without a transaction; everything else in one transaction the route commits."""
    async with (AutocommitSession if uses_autocommit(request) else SessionLocal)() as session:
        yield session
