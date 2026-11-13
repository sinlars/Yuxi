# 资源分类标引

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/resource_service.py

## 问题

分类树（见[分类管理树与分类类型切换](2026-11-04-category-management.md)）建立后，资源与分类之间没有持久关联，无法按分类体系组织知识资产。列表页需要一次为多条资源追加同一批分类，单条资源的查看与编辑也需要展示现有分类并调整标引。缺少关联表时，任何“资源属于哪些分类”的判断都只能停留在前端临时状态。

## 决策

建立 `resource_categories` 多对多关联表，把标引关系作为资源与分类之间的唯一事实；列表页批量操作采用幂等追加，单资源编辑采用整体覆盖。

### 实现方案

- **持久化**：`resource_categories(resource_id, category_id)` 以复合主键保证同一组合只有一条记录，`created_by` 与 `created_at` 记录标引人。`resource_id` 外键 `ON DELETE CASCADE`，删除资源时标引随之清除；`category_id` 外键 `ON DELETE RESTRICT`，分类仍被标引时不能直接删除。按 `category_id` 建索引支撑占用统计。schema 由 `PostgresManager.ensure_resource_category_schema()` 幂等建表并记录 business 版本 13；`storage_migration` 按版本条件执行资源、分类、标引三个步骤，已完成的步骤不再重放，避免把当前版本回写成旧版本号。
- **职责分层**：`ResourceRepository` 负责关联写入、按资源回读分类和按 ID 批量读取分类；`resource_service` 负责分类存在性校验、批量追加与整体覆盖；`resource_router` 只做鉴权与请求模型校验。`CategoryRepository.count_resource_links()` 为分类删除提供占用事实。
- **批量标引**：`POST /api/resources/categories/assign` 接收 `resource_ids` 与 `category_ids`，使用 `INSERT ... ON CONFLICT DO NOTHING` 追加，已存在的组合跳过，响应返回实际新增条数。资源或分类缺失分别返回 404 与 400。
- **单资源标引**：资源详情响应附带 `categories`（分类节点字典列表）。`PUT /api/resources/{id}` 的 `category_ids` 缺省表示保留现有标引，空数组表示清空，非空数组表示整体替换；创建资源时可直接携带 `category_ids`。
- **删除约束**：分类存在子节点或仍被资源标引时返回 409，需先解除标引或删除子节点。
- **权限**：标引写入要求管理员；资源详情与分类读取对已登录用户开放。前端按管理员身份隐藏批量标引入口和编辑态分类选择器，最终授权在后端依赖处执行。
- **界面结构**：资源列表工具栏新增「分类标引」按钮，要求先勾选行；弹窗内以跨分类类型的多选树选择一个或多个分类。资源查看与编辑页的「分类与封面」区新增「分类标引」字段：只读模式以标签展示「类型 / 分类路径」，编辑模式使用同一棵多选树。

## 替代方案

- **在 `resources` 上存分类 ID 数组**：少一张表，但无法用外键约束分类存在性与删除占用，标引人审计也只能塞进 JSON；选择关联表。
- **批量标引改为整体覆盖**：语义更统一，但列表页多选操作会静默清掉未展示的既有分类；选择追加，覆盖只发生在单资源编辑。
- **分类删除级联清除标引**：操作更省事，但会静默改变资源分类结果；选择返回 409 要求显式解除。
- **限制只允许叶子节点被标引**：更利于聚合统计，但当前分类树允许任意节点承载语义，先不引入额外约束。
- **前端提交分类路径字符串**：省一次查询，但路径随节点重命名漂移；选择提交分类 ID，展示时按当前树计算路径。

## 后果

- business schema 升至 13；升级必须先运行 `storage-migrator`，否则 API readiness 因版本不匹配失败。
- 批量标引是追加语义，无法通过该接口移除标引；移除需要在单资源编辑中提交完整分类集合。
- 分类删除被资源占用时需先逐个解除标引，没有批量解除入口。
- 列表响应不包含分类，分类信息只在详情接口返回，列表页不展示分类列。

## 验证

- `docker compose run --rm storage-migrator` 后 `yuxi_schema_migrations` 返回 `business=13`，`resource_categories` 存在且列为 `resource_id`、`category_id`、`created_by`、`created_at`。
- `docker compose exec api uv run --no-sync --group test pytest test/integration/api/test_resource_category_router.py -q` 覆盖批量追加新增 4 条、重复追加 0 条、详情回读分类、PUT 覆盖/省略保留/空数组清空、创建时携带分类、非法分类 400、缺失资源 404、空分类 422、非法更新 400、普通用户标引 403 但可读详情、分类被标引时删除 409、清空后可删、删除资源后分类可删。
- `docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_schema_migration_version.py -q` 在隔离 Schema 上重放 `ensure_resource_category_schema()` 两次，断言列集合、两条外键的 CASCADE/RESTRICT 与 business 版本为 13。
- 新增与修改的后端文件通过 `ruff check` 与 `ruff format --check`。
- 新增与修改的前端文件通过 `eslint`，`pnpm run build` 通过。
