"""资源管理持久化 Repository。"""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import CategoryNode, Resource, ResourceCategory, ResourceType
from yuxi.utils.datetime_utils import utc_now_naive


class ResourceRepository:
    """读写资源类型与资源条目。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_types(self) -> list[ResourceType]:
        """按排序值列出全部资源类型。"""
        result = await self.db.execute(select(ResourceType).order_by(ResourceType.sort_order, ResourceType.id))
        return list(result.scalars().all())

    async def get_type_by_code(self, code: str) -> ResourceType | None:
        """按编码读取资源类型。"""
        return await self.db.scalar(select(ResourceType).where(ResourceType.code == code))

    async def add_type(self, resource_type: ResourceType) -> ResourceType:
        """新增资源类型并 flush。"""
        self.db.add(resource_type)
        await self.db.flush()
        return resource_type

    async def count_resources_of_type(self, code: str) -> int:
        """统计某类型下的资源数量，用于删除前占用校验。"""
        count = await self.db.scalar(
            select(func.count()).select_from(Resource).where(Resource.resource_type == code)
        )
        return int(count or 0)

    async def delete_type(self, resource_type: ResourceType) -> None:
        """删除资源类型。"""
        await self.db.delete(resource_type)

    async def get(self, resource_id: int) -> Resource | None:
        """按主键读取资源。"""
        return await self.db.scalar(select(Resource).where(Resource.id == resource_id))

    async def get_many(self, resource_ids: list[int]) -> list[Resource]:
        """按主键批量读取资源。"""
        if not resource_ids:
            return []
        result = await self.db.execute(select(Resource).where(Resource.id.in_(resource_ids)))
        return list(result.scalars().all())

    async def add(self, resource: Resource) -> Resource:
        """新增资源并 flush。"""
        self.db.add(resource)
        await self.db.flush()
        return resource

    async def delete(self, resource: Resource) -> None:
        """删除资源。"""
        await self.db.delete(resource)

    async def list_categories(self, resource_id: int) -> list[CategoryNode]:
        """列出资源已标引的分类节点。"""
        result = await self.db.execute(
            select(CategoryNode)
            .join(ResourceCategory, ResourceCategory.category_id == CategoryNode.id)
            .where(ResourceCategory.resource_id == resource_id)
            .order_by(CategoryNode.category_type, CategoryNode.level, CategoryNode.id)
        )
        return list(result.scalars().all())

    async def get_categories_by_ids(self, category_ids: list[int]) -> list[CategoryNode]:
        """按主键批量读取分类节点，用于标引前存在性校验。"""
        if not category_ids:
            return []
        result = await self.db.execute(select(CategoryNode).where(CategoryNode.id.in_(category_ids)))
        return list(result.scalars().all())

    async def add_resource_categories(
        self,
        resource_ids: list[int],
        category_ids: list[int],
        *,
        uid: str,
    ) -> int:
        """批量追加标引，已存在的组合跳过，返回新增条数。"""
        if not resource_ids or not category_ids:
            return 0
        now = utc_now_naive()
        rows = [
            {"resource_id": resource_id, "category_id": category_id, "created_by": uid, "created_at": now}
            for resource_id in resource_ids
            for category_id in category_ids
        ]
        statement = (
            insert(ResourceCategory)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["resource_id", "category_id"])
        )
        result = await self.db.execute(statement)
        return int(result.rowcount or 0)

    async def replace_resource_categories(
        self,
        resource_id: int,
        category_ids: list[int],
        *,
        uid: str,
    ) -> None:
        """用给定分类集合覆盖资源现有标引。"""
        await self.db.execute(delete(ResourceCategory).where(ResourceCategory.resource_id == resource_id))
        await self.add_resource_categories([resource_id], category_ids, uid=uid)

    async def list_page(
        self,
        *,
        resource_type: str | None,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Resource], int]:
        """按类型与关键字分页查询资源，返回当前页与总数。"""
        conditions = []
        if resource_type:
            conditions.append(Resource.resource_type == resource_type)
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    Resource.title.ilike(pattern),
                    Resource.subtitle.ilike(pattern),
                    Resource.summary.ilike(pattern),
                    Resource.author.ilike(pattern),
                    Resource.institution.ilike(pattern),
                )
            )

        total = int(
            await self.db.scalar(select(func.count()).select_from(Resource).where(*conditions)) or 0
        )
        result = await self.db.execute(
            select(Resource)
            .where(*conditions)
            .order_by(Resource.updated_at.desc(), Resource.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total
