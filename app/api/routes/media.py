"""Stored images, served to <img> tags: public but signed and versioned (see app/services/images.py)."""

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.api.deps import Db
from app.core.errors import ApiError
from app.models import LibraryItem, User, Work
from app.services import images
from app.services.comics import parse_uuid

router = APIRouter(tags=["media"])


def _not_found() -> ApiError:
    return ApiError(404, "Không tìm thấy ảnh", "not_found")


@router.get("/comics/{comic_id}/cover", response_class=Response)
async def comic_cover(comic_id: str, request: Request, db: Db, v: str = Query(), sig: str = Query()) -> Response:
    """GET /api/comics/{id}/cover?v=&sig= → image/webp (Cache-Control: immutable; 304 on If-None-Match).
    Serves the user's personal cover if they set one, else the work's (stored once, shared)."""
    cid = parse_uuid(comic_id)
    row = cid and (
        await db.execute(
            select(LibraryItem.custom_cover, LibraryItem.custom_cover_updated_at, Work.cover, Work.cover_updated_at)
            .join(Work, Work.id == LibraryItem.work_id)
            .where(LibraryItem.id == cid)
        )
    ).first()
    if not row:
        raise _not_found()
    image, version = (row.custom_cover, row.custom_cover_updated_at) if row.custom_cover_updated_at else (row.cover, row.cover_updated_at)
    if image is None or not images.verify_media("cover", cid, version, v, sig):
        raise _not_found()
    return images.image_response(request, image, v)


@router.get("/users/{user_id}/avatar", response_class=Response)
async def user_avatar(user_id: str, request: Request, db: Db, v: str = Query(), sig: str = Query()) -> Response:
    """GET /api/users/{id}/avatar?v=&sig= → image/webp (Cache-Control: immutable; 304 on If-None-Match)"""
    uid = parse_uuid(user_id)
    row = uid and (await db.execute(select(User.avatar, User.avatar_updated_at).where(User.id == uid))).first()
    if not row or row.avatar is None or not images.verify_media("avatar", uid, row.avatar_updated_at, v, sig):
        raise _not_found()
    return images.image_response(request, row.avatar, v)
