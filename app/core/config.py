"""Application settings, read from environment variables (and .env locally)."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The frontend's dev server. Vite's default port is 5173; BeMeoLac runs on 3000.
LOCAL_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    # Neon pooled connection (used by the app).
    database_url: str
    # Neon direct connection (used by Alembic). Neon writes it as DATABASE_URL_UNPOOLED.
    database_url_direct: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL_DIRECT", "DATABASE_URL_UNPOOLED")
    )

    # Deployed frontend origin(s), comma-separated, e.g. https://tu-truyen-nho.vercel.app
    frontend_url: str = ""

    @property
    def cors_origins(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.frontend_url.split(",") if o.strip()]
        return list(dict.fromkeys(LOCAL_ORIGINS + extra))


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
