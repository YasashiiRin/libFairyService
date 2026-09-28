from fastapi import APIRouter, Query

from app.api.deps import CurrentAuth, Db
from app.schemas.discover import DiscoverPage
from app.services import discover as svc

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("/search")
async def search(
    auth: CurrentAuth,
    db: Db,
    q: str = Query(min_length=2, max_length=100),
    page: int = Query(default=1, ge=1, le=10),
) -> DiscoverPage:
    """GET /api/discover/search?q=&page= → DiscoverPage
    (every provider at once, ~4 s each; a slow or failing one is listed in providers_failed)"""
    result = await svc.search(db, auth.user.id, q.strip(), page)
    await db.commit()  # the provider answers just cached
    return result
