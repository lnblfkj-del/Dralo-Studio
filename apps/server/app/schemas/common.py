"""通用响应与分页模型。"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """可从 ORM 实例直接构造的响应模型基类。"""

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    """统一错误响应体。前端据 code 分支处理，message 可直接展示。"""

    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel, Generic[T]):
    """分页结果。"""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total


class PageParams(BaseModel):
    """分页查询参数。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
