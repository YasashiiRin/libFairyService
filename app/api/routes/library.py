from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAuth, Db
from app.schemas.comic import ComicOut
from app.schemas.discover import LibraryAddIn
from app.services import comics
from app.services import library as svc

router = APIRouter(prefix="/library", tags=["library"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_to_library(data: LibraryAddIn, auth: CurrentAuth, db: Db, response: Response) -> ComicOut:
    """POST /api/library body {provider, external_id} | {url} | {manual: ComicCreate}
    (+ status?, current_chapter?, shelf_ids?) → 201 Comic, or 200 with the Comic the user already has
    404 work_not_found | shelf_not_found; 422 unknown_provider | link_not_supported | validation_error;
    503 provider_unavailable"""
    item, created = await svc.add(db, auth.user.id, data)
    await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return comics.comic_out(item)
