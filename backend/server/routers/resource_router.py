"""资源管理路由：类型字典、资源条目与封面/附件上传。"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.resource_service import (
    assign_categories_to_resources,
    create_resource,
    create_resource_type,
    delete_resource,
    delete_resource_type,
    get_resource,
    list_resource_types,
    list_resources,
    publish_resources,
    update_resource,
    update_resource_type,
)
from yuxi.storage.minio.client import StorageError, aupload_file_to_minio
from yuxi.storage.minio.utils import upload_image_to_minio
from yuxi.storage.postgres.models_business import User
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, read_upload_with_limit

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

resources = APIRouter(prefix="/resources", tags=["resources"])

RESOURCE_COVER_PREFIX = "resources/covers"
RESOURCE_ATTACHMENT_PREFIX = "resources/attachments"
RESOURCE_COVER_MAX_BYTES = 10 * 1024 * 1024
RESOURCE_ATTACHMENT_MAX_BYTES = MAX_UPLOAD_SIZE_BYTES
RESOURCE_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".txt",
    ".md",
    ".csv",
    ".mp3",
    ".mp4",
    ".wav",
    ".m4a",
    ".mov",
    ".avi",
    ".webm",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".stl",
    ".obj",
    ".glb",
    ".gltf",
    ".fbx",
    ".3ds",
    ".step",
    ".stp",
    ".zip",
}


class ResourceTypeCreatePayload(BaseModel):
    code: str = Field(..., description="类型编码，小写字母开头")
    name: str = Field(..., description="类型名称")
    sort_order: int = Field(0, description="排序值，越小越靠前")


class ResourceTypeUpdatePayload(BaseModel):
    name: str = Field(..., description="类型名称")
    sort_order: int = Field(0, description="排序值，越小越靠前")


class ResourcePayload(BaseModel):
    resource_type: str = Field(..., description="资源类型编码")
    title: str = Field(..., description="标题")
    subtitle: str | None = Field(None, description="副标题")
    summary: str = Field("", description="摘要或简介")
    author: str | None = Field(None, description="作者")
    institution: str | None = Field(None, description="所属机构")
    keywords: list[str] = Field(default_factory=list, description="关键词")
    subject_terms: list[str] = Field(default_factory=list, description="主题词")
    tags: list[str] = Field(default_factory=list, description="标签")
    cover_url: str | None = Field(None, description="封面或缩略图地址")
    content_markdown: str = Field("", description="Markdown 正文")
    attachments: list[dict] = Field(default_factory=list, description="附件列表")
    category_ids: list[int] | None = Field(None, description="分类标引 ID 列表；空数组表示清空")


class ResourcePublishPayload(BaseModel):
    ids: list[int] = Field(..., min_length=1, description="要发布的资源 ID 列表")


class ResourceCategoryAssignPayload(BaseModel):
    resource_ids: list[int] = Field(..., min_length=1, description="要标引的资源 ID 列表")
    category_ids: list[int] = Field(..., min_length=1, description="要关联的分类节点 ID 列表")


def _safe_file_name(file_name: str | None) -> str:
    """移除路径片段，避免上传对象名越过前缀。"""
    return Path(file_name or "").name.replace("\\", "_").strip(" .") or "attachment.bin"


@resources.get("/types")
async def list_resource_types_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return {"items": await list_resource_types(db)}


@resources.post("/types")
async def create_resource_type_route(
    payload: ResourceTypeCreatePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_resource_type(
        db, code=payload.code, name=payload.name, sort_order=payload.sort_order
    )


@resources.put("/types/{code}")
async def update_resource_type_route(
    code: str,
    payload: ResourceTypeUpdatePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_resource_type(
        db, code=code, name=payload.name, sort_order=payload.sort_order
    )


@resources.delete("/types/{code}")
async def delete_resource_type_route(
    code: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_resource_type(db, code=code)
    return {"success": True}


@resources.post("/uploads")
async def upload_resource_file_route(
    file: UploadFile = File(...),
    kind: str = Form("attachment", description="cover 或 attachment"),
    current_user: User = Depends(get_admin_user),
):
    """上传封面或附件到对象存储，返回可直接写入资源的文件元数据。"""
    if kind not in {"cover", "attachment"}:
        raise HTTPException(status_code=400, detail="上传类型必须是 cover 或 attachment")

    safe_name = _safe_file_name(file.filename)
    suffix = Path(safe_name).suffix.lower()

    try:
        if kind == "cover":
            url = await upload_image_to_minio(
                file,
                object_prefix=RESOURCE_COVER_PREFIX,
                max_size_bytes=RESOURCE_COVER_MAX_BYTES,
                too_large_message="封面不能超过 10 MB",
            )
            size = file.size or 0
        else:
            if suffix not in RESOURCE_ATTACHMENT_EXTENSIONS:
                raise HTTPException(status_code=400, detail="不支持该附件格式")
            content = await read_upload_with_limit(
                file,
                max_size_bytes=RESOURCE_ATTACHMENT_MAX_BYTES,
                too_large_message="附件不能超过 100 MB",
            )
            object_name = f"{RESOURCE_ATTACHMENT_PREFIX}/{uuid.uuid4().hex}/{safe_name}"
            url = await aupload_file_to_minio("public", object_name, content)
            size = len(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except StorageError:
        raise HTTPException(status_code=502, detail="文件存储服务不可用") from None

    return {
        "name": safe_name,
        "url": url,
        "size": size,
        "content_type": file.content_type or "",
    }


@resources.get("")
async def list_resources_route(
    resource_type: str | None = Query(None, description="按类型编码过滤"),
    keyword: str | None = Query(None, description="标题/摘要/作者/机构关键字"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_resources(
        db,
        resource_type=resource_type,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@resources.post("/publish")
async def publish_resources_route(
    payload: ResourcePublishPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await publish_resources(db, resource_ids=payload.ids, uid=current_user.uid)


@resources.post("/categories/assign")
async def assign_resource_categories_route(
    payload: ResourceCategoryAssignPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """批量给选中的资源追加分类标引。"""
    return await assign_categories_to_resources(
        db,
        resource_ids=payload.resource_ids,
        category_ids=payload.category_ids,
        uid=current_user.uid,
    )


@resources.get("/{resource_id}")
async def get_resource_route(
    resource_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_resource(db, resource_id=resource_id)


@resources.post("")
async def create_resource_route(
    payload: ResourcePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_resource(db, payload=payload.model_dump(), uid=current_user.uid)


@resources.put("/{resource_id}")
async def update_resource_route(
    resource_id: int,
    payload: ResourcePayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_resource(db, resource_id=resource_id, payload=payload.model_dump(), uid=current_user.uid)


@resources.delete("/{resource_id}")
async def delete_resource_route(
    resource_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_resource(db, resource_id=resource_id)
    return {"success": True}
