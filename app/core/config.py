"""Application settings, read from environment variables (and .env locally)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The frontend's dev server. Vite's default port is 5173; BeMeoLac runs on 3000.
LOCAL_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    # Neon pooled connection (used by the app).
    database_url: str
    # Neon direct (non-pooled) connection, used by Alembic migrations: DATABASE_URL_UNPOOLED.
    database_url_unpooled: str | None = None
    # Reuse prepared statements on each connection: one round trip per query instead of two.
    # Neon's pooler (PgBouncer in transaction mode) supports them; turn off for a pooler that doesn't.
    database_prepared_statements: bool = True

    # Deployed frontend origin(s), comma-separated, e.g. https://tu-truyen-nho.vercel.app
    frontend_url: str = ""

    # Signs access tokens (HS256). At least 32 random characters: `openssl rand -hex 32`.
    jwt_secret: str = Field(min_length=32)
    access_token_ttl_minutes: int = 15
    # Sliding: every refresh pushes the expiry back by this much.
    refresh_token_ttl_days: int = 30
    password_reset_ttl_minutes: int = 30

    # "Today" for reading logs and stats (api-contract.md, open point 5).
    app_timezone: str = "Asia/Ho_Chi_Minh"

    # Search across the web (app/providers). Each provider gets this long per search before it is skipped.
    provider_timeout_seconds: float = 4.0
    # Sent in the User-Agent to providers (their terms ask for a way to reach the app's maintainer):
    # a URL or an email address.
    provider_contact: str = ""
    search_cache_ttl_hours: int = 6
    # Vercel Cron calls /api/cron/daily with "Authorization: Bearer <CRON_SECRET>".
    cron_secret: str = ""
    # The home feed: how long the first home request of the day may spend building it (the rest is
    # added on the next request), and the daily cron's time budget (Vercel stops it at maxDuration).
    feed_build_seconds: float = 5.0
    feed_retry_seconds: float = 2.0  # finishing a partial feed on a later request
    cron_budget_seconds: float = 40.0

    @property
    def cors_origins(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.frontend_url.split(",") if o.strip()]
        return list(dict.fromkeys(LOCAL_ORIGINS + extra))

    @property
    def app_url(self) -> str:
        """Where the frontend lives, for links sent to users (password reset)."""
        return self.cors_origins[len(LOCAL_ORIGINS)] if self.frontend_url.strip() else "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
