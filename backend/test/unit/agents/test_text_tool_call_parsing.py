"""回归测试：OpenAI 兼容端点的文本工具调用解析。

背景：部分 OpenAI 兼容提供商（如 yuanzhi-m1）不支持原生 function calling：
请求里正常下发 ``tools``，模型却把工具调用以 Qwen 风格 XML 文本写在 content
里返回（``<tool_call><function=name><parameter=k>v</parameter></function>
</tool_call>``），并连带 ``</think>`` 推理结束标记与空 schema 渲染出的
``<parameter=dummy>`` 模板残留。langchain 拿到的 tool_calls 恒为空，智能体
的技能与工具全部失效（模型只能把"调用意图"当正文念出来）。

修复：`ChatCompletionsAdapter` 在请求绑定工具时，把流式 chunk 与非流式
结果里的文本工具调用还原为原生 tool_calls（`_StreamingTextToolCallParser`），
并剥掉透传的推理文本；未绑定工具时完全直通。

本测试用 fake 模型确定性复现（无需网络/API key）。
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from yuxi.models.chat import (
    _StreamingTextToolCallParser,
    ChatCompletionsAdapter,
    _extract_text_tool_calls_from_message,
)

# 数据库中捕获的 yuanzhi-m1 真实返回原文（推理 + </think> + 文本工具调用）
_RAW_TOOL_CALL_TEXT = (
    "用户希望我基于知识库回答问题，先列出可用的知识库。\n"
    "</think>\n\n"
    "<tool_call>\n<function=list_kbs>\n</function>\n</tool_call>"
)


def _message_text(message) -> str:
    """提取消息正文文本，兼容 content blocks 形态。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


@tool
def list_kbs():
    """列出可用的知识库。"""
    return [{"id": "kb_demo", "name": "临床输血知识库"}]


class _TextToolCallProviderMixin(ChatOpenAI):
    """模拟 yuanzhi-m1：把工具调用以文本形式写进 content 返回。

    第一次调用返回 `text_chunks`（含文本工具调用），后续调用返回
    `follow_up_content`（模拟拿到工具结果后的最终回答），避免智能体循环。
    流式与非流式两条路径都覆盖。
    """

    text_chunks: list = Field(default_factory=list)
    follow_up_content: str = "这是基于知识库的最终回答。"
    invocation_count: int = 0

    def _next_chunks(self) -> list[str]:
        self.invocation_count += 1
        if self.invocation_count == 1:
            return list(self.text_chunks)
        return [self.follow_up_content]

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        for text in self._next_chunks():
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        for text in self._next_chunks():
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="".join(self._next_chunks())))]
        )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="".join(self._next_chunks())))]
        )


class _FakeTextToolCallModel(ChatCompletionsAdapter, _TextToolCallProviderMixin):
    """模拟 yuanzhi-m1 的 fake 模型：wrapper 的修复逻辑走真实实现。"""


def _make_model(chunks: list[str]) -> _FakeTextToolCallModel:
    model = _FakeTextToolCallModel(model="fake-model", api_key="test")
    model.text_chunks = chunks
    return model


async def test_text_tool_call_executes_under_langgraph():
    """负向回归（核心缺陷）：修复前 tool_calls 恒为空、工具从不执行。

    v3 事件流（LangGraph stream_mode="messages" 同路径）下，文本工具调用
    应被还原为原生 tool_calls，工具真正执行，最终回答来自工具结果之后。
    """
    model = _make_model([_RAW_TOOL_CALL_TEXT[i:i + 5] for i in range(0, len(_RAW_TOOL_CALL_TEXT), 5)])
    agent = create_agent(model=model, tools=[list_kbs], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    run = await agent.astream_events(
        {"messages": [HumanMessage("基于知识库回答临床输血技术规范")]}, config=config, version="v3"
    )
    async for _ in run:
        pass

    state = await agent.aget_state(config)
    messages = state.values.get("messages", [])
    ai_msgs = [m for m in messages if m.type == "ai"]
    tool_msgs = [m for m in messages if m.type == "tool"]

    assert any(
        m.tool_calls and m.tool_calls[0]["name"] == "list_kbs" for m in ai_msgs
    ), "文本工具调用应还原为 tool_calls（修复前恒为空）"
    assert not any("<tool_call>" in _message_text(m) for m in ai_msgs), "content 不应残留 tool_call 文本"
    assert not any("</think>" in _message_text(m) for m in ai_msgs), "content 不应残留 think 标记"
    assert tool_msgs, "工具应真正执行（修复前从不执行）"
    assert any("kb_demo" in _message_text(m) for m in tool_msgs)
    # 第二轮模型调用应产出最终回答
    assert any("最终回答" in _message_text(m) for m in ai_msgs)
    assert model.invocation_count >= 2


async def test_text_passthrough_without_tools_bound():
    """未绑定工具时完全直通：content 原样保留，不做任何解析或改写。"""
    model = _make_model([_RAW_TOOL_CALL_TEXT])
    agent = create_agent(model=model, tools=[], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    run = await agent.astream_events(
        {"messages": [HumanMessage("你好")]}, config=config, version="v3"
    )
    async for _ in run:
        pass

    state = await agent.aget_state(config)
    ai_msgs = [m for m in state.values.get("messages", []) if m.type == "ai"]
    assert ai_msgs
    assert all(not m.tool_calls for m in ai_msgs)
    assert any(_message_text(m) == _RAW_TOOL_CALL_TEXT for m in ai_msgs), "无工具绑定时 content 应原样透传"


def test_parser_handles_chunk_splits():
    """标记跨 chunk 拆分（3 字符一段）时仍能正确解析出工具调用。"""
    parser = _StreamingTextToolCallParser()
    events = []
    for i in range(0, len(_RAW_TOOL_CALL_TEXT), 3):
        events.extend(parser.feed(_RAW_TOOL_CALL_TEXT[i:i + 3]))
    events.extend(parser.flush())

    calls = [payload for kind, payload in events if kind == "tool_call"]
    content = "".join(payload for kind, payload in events if kind == "content")
    assert [call["name"] for call in calls] == ["list_kbs"]
    assert calls[0]["args"] == {}
    assert calls[0]["id"].startswith("call_")
    assert "<tool_call>" not in content, "块文本不应泄入 content"
    assert "</think>" not in content, "think 标记应被剥除"


def test_parser_drops_empty_template_parameters():
    """空 schema 渲染出的 <parameter=dummy></parameter> 模板残留应被丢弃，其余参数保留并还原类型。"""
    parser = _StreamingTextToolCallParser()
    text = (
        "<tool_call>\n<function=search>\n<parameter=query>临床输血</parameter>\n"
        "<parameter=limit>5</parameter>\n<parameter=dummy>\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    events = parser.feed(text) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    assert len(calls) == 1
    assert calls[0]["name"] == "search"
    assert calls[0]["args"] == {"query": "临床输血", "limit": 5}


def test_parser_supports_qwen_json_format():
    """兼容 Qwen 标准 JSON 形态：<tool_call>{"name": ..., "arguments": {...}}</tool_call>。"""
    parser = _StreamingTextToolCallParser()
    text = '<tool_call>\n{"name": "search", "arguments": {"query": "输血"}}\n</tool_call>'
    events = parser.feed(text) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    assert calls == [] or len(calls) == 1
    if calls:
        assert calls[0]["name"] == "search"
        assert calls[0]["args"] == {"query": "输血"}


def test_parser_preserves_unparseable_block_as_content():
    """块内无 <function> 也非合法 JSON 时，按原文吐回，不吞内容。"""
    parser = _StreamingTextToolCallParser()
    text = "正文<tool_call>这不是工具调用</tool_call>结尾"
    events = parser.feed(text) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    content = "".join(payload for kind, payload in events if kind == "content")
    assert calls == []
    assert content == text, "无法解析的块应原样保留"


def test_parser_flushes_unterminated_block():
    """流意外截断（未闭合块）时，flush 把已收文本按原文归还，不丢字。"""
    parser = _StreamingTextToolCallParser()
    events = parser.feed("正文<tool_call>\n<function=sea") + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    content = "".join(payload for kind, payload in events if kind == "content")
    assert calls == []
    assert content == "正文<tool_call>\n<function=sea"


def test_parser_supports_parallel_tool_calls():
    """多个 <tool_call> 块应解析为多个工具调用，index 递增。"""
    parser = _StreamingTextToolCallParser()
    text = (
        "</think>\n\n"
        "<tool_call>\n<function=list_kbs>\n</function>\n</tool_call>\n"
        "<tool_call>\n<function=search>\n<parameter=query>输血</parameter>\n</function>\n</tool_call>"
    )
    events = parser.feed(text) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    assert [call["name"] for call in calls] == ["list_kbs", "search"]
    assert [call["index"] for call in calls] == [0, 1]
    assert calls[1]["args"] == {"query": "输血"}


def test_extract_text_tool_calls_from_message_non_streaming():
    """非流式结果后处理：tool_calls 还原、推理与标记剥除。"""
    message = AIMessage(content=_RAW_TOOL_CALL_TEXT)
    _extract_text_tool_calls_from_message(message)
    assert message.tool_calls and message.tool_calls[0]["name"] == "list_kbs"
    assert "<tool_call>" not in message.content
    assert "</think>" not in message.content
    assert "用户希望我" not in message.content, "推理文本应被剥除"


def test_extract_keeps_plain_message_untouched():
    """不含任何标记的消息完全不动。"""
    message = AIMessage(content="普通回答，没有任何标记")
    _extract_text_tool_calls_from_message(message)
    assert message.content == "普通回答，没有任何标记"
    assert message.tool_calls == []
