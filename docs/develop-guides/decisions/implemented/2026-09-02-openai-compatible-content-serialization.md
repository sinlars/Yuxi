# OpenAI 兼容提供商的消息 content 序列化归一化

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/models.py

## 问题

使用 yuanzhi-m1 等 OpenAI 兼容提供商问答时，模型调用经 ModelRetryMiddleware 重试 3 次后报 `Model call failed after 3 attempts`，根因是端点返回 500 错误体（`{"code":500,"message":"【内容】 Not a valid string.","data":null}`），被 OpenAI 客户端宽松解析成 `choices=null`，langchain 的 `_create_chat_result` 据此抛 `TypeError: Received response with null value for 'choices'`。

真正根因有两点，都发生在请求消息序列化阶段：deepagents 的 `append_to_system_message` 用 `content_blocks` 拼出的 system 消息会被 langchain-openai 序列化成 `[{"type":"text","text":...}]` 列表而非字符串，端点只接受字符串 content，返回「Not a valid string.」；带 `tool_calls` 的 assistant 消息空 content 被序列化为 `null`，端点返回「该字段不能为 null」。

## 决策

在 `_ToolCallChunkFixChatOpenAI._get_request_payload`（流式与非流式请求唯一的序列化点）中统一归一化消息 content：把纯文本 content 块列表合并为单个字符串（`_collapse_text_content_blocks`），并补齐带 `tool_calls`/`function_call` 的 assistant 消息的空 content 占位符 `(tool call)`。含图片等非文本块的列表保持原样，不影响多模态提供商。

## 替代方案

不修改全局序列化，而是在每条请求前手动把 system 消息改写为字符串 content。这需要渗透到所有拼装 system 消息的中间件，改动面更大且容易遗漏；全局在 `_get_request_payload` 拦截，一处收敛所有提供商与所有中间件的序列化差异。

## 后果

所有 OpenAI 兼容提供商（不仅 yuanzhi-m1）的请求在序列化前都会做一次幂等的 content 归一化：纯文本块列表变字符串对接受字符串的官方端点语义等价，多模态块列表不受影响。同一 `_ToolCallChunkFixChatOpenAI` 还包含流式零 chunk 回退非流式（应对 v2 流式协议下零 chunk 抛 RuntimeError 的另一类“Model call failed after 3 attempts”根因）与流式 tool_call 空串 name/id 归一化，三者同属对 yuanzhi-m1 问答失败的修复。

## 验证

- `backend/test/unit/agents/test_streaming_v2_compat.py::test_get_request_payload_collapses_system_content_blocks` 验证 `SystemMessage(content_blocks=[...text...])` 序列化为字符串而非列表；回退为不合并列表时该测试失败。
- `test_collapse_text_content_blocks_merges_pure_text_blocks` 与 `test_collapse_text_content_blocks_preserves_non_text_blocks` 分别验证纯文本块合并与多模态块保持原样。
- 真实链路：走完整 `ChatbotAgent.stream_messages_with_state` 流程调用 yuanzhi-m1，模型正常返回自我介绍，不再出现 `Model call failed after 3 attempts` 或 `stream produced no chunks` 警告。
