"""分类管理路由：分类类型切换与树节点读写。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.category_service import (
    create_category,
    delete_category,
    list_category_tree,
    list_category_types,
    update_category,
)
from yuxi.storage.postgres.models_business import User

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

categories = APIRouter(prefix="/categories", tags=["categories"])


class CategoryCreatePayload(BaseModel):
    category_type: str = Field(..., description="分类类型编码")
    parent_id: int | None = Field(None, description="父节点 ID，空值表示根节点")
    name: str = Field(..., description="分类名称")
    code: str | None = Field(None, description="分类编码，同类型内唯一")
    remark: str = Field("", description="备注")
    enabled: bool = Field(True, description="是否启用")


class CategoryUpdatePayload(BaseModel):
    parent_id: int | None = Field(None, description="父节点 ID，空值表示根节点")
    name: str = Field(..., description="分类名称")
    code: str | None = Field(None, description="分类编码，同类型内唯一")
    remark: str = Field("", description="备注")
    enabled: bool = Field(True, description="是否启用")


@categories.get("/types")
async def list_category_types_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await list_category_types(db)}


@categories.get("")
async def list_category_tree_route(
    category_type: str = Query(..., description="分类类型编码"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_category_tree(db, category_type=category_type)


@categories.post("")
async def create_category_route(
    payload: CategoryCreatePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_category(db, payload=payload.model_dump(), uid=current_user.uid)


@categories.put("/{node_id}")
async def update_category_route(
    node_id: int,
    payload: CategoryUpdatePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_category(db, node_id=node_id, payload=payload.model_dump(), uid=current_user.uid)


@categories.delete("/{node_id}")
async def delete_category_route(
    node_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_category(db, node_id=node_id)
    return {"success": True}
