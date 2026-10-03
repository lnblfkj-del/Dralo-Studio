"""鉴权相关 schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    keep_logged_in: bool = False


class UserOut(ORMModel):
    """用户信息。绝不包含 password_hash。"""

    id: int
    username: str
    display_name: str | None = None
    role: str
    is_active: bool
    must_change_password: bool = False
    permissions: dict[str, bool] = Field(default_factory=dict)
    last_login_at: datetime | None = None
    workspace_id: str | None = None
    workspace_role: str | None = None
    platform_admin: bool = False
    personal_only: bool = False
    contact: str | None = None
    avatar_media_id: int | None = None
    created_at: datetime | None = None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    contact: str = Field(default="", max_length=128)
    avatar_media_id: int | None = Field(default=None, ge=1)

    @field_validator("display_name", "contact")
    @classmethod
    def trim_text(cls, value):
        if value is None:
            raise ValueError("昵称不能为空")
        value = value.strip()
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)

    @field_validator("new_password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 72 个 UTF-8 字节")
        return value
