"""回归测试：langchain 流式协议 v1→v2 兼容（零 chunk 回退非流式生成）。

背景：langchain-core >= 1.6 起，LangGraph（stream_mode="messages" /
astream_events v3）挂载的 v2 流式回调会让模型 invoke 也改走 v2 协议事件流
（`_aiter_v2_events` 桥接 `achunks_to_events(self._astream(...))`）。
部分 OpenAI 兼容提供商（如 yuanzhi-m1）的流式 SSE 事件全部被
langchain-openai 丢弃（delta 为 null / type=content.delta），`_astream`
零产出，v2 累积器拿不到 message-start/finish，抛
RuntimeError("v2 stream finished without producing a message")，
经 ModelRetryMiddleware 重试后报 "Model call failed after 3 attempts"。

修复：`_ToolCallChunkFixChatOpenAI` 在流式零 chunk 时回退调用非流式
`_agenerate`，把完整回复包装成单个 chunk 产出（对齐 0.6.1 / v1 行为）。

本测试用 fake 模型确定性复现（无需网络/API key）。fake 通过一个插在
`_ToolCallChunkFixChatOpenAI` 与 `ChatOpenAI` 之间 MRO 的 mixin，让 wrapper
的 `super()._astream()` 命中“零 chunk”的实现，从而走到 wrapper 的回退逻辑。
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from yuxi.agents.models import (
    _EMPTY_ASSISTANT_CONTENT_PLACEHOLDER,
    _ToolCallChunkFixChatOpenAI,
    _collapse_text_content_blocks,
)


def _message_text(message) -> str:
    """提取消息正文文本，兼容 v2 协议的 content blocks 形态。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


class _ZeroChunkProviderMixin(ChatOpenAI):
    """模拟 yuanzhi-m1 提供商：流式接口零 chunk。

    真实链路里 `BaseChatOpenAI._astream` 收到的 SSE 事件 delta 全为 null，被
    `_convert_chunk_to_generation_chunk` 丢弃，`_astream` 零产出。这里直接让
    `_astream` 零产出等价复现。该 mixin 作为 `_ToolCallChunkFixChatOpenAI` 的
    第二基类，其 `_astream`/`_stream` 会被 wrapper 的 `super()` 命中。
    """

    async def _astream(self, *args, **kwargs):  # noqa: ARG002
        self.stream_calls += 1
        return
        yield  # pragma: no cover - 仅为把函数变成 async generator

    def _stream(self, *args, **kwargs):  # noqa: ARG002
        self.stream_calls += 1
        return
        yield  # pragma: no cover - 仅为把函数变成 generator


class _FakeZeroChunkModel(_ToolCallChunkFixChatOpenAI, _ZeroChunkProviderMixin):
    """模拟 yuanzhi-m1：流式零 chunk，非流式正常返回。

    `_astream`/`_stream` 由 `_ZeroChunkProviderMixin` 提供（零 chunk），
    非流式 `_agenerate`/`_generate` 返回完整回复，wrapper 的回退逻辑走真实实现。
    """

    non_stream_content: str = "这是来自非流式接口的回复"
    stream_calls: int = 0
    generate_calls: int = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.generate_calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.non_stream_content))])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.generate_calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.non_stream_content))])


async def test_astream_zero_chunks_falls_back_to_generate():
    """流式零 chunk 时回退非流式生成，至少产出一个含完整回复的 chunk。"""
    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    chunks = [chunk async for chunk in model.astream([HumanMessage("你好")])]

    assert model.stream_calls == 1, "应先尝试流式接口"
    assert model.generate_calls == 1, "流式零产出后应回退非流式接口"
    assert chunks, "回退后应至少产出一个 chunk"
    # 公共 astream 会再补一个 chunk_position="last" 的空 chunk，这里只取正文
    assert any(c.content == "这是来自非流式接口的回复" for c in chunks)


def test_stream_zero_chunks_falls_back_to_generate():
    """同步 stream() 同样回退非流式生成，避免未 await 协程。"""
    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    chunks = list(model.stream([HumanMessage("你好")]))

    assert model.stream_calls == 1
    assert model.generate_calls == 1
    assert any(c.content == "这是来自非流式接口的回复" for c in chunks)


async def test_v2_protocol_no_runtime_error_under_langgraph():
    """v2 协议路径（LangGraph astream_events v3 挂载 v2 回调）下零 chunk 回退
    修复生效，不再抛 RuntimeError('v2 stream finished without producing a message')。"""
    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    agent = create_agent(model=model, tools=[], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}
    graph_input = {"messages": [HumanMessage("你好")]}

    # astream_events(version="v3") 会让模型 invoke 改走 v2 事件流，此前会抛 RuntimeError
    run = await agent.astream_events(graph_input, config=config, version="v3")
    async for _ in run:
        pass

    # 最终落库的回复来自非流式回退接口
    state = await agent.aget_state(config)
    messages = state.values.get("messages", [])
    assert any(m.type == "ai" and "这是来自非流式接口的回复" in _message_text(m) for m in messages)


async def test_extract_generate_args_variants():
    """参数还原：位置参数与关键字参数两种调用形态都能正确解析。"""
    from yuxi.agents.models import _extract_generate_args

    # 位置形态: (messages, stop, run_manager)
    messages, stop, run_manager, kwargs = _extract_generate_args(
        (["m"], ["END"], "rm"), {"tools": []}
    )
    assert messages == ["m"]
    assert stop == ["END"]
    assert run_manager == "rm"
    assert kwargs == {"tools": []}

    # 关键字形态
    messages, stop, run_manager, kwargs = _extract_generate_args(
        (), {"messages": ["m"], "stop": None, "run_manager": "rm",
             "stream_usage": True, "stream": True, "extra_body": {"k": 1}}
    )
    assert messages == ["m"]
    assert stop is None
    assert run_manager == "rm"
    assert kwargs == {"extra_body": {"k": 1}}, "stream/stream_usage 应被剥除"


async def test_message_to_chunk_converts_tool_calls():
    """非流式消息中的 tool_calls 应转换为 tool_call_chunks 形态。"""
    from yuxi.agents.models import _message_to_chunk

    message = AIMessage(
        content="",
        tool_calls=[{"name": "get_weather", "args": {"city": "北京"}, "id": "call_1", "type": "tool_call"}],
    )
    chunk = _message_to_chunk(message)
    assert chunk.message.tool_call_chunks
    tc = chunk.message.tool_call_chunks[0]
    assert tc["name"] == "get_weather"
    assert tc["id"] == "call_1"
    assert '"city"' in tc["args"] and '"北京"' in tc["args"]


async def test_message_to_chunk_without_tool_calls():
    """无 tool_calls 的纯文本消息应正常包装为 chunk（tool_call_chunks 为空列表）。"""
    from yuxi.agents.models import _message_to_chunk

    message = AIMessage(content="你好")
    chunk = _message_to_chunk(message)
    assert chunk.message.content == "你好"
    assert chunk.message.tool_call_chunks == []


def test_get_request_payload_pads_empty_tool_call_content():
    """带 tool_calls 的 assistant 消息空 content 应被补齐占位符，避免端点拒绝 null。"""
    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    msgs = [
        HumanMessage("现在几点了"),
        AIMessage(content="", tool_calls=[{"name": "get_time", "args": {}, "id": "call_1"}]),
        ToolMessage(content="2026-09-02 10:30", tool_call_id="call_1"),
    ]

    payload = model._get_request_payload(msgs, stop=None)
    assistant = [m for m in payload["messages"] if m.get("role") == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == _EMPTY_ASSISTANT_CONTENT_PLACEHOLDER
    assert assistant[0]["tool_calls"], "tool_calls 应保留"


def test_get_request_payload_keeps_nonempty_content():
    """content 非空（含带 tool_calls 且有正文）的消息不应被改动。"""
    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    msgs = [
        AIMessage(content="正常回复"),
        AIMessage(content="让我查一下", tool_calls=[{"name": "get_time", "args": {}, "id": "call_1"}]),
    ]

    payload = model._get_request_payload(msgs, stop=None)
    contents = [m["content"] for m in payload["messages"] if m.get("role") == "assistant"]
    assert contents == ["正常回复", "让我查一下"]


def test_get_request_payload_collapses_system_content_blocks():
    """system 消息被 deepagents 用 content_blocks 拼成列表时，应序列化为字符串。

    这是 yuanzhi-m1「【内容】 Not a valid string.」报错的回归测试：修复前
    `SystemMessage(content_blocks=[...text...])` 会被 langchain-openai 序列化成
    ``[{"type":"text","text":...}]`` 列表，端点拒绝后返回 choices=null，被
    ModelRetryMiddleware 重试 3 次后报 "Model call failed after 3 attempts"。
    """
    from langchain_core.messages import SystemMessage

    model = _FakeZeroChunkModel(model="fake-model", api_key="test")
    sm = SystemMessage(
        content_blocks=[
            {"type": "text", "text": "你是助手。"},
            {"type": "text", "text": "\n\nPreloaded Skills: ..."},
        ]
    )
    payload = model._get_request_payload([sm], stop=None)
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert isinstance(system["content"], str), "纯文本 content 块列表必须合并为字符串"
    assert system["content"] == "你是助手。\n\nPreloaded Skills: ..."


def test_collapse_text_content_blocks_merges_pure_text_blocks():
    """纯文本块列表合并为单个字符串，多个块按空串拼接（分隔符已含在块文本内）。"""
    message = {
        "content": [
            {"type": "text", "text": "第一部分"},
            {"type": "text", "text": "\n\n第二部分"},
        ]
    }
    _collapse_text_content_blocks(message)
    assert message["content"] == "第一部分\n\n第二部分"


def test_collapse_text_content_blocks_preserves_non_text_blocks():
    """含图片等非文本块的列表保持原样，不影响多模态提供商。"""
    message = {
        "content": [
            {"type": "text", "text": "看图"},
            {"type": "image", "base64": "abc", "mime_type": "image/png"},
        ]
    }
    original = list(message["content"])
    _collapse_text_content_blocks(message)
    assert message["content"] == original, "含非文本块的列表不应被合并"

    # 字符串 content 也不应被改动
    message = {"content": "已经是字符串"}
    _collapse_text_content_blocks(message)
    assert message["content"] == "已经是字符串"
