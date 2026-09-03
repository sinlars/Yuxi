---
name: knowledge-base
slug: knowledge-base
description: "使用 Yuxi 知识库进行检索、打开文档、文档内定位和查看思维导图。当用户需要基于已配置知识库回答问题、核验资料或引用文档内容时使用此技能。"
---

# 知识库技能

当当前会话已经配置知识库，且用户提出事实性问题时，优先使用此技能检索内部资料。只有知识库没有相关内容、
证据不足，或问题明确需要最新公开信息时，再使用网络搜索补充。

## 可用工具

- `list_kbs`：列出当前会话可访问且已启用的知识库。
- `query_kb`：按 `kb_id` 在指定知识库中检索内容，返回 `file_id` 和相关片段。
- `open_kb_document`：按 `kb_id` 和 `file_id` 打开文档原文窗口，适合查看更完整上下文。
- `find_kb_document`：在已知文档内用关键词或正则定位段落。
- `get_mindmap`：查看知识库思维导图结构。
- `search_file`：按文件名关键词搜索知识库中的文件，支持指定知识库或跨知识库，返回文件列表与分页信息。
- `download_kb_file`：按 `kb_id` 和 `file_id` 下载知识库原始二进制（pdf/docx/xlsx 等）到沙盒 `outputs` 目录，返回沙盒内可见的 `virtual_path`。当后续需要用代码读取原始文件结构（如 `openpyxl` 读 xlsx 单元格、`pdfplumber` 重新解析版面）时使用；`query_kb`/`open_kb_document` 只返回文本切片，无法满足需要文件对象的场景。

## 操作流程

1. 需要先确认当前会话有哪些知识库可用；不确定时调用 `list_kbs`。
2. 针对用户问题选择最相关的知识库，使用 `query_kb` 检索。
3. 如果检索片段不足以回答，使用返回的 `file_id` 调用 `open_kb_document` 查看上下文。
4. 如果用户要求定位术语、指标、章节或原文证据，使用 `find_kb_document` 在候选文档内查找。
5. 当用户关心知识库结构、文件分类或知识框架时，使用 `get_mindmap`。

## 关键约束

- 只能访问当前会话配置和用户权限允许的知识库。
- 不要编造 `kb_id` 或 `file_id`；优先从 `list_kbs` 和 `query_kb` 的返回结果中获取。
- 使用知识库内容形成回答时，必须把工具结果中的 `citation_source` 原样写为
  `<cite source="citation_source"></cite>`，并紧跟在它支持的句子或段落后；不要自行填写编号。
- Dify 等外部只读知识库可能只支持检索，不一定支持打开全文或文档内查找；遇到工具返回限制说明时，应如实告知用户。
