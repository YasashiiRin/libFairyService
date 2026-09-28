"""Auth request/response shapes (api-contract.md#auth)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.user import DISPLAY_NAME_MAX, UserOut

PASSWORD_MIN = 8  # SecuritySection: "Mật khẩu mới cần ít nhất 8 ký tự"
PASSWORD_MAX = 128
USERNAME_PATTERN = r"^[a-z0-9_.]{3,32}$"

Password = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class LoginIn(BaseModel):
    # the username or the email
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=USERNAME_PATTERN)
    email: EmailStr
    password: str = Password
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)

    @field_validator("username", mode="before")
    @classmethod
    def _lower_username(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("email", mode="after")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        return (v.strip() or None) if isinstance(v, str) else v


class LoginResult(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class RefreshOut(BaseModel):
    """The refresh token is not rotated, so only a new access token comes back (the client keeps its refresh token)."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    new_password: str = Password


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Password
