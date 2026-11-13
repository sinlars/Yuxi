"""分类管理用例：类型切换、树结构校验与节点写入。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.category_repository import CategoryRepository
from yuxi.storage.postgres.models_business import CategoryNode, CategoryType

MAX_TREE_DEPTH = 64


def _clean_text(value: object, *, field: str, max_length: int | None = None) -> str:
    """把可选文本规整为去首尾空白的字符串，并校验长度。"""
    if value is None:
        return ""
    text = str(value).strip()
    if max_length is not None and len(text) > max_length:
        raise HTTPException(status_code=400, detail=f"{field}长度不能超过 {max_length} 个字符")
    return text


async def _require_type(repo: CategoryRepository, code: object) -> CategoryType:
    """校验分类类型存在，返回类型实体。"""
    normalized = _clean_text(code, field="分类类型", max_length=64)
    if not normalized:
        raise HTTPException(status_code=400, detail="分类类型不能为空")
    category_type = await repo.get_type_by_code(normalized)
    if category_type is None:
        raise HTTPException(status_code=400, detail="分类类型不存在")
    return category_type


async def _resolve_parent(
    repo: CategoryRepository,
    category_type: str,
    parent_id: object,
    *,
    moving_id: int | None = None,
) -> CategoryNode | None:
    """校验父节点属于同一分类类型，并阻断把节点移动到自身子孙链之下。"""
    if parent_id is None:
        return None

    parent = await repo.get(int(parent_id))
    if parent is None:
        raise HTTPException(status_code=400, detail="父分类不存在")
    if parent.category_type != category_type:
        raise HTTPException(status_code=400, detail="父分类必须属于同一分类类型")

    ancestor = parent
    for _ in range(MAX_TREE_DEPTH):
        if moving_id is not None and ancestor.id == moving_id:
            raise HTTPException(status_code=400, detail="不能把分类移动到自身或其子分类下")
        if ancestor.parent_id is None:
            return parent
        ancestor = await repo.get(ancestor.parent_id)
        if ancestor is None:
            raise HTTPException(status_code=400, detail="父分类层级异常")
    raise HTTPException(status_code=400, detail="分类层级超过允许深度")


def _build_tree(nodes: list[CategoryNode]) -> list[dict]:
    """把已按层级排序的节点组装为嵌套树。"""
    items: dict[int, dict] = {node.id: {**node.to_dict(), "children": []} for node in nodes}
    roots: list[dict] = []
    for node in nodes:
        parent_item = items.get(node.parent_id) if node.parent_id else None
        if parent_item is None:
            roots.append(items[node.id])
        else:
            parent_item["children"].append(items[node.id])
    return roots


def _sync_tree_shape(nodes: list[CategoryNode]) -> None:
    """按父链重算全部节点的层级与叶子标记，保证树结构只由 parent_id 决定。"""
    by_id = {node.id: node for node in nodes}
    children_of: dict[int, list[CategoryNode]] = {node.id: [] for node in nodes}
    roots: list[CategoryNode] = []
    for node in nodes:
        if node.parent_id is not None and node.parent_id in by_id:
            children_of[node.parent_id].append(node)
        else:
            roots.append(node)

    stack = [(node, 1) for node in roots]
    while stack:
        node, level = stack.pop()
        children = children_of[node.id]
        node.level = level
        node.is_leaf = not children
        for child in children:
            stack.append((child, level + 1))


async def list_category_types(db: AsyncSession) -> list[dict]:
    """列出可切换的分类类型字典。"""
    return [item.to_dict() for item in await CategoryRepository(db).list_types()]


async def list_category_tree(db: AsyncSession, *, category_type: str) -> dict:
    """按分类类型返回嵌套树与节点总数。"""
    repo = CategoryRepository(db)
    type_entity = await _require_type(repo, category_type)
    nodes = await repo.list_nodes(type_entity.code)
    return {
        "category_type": type_entity.code,
        "items": _build_tree(nodes),
        "total": len(nodes),
    }


async def create_category(db: AsyncSession, *, payload: dict, uid: str) -> dict:
    """在指定类型下创建节点；缺省 parent_id 表示根节点。"""
    repo = CategoryRepository(db)
    type_entity = await _require_type(repo, payload.get("category_type"))
    name = _clean_text(payload.get("name"), field="分类名称", max_length=255)
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    code = _clean_text(payload.get("code"), field="分类编码", max_length=128) or None
    if code is not None and await repo.code_taken(type_entity.code, code):
        raise HTTPException(status_code=409, detail="分类编码已存在")
    parent = await _resolve_parent(repo, type_entity.code, payload.get("parent_id"))

    node = CategoryNode(
        category_type=type_entity.code,
        parent_id=parent.id if parent is not None else None,
        name=name,
        code=code,
        remark=_clean_text(payload.get("remark"), field="备注", max_length=1000),
        enabled=bool(payload.get("enabled", True)),
        level=(parent.level + 1) if parent is not None else 1,
        is_leaf=True,
        created_by=str(uid),
        updated_by=str(uid),
    )
    if parent is not None:
        parent.is_leaf = False

    try:
        await repo.add(node)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="分类编码已存在") from None
    await db.refresh(node)
    return node.to_dict()


async def update_category(db: AsyncSession, *, node_id: int, payload: dict, uid: str) -> dict:
    """更新节点字段与父节点；类型不可变更，移动后重算整棵类型的层级。"""
    repo = CategoryRepository(db)
    node = await repo.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="分类节点不存在")

    name = _clean_text(payload.get("name"), field="分类名称", max_length=255)
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    code = _clean_text(payload.get("code"), field="分类编码", max_length=128) or None
    if code is not None and await repo.code_taken(node.category_type, code, exclude_id=node.id):
        raise HTTPException(status_code=409, detail="分类编码已存在")
    parent = await _resolve_parent(repo, node.category_type, payload.get("parent_id"), moving_id=node.id)

    node.name = name
    node.code = code
    node.remark = _clean_text(payload.get("remark"), field="备注", max_length=1000)
    node.enabled = bool(payload.get("enabled", node.enabled))
    node.parent_id = parent.id if parent is not None else None
    node.updated_by = str(uid)

    try:
        _sync_tree_shape(await repo.list_nodes(node.category_type))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="分类编码已存在") from None
    await db.refresh(node)
    return node.to_dict()


async def delete_category(db: AsyncSession, *, node_id: int) -> None:
    """删除叶子节点；存在子分类或资源标引时拒绝，并同步父节点叶子标记。"""
    repo = CategoryRepository(db)
    node = await repo.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="分类节点不存在")

    nodes = await repo.list_nodes(node.category_type)
    if any(item.parent_id == node.id for item in nodes):
        raise HTTPException(status_code=409, detail="存在子分类，无法删除")
    if await repo.count_resource_links(node.id):
        raise HTTPException(status_code=409, detail="该分类已关联资源，无法删除")
    await repo.delete(node)
    _sync_tree_shape([item for item in nodes if item.id != node.id])
    await db.commit()
