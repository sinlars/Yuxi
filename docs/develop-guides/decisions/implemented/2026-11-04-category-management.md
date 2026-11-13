# 分类管理树与分类类型切换

状态：implemented
类型：feature
Owner：backend/server/routers/category_router.py

## 问题

平台需要按 ICD10、中图分类等体系维护层级化的分类字典，作为后续资源标注与检索的基础。此前没有分类的类型命名空间、父子关系与启停状态，无法在同一界面切换体系，也无法可靠判断节点层级与叶子位置。

## 决策

建立 `category_types` 与 `category_nodes` 两张 PostgreSQL 表，以类型编码作为树命名空间，节点只通过 `parent_id` 表达父子关系。

### 实现方案

- **持久化**：`category_nodes.category_type` 以 `category_types.code` 为外键（`ON DELETE RESTRICT`），`parent_id` 自引用；`code` 在同类型内以部分唯一索引约束且允许为空；节点保存 `name`、`remark`、`enabled`，并由服务层维护 `level` 与 `is_leaf`。schema 由 `PostgresManager.ensure_category_schema()` 幂等建表、播种内置类型 `icd10`（ICD10分类）与 `clc`（中图分类），记录 business 版本 12；`storage_migration` 在版本校验后无条件执行该步骤。
- **职责分层**：`CategoryRepository` 负责类型与节点查询、编码占用判断；`category_service` 负责类型存在性、同类型父节点、编码唯一、成环阻断和整棵类型的层级/叶子重算；`category_router` 只做鉴权与响应组装。
- **树不变量**：每次写入后按同类型全部节点的 `parent_id` 重新计算 `level`（根节点为 1）与 `is_leaf`，因此层级和叶子状态不依赖客户端提交，也不能与父子关系漂移。
- **删除与移动**：存在子分类时删除返回 409；把节点移动到自身或其后代之下返回 400。分类类型在节点创建后不可更改，跨类型父节点被拒绝。
- **权限**：类型字典与树读取对已登录用户开放；节点增删改要求管理员。前端按管理员身份隐藏写操作，最终授权在后端依赖处执行。
- **界面结构**：侧边菜单新增「分类管理」，路由 `/categories`。页面顶部以分类类型下拉切换体系，表格以树形行展示节点，列包含名称、ID、编码、节点类型、层级、状态、备注与更新时间；管理员行操作统一收敛为「...」下拉菜单（新增子分类、编辑、启用/停用、删除）。新增与编辑使用弹窗表单，父分类通过树选择器指定，清空即为根节点。

## 替代方案

- **把整棵树存进单个 JSONB 字段**：读写简单，但无法用外键约束父节点与类型，编码唯一和部分更新都失去数据库保证。
- **固定分类类型枚举**：实现更简单，但新增体系必须改代码与迁移；选择字典表，当前只播种两个内置类型并保持只读。
- **提交时由前端传入 level / is_leaf**：减少一次重算，但客户端可伪造结构；选择服务端按 `parent_id` 推导。
- **级联删除子树**：一次操作更省事，但误删不可恢复且隐藏影响范围；选择存在子节点时显式拒绝。
- **只允许根节点或只允许叶子节点挂资源**：当前不引入该约束，资源标引可在任意分类节点上建立，详见[资源分类标引](2026-11-04-resource-category-tagging.md)。

## 后果

- business schema 升至 12（后续资源分类标引步骤推进到 13）；升级必须先运行 `storage-migrator`，否则 API 因缺表无法启动。
- 分类类型当前只读；新增体系需要追加迁移播种。
- 停用节点只表达自身状态，不禁用其子节点；使用方需自行按 `enabled` 过滤。
- 移动节点会重算该类型的全部节点层级，单类型节点量较大时写放大，但分类字典规模有限。
- 删除只能从叶子开始逐级进行，且被资源标引的分类同样返回 409；前端需展示错误并提示先解除引用。

## 验证

- `docker compose run --rm storage-migrator` 后 `yuxi_schema_migrations` 返回 `business=12`，`category_types` 存在 `icd10` 与 `clc`。
- `docker compose exec api uv run --no-sync --group test pytest test/integration/api/test_category_router.py -q` 覆盖类型切换、根/子/孙层级、树嵌套、叶子标记重算、跨类型父节点 400、成环 400、编码重复 409、分支删除 409、普通用户写 403 与删除后 404。
- `docker compose exec api uv run --no-sync --group test pytest test/unit/services/test_storage_migration.py -q` 通过。
- 新增与修改的前端文件通过 `eslint`，`pnpm run build` 通过。
- `python scripts/verify_engineering_contracts.py` 通过。
