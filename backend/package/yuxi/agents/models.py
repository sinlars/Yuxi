import json
import re
import uuid

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger

# 部分 OpenAI 兼容提供商（如 yuanzhi-m1）要求每条消息的 content 字段非空。
# langchain-openai 在 assistant 消息带 tool_calls / function_call 时，会把空
# content 序列化为 null，端点会返回 500「该字段不能为 null / 空」。这里用占位符
# 补齐，使工具调用后的下一轮请求能正常投递。占位符仅出现在工具调用消息里，
# 该消息的语义由 tool_calls 与随后的 tool 结果承载，占位符不影响对话内容。
_EMPTY_ASSISTANT_CONTENT_PLACEHOLDER = "(tool call)"

# 部分 OpenAI 兼容提供商（如 yuanzhi-m1）不支持原生 function calling：模型把
# 工具调用以 Qwen 风格 XML 文本写在 content 里返回（``<tool_call><function=name>
# <parameter=key>value</parameter></function></tool_call>``），并把推理文本连
# 同 ``</think>`` 结束标记一并透传。langchain 拿到的 tool_calls 恒为空，智能体
# 的技能与工具全部失效。下面的解析器把这种文本工具调用还原为原生 tool_calls。
_TEXT_TOOL_CALL_OPEN = "<tool_call>"
_TEXT_TOOL_CALL_CLOSE = "</tool_call>"
_THINK_CLOSE = "</think>"


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, **kwargs) -> BaseChatModel:
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)

    info = model_cache.get_model_info(fully_specified_name)
    if not info:
        available_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available_specs[:10]]
        raise ValueError(
            f"Unknown model spec: '{fully_specified_name}'. "
            f"Available chat models ({len(available_specs)}): {available_ids}"
        )

    if info.model_type != "chat":
        raise ValueError(f"Model {fully_specified_name} is not a chat model (type={info.model_type})")

    api_key = info.api_key
    base_url = get_docker_safe_url(info.base_url)
    if info.request_body_overrides:
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.update(info.request_body_overrides)
        kwargs = {**kwargs, "extra_body": extra_body}

    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.update(
        {
            "yuxi_provider_id": info.provider_id,
            "yuxi_provider_type": info.provider_type,
            "yuxi_model_id": info.model_id,
            "yuxi_model_spec": info.spec,
        }
    )
    kwargs["metadata"] = metadata

    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **kwargs,
        )
    if info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **kwargs,
        )

    return _ToolCallChunkFixChatOpenAI(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        **kwargs,
    )


class _ToolCallChunkFixChatOpenAI(ChatOpenAI):
    """OpenAI 兼容模型的流式兼容层，包含五类修复。

    1. 归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。

    2. 消息 content 序列化归一化（见 `_get_request_payload`）：
       纯文本 content 块列表合并为字符串；补齐带 tool_calls 的 assistant
       消息的空 content。

       部分 OpenAI 兼容提供商（如 yuanzhi-m1）要求每条消息的 content 是
       非空字符串。langchain-openai 一方面会把带 tool_calls 的 assistant
       消息空 content 序列化为 null（端点返回 500「该字段不能为 null」），
       另一方面会把 deepagents 中间件用 content_blocks 拼出的 system 消息
       序列化为 ``[{"type":"text",...}]`` 列表（端点返回 500「【内容】
       Not a valid string.」）。两种情形流式下都表现为零 chunk，非流式下
       表现为 choices=null 的 TypeError，均被 ModelRetryMiddleware 重试 3
       次后报 "Model call failed after 3 attempts"。

    3. langchain 流式协议 v1→v2 兼容（零 chunk 回退非流式生成）。

       langchain-core >= 1.6 起，LangGraph（stream_mode="messages" /
       astream_events v3）挂载的 v2 流式回调会让模型 invoke 也改走
       v2 协议事件流：`_agenerate_with_cache` → `_aiter_v2_events` →
       桥接 `achunks_to_events(self._astream(...))`。若 `_astream` 一个
       chunk 都没产出，桥接不会发出 message-start/message-finish，
       累积器 output_message 为 None，上层抛
       RuntimeError("v2 stream finished without producing a message")，
       被 ModelRetryMiddleware 重试 3 次后报
       "Model call failed after 3 attempts"。

       这是第 2 点 content 序列化被端点拒绝之外的另一层兜底：即便流式真的一
       个 chunk 都不产出（某些提供商在流式接口上只回传推理内容、正文为空，
       或端点对某些请求直接返回错误），也能回退调用非流式
       `_generate`/`_agenerate`，把完整回复包装成单个 chunk 产出，对齐 v1
       行为，v2 桥接由此能拿到 message-start/finish 事件，不再抛错。

    4. 文本工具调用解析（见 `_StreamingTextToolCallParser`）。

       部分 OpenAI 兼容提供商（如 yuanzhi-m1）不支持原生 function
       calling：请求里正常下发 `tools`，模型却把工具调用以 Qwen 风格 XML
       文本（``<tool_call><function=name>...</function></tool_call>``）连同
       ``</think>`` 推理结束标记一起写在 content 里返回，tool_calls 恒为空，
       智能体的技能与工具全部失效。仅当请求绑定了工具时，流式与非流式结果
       都经过解析器把文本工具调用还原为原生 tool_calls，并剥掉透传的推理
       文本；对支持原生 function calling 的端点无影响（content 中不会出现
       这些标记，解析器直通）。
    """

    async def _astream(self, *args, **kwargs):
        parser = _make_text_tool_call_parser(kwargs)
        yielded = False
        async for chunk in super()._astream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yielded = True
            yield _apply_text_tool_call_parser(parser, chunk) if parser else chunk
        if parser is not None:
            tail = _flush_text_tool_call_parser(parser)
            if tail is not None:
                yielded = True
                yield tail
        if not yielded:
            fallback = await self._fallback_generate_async(args=args, kwargs=kwargs)
            if fallback is not None:
                yield fallback

    def _stream(self, *args, **kwargs):
        parser = _make_text_tool_call_parser(kwargs)
        yielded = False
        for chunk in super()._stream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yielded = True
            yield _apply_text_tool_call_parser(parser, chunk) if parser else chunk
        if parser is not None:
            tail = _flush_text_tool_call_parser(parser)
            if tail is not None:
                yielded = True
                yield tail
        if not yielded:
            fallback = self._fallback_generate(args=args, kwargs=kwargs)
            if fallback is not None:
                yield fallback

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if kwargs.get("tools"):
            for generation in result.generations:
                _extract_text_tool_calls_from_message(generation.message)
        return result

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if kwargs.get("tools"):
            for generation in result.generations:
                _extract_text_tool_calls_from_message(generation.message)
        return result

    def _log_fallback(self) -> None:
        logger.warning(
            f"Model {self.model_name} stream produced no chunks "
            f"(v2 protocol would raise 'stream finished without producing a message'); "
            "falling back to non-streaming generation."
        )

    def _fallback_generate(self, *, args: tuple, kwargs: dict) -> ChatGenerationChunk | None:
        """同步：流式零 chunk 时回退非流式生成，返回单个 ChatGenerationChunk。"""
        messages, stop, run_manager, generate_kwargs = _extract_generate_args(args, kwargs)
        self._log_fallback()
        result = self._generate(messages, stop=stop, run_manager=run_manager, **generate_kwargs)
        if not result.generations:
            return None
        return _message_to_chunk(result.generations[0].message)

    async def _fallback_generate_async(self, *, args: tuple, kwargs: dict) -> ChatGenerationChunk | None:
        """异步：流式零 chunk 时回退非流式生成，返回单个 ChatGenerationChunk。"""
        messages, stop, run_manager, generate_kwargs = _extract_generate_args(args, kwargs)
        self._log_fallback()
        result = await self._agenerate(messages, stop=stop, run_manager=run_manager, **generate_kwargs)
        if not result.generations:
            return None
        return _message_to_chunk(result.generations[0].message)

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """构建请求载荷，并做 OpenAI 兼容提供商需要的消息序列化归一化。

        流式与非流式（`_generate`/`_agenerate`/`_astream`/`_stream`）都经此
        方法组装请求体，是唯一需要拦截的序列化点。两类归一化：

        1. 把纯文本 content 块列表合并为字符串。部分中间件（deepagents 的
           append_to_system_message）会把 system 消息写成 content_blocks 列表，
           langchain-openai 原样序列化为 ``[{"type": "text", "text": ...}]``。
           部分端点（如 yuanzhi-m1）只接受字符串 content，会返回 500
           「【内容】 Not a valid string.」。纯文本块合并为字符串对 OpenAI 官方
           等接受字符串的端点语义等价；含非文本块（图片等）的列表保持原样，
           不影响多模态提供商。

        2. 补齐带 tool_calls 的 assistant 消息的空 content。langchain-openai 会
           把带 tool_calls 的 assistant 消息的空 content 序列化为 null，部分端点
           要求 content 非空，否则返回 500「该字段不能为 null」。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message in payload.get("messages") or []:
            if not isinstance(message, dict):
                continue
            _collapse_text_content_blocks(message)
            if (
                message.get("role") == "assistant"
                and ("tool_calls" in message or "function_call" in message)
                and not message.get("content")
            ):
                message["content"] = _EMPTY_ASSISTANT_CONTENT_PLACEHOLDER
        return payload


def _collapse_text_content_blocks(message: dict) -> None:
    """把纯文本 content 块列表合并为单个字符串（就地修改 message）。

    OpenAI 兼容端点的消息 content 既可以是字符串，也可以是 content 块列表
    （多模态格式）。部分提供商只接受字符串，langchain 对 system 消息可能
    序列化成多个 ``{"type": "text", "text": ...}`` 块。这里仅当所有块都是
    text 块时才合并（按空串拼接，块间分隔符已包含在块文本里）；含图片等
    非文本块的列表保持原样。
    """
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return
    if not all(isinstance(block, dict) and block.get("type") == "text" for block in content):
        return
    message["content"] = "".join(block.get("text") or "" for block in content)


def _make_text_tool_call_parser(kwargs: dict) -> "_StreamingTextToolCallParser | None":
    """请求绑定了工具时启用文本工具调用解析器，否则直通不做任何变换。"""
    if kwargs.get("tools"):
        return _StreamingTextToolCallParser()
    return None


class _StreamingTextToolCallParser:
    """从流式文本增量中提取 ``<tool_call>`` 文本工具调用与 ``</think>`` 推理标记。

    适配不支持原生 function calling 的 OpenAI 兼容端点（如 yuanzhi-m1）：
    模型把工具调用写成 ``<tool_call><function=name><parameter=k>v</parameter>
    </function></tool_call>`` 文本、把推理文本连同 ``</think>`` 一起放进
    content。本解析器按增量喂入文本，产出两类事件：

    - ``("content", str)``：可直接作为正文输出的文本；
    - ``("tool_call", dict)``：解析出的工具调用（name/args/id/index）。

    为支持标记跨 chunk 拆分，普通态下最多扣留 ``len(标记)-1`` 个字符不吐出，
    待下一段增量到达后判定；流结束时 ``flush`` 把扣留内容全部归还。块解析失败
    （非工具调用文本）时按原文吐回，不吞内容。仅在请求绑定工具时由
    `_make_text_tool_call_parser` 启用，纯文本对话不受影响。
    """

    _HOLD_BACK = max(len(_TEXT_TOOL_CALL_OPEN), len(_THINK_CLOSE)) - 1
    _FUNCTION_RE = re.compile(r"<function\s*=\s*(?P<name>[^>\s]+)\s*>(?P<body>.*?)</function>", re.DOTALL)
    _PARAMETER_RE = re.compile(r"<parameter\s*=\s*(?P<key>[^>\s]+)\s*>(?P<value>.*?)</parameter>", re.DOTALL)

    def __init__(self):
        self._buffer = ""
        self._block: str | None = None
        self._index = 0

    def feed(self, text: str) -> list[tuple[str, object]]:
        """喂入一段文本增量，返回本段可产出的 content / tool_call 事件。"""
        events: list[tuple[str, object]] = []
        if self._block is not None:
            self._block += text
            if _TEXT_TOOL_CALL_CLOSE not in self._block:
                return events
            block, _, rest = self._block.partition(_TEXT_TOOL_CALL_CLOSE)
            self._block = None
            events.extend(self._emit_block(block))
            if rest:
                events.extend(self.feed(rest))
            return events

        data = self._buffer + text
        self._buffer = ""
        while data:
            tool_at = data.find(_TEXT_TOOL_CALL_OPEN)
            think_at = data.find(_THINK_CLOSE)
            if tool_at != -1 and (think_at == -1 or tool_at < think_at):
                if tool_at:
                    events.append(("content", data[:tool_at]))
                self._block = data[tool_at + len(_TEXT_TOOL_CALL_OPEN):]
                events.extend(self.feed(""))
                return events
            if think_at != -1:
                # 丢弃透传的推理文本与其结束标记，只保留其后的正文
                data = data[think_at + len(_THINK_CLOSE):]
                continue
            hold = self._partial_marker_len(data)
            if hold:
                self._buffer = data[-hold:]
                data = data[:-hold]
            if data:
                events.append(("content", data))
            break
        return events

    def flush(self) -> list[tuple[str, object]]:
        """流结束：归还扣留的尾部内容；未闭合的 tool_call 块按原文吐回。"""
        events: list[tuple[str, object]] = []
        if self._block is not None:
            events.append(("content", _TEXT_TOOL_CALL_OPEN + self._block))
            self._block = None
        if self._buffer:
            events.append(("content", self._buffer))
            self._buffer = ""
        return events

    def _emit_block(self, block: str) -> list[tuple[str, object]]:
        """把一个完整 ``</tool_call>`` 之前的内容解析为工具调用事件。"""
        calls = self._parse_block(block)
        if calls is None:
            return [("content", _TEXT_TOOL_CALL_OPEN + block + _TEXT_TOOL_CALL_CLOSE)]
        events: list[tuple[str, object]] = []
        for name, args in calls:
            events.append(
                (
                    "tool_call",
                    {
                        "name": name,
                        "args": args,
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "index": self._index,
                    },
                )
            )
            self._index += 1
        return events

    @classmethod
    def _parse_block(cls, block: str) -> list[tuple[str, dict]] | None:
        """解析 tool_call 块内部文本，返回 [(name, args), ...]；无法解析返回 None。"""
        calls: list[tuple[str, dict]] = []
        for match in cls._FUNCTION_RE.finditer(block):
            args = {}
            for param in cls._PARAMETER_RE.finditer(match.group("body")):
                value = param.group("value").strip()
                if not value:
                    # 模板残留的空参数（如空 schema 渲染出的 <parameter=dummy>）
                    continue
                args[param.group("key")] = _parse_parameter_value(value)
            calls.append((match.group("name"), args))
        if calls:
            return calls
        # 兼容 Qwen 标准 JSON 形态：<tool_call>{"name": ..., "arguments": {...}}</tool_call>
        try:
            data = json.loads(block.strip())
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return None
        raw_args = data.get("arguments")
        if not isinstance(raw_args, dict):
            raw_args = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        return [(data["name"], dict(raw_args))]

    def _partial_marker_len(self, data: str) -> int:
        """返回 data 尾部可能是标记前缀的最长长度（用于跨 chunk 判定）。"""
        for size in range(min(len(data), self._HOLD_BACK), 0, -1):
            suffix = data[-size:]
            if _TEXT_TOOL_CALL_OPEN.startswith(suffix) or _THINK_CLOSE.startswith(suffix):
                return size
        return 0


def _parse_parameter_value(value: str):
    """参数值尽量按 JSON 还原类型（数字/布尔/对象），失败则保留原字符串。"""
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _apply_text_tool_call_parser(parser: "_StreamingTextToolCallParser", chunk: ChatGenerationChunk) -> ChatGenerationChunk:
    """把解析器事件套用到流式 chunk 上：文本工具调用变为 tool_call_chunks。"""
    message = chunk.message
    text = message.content
    if not isinstance(text, str):
        return chunk
    events = parser.feed(text)
    calls = [payload for kind, payload in events if kind == "tool_call"]
    content = "".join(payload for kind, payload in events if kind == "content")
    if not calls and content == text:
        return chunk
    tool_call_chunks = [
        {
            "name": call["name"],
            "args": json.dumps(call["args"], ensure_ascii=False),
            "id": call["id"],
            "index": call["index"],
            "type": "tool_call_chunk",
        }
        for call in calls
    ]
    new_message = AIMessageChunk(
        content=content,
        additional_kwargs=dict(message.additional_kwargs or {}),
        response_metadata=dict(message.response_metadata or {}),
        id=message.id,
        usage_metadata=message.usage_metadata,
        tool_call_chunks=tool_call_chunks + list(message.tool_call_chunks or []),
    )
    return ChatGenerationChunk(message=new_message, generation_info=chunk.generation_info)


def _flush_text_tool_call_parser(parser: "_StreamingTextToolCallParser") -> ChatGenerationChunk | None:
    """流结束时归还解析器扣留的内容与未产出的工具调用，无残留返回 None。"""
    events = parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    content = "".join(payload for kind, payload in events if kind == "content")
    if not calls and not content:
        return None
    tool_call_chunks = [
        {
            "name": call["name"],
            "args": json.dumps(call["args"], ensure_ascii=False),
            "id": call["id"],
            "index": call["index"],
            "type": "tool_call_chunk",
        }
        for call in calls
    ]
    return ChatGenerationChunk(
        message=AIMessageChunk(content=content, tool_call_chunks=tool_call_chunks)
    )


def _extract_text_tool_calls_from_message(message: AIMessage) -> None:
    """非流式结果后处理：把 content 里的文本工具调用还原为原生 tool_calls。"""
    content = message.content
    if not isinstance(content, str):
        return
    if _TEXT_TOOL_CALL_OPEN not in content and _THINK_CLOSE not in content:
        return
    parser = _StreamingTextToolCallParser()
    events = parser.feed(content) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    new_content = "".join(payload for kind, payload in events if kind == "content")
    if not calls:
        # 无工具调用：仅当存在透传的 </think> 推理标记时剥掉推理文本
        if new_content != content:
            message.content = new_content
        return
    message.content = new_content
    message.tool_calls = list(message.tool_calls or []) + [
        {"name": call["name"], "args": call["args"], "id": call["id"], "type": "tool_call"}
        for call in calls
    ]


def _extract_generate_args(args: tuple, kwargs: dict) -> tuple:
    """从 _stream/_astream 的调用参数中提取非流式生成所需参数。

    `_stream(self, messages, stop=None, run_manager=None, **kwargs)`
    的三个位置参数可能以位置或关键字两种形式传入，这里统一还原，
    并剥掉仅对流式请求有意义的参数。
    """
    kwargs = dict(kwargs)
    messages = args[0] if args else kwargs.pop("messages", None)
    stop = kwargs.pop("stop", None)
    if stop is None and len(args) > 1:
        stop = args[1]
    run_manager = kwargs.pop("run_manager", None)
    if run_manager is None and len(args) > 2:
        run_manager = args[2]
    for key in ("stream", "stream_usage", "stream_options"):
        kwargs.pop(key, None)
    return messages, stop, run_manager, kwargs


def _message_to_chunk(message: AIMessage) -> ChatGenerationChunk:
    """把非流式生成的 AIMessage 包装成单个 ChatGenerationChunk。

    tool_calls 需转换为 tool_call_chunks 形态（args 序列化为字符串增量），
    这样流式累积器才能还原出等价的 tool_call。
    """
    tool_call_chunks = [
        {
            "name": call.get("name"),
            "args": json.dumps(call.get("args") or {}, ensure_ascii=False),
            "id": call.get("id"),
            "index": index,
            "type": "tool_call_chunk",
        }
        for index, call in enumerate(getattr(message, "tool_calls", None) or [])
    ]
    chunk_message = AIMessageChunk(
        content=message.content,
        additional_kwargs=dict(getattr(message, "additional_kwargs", None) or {}),
        response_metadata=dict(getattr(message, "response_metadata", None) or {}),
        id=getattr(message, "id", None),
        usage_metadata=getattr(message, "usage_metadata", None),
        tool_call_chunks=tool_call_chunks,
    )
    return ChatGenerationChunk(message=chunk_message)


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是“后值覆盖”：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在“进行中”。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    for chunk in message.tool_call_chunks:
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None
