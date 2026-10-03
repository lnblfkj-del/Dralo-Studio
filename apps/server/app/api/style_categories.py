"""Shared category management; deleting a category never deletes styles."""
from hashlib import sha256
import json
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.errors import ConflictError, NotFoundError
from app.models.agent_config import StyleCategory, StylePreset

router = APIRouter(prefix="/agent-config/styles/categories", tags=["agent-config"])


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def normalized(cls, value):
        value = value.strip()
        if not value or value in {"全部", "未分类"}:
            raise ValueError("请输入分类名称，全部和未分类为系统保留名称")
        return value


class CategoryOrder(BaseModel):
    ids: list[int] = Field(max_length=1000)


async def require_category(session, category_id):
    category = await session.get(StyleCategory, category_id)
    if category is None:
        raise NotFoundError("分类不存在，请刷新")
    return category


@router.get("")
async def list_categories(request: Request, session: SessionDep, _user: CurrentUser):
    categories = (await session.scalars(select(StyleCategory).order_by(StyleCategory.position, StyleCategory.id))).all()
    styles = (await session.scalars(select(StylePreset))).all()
    values = [{"id": item.id, "name": item.name, "position": item.position,
             "count": sum(item.id in (style.category_ids or ([style.category_id] if style.category_id else [])) for style in styles)} for item in categories]
    raw = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    etag = '"' + sha256(raw).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-store", "Vary": "Cookie, Authorization, X-Workspace-ID"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(raw, media_type="application/json", headers=headers)


@router.post("", status_code=201)
async def create_category(payload: CategoryInput, session: SessionDep, _user: AdminUser):
    position = (await session.scalar(select(func.max(StyleCategory.position))) or 0) + 1
    item = StyleCategory(name=payload.name, position=position)
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("分类名称已存在") from exc
    return {"id": item.id, "name": item.name, "position": item.position, "count": 0}


@router.put("/order")
async def reorder_categories(payload: CategoryOrder, session: SessionDep, _user: AdminUser):
    ids = set((await session.scalars(select(StyleCategory.id))).all())
    if len(payload.ids) != len(set(payload.ids)) or set(payload.ids) != ids:
        raise ConflictError("分类列表已变化，请刷新后排序")
    for position, category_id in enumerate(payload.ids):
        await session.execute(update(StyleCategory).where(StyleCategory.id == category_id).values(position=position))
    await session.commit()
    return {"ok": True}


@router.patch("/{category_id}")
async def rename_category(category_id: int, payload: CategoryInput, session: SessionDep, _user: AdminUser):
    item = await require_category(session, category_id)
    item.name = payload.name
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("分类名称已存在") from exc
    return {"ok": True}


@router.delete("/{category_id}")
async def delete_category(category_id: int, session: SessionDep, _user: AdminUser, target_id: int | None = Query(None, ge=1)):
    item = await require_category(session, category_id)
    if target_id == category_id:
        raise ConflictError("请选择其他分类")
    if target_id is not None:
        await require_category(session, target_id)
    moved = 0
    styles = (await session.scalars(select(StylePreset).with_for_update())).all()
    for style in styles:
        ids = style.category_ids or ([style.category_id] if style.category_id else [])
        if category_id not in ids:
            continue
        ids = [value for value in ids if value != category_id]
        if target_id is not None and target_id not in ids:
            ids.append(target_id)
        style.category_ids = ids
        style.category_id = ids[0] if ids else None
        moved += 1
    await session.delete(item)
    await session.commit()
    return {"moved": moved, "target_id": target_id}
