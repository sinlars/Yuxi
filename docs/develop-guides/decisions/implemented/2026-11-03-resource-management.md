# 资源管理统一条目与类型字典

状态：implemented
类型：feature
Owner：backend/server/routers/resource_router.py

## 问题

平台需要统一管理案例、图表、指南共识、视频、试题与图书六类知识资产。此前这些资源没有统一的持久化模型、类型约束与访问边界，导致同类元数据分散、类型判断依赖客户端约定，且正文与附件没有稳定的存储位置。

## 决策

建立 `resource_types` 与 `resources` 两张 PostgreSQL 表，把类型作为可配置字典，把资源条目作为统一事实。

### 实现方案

- **持久化**：`resources.resource_type` 以 `resource_types.code` 为外键（`ON DELETE RESTRICT`），正文存 Markdown 原文，关键词/主题词/标签/附件存 JSONB 数组；列表响应剔除正文，详情返回全文。schema 由 `PostgresManager.ensure_resource_schema()` 幂等建表、播种六个内置类型（`case`、`chart`、`guideline`、`video`、`question`、`book`）并记录 business 版本 11；`storage_migration` 在兼容版本校验后执行该步骤，已完成该版本的库不再重放。
- **职责分层**：`ResourceRepository` 只做查询与写入；`resource_service` 负责字段规整、类型存在性、内置类型与占用类型的删除拒绝；`resource_router` 只做鉴权、上传前置校验与响应组装。
- **权限**：列表、详情、类型读取对已登录用户开放；类型维护、资源增删改与文件上传要求管理员。前端只按管理员身份隐藏入口，最终授权在依赖处执行。
- **富文本**：复用仓库现有 markdown-it/DOMPurify/Shiki 渲染链，管理页提供 Markdown 编辑与实时预览，不引入 WYSIWYG 依赖。
- **文件**：封面与附件经专用上传接口写入 MinIO `public` 桶的 `resources/` 前缀，返回可访问 URL 后作为资源字段持久化；封面限图片且不超过 10 MB，附件按扩展名白名单校验且不超过 100 MB。
- **界面结构**：列表页在表格上方以一行工具栏承载类型下拉筛选、搜索、新建、类型管理与批量发布；行操作统一收敛为「...」下拉菜单（查看/编辑/发布/删除），并展示创建人与更新人；新建、编辑、查看分别走 `/resources/create`、`/resources/:id/edit`、`/resources/:id` 独立路由页面，表单在页面内展开，不再使用抽屉。
- **状态与发布**：资源带 `status`（`draft` 编辑中 / `published` 已发布），新建与任意保存都回到 `draft`；管理员可对单条或复选框选中的多条执行发布，发布只改状态、不改资源内容。列表类型筛选改为列表上方的下拉框。

## 替代方案

- **WYSIWYG 编辑器（Tiptap 等）**：编辑体验更好，但需要新依赖与独立的 HTML 清洗策略，超出统一管理的当前需求。
- **固定六种类型枚举**：实现更简单，但类型无法按业务扩展；选择可配置字典，保留内置类型不可删除和占用类型不可删除两条约束。
- **附件存聊天临时目录或本地卷**：复用成本低，但临时目录会过期、本地卷增加部署搬运；选择对象存储。
- **所有登录用户共享写权限**：协作更方便，但资源是全局知识资产，缺少归属与审计约束；选择管理员写、全员读。

## 后果

- 资源管理新增两条持久化表与一个 business schema 版本；升级必须先运行 `storage-migrator`，否则 API 启动会因缺列/缺表失败。
- 类型编码创建后不可修改；删除内置类型或仍被资源引用的类型返回 400/409，前端需展示错误。
- 附件 URL 指向公开桶，持有链接即可下载；资源可见性仍是登录用户级别，附件本身不做额外授权。
- 列表只返回摘要字段，正文与附件详情按需通过详情接口获取。

## 验证

- `docker compose up storage-migrator` 后 `SELECT domain, version FROM yuxi_schema_migrations` 返回 `business>=11`，六个内置类型存在。
- `docker compose exec api uv run --no-sync pytest test/integration/api/test_resource_router.py -q` 覆盖管理员读写、普通用户只读 403、类型不存在/标题为空 400、内置类型删除 400、删除后详情 404。
- `docker compose exec web pnpm run build` 通过；新增前端文件通过 `eslint`。
- `python scripts/verify_engineering_contracts.py` 通过。
