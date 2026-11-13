"""资源管理用例：类型字典与资源条目的校验、分页和写入。"""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.resource_repository import ResourceRepository
from yuxi.storage.postgres.models_business import Resource, ResourceType

TYPE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
RESOURCE_STATUS_DRAFT = "draft"
RESOURCE_STATUS_PUBLISHED = "published"

LIST_EXCLUDED_FIELDS = ("content_markdown",)


def _clean_text(value: object, *, field: str, max_length: int | None = None) -> str:
    """把可选文本规整为去首尾空白的字符串，并校验长度。"""
    if value is None:
        return ""
    text = str(value).strip()
    if max_length is not None and len(text) > max_length:
        raise HTTPException(status_code=400, detail=f"{field}长度不能超过 {max_length} 个字符")
    return text


def _clean_text_list(value: object, *, field: str) -> list[str]:
    """规整字符串列表：去空白、去空值、去重并保持原顺序。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"{field}必须是字符串数组")
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _clean_attachments(value: object) -> list[dict]:
    """校验附件条目，只保留展示与下载需要的字段。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="附件必须是对象数组")
    cleaned: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="附件条目必须是对象")
        name = _clean_text(item.get("name"), field="附件名称", max_length=255)
        url = _clean_text(item.get("url"), field="附件地址", max_length=1000)
        if not name or not url:
            raise HTTPException(status_code=400, detail="附件必须包含名称与地址")
        size = item.get("size")
        cleaned.append(
            {
                "name": name,
                "url": url,
                "size": int(size) if isinstance(size, int) and size >= 0 else 0,
                "content_type": _clean_text(item.get("content_type"), field="附件类型", max_length=128),
            }
        )
    return cleaned


def _clean_category_ids(value: object) -> list[int] | None:
    """规整分类标引 ID 列表；None 表示请求未提供该字段。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="分类标引必须是分类 ID 数组")
    cleaned: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise HTTPException(status_code=400, detail="分类 ID 必须是整数")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


async def _ensure_categories_exist(repo: ResourceRepository, category_ids: list[int]) -> None:
    """校验分类节点全部存在，避免写入悬空标引。"""
    if not category_ids:
        return
    nodes = await repo.get_categories_by_ids(category_ids)
    if len(nodes) != len(category_ids):
        raise HTTPException(status_code=400, detail="部分分类不存在")


async def _resource_detail(repo: ResourceRepository, resource: Resource) -> dict:
    """资源完整内容加上已标引分类。"""
    detail = resource.to_dict()
    detail["categories"] = [node.to_dict() for node in await repo.list_categories(resource.id)]
    return detail


def _apply_resource_payload(resource: Resource, payload: dict) -> None:
    """把已校验的表单字段写入资源实体。"""
    resource.resource_type = payload["resource_type"]
    resource.title = payload["title"]
    resource.subtitle = payload["subtitle"] or None
    resource.summary = payload["summary"]
    resource.author = payload["author"] or None
    resource.institution = payload["institution"] or None
    resource.keywords = payload["keywords"]
    resource.subject_terms = payload["subject_terms"]
    resource.tags = payload["tags"]
    resource.cover_url = payload["cover_url"] or None
    resource.content_markdown = payload["content_markdown"]
    resource.attachments = payload["attachments"]


async def _normalize_resource_payload(
    repo: ResourceRepository,
    payload: dict,
) -> dict:
    """校验资源表单，返回可直接写入实体的规整字段。"""
    type_code = _clean_text(payload.get("resource_type"), field="资源类型", max_length=64)
    if not type_code:
        raise HTTPException(status_code=400, detail="资源类型不能为空")
    if await repo.get_type_by_code(type_code) is None:
        raise HTTPException(status_code=400, detail="资源类型不存在")

    title = _clean_text(payload.get("title"), field="标题", max_length=255)
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")

    return {
        "resource_type": type_code,
        "title": title,
        "subtitle": _clean_text(payload.get("subtitle"), field="副标题", max_length=255),
        "summary": _clean_text(payload.get("summary"), field="摘要"),
        "author": _clean_text(payload.get("author"), field="作者", max_length=255),
        "institution": _clean_text(payload.get("institution"), field="所属机构", max_length=255),
        "keywords": _clean_text_list(payload.get("keywords"), field="关键词"),
        "subject_terms": _clean_text_list(payload.get("subject_terms"), field="主题词"),
        "tags": _clean_text_list(payload.get("tags"), field="标签"),
        "cover_url": _clean_text(payload.get("cover_url"), field="封面地址", max_length=1000),
        "content_markdown": _clean_text(payload.get("content_markdown"), field="正文"),
        "attachments": _clean_attachments(payload.get("attachments")),
    }


async def list_resource_types(db: AsyncSession) -> list[dict]:
    """列出资源类型字典。"""
    return [item.to_dict() for item in await ResourceRepository(db).list_types()]


async def create_resource_type(db: AsyncSession, *, code: str, name: str, sort_order: int) -> dict:
    """新增资源类型；编码唯一且不可与已有类型重复。"""
    normalized_code = _clean_text(code, field="类型编码", max_length=64).lower()
    if not TYPE_CODE_PATTERN.fullmatch(normalized_code):
        raise HTTPException(status_code=400, detail="类型编码需为小写字母、数字、下划线或连字符，且以字母开头")
    normalized_name = _clean_text(name, field="类型名称", max_length=64)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="类型名称不能为空")

    repo = ResourceRepository(db)
    if await repo.get_type_by_code(normalized_code) is not None:
        raise HTTPException(status_code=409, detail="类型编码已存在")

    resource_type = ResourceType(
        code=normalized_code,
        name=normalized_name,
        sort_order=int(sort_order),
        is_builtin=False,
    )
    try:
        await repo.add_type(resource_type)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="类型编码已存在") from None
    return resource_type.to_dict()


async def update_resource_type(db: AsyncSession, *, code: str, name: str, sort_order: int) -> dict:
    """修改类型名称与排序；编码作为资源外键不可变更。"""
    repo = ResourceRepository(db)
    resource_type = await repo.get_type_by_code(code)
    if resource_type is None:
        raise HTTPException(status_code=404, detail="资源类型不存在")

    normalized_name = _clean_text(name, field="类型名称", max_length=64)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="类型名称不能为空")

    resource_type.name = normalized_name
    resource_type.sort_order = int(sort_order)
    await db.commit()
    return resource_type.to_dict()


async def delete_resource_type(db: AsyncSession, *, code: str) -> None:
    """删除自定义类型；内置类型与被资源占用的类型不可删除。"""
    repo = ResourceRepository(db)
    resource_type = await repo.get_type_by_code(code)
    if resource_type is None:
        raise HTTPException(status_code=404, detail="资源类型不存在")
    if resource_type.is_builtin:
        raise HTTPException(status_code=400, detail="内置类型不可删除")
    if await repo.count_resources_of_type(code):
        raise HTTPException(status_code=409, detail="该类型下仍有资源，无法删除")

    await repo.delete_type(resource_type)
    await db.commit()


async def list_resources(
    db: AsyncSession,
    *,
    resource_type: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> dict:
    """分页列出资源摘要，正文不进入列表响应。"""
    safe_page = max(int(page), 1)
    safe_page_size = min(max(int(page_size), 1), MAX_PAGE_SIZE)
    items, total = await ResourceRepository(db).list_page(
        resource_type=_clean_text(resource_type, field="资源类型") or None,
        keyword=_clean_text(keyword, field="搜索关键字") or None,
        offset=(safe_page - 1) * safe_page_size,
        limit=safe_page_size,
    )
    summaries = []
    for item in items:
        item_dict = item.to_dict()
        for field in LIST_EXCLUDED_FIELDS:
            item_dict.pop(field, None)
        summaries.append(item_dict)
    return {"items": summaries, "total": total, "page": safe_page, "page_size": safe_page_size}


async def get_resource(db: AsyncSession, *, resource_id: int) -> dict:
    """读取单个资源的完整内容与已标引分类。"""
    repo = ResourceRepository(db)
    resource = await repo.get(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return await _resource_detail(repo, resource)


async def create_resource(db: AsyncSession, *, payload: dict, uid: str) -> dict:
    """创建资源条目，初始状态为编辑中，可同时写入分类标引。"""
    repo = ResourceRepository(db)
    normalized = await _normalize_resource_payload(repo, payload)
    category_ids = _clean_category_ids(payload.get("category_ids")) or []
    await _ensure_categories_exist(repo, category_ids)

    resource = Resource(created_by=str(uid), updated_by=str(uid), status=RESOURCE_STATUS_DRAFT)
    _apply_resource_payload(resource, normalized)
    await repo.add(resource)
    await repo.add_resource_categories([resource.id], category_ids, uid=str(uid))
    await db.commit()
    await db.refresh(resource)
    return await _resource_detail(repo, resource)


async def update_resource(db: AsyncSession, *, resource_id: int, payload: dict, uid: str) -> dict:
    """整体更新资源条目；未提供分类字段时保留现有标引。"""
    repo = ResourceRepository(db)
    resource = await repo.get(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")

    normalized = await _normalize_resource_payload(repo, payload)
    category_ids = _clean_category_ids(payload.get("category_ids"))
    if category_ids is not None:
        await _ensure_categories_exist(repo, category_ids)

    _apply_resource_payload(resource, normalized)
    resource.status = RESOURCE_STATUS_DRAFT
    resource.updated_by = str(uid)
    if category_ids is not None:
        await repo.replace_resource_categories(resource.id, category_ids, uid=str(uid))
    await db.commit()
    await db.refresh(resource)
    return await _resource_detail(repo, resource)


async def assign_categories_to_resources(
    db: AsyncSession,
    *,
    resource_ids: list[int],
    category_ids: list[int],
    uid: str,
) -> dict:
    """为多个资源追加分类标引，已存在的标引保持不变。"""
    ids = sorted({int(resource_id) for resource_id in resource_ids})
    categories = sorted({int(category_id) for category_id in category_ids})
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要标引的资源")
    if not categories:
        raise HTTPException(status_code=400, detail="请选择分类")

    repo = ResourceRepository(db)
    resources = await repo.get_many(ids)
    if len(resources) != len(ids):
        raise HTTPException(status_code=404, detail="部分资源不存在")
    await _ensure_categories_exist(repo, categories)

    linked = await repo.add_resource_categories(ids, categories, uid=str(uid))
    await db.commit()
    return {"linked": linked, "resources": len(resources), "categories": len(categories)}


async def publish_resources(db: AsyncSession, *, resource_ids: list[int], uid: str) -> dict:
    """把选中的资源状态改为已发布，返回实际发布数量。"""
    ids = sorted({int(resource_id) for resource_id in resource_ids})
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要发布的资源")

    repo = ResourceRepository(db)
    resources = await repo.get_many(ids)
    if len(resources) != len(ids):
        raise HTTPException(status_code=404, detail="部分资源不存在")

    for resource in resources:
        resource.status = RESOURCE_STATUS_PUBLISHED
        resource.updated_by = str(uid)
    await db.commit()
    return {"published": len(resources)}


async def delete_resource(db: AsyncSession, *, resource_id: int) -> None:
    """删除资源条目。"""
    repo = ResourceRepository(db)
    resource = await repo.get(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    await repo.delete(resource)
    await db.commit()
