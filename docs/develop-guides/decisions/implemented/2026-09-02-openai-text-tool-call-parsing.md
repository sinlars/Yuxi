# OpenAI 兼容提供商的文本工具调用解析

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/models.py

## 问题

yuanzhi-m1 问答可以正常出文本回复后，用户发现技能与工具全部失效：模型把"调用意图"当正文念出来（"让我先调用 list_kbs 工具查看可用的知识库"），随后以纯文本输出 `<tool_call><function=list_kbs>...</function></tool_call>`，工具从未执行。

根因：该端点不支持原生 function calling。请求里 `tools` 正常下发，模型却把工具调用以 Qwen 风格 XML 文本写在 `content` 里返回，并连带 `</think>` 推理结束标记与空 schema 渲染出的 `<parameter=dummy>` 模板残留一起透传。langchain 拿到的 `tool_calls` 恒为空，LangGraph 的工具路由永不触发。

## 决策

在 `_ToolCallChunkFixChatOpenAI`（所有 OpenAI 兼容提供商的统一入口）增加文本工具调用解析：仅当请求绑定了工具（`kwargs["tools"]` 非空，来自 `bind_tools`）时启用，流式路径（`_astream`/`_stream`）经 `_StreamingTextToolCallParser` 增量解析并转换为 `tool_call_chunks`，非流式路径（`_generate`/`_agenerate`）经 `_extract_text_tool_calls_from_message` 整体后处理。解析器特性：

- 支持标记跨 chunk 拆分（普通态最多扣留 `len(标记)-1` 个字符，流结束 `flush` 归还，不吞内容）；
- 支持 `<function=name><parameter=k>v</parameter></function>` XML 形态与 Qwen 标准 JSON 形态，多个 `<tool_call>` 块解析为并行调用；
- 空字符串参数值（模板残留的 `<parameter=dummy>`）被丢弃，其余参数值按 JSON 还原类型；
- 剥除 `</think>` 及其之前的透传推理文本（流式下推理文本在标记到达前已流出、无法回收，属已知限制；非流式可完整剥除）；
- 块解析失败时按原文吐回，不吞内容；未绑定工具时完全直通。

## 替代方案

在端点/网关侧启用原生 function calling。该端点为外部服务，不受本仓库控制，只能由客户端兼容。另一替代是在 deepagents 中间件层解析文本工具调用，但中间件拿到的已是聚合后的消息，无法覆盖流式增量语义，且会与 ModelRetryMiddleware 的重试路径交叉；在模型客户端层（`_ToolCallChunkFixChatOpenAI`）收敛对流式与非流式都成立。

## 后果

对支持原生 function calling 的端点无影响：其 content 中不会出现这些标记，解析器直通；未绑定工具的纯文本对话也完全不受影响。风险面是"绑定了工具且模型正文恰好包含字面 `<tool_call>` 标签"的场景会被误解析——该场景在真实对话中概率极低，且解析失败（块内无 `<function>` 也非合法 JSON）时按原文吐回，不吞内容。

与既有四类修复（tool_call 续片空串 name/id 归一化、content 序列化归一化、空 content 占位符、流式零 chunk 回退非流式）同属对 yuanzhi-m1 问答链路的兼容层。

## 验证

- `backend/test/unit/agents/test_text_tool_call_parsing.py::test_text_tool_call_executes_under_langgraph`：负向回归（核心缺陷）——fake 模型按 5 字符一段流式返回数据库捕获的真实原文（含 `</think>` 与 `<tool_call>` 文本），断言 tool_calls 被还原为 `list_kbs`、工具真正执行（修复前从不执行）、content 不残留标记、第二轮模型调用产出最终回答。回退为不解析时该测试失败。
- `test_text_passthrough_without_tools_bound`：未绑定工具时 content 原样透传。
- 其余 8 个用例覆盖跨 chunk 拆分、dummy 参数丢弃、JSON 形态、并行调用、解析失败保原文、未闭合块 flush 归还、非流式后处理。
- 真实链路：完整 `ChatbotAgent.stream_messages_with_state` 流程调用 yuanzhi-m1 提问"临床输血技术规范"，验证工具调用被还原执行、最终回答来自知识库检索结果。
