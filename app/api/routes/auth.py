from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentAuth, Db
from app.schemas.auth import (
    ForgotPasswordIn,
    LoginIn,
    LoginResult,
    LogoutIn,
    RefreshIn,
    RefreshOut,
    RegisterIn,
    ResetPasswordIn,
)
from app.schemas.user import UserOut
from app.services import auth as auth_service
from app.services.devices import describe_request

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, request: Request, db: Db) -> LoginResult:
    """POST /api/auth/register body { username, email, password, display_name? } → 201 LoginResult (signed in)
    409 username_taken | email_taken; 422 validation_error"""
    user = await auth_service.register(db, data)
    access, refresh = await auth_service.start_session(db, user, describe_request(request))
    await db.commit()
    return LoginResult(access_token=access, refresh_token=refresh, user=UserOut.from_model(user))


@router.post("/login")
async def login(data: LoginIn, request: Request, db: Db) -> LoginResult:
    """POST /api/auth/login body { username, password } → LoginResult (username may also be the email)
    401 invalid_credentials; 403 account_locked"""
    user = await auth_service.authenticate(db, data.username, data.password)
    access, refresh = await auth_service.start_session(db, user, describe_request(request))
    await db.commit()
    return LoginResult(access_token=access, refresh_token=refresh, user=UserOut.from_model(user))


@router.post("/refresh")
async def refresh(data: RefreshIn, request: Request, db: Db) -> RefreshOut:
    """POST /api/auth/refresh body { refresh_token } → { access_token } (the refresh token stays the same)
    401 invalid_refresh_token; 403 account_locked"""
    access = await auth_service.refresh_access_token(db, data.refresh_token, describe_request(request))
    await db.commit()
    return RefreshOut(access_token=access)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(auth: CurrentAuth, db: Db, data: LogoutIn | None = None) -> Response:
    """POST /api/auth/logout body { refresh_token } → 204 (signs out this device only)"""
    await auth_service.logout(db, auth.user, auth.session_id, data.refresh_token if data else None)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(data: ForgotPasswordIn, db: Db) -> dict[str, str]:
    """POST /api/auth/forgot-password body { email } → 202, whether or not the email has an account"""
    await auth_service.request_password_reset(db, data.email)
    await db.commit()
    return {"detail": "Nếu email này có tài khoản, liên kết đặt lại mật khẩu đã được gửi tới nàng 🌸"}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(data: ResetPasswordIn, db: Db) -> Response:
    """POST /api/auth/reset-password body { token, new_password } → 204 (signs out every device)
    400 invalid_reset_token; 403 account_locked; 422 validation_error"""
    await auth_service.reset_password(db, data.token, data.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
