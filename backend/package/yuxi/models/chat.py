"""聊天模型加载、供应商协议适配与通用调用入口。"""

import json
import re
from uuid import uuid4

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, convert_to_messages
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from yuxi import get_version
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url, logger

# 部分 OpenAI 兼容提供商（如 yuanzhi-m1）要求每条消息的 content 字段非空。
# langchain-openai 在 assistant 消息带 tool_calls / function_call 时，会把空
# content 序列化为 null，端点返回 500「该字段不能为 null」。用占位符补齐使
# 工具调用后的下一轮请求能正常投递；占位符仅出现在工具调用消息里，该消息的
# 语义由 tool_calls 与随后的 tool 结果承载，不影响对话内容。
_EMPTY_ASSISTANT_CONTENT_PLACEHOLDER = "(tool call)"

# 部分 OpenAI 兼容提供商（如 yuanzhi-m1）不支持原生 function calling：模型把
# 工具调用以 Qwen 风格 XML 文本写在 content 里返回，并把推理文本连同
# ``</think>`` 结束标记一并透传。langchain 拿到的 tool_calls 恒为空，智能体
# 的技能与工具全部失效。解析器把这种文本工具调用还原为原生 tool_calls。
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


def load_chat_model(fully_specified_name: str | None, *, session_id: str | None = None, **kwargs) -> BaseChatModel:
    """加载模型，为 OpenCode 请求绑定稳定会话路由。"""
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
    if info.provider_id in {"opencode", "opencode-go"}:
        kwargs["default_headers"] = {
            **(kwargs.get("default_headers") or {}),
            "User-Agent": f"yuxi/{get_version()}",
            "x-opencode-session": session_id or str(uuid4()),
        }
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

    logger.debug(
        f"Loading model {fully_specified_name} with "
        f"provider_type={info.provider_type} and provider_id={info.provider_id}"
    )

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

    return ChatCompletionsAdapter(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        preserve_reasoning=info.provider_id
        in {
            "siliconflow",
            "siliconflow-cn",
            "opencode",
            "opencode-go",
            "zhipuai",
            "zhipuai-coding-plan",
            "zai",
            "zai-coding-plan",
            "YuanZhi",
        },
        **kwargs,
    )


def reasoning_content(message: dict) -> str:
    """读取供应商原始推理文本，保持空白与工具续答输入不变。"""
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def normalize_tool_call_chunks(message: AIMessageChunk) -> None:
    """将续片空串改为 None，防止 v3 累积覆盖工具首片的名称与 ID。"""
    for tool in message.tool_call_chunks:
        for key in ("name", "id"):
            if tool.get(key) == "":
                tool[key] = None


class ChatCompletionsAdapter(ChatOpenAI):
    """OpenAI 兼容模型的协议适配与兼容层。

    在解析边界保留扩展字段（推理内容），HTTP、重试和工具绑定由上游负责。
    同时收敛对不严格遵循 OpenAI 协议的提供商（如 yuanzhi-m1）的兼容修复：

    1. 归一化流式 tool_call 续片的空串 name/id（`_convert_chunk_to_generation_chunk`），
       规避 v3 流式累积"后值覆盖"导致丢工具名与 id。
    2. 推理内容保留（`preserve_reasoning`）：流式 chunk 与非流式响应都把供应商
       reasoning 字段转成标准 reasoning 块，工具续答时回填 `reasoning_content`。
    3. 消息 content 序列化归一化（`_get_request_payload`）：纯文本块列表合并为
       字符串（端点拒绝列表时报 500「【内容】 Not a valid string.」）；补齐带
       tool_calls 的 assistant 消息空 content（端点拒绝 null 时报 500
       「该字段不能为 null」）。两类故障表现为 choices=null 或流式零 chunk，
       经 ModelRetryMiddleware 重试 3 次后报 "Model call failed after N attempts"。
    4. 流式零 chunk 回退非流式生成（`_astream`/`_stream`）：langchain-core 的
       v2 流式桥接在零产出时抛 RuntimeError("v2 stream finished without
       producing a message")，回退把非流式完整回复包装成单个 chunk 产出。
    5. 文本工具调用解析（`_StreamingTextToolCallParser`）：不支持原生 function
       calling 的端点把工具调用写成 ``<tool_call>`` XML 文本连同 ``</think>``
       推理标记返回，仅在请求绑定工具时把其还原为原生 tool_calls 并剥除推理
       文本；对支持原生 function calling 的端点无影响。
    """

    preserve_reasoning: bool = Field(default=False, exclude=True)

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        """在上游丢弃扩展字段前读取推理，并归一化工具续片。"""
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        message = generation.message
        normalize_tool_call_chunks(message)
        choices = chunk.get("choices") or []
        if self.preserve_reasoning:
            self._standardize_content(message, choices[0].get("delta") or {} if choices else {})
        return generation

    def _create_chat_result(self, response, generation_info=None):
        """非流式响应也保留同一供应商字段。"""
        result = super()._create_chat_result(response, generation_info)
        if self.preserve_reasoning:
            data = response if isinstance(response, dict) else response.model_dump()
            for generation, choice in zip(result.generations, data.get("choices", []), strict=True):
                self._standardize_content(generation.message, choice.get("message") or {})
        return result

    def _standardize_content(self, message: AIMessage, raw: dict) -> None:
        """在模型边界生成标准块，文本不经过裁剪以便原样续答。"""
        blocks = message.content_blocks
        for block in blocks:
            if block["type"] == "text":
                block["index"] = "lc_text"
        if reasoning := reasoning_content(raw):
            # lc_ 索引支持 v1 多片合并，并与整数工具索引隔离。
            blocks.insert(0, {"type": "reasoning", "reasoning": reasoning, "index": "lc_reasoning"})
        message.content = blocks
        message.response_metadata["output_version"] = "v1"

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """构建请求载荷：归一化消息 content，并为推理供应商回填续答原文。

        流式与非流式（`_generate`/`_agenerate`/`_astream`/`_stream`）都经此
        方法组装请求体，是唯一需要拦截的序列化点。归一化对所有提供商幂等：
        纯文本块列表合并为字符串对接受字符串的端点语义等价；含图片等非文本块
        的列表保持原样，不影响多模态提供商。
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
        if self.preserve_reasoning and "messages" in payload:
            originals = self._convert_input(input_).to_messages()
            for original, wire in zip(originals, payload["messages"], strict=True):
                if isinstance(original, AIMessage):
                    reasoning = "".join(
                        block.get("reasoning", "") for block in original.content_blocks if block["type"] == "reasoning"
                    ) or reasoning_content(original.additional_kwargs)
                    if reasoning:
                        wire["reasoning_content"] = reasoning
        return payload

    async def _astream(self, *args, **kwargs):
        """流式包装：解析文本工具调用，零 chunk 时回退非流式生成。"""
        parser = _make_text_tool_call_parser(kwargs)
        yielded = False
        async for chunk in super()._astream(*args, **kwargs):
            yielded = True
            yield _apply_text_tool_call_parser(parser, chunk) if parser else chunk
        if parser is not None:
            tail = _flush_text_tool_call_parser(parser)
            if tail is not None:
                yield tail
        if not yielded:
            fallback = await self._fallback_generate_async(args=args, kwargs=kwargs)
            if fallback is not None:
                yield fallback

    def _stream(self, *args, **kwargs):
        """同步流式包装：行为与 `_astream` 对齐。"""
        parser = _make_text_tool_call_parser(kwargs)
        yielded = False
        for chunk in super()._stream(*args, **kwargs):
            yielded = True
            yield _apply_text_tool_call_parser(parser, chunk) if parser else chunk
        if parser is not None:
            tail = _flush_text_tool_call_parser(parser)
            if tail is not None:
                yield tail
        if not yielded:
            fallback = self._fallback_generate(args=args, kwargs=kwargs)
            if fallback is not None:
                yield fallback

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """非流式生成：请求绑定工具时把文本工具调用还原为原生 tool_calls。"""
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if kwargs.get("tools"):
            for generation in result.generations:
                _extract_text_tool_calls_from_message(generation.message)
        return result

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        """异步非流式生成：行为与 `_generate` 对齐。"""
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


def _collapse_text_content_blocks(message: dict) -> None:
    """把纯文本 content 块列表合并为单个字符串（就地修改 message）。

    OpenAI 兼容端点的消息 content 既可以是字符串，也可以是 content 块列表
    （多模态格式）。部分提供商只接受字符串，langchain 对 system 消息可能
    序列化成多个 ``{"type": "text", "text": ...}`` 块。仅当所有块都是 text
    块时才合并（按空串拼接，块间分隔符已包含在块文本里）；含图片等非文本块
    的列表保持原样。
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
                self._block = data[tool_at + len(_TEXT_TOOL_CALL_OPEN) :]
                events.extend(self.feed(""))
                return events
            if think_at != -1:
                # 丢弃透传的推理文本与其结束标记，只保留其后的正文
                data = data[think_at + len(_THINK_CLOSE) :]
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
                        "id": f"call_{uuid4().hex[:24]}",
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


def _aggregate_text_from_blocks(content) -> tuple[list | None, str | None]:
    """从字符串或推理适配后的块列表中聚合出待解析文本。

    返回 ``(blocks, text)``：字符串形态时 blocks 为 None；块列表形态时仅聚合
    text 块文本、reasoning 等非文本块不参与解析。既非字符串也非纯 dict 块列表
    时返回 ``(None, None)``，调用方直通。
    """
    if isinstance(content, str):
        return None, content
    if isinstance(content, list) and all(isinstance(block, dict) for block in content):
        return content, "".join(block.get("text") or "" for block in content if block.get("type") == "text")
    return None, None


def _rebuild_message_content(blocks: list | None, text: str):
    """把解析后的正文写回原 content 形态：块列表保留非文本块，字符串原样返回。"""
    if blocks is None:
        return text
    kept = [block for block in blocks if block.get("type") != "text"]
    if text:
        kept.append({"type": "text", "text": text, "index": "lc_text"})
    return kept


def _apply_text_tool_call_parser(
    parser: "_StreamingTextToolCallParser", chunk: ChatGenerationChunk
) -> ChatGenerationChunk:
    """把解析器事件套用到流式 chunk 上：文本工具调用变为 tool_call_chunks。"""
    message = chunk.message
    blocks, text = _aggregate_text_from_blocks(message.content)
    if not text:
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
        content=_rebuild_message_content(blocks, content),
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
    return ChatGenerationChunk(message=AIMessageChunk(content=content, tool_call_chunks=tool_call_chunks))


def _extract_text_tool_calls_from_message(message: AIMessage) -> None:
    """非流式结果后处理：把 content 里的文本工具调用还原为原生 tool_calls。

    无工具调用时仅当存在透传的 ``</think>`` 推理标记才剥掉推理文本，其余消息
    完全不动。
    """
    blocks, text = _aggregate_text_from_blocks(message.content)
    if not text or (_TEXT_TOOL_CALL_OPEN not in text and _THINK_CLOSE not in text):
        return
    parser = _StreamingTextToolCallParser()
    events = parser.feed(text) + parser.flush()
    calls = [payload for kind, payload in events if kind == "tool_call"]
    new_content = "".join(payload for kind, payload in events if kind == "content")
    if not calls and new_content == text:
        return
    message.content = _rebuild_message_content(blocks, new_content)
    if calls:
        message.tool_calls = list(message.tool_calls or []) + [
            {"name": call["name"], "args": call["args"], "id": call["id"], "type": "tool_call"} for call in calls
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


class GeneralResponse:
    def __init__(self, content):
        self.content = content
        self.is_full = False


class LangChainChatAdapter:
    def __init__(self, model, *, model_name: str, base_url: str | None = None, info: dict | None = None):
        self.model = model
        self.model_name = model_name
        self.base_url = base_url
        self.info = info or {}

    @staticmethod
    def _normalize_messages(message):
        if isinstance(message, str):
            return message
        return convert_to_messages(message)

    async def call(self, message, stream=False):
        messages = self._normalize_messages(message)
        try:
            if stream:
                return self._stream_response(messages)
            response = await self.model.ainvoke(messages)
            return GeneralResponse(response.text)
        except Exception as e:
            err = f"Error calling model: {e}, URL: {self.base_url}, Model: {self.model_name}"
            logger.error(err)
            raise Exception(err)

    async def _stream_response(self, messages):
        async for chunk in self.model.astream(messages):
            if chunk.text:
                yield GeneralResponse(chunk.text)


def _langchain_kwargs(provider_type: str, kwargs: dict) -> dict:
    langchain_kwargs = dict(kwargs.pop("model_params", {}) or {})
    langchain_kwargs.update(kwargs)
    if provider_type == "anthropic" and "max_completion_tokens" in langchain_kwargs:
        langchain_kwargs.setdefault("max_tokens", langchain_kwargs.pop("max_completion_tokens"))
    return langchain_kwargs


def select_model(model_spec: str, **kwargs) -> LangChainChatAdapter:
    if not model_spec:
        raise ValueError("model_spec 不能为空")

    info = model_cache.get_model_info(model_spec)
    if not info:
        available = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available[:10]]
        raise ValueError(f"未找到模型: '{model_spec}'。可用聊天模型 ({len(available)}): {available_ids}")

    if info.model_type != "chat":
        raise ValueError(f"Model {model_spec} is not a chat model (type={info.model_type})")

    logger.info(f"Selecting model: {model_spec} (provider_type={info.provider_type})")

    model = load_chat_model(
        model_spec,
        **_langchain_kwargs(info.provider_type, kwargs),
    )
    return LangChainChatAdapter(
        model,
        model_name=info.model_id,
        base_url=info.base_url,
        info={"provider_type": info.provider_type, "provider_id": info.provider_id},
    )


if __name__ == "__main__":
    pass
