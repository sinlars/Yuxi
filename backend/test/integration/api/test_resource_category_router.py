"""通过真实 HTTP 与 PostgreSQL 验证资源分类标引的追加、覆盖、展示与权限。"""

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

RESOURCE_FIELDS = (
    "resource_type",
    "title",
    "subtitle",
    "summary",
    "author",
    "institution",
    "keywords",
    "subject_terms",
    "tags",
    "cover_url",
    "content_markdown",
    "attachments",
)


async def test_resource_category_tagging_http_round_trip_and_permissions():
    """批量追加、单资源覆盖、详情回读、非法分类与权限边界均按契约工作。"""
    schema = f"pytest_resource_categories_{uuid.uuid4().hex[:16]}"
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
    await manager.ensure_resource_schema()
    await manager.ensure_category_schema()
    await manager.ensure_resource_category_schema()

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_admin_user] = provide_admin
    app.dependency_overrides[get_required_user] = provide_admin

    def resource_payload(resource: dict, **changes) -> dict:
        payload = {field: resource[field] for field in RESOURCE_FIELDS}
        payload.update(changes)
        return payload

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

            async def create_category(payload: dict) -> dict:
                response = await client.post("/api/categories", json=payload)
                assert response.status_code == 200, response.text
                return response.json()

            async def create_resource(title: str, **extra) -> dict:
                response = await client.post(
                    "/api/resources",
                    json={"resource_type": "case", "title": title, **extra},
                )
                assert response.status_code == 200, response.text
                return response.json()

            root = await create_category(
                {"category_type": "icd10", "name": "呼吸系统疾病", "code": "J00-J99"}
            )
            category_a = await create_category(
                {
                    "category_type": "icd10",
                    "parent_id": root["id"],
                    "name": "上呼吸道感染",
                    "code": "J00",
                }
            )
            category_b = await create_category(
                {
                    "category_type": "icd10",
                    "parent_id": root["id"],
                    "name": "流行性感冒",
                    "code": "J10",
                }
            )
            category_clc = await create_category(
                {"category_type": "clc", "name": "内科学", "code": "R5"}
            )

            resource_1 = await create_resource("资源A")
            resource_2 = await create_resource("资源B")

            assign = await client.post(
                "/api/resources/categories/assign",
                json={
                    "resource_ids": [resource_1["id"], resource_2["id"]],
                    "category_ids": [category_a["id"], category_clc["id"]],
                },
            )
            assert assign.status_code == 200, assign.text
            assert assign.json() == {"linked": 4, "resources": 2, "categories": 2}

            repeated = await client.post(
                "/api/resources/categories/assign",
                json={
                    "resource_ids": [resource_1["id"], resource_2["id"]],
                    "category_ids": [category_a["id"], category_clc["id"]],
                },
            )
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["linked"] == 0

            detail = await client.get(f"/api/resources/{resource_1['id']}")
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert {item["id"] for item in detail_body["categories"]} == {
                category_a["id"],
                category_clc["id"],
            }
            assert detail_body["categories"][0]["category_type"] in {"icd10", "clc"}

            replaced = await client.put(
                f"/api/resources/{resource_1['id']}",
                json=resource_payload(detail_body, category_ids=[category_a["id"]]),
            )
            assert replaced.status_code == 200, replaced.text
            assert [item["id"] for item in replaced.json()["categories"]] == [category_a["id"]]

            unchanged = await client.put(
                f"/api/resources/{resource_1['id']}",
                json=resource_payload(replaced.json()),
            )
            assert unchanged.status_code == 200, unchanged.text
            assert [item["id"] for item in unchanged.json()["categories"]] == [category_a["id"]]

            cleared = await client.put(
                f"/api/resources/{resource_1['id']}",
                json=resource_payload(unchanged.json(), category_ids=[]),
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["categories"] == []

            resource_3 = await create_resource("资源C", category_ids=[category_b["id"]])
            assert [item["id"] for item in resource_3["categories"]] == [category_b["id"]]

            invalid_category = await client.post(
                "/api/resources/categories/assign",
                json={"resource_ids": [resource_2["id"]], "category_ids": [999999]},
            )
            assert invalid_category.status_code == 400
            missing_resource = await client.post(
                "/api/resources/categories/assign",
                json={"resource_ids": [999999], "category_ids": [category_a["id"]]},
            )
            assert missing_resource.status_code == 404
            empty_categories = await client.post(
                "/api/resources/categories/assign",
                json={"resource_ids": [resource_2["id"]], "category_ids": []},
            )
            assert empty_categories.status_code == 422
            invalid_update = await client.put(
                f"/api/resources/{resource_2['id']}",
                json=resource_payload(
                    (await client.get(f"/api/resources/{resource_2['id']}")).json(),
                    category_ids=[999999],
                ),
            )
            assert invalid_update.status_code == 400

            app.dependency_overrides[get_required_user] = provide_reader
            app.dependency_overrides.pop(get_admin_user, None)
            forbidden_assign = await client.post(
                "/api/resources/categories/assign",
                json={"resource_ids": [resource_2["id"]], "category_ids": [category_a["id"]]},
            )
            assert forbidden_assign.status_code == 403
            readable_detail = await client.get(f"/api/resources/{resource_2['id']}")
            assert readable_detail.status_code == 200, readable_detail.text
            assert len(readable_detail.json()["categories"]) == 2

            app.dependency_overrides[get_admin_user] = provide_admin
            app.dependency_overrides[get_required_user] = provide_admin

            blocked_delete = await client.delete(f"/api/categories/{category_a['id']}")
            assert blocked_delete.status_code == 409

            cleared_resource_2 = await client.put(
                f"/api/resources/{resource_2['id']}",
                json=resource_payload(readable_detail.json(), category_ids=[]),
            )
            assert cleared_resource_2.status_code == 200, cleared_resource_2.text
            assert (await client.delete(f"/api/categories/{category_a['id']}")).status_code == 200

            assert (await client.delete(f"/api/resources/{resource_3['id']}")).status_code == 200
            assert (await client.delete(f"/api/categories/{category_b['id']}")).status_code == 200
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
