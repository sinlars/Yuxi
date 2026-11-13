"""通过真实 HTTP 与 PostgreSQL 验证分类管理接口、树结构与权限边界。"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.main import app
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.manager import PostgresManager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_category_management_http_round_trip_and_permissions():
    """管理员可维护树节点，普通用户只读，树结构约束与非法输入被拒绝。"""
    schema = f"pytest_categories_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = scoped_engine
    manager._initialized = True
    session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)

    async def provide_db():
        async with session_factory() as session:
            yield session

    async def provide_admin():
        return SimpleNamespace(uid="admin-uid", role="admin")

    async def provide_reader():
        return SimpleNamespace(uid="reader-uid", role="user", department_id="dept-1")

    await manager.create_business_tables()
    await manager.create_schema_version_table()
    await manager.ensure_category_schema()
    await manager.ensure_resource_category_schema()

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_admin_user] = provide_admin
    app.dependency_overrides[get_required_user] = provide_admin
    path = "/api/categories"

    def update_payload(node: dict, **changes) -> dict:
        payload = {
            "parent_id": node["parent_id"],
            "name": node["name"],
            "code": node["code"] or "",
            "remark": node["remark"],
            "enabled": node["enabled"],
        }
        payload.update(changes)
        return payload

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            types = await client.get(f"{path}/types")
            assert types.status_code == 200, types.text
            assert {item["code"] for item in types.json()["items"]} == {"icd10", "clc"}

            empty = await client.get(path, params={"category_type": "icd10"})
            assert empty.status_code == 200, empty.text
            assert empty.json()["items"] == []
            invalid_type = await client.get(path, params={"category_type": "unknown"})
            assert invalid_type.status_code == 400

            root_response = await client.post(
                path,
                json={"category_type": "icd10", "name": " 呼吸系统疾病 ", "code": "J00-J99"},
            )
            assert root_response.status_code == 200, root_response.text
            root = root_response.json()
            assert root["name"] == "呼吸系统疾病"
            assert root["level"] == 1
            assert root["parent_id"] is None
            assert root["is_leaf"] is True
            assert root["enabled"] is True

            child_a_response = await client.post(
                path,
                json={
                    "category_type": "icd10",
                    "parent_id": root["id"],
                    "name": "上呼吸道感染",
                    "code": "J00",
                    "remark": "含急性咽炎",
                },
            )
            assert child_a_response.status_code == 200, child_a_response.text
            child_a = child_a_response.json()
            child_b_response = await client.post(
                path,
                json={"category_type": "icd10", "parent_id": root["id"], "name": "流行性感冒", "code": "J10"},
            )
            assert child_b_response.status_code == 200, child_b_response.text
            child_b = child_b_response.json()
            assert child_a["level"] == 2
            assert child_a["parent_id"] == root["id"]

            grandchild_response = await client.post(
                path,
                json={"category_type": "icd10", "parent_id": child_a["id"], "name": "急性咽炎", "code": "J00.0"},
            )
            assert grandchild_response.status_code == 200, grandchild_response.text
            grandchild = grandchild_response.json()
            assert grandchild["level"] == 3

            tree = (await client.get(path, params={"category_type": "icd10"})).json()
            assert tree["total"] == 4
            tree_root = tree["items"][0]
            assert tree_root["id"] == root["id"]
            assert tree_root["is_leaf"] is False
            assert [item["id"] for item in tree_root["children"]] == [child_a["id"], child_b["id"]]
            assert tree_root["children"][0]["is_leaf"] is False
            assert tree_root["children"][0]["children"][0]["id"] == grandchild["id"]

            toggled = await client.put(f"{path}/{child_a['id']}", json=update_payload(child_a, enabled=False))
            assert toggled.status_code == 200, toggled.text
            assert toggled.json()["enabled"] is False
            assert toggled.json()["level"] == 2

            same_code = await client.put(f"{path}/{child_a['id']}", json=update_payload(child_a))
            assert same_code.status_code == 200, same_code.text

            duplicate_code = await client.post(
                path,
                json={"category_type": "icd10", "parent_id": root["id"], "name": "重复编码", "code": "J00"},
            )
            assert duplicate_code.status_code == 409

            clc_root_response = await client.post(
                path,
                json={"category_type": "clc", "name": "医药卫生", "code": "R"},
            )
            assert clc_root_response.status_code == 200, clc_root_response.text
            clc_root = clc_root_response.json()
            cross_type_code = await client.post(
                path,
                json={"category_type": "clc", "parent_id": clc_root["id"], "name": "内科学", "code": "J00"},
            )
            assert cross_type_code.status_code == 200, cross_type_code.text
            assert cross_type_code.json()["level"] == 2

            mismatched_parent = await client.post(
                path,
                json={"category_type": "icd10", "parent_id": clc_root["id"], "name": "类型不一致", "code": "X1"},
            )
            assert mismatched_parent.status_code == 400

            cycle = await client.put(f"{path}/{root['id']}", json=update_payload(root, parent_id=child_a["id"]))
            assert cycle.status_code == 400

            move_to_self = await client.put(f"{path}/{root['id']}", json=update_payload(root, parent_id=root["id"]))
            assert move_to_self.status_code == 400

            delete_branch = await client.delete(f"{path}/{child_a['id']}")
            assert delete_branch.status_code == 409

            app.dependency_overrides[get_required_user] = provide_reader
            app.dependency_overrides.pop(get_admin_user, None)
            forbidden_create = await client.post(path, json={"category_type": "icd10", "name": "普通用户分类"})
            assert forbidden_create.status_code == 403
            forbidden_update = await client.put(
                f"{path}/{child_b['id']}", json=update_payload(child_b, name="普通用户改名称")
            )
            assert forbidden_update.status_code == 403
            forbidden_delete = await client.delete(f"{path}/{child_b['id']}")
            assert forbidden_delete.status_code == 403
            readable = await client.get(path, params={"category_type": "icd10"})
            assert readable.status_code == 200, readable.text

            app.dependency_overrides[get_admin_user] = provide_admin
            app.dependency_overrides[get_required_user] = provide_admin
            missing_name = await client.post(path, json={"category_type": "icd10", "name": " "})
            assert missing_name.status_code == 400

            deleted_grandchild = await client.delete(f"{path}/{grandchild['id']}")
            assert deleted_grandchild.status_code == 200
            tree_after = (await client.get(path, params={"category_type": "icd10"})).json()
            child_after = next(item for item in tree_after["items"][0]["children"] if item["id"] == child_a["id"])
            assert child_after["is_leaf"] is True

            assert (await client.delete(f"{path}/{child_a['id']}")).status_code == 200
            assert (await client.delete(f"{path}/{child_b['id']}")).status_code == 200
            empty_again = (await client.get(path, params={"category_type": "icd10"})).json()
            assert empty_again["total"] == 1
            assert empty_again["items"][0]["is_leaf"] is True
            assert (await client.delete(f"{path}/{root['id']}")).status_code == 200
            assert (await client.get(path, params={"category_type": "icd10"})).json()["total"] == 0
            assert (await client.delete(f"{path}/{root['id']}")).status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
