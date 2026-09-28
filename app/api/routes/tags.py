from fastapi import APIRouter

from app.api.deps import CurrentAuth, Db
from app.schemas.comic import TagCount
from app.services import comics as svc

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(auth: CurrentAuth, db: Db) -> list[TagCount]:
    """GET /api/tags → TagCount[] (tags used in the library, most used first).
    Tags are created when a comic first uses them and removed when no comic uses them."""
    return [TagCount(name=name, count=n) for name, n in await svc.list_tags(db, auth.user.id)]
