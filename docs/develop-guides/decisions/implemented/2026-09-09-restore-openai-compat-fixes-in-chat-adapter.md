# 恢复 ChatCompletionsAdapter 的 OpenAI 兼容提供商修复

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/models/chat.py

## 问题

使用 yuanzhi-m1 问答时报 `Model call failed after 3 attempts with RuntimeError: v2 stream finished without producing a message`。提交 44e06198（fix(reasoning): 统一推理内容适配与流式展示）删除 `backend/package/yuxi/agents/models.py` 并把模型适配收拢到 `ChatCompletionsAdapter` 时，只迁移了 tool_call 续片空串归一化与推理内容保留，丢失了此前沉淀的四类 OpenAI 兼容修复：

1. 流式零 chunk 回退非流式生成（本次报错的直接根因：v2 流式桥接零产出抛 RuntimeError，重试 3 次后失败）。
2. 纯文本 content 块列表合并为字符串（端点拒绝列表时报 500「【内容】 Not a valid string.」，表现为 choices=null 或零 chunk）。
3. 带 tool_calls 的 assistant 消息空 content 占位符（端点拒绝 null 时报 500「该字段不能为 null」）。
4. 文本工具调用解析 `_StreamingTextToolCallParser`（不支持原生 function calling 的端点上工具与技能全部失效）。

回归测试 `test_streaming_v2_compat.py` 与 `test_text_tool_call_parsing.py` 仍导入已删除的 `yuxi.agents.models`，模块导入失败使这两份防线静默失效，44e06198 的验证（后端 1925 通过）未覆盖到该回归。

## 决策

在 `ChatCompletionsAdapter`（backend/package/yuxi/models/chat.py）中恢复上述四类修复，并与 44e06198 引入的推理适配融合：

- `_get_request_payload` 依次执行通用 content 归一化（`_collapse_text_content_blocks` 合并纯文本块列表、补齐空 content 占位符）与推理续答回填；原推理分支内的列表合并由通用归一化覆盖，随之移除。
- `_astream`/`_stream` 在流包装层做文本工具调用解析与零 chunk 回退；tool_call 归一化与推理标准化保持在 `_convert_chunk_to_generation_chunk`。
- `_generate`/`_agenerate` 在非流式结果上做文本工具调用提取。
- 文本解析兼容两种 content 形态：纯字符串，以及 `_standardize_content` 产出的块列表（`_aggregate_text_from_blocks` 只聚合 text 块，reasoning 等非文本块经 `_rebuild_message_content` 保留）。

两个回归测试的导入源改到 `yuxi.models.chat`，类名 `_ToolCallChunkFixChatOpenAI` 改为 `ChatCompletionsAdapter`。另将 `test/unit/agents/buildin/test_chatbot_prompt.py` 重命名为 `test_buildin_chatbot_prompt.py`，解除其与 `test/unit/agents/test_chatbot_prompt.py` 的同名模块导入冲突（该冲突阻塞 `pytest test/unit` 全量收集，与本次修复无关但先于其存在）。

## 替代方案

不迁移旧实现，改为要求 yuanzhi-m1 网关侧修复流式 SSE 与原生 function calling 支持。网关不受本仓库控制且其他 OpenAI 兼容提供商（siliconflow、百炼等）存在同类协议偏差，客户端兼容层仍是当前唯一可闭合修复的位置。

把旧 `agents/models.py` 原样恢复为独立文件会与 `ChatCompletionsAdapter` 形成两套并行适配（同一请求两个拦截点），叠加推理适配后行为难以推理；在现有类内收敛保持唯一序列化点与单一 Owner。

## 后果

所有 OpenAI 兼容提供商的请求在序列化前做幂等 content 归一化，流式零产出时回退非流式并记录 warning，绑定工具时对文本工具调用做解析；对支持原生协议的端点这些变换均为直通。文件级兼容逻辑集中在 `models/chat.py`，不再存在 `yuxi.agents.models` 模块。全量 unit 收集恢复可用（同名测试冲突解除）。

## 验证

- `backend/test/unit/agents/test_streaming_v2_compat.py`（12 项）与 `test_text_tool_call_parsing.py`（10 项）、`test_streaming_toolcall_fix.py`（3 项）全部通过：零 chunk 回退、v2 协议下不抛 RuntimeError、system 纯文本块序列化为字符串、空 content 占位符、文本工具调用在 LangGraph v3 事件流下被还原执行、未绑定工具时直通。
- `pytest test/unit -m "not slow"` 全量通过（同名测试冲突解除后可完整收集）。
- `ruff check package/yuxi/models/chat.py` 与 `ruff format package/yuxi/models/chat.py --check` 通过。
- 真实链路（yuanzhi-m1 问答）待 api 容器重启后在运行环境中复核，本决策记录提交时尚未执行付费模型调用。
