from datetime import UTC, datetime

from fastapi import APIRouter, File, Response, UploadFile, status

from app.api.deps import CurrentAuth, Db
from app.core.errors import ApiError
from app.models import User
from app.models.user import DEFAULT_SETTINGS
from app.schemas.auth import PasswordChangeIn
from app.schemas.user import (
    DeviceSessionOut,
    ProfileUpdate,
    RevokedOut,
    UserOut,
    StarterTastesIn,
    UserSettingsUpdate,
)
from app.services import auth as auth_service
from app.services import feed, images

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("")
async def get_me(auth: CurrentAuth) -> UserOut:
    """GET /api/users/me → User"""
    return UserOut.from_model(auth.user)


def _refuse_other_fields(data: ProfileUpdate) -> None:
    extra = list(data.model_extra or {})
    if "username" in extra:
        raise ApiError(
            400,
            "Tên đăng nhập không thể thay đổi. Nàng chỉ đổi được bút danh, lời giới thiệu, ảnh đại diện và cài đặt thôi nhé.",
            "username_not_editable",
        )
    if extra:
        hint = " Mật khẩu đổi ở mục Bảo mật nhé." if any("password" in f for f in extra) else ""
        raise ApiError(
            400,
            f"Không thể thay đổi {', '.join(sorted(extra))} ở đây. "
            f"Chỉ đổi được bút danh, lời giới thiệu, ảnh đại diện và cài đặt.{hint}",
            "field_not_editable",
        )


async def _merge_settings(db: Db, user: User, patch: UserSettingsUpdate) -> None:
    changes = patch.model_dump(exclude_unset=True, exclude_none=True)
    current = {**DEFAULT_SETTINGS, **(user.settings or {})}
    if any(key in changes and changes[key] != current[key] for key in ("personalization_enabled", "priority_tastes")):
        await feed.reset_today(db, user.id)  # today's home feed is built again with the new setting
    # a new dict, so SQLAlchemy sees the JSONB change
    user.settings = {**(user.settings or {}), **changes}


def _set_avatar(user: User, webp: bytes | None) -> None:
    user.avatar = webp
    user.avatar_updated_at = datetime.now(UTC) if webp else None


@router.patch("")
async def update_me(data: ProfileUpdate, auth: CurrentAuth, db: Db) -> UserOut:
    """PATCH /api/users/me body { display_name?, bio?, avatar?, settings? } → User
    (avatar: a data:image URL, or null to remove it)
    400 username_not_editable | field_not_editable (any other field, e.g. email, role); 422 validation_error"""
    _refuse_other_fields(data)
    user = auth.user
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.bio is not None:
        user.bio = data.bio
    if "avatar" in data.model_fields_set:
        _set_avatar(user, images.avatar_thumbnail(images.decode_data_url(data.avatar)) if data.avatar else None)
    if data.settings is not None:
        await _merge_settings(db, user, data.settings)
    await db.commit()
    return UserOut.from_model(user)


@router.put("/avatar")
async def upload_avatar(auth: CurrentAuth, db: Db, file: UploadFile = File()) -> UserOut:
    """PUT /api/users/me/avatar multipart { file } (image ≤ 4 MB) → User with the new avatar_url
    400 invalid_file; 413 file_too_large. Stored as a 256×256 WebP."""
    _set_avatar(auth.user, images.avatar_thumbnail(await images.read_upload(file)))
    await db.commit()
    return UserOut.from_model(auth.user)


@router.patch("/settings")
async def update_settings(data: UserSettingsUpdate, auth: CurrentAuth, db: Db) -> UserOut:
    """PATCH /api/users/me/settings body Partial<UserSettings> → User; 422 validation_error
    (starter_tastes is set with PUT /api/users/me/starter-tastes; priority_tastes: moods, at most 4)"""
    await _merge_settings(db, auth.user, data)
    await db.commit()
    return UserOut.from_model(auth.user)


@router.put("/starter-tastes")
async def set_starter_tastes(data: StarterTastesIn, auth: CurrentAuth, db: Db) -> UserOut:
    """PUT /api/users/me/starter-tastes body { starter_tastes: string[] } → User
    (moods from GET /api/home starter_options; [] clears them). Today's home feed is built again.
    422 validation_error (an unknown mood, or more than there are)"""
    user = auth.user
    user.settings = {**(user.settings or {}), "starter_tastes": data.starter_tastes}
    await feed.reset_today(db, user.id)
    await db.commit()
    return UserOut.from_model(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(data: PasswordChangeIn, auth: CurrentAuth, db: Db) -> Response:
    """POST /api/users/me/password body { current_password, new_password } → 204 (other devices are signed out)
    400 wrong_password; 422 validation_error"""
    await auth_service.change_password(db, auth.user, auth.session_id, data.current_password, data.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions")
async def list_sessions(auth: CurrentAuth, db: Db) -> list[DeviceSessionOut]:
    """GET /api/users/me/sessions → DeviceSession[] (current first)"""
    rows = await auth_service.list_sessions(db, auth.user)
    rows.sort(key=lambda r: r.id != auth.session_id)  # stable: the rest stay newest first
    return [
        DeviceSessionOut(
            id=str(r.id),
            device_name=r.device_name,
            device_type=r.device_type,  # type: ignore[arg-type]
            browser=r.browser,
            location=r.location,
            last_active_at=r.last_active_at,
            is_current=r.id == auth.session_id,
        )
        for r in rows
    ]


@router.delete("/sessions")
async def logout_other_devices(auth: CurrentAuth, db: Db) -> RevokedOut:
    """DELETE /api/users/me/sessions → { revoked } (signs out every other device; this one stays)"""
    revoked = await auth_service.revoke_sessions(db, auth.user.id, keep=auth.session_id)
    await db.commit()
    return RevokedOut(revoked=revoked)
