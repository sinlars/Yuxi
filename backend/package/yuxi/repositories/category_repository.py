"""分类管理持久化 Repository。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import CategoryNode, CategoryType, ResourceCategory


class CategoryRepository:
    """读写分类类型字典与分类树节点。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_types(self) -> list[CategoryType]:
        """按排序值列出全部分类类型。"""
        result = await self.db.execute(select(CategoryType).order_by(CategoryType.sort_order, CategoryType.id))
        return list(result.scalars().all())

    async def get_type_by_code(self, code: str) -> CategoryType | None:
        """按编码读取分类类型。"""
        return await self.db.scalar(select(CategoryType).where(CategoryType.code == code))

    async def list_nodes(self, category_type: str) -> list[CategoryNode]:
        """按类型列出全部节点，根节点在前且同级按 ID 稳定排序。"""
        result = await self.db.execute(
            select(CategoryNode)
            .where(CategoryNode.category_type == category_type)
            .order_by(CategoryNode.level, CategoryNode.id)
        )
        return list(result.scalars().all())

    async def get(self, node_id: int) -> CategoryNode | None:
        """按主键读取分类节点。"""
        return await self.db.scalar(select(CategoryNode).where(CategoryNode.id == node_id))

    async def code_taken(self, category_type: str, code: str, *, exclude_id: int | None = None) -> bool:
        """判断同类型内分类编码是否已被其他节点占用。"""
        statement = select(CategoryNode.id).where(
            CategoryNode.category_type == category_type,
            CategoryNode.code == code,
        )
        if exclude_id is not None:
            statement = statement.where(CategoryNode.id != exclude_id)
        return await self.db.scalar(statement) is not None

    async def count_resource_links(self, category_id: int) -> int:
        """统计该分类被多少资源标引，用于删除前占用校验。"""
        return int(
            await self.db.scalar(
                select(func.count()).select_from(ResourceCategory).where(ResourceCategory.category_id == category_id)
            )
            or 0
        )

    async def add(self, node: CategoryNode) -> CategoryNode:
        """新增分类节点并 flush。"""
        self.db.add(node)
        await self.db.flush()
        return node

    async def delete(self, node: CategoryNode) -> None:
        """删除分类节点。"""
        await self.db.delete(node)
