"""通过真实 HTTP 与 PostgreSQL 验证资源管理接口与权限边界。"""

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


async def test_resource_management_http_round_trip_and_permissions():
    """管理员可维护类型与资源，普通用户只读，且非法输入被拒绝。"""
    schema = f"pytest_resources_{uuid.uuid4().hex[:16]}"
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

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_admin_user] = provide_admin
    app.dependency_overrides[get_required_user] = provide_admin
    path = "/api/resources"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            types = await client.get(f"{path}/types")
            assert types.status_code == 200, types.text
            codes = {item["code"] for item in types.json()["items"]}
            assert codes == {"case", "chart", "guideline", "video", "question", "book"}

            created = await client.post(
                path,
                json={
                    "resource_type": "case",
                    "title": "测试案例",
                    "subtitle": "副标题",
                    "summary": "摘要",
                    "author": "张三",
                    "institution": "测试机构",
                    "keywords": ["关键词", "关键词", " "],
                    "subject_terms": ["主题词"],
                    "tags": ["标签"],
                    "content_markdown": "# 正文",
                    "attachments": [{"name": "说明.pdf", "url": "/minio/public/demo.pdf", "size": 12}],
                },
            )
            assert created.status_code == 200, created.text
            resource_id = created.json()["id"]
            assert created.json()["keywords"] == ["关键词"]
            assert created.json()["status"] == "draft"

            listing = await client.get(path, params={"resource_type": "case", "keyword": "测试案例"})
            assert listing.status_code == 200, listing.text
            assert listing.json()["total"] == 1
            assert "content_markdown" not in listing.json()["items"][0]

            app.dependency_overrides[get_required_user] = provide_reader
            app.dependency_overrides.pop(get_admin_user, None)
            forbidden = await client.post(path, json={"resource_type": "case", "title": "普通用户资源"})
            assert forbidden.status_code == 403
            forbidden_update = await client.put(
                f"{path}/{resource_id}", json={"resource_type": "case", "title": "普通用户改标题"}
            )
            assert forbidden_update.status_code == 403
            forbidden_delete = await client.delete(f"{path}/{resource_id}")
            assert forbidden_delete.status_code == 403
            forbidden_publish = await client.post(f"{path}/publish", json={"ids": [resource_id]})
            assert forbidden_publish.status_code == 403
            forbidden_type = await client.post(
                f"{path}/types", json={"code": "reader", "name": "普通用户类型", "sort_order": 99}
            )
            assert forbidden_type.status_code == 403
            forbidden_upload = await client.post(
                f"{path}/uploads",
                files={"file": ("note.txt", b"resource attachment", "text/plain")},
                data={"kind": "attachment"},
            )
            assert forbidden_upload.status_code == 403

            readable = await client.get(f"{path}/{resource_id}")
            assert readable.status_code == 200, readable.text
            assert readable.json()["content_markdown"] == "# 正文"

            app.dependency_overrides[get_admin_user] = provide_admin
            app.dependency_overrides[get_required_user] = provide_admin
            published = await client.post(f"{path}/publish", json={"ids": [resource_id]})
            assert published.status_code == 200, published.text
            assert published.json()["published"] == 1
            assert (await client.get(f"{path}/{resource_id}")).json()["status"] == "published"

            updated = await client.put(f"{path}/{resource_id}", json={"resource_type": "case", "title": "更新标题"})
            assert updated.status_code == 200, updated.text
            assert updated.json()["title"] == "更新标题"
            assert updated.json()["status"] == "draft"

            second = await client.post(path, json={"resource_type": "case", "title": "批量发布资源"})
            assert second.status_code == 200, second.text
            batch = await client.post(f"{path}/publish", json={"ids": [resource_id, second.json()["id"]]})
            assert batch.status_code == 200, batch.text
            assert batch.json()["published"] == 2
            statuses = {
                item["id"]: item["status"]
                for item in (await client.get(path, params={"page_size": 100})).json()["items"]
            }
            assert statuses[resource_id] == "published"
            assert statuses[second.json()["id"]] == "published"
            assert (await client.delete(f"{path}/{second.json()['id']}")).status_code == 200

            invalid_type = await client.post(path, json={"resource_type": "unknown", "title": "非法类型"})
            assert invalid_type.status_code == 400

            missing_title = await client.post(path, json={"resource_type": "case", "title": " "})
            assert missing_title.status_code == 400

            custom = await client.post(f"{path}/types", json={"code": "custom", "name": "自定义类型", "sort_order": 99})
            assert custom.status_code == 200, custom.text
            builtin_delete = await client.delete(f"{path}/types/case")
            assert builtin_delete.status_code == 400
            custom_delete = await client.delete(f"{path}/types/custom")
            assert custom_delete.status_code == 200

            uploaded = await client.post(
                f"{path}/uploads",
                files={"file": ("note.txt", b"resource attachment", "text/plain")},
                data={"kind": "attachment"},
            )
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["name"] == "note.txt"
            assert uploaded.json()["url"].startswith("/minio/")

            rejected_upload = await client.post(
                f"{path}/uploads",
                files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
                data={"kind": "attachment"},
            )
            assert rejected_upload.status_code == 400

            deleted = await client.delete(f"{path}/{resource_id}")
            assert deleted.status_code == 200
            assert (await client.get(f"{path}/{resource_id}")).status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
