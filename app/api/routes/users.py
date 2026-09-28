from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAuth, Db
from app.schemas.auth import PasswordChangeIn
from app.schemas.user import DeviceSessionOut, ProfileUpdate, RevokedOut, UserOut, UserSettingsUpdate
from app.services import auth as auth_service

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("")
async def get_me(auth: CurrentAuth) -> UserOut:
    """GET /api/users/me → User"""
    return UserOut.from_model(auth.user)


@router.patch("")
async def update_me(data: ProfileUpdate, auth: CurrentAuth, db: Db) -> UserOut:
    """PATCH /api/users/me body { display_name?, bio? } → User; 422 validation_error"""
    user = auth.user
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.bio is not None:
        user.bio = data.bio
    await db.commit()
    return UserOut.from_model(user)


@router.patch("/settings")
async def update_settings(data: UserSettingsUpdate, auth: CurrentAuth, db: Db) -> UserOut:
    """PATCH /api/users/me/settings body Partial<UserSettings> → User; 422 validation_error"""
    user = auth.user
    # a new dict, so SQLAlchemy sees the JSONB change
    user.settings = {**(user.settings or {}), **data.model_dump(exclude_unset=True, exclude_none=True)}
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
