import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ApiError

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """GET /api/health → { status: "ok", database: "ok" }; 503 database_unavailable when Postgres can't be reached."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        raise ApiError(503, "Không kết nối được cơ sở dữ liệu", "database_unavailable") from exc
    return {"status": "ok", "database": "ok"}
