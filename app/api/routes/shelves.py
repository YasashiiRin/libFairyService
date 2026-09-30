"""/api/shelves (api-contract.md#shelves). Every query is scoped to the signed-in user."""

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAuth, Db
from app.schemas.comic import ComicOut
from app.schemas.shelf import ShelfComicIn, ShelfComicsOrderIn, ShelfDetailOut, ShelfIn, ShelfOut, ShelfUpdate, ShelvesOrderIn
from app.services import comics
from app.services import shelves as svc

router = APIRouter(prefix="/shelves", tags=["shelves"])


@router.get("")
async def list_shelves(auth: CurrentAuth, db: Db) -> list[ShelfOut]:
    """GET /api/shelves → Shelf[] (the virtual "all" first, then hers by position; covers: up to 4)"""
    return await svc.list_shelves(db, auth.user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shelf(data: ShelfIn, auth: CurrentAuth, db: Db) -> ShelfOut:
    """POST /api/shelves body ShelfInput → 201 Shelf (icon 🌸 and color #FEB2C0 by default; last)
    422 validation_error (empty name, a color outside the palette)"""
    shelf = await svc.create(db, auth.user.id, data)
    await db.commit()
    return shelf


@router.put("/order")
async def reorder_shelves(data: ShelvesOrderIn, auth: CurrentAuth, db: Db) -> list[ShelfOut]:
    """PUT /api/shelves/order body { shelf_ids } (every one of her shelves) → Shelf[] in the new order
    422 validation_error (a shelf missing, repeated or not hers)"""
    result = await svc.reorder_shelves(db, auth.user.id, data.shelf_ids)
    await db.commit()
    return result


@router.get("/{shelf_id}")
async def get_shelf(shelf_id: str, auth: CurrentAuth, db: Db) -> ShelfDetailOut:
    """GET /api/shelves/{id} → Shelf + { completed_count, last_updated_at } ("all" too); 404 shelf_not_found"""
    return await svc.shelf_detail(db, auth.user.id, shelf_id)


@router.patch("/{shelf_id}")
async def update_shelf(shelf_id: str, data: ShelfUpdate, auth: CurrentAuth, db: Db) -> ShelfDetailOut:
    """PATCH /api/shelves/{id} body Partial<ShelfInput> → Shelf (+ stats)
    404 shelf_not_found; 422 validation_error ("all" can't be changed)"""
    shelf = await svc.update_shelf(db, auth.user.id, shelf_id, data)
    await db.commit()
    return shelf


@router.delete("/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shelf(shelf_id: str, auth: CurrentAuth, db: Db) -> Response:
    """DELETE /api/shelves/{id} → 204 (the comics stay in the library; only their membership goes)
    404 shelf_not_found; 422 validation_error ("all")"""
    await svc.delete_shelf(db, auth.user.id, shelf_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{shelf_id}/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_comics(shelf_id: str, data: ShelfComicsOrderIn, auth: CurrentAuth, db: Db) -> Response:
    """PUT /api/shelves/{id}/order body { comic_ids } → 204; read back with GET /api/comics?shelf={id}&sort=position
    404 shelf_not_found; 422 validation_error (a comic not on the shelf, repeated ids)"""
    await svc.reorder_comics(db, auth.user.id, shelf_id, data.comic_ids)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{shelf_id}/comics")
async def add_comic(shelf_id: str, data: ShelfComicIn, auth: CurrentAuth, db: Db) -> ComicOut:
    """POST /api/shelves/{shelf_id}/comics body { comic_id } → Comic (idempotent)
    404 shelf_not_found | comic_not_found"""
    item = await svc.add_comic(db, auth.user.id, shelf_id, data.comic_id)
    await db.commit()
    return comics.comic_out(item)


@router.delete("/{shelf_id}/comics/{comic_id}")
async def remove_comic(shelf_id: str, comic_id: str, auth: CurrentAuth, db: Db) -> ComicOut:
    """DELETE /api/shelves/{shelf_id}/comics/{comic_id} → Comic (idempotent)
    404 shelf_not_found | comic_not_found"""
    item = await svc.remove_comic(db, auth.user.id, shelf_id, comic_id)
    await db.commit()
    return comics.comic_out(item)
