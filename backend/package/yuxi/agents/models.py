from contextvars import ContextVar

from langchain.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger


# 防止 _agenerate/_generate 兜底时，ainvoke/invoke 内部又回调覆写方法造成无限递归。
# True 表示「当前已经在兜底路径内，不应再次触发 fallback」。
_IN_FALLBACK: ContextVar[bool] = ContextVar("_IN_FALLBACK", default=False)


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback, sys_config.default_model):
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
    # ModelInfo 不一定存在 request_body_overrides 字段，保守跳过
    # if getattr(info, "request_body_overrides", None):
    #     extra_body = dict(kwargs.get("extra_body") or {})
    #     extra_body.update(info.request_body_overrides)
    #     kwargs = {**kwargs, "extra_body": extra_body}

    # metadata = dict(kwargs.pop("metadata", {}) or {})
    # metadata.update(
    #     {
    #         "yuxi_provider_id": getattr(info, "provider_id", None),
    #         "yuxi_provider_type": getattr(info, "provider_type", None),
    #         "yuxi_model_id": getattr(info, "model_id", None),
    #         "yuxi_model_spec": getattr(info, "spec", None),
    #     }
    # )
    # kwargs["metadata"] = metadata

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
    """归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。

    同时叠加「零 chunk 空流 + choices=null」兜底能力：
    1) 在 _astream/_stream 中检测"0 chunk 流式响应"（即 SSE body 为空/完全没有
       message-start 事件），此时退化为调用一次 ainvoke/invoke 非流式调用，
       把完整回答包装成单个 AIMessageChunk/AIMessage 返回；
    2) 在 _agenerate/_generate 中捕获 TypeError("choices=null")，同样退化为
       非流式 ainvoke/invoke 兜底；
    3) 兜底采用 5 步降级（详见 _ainvoke_as_single_chunk），终极 Step ⑤ 用
       raw httpx POST 绕过 LangChain 自己解析响应体，避免网关返回业务错误
       体（HTTP 200 + code/message + choices=null）时 LangChain 无法解析。
    """

    async def _astream(self, *args, **kwargs):
        _normalize_tool_call_chunks_gen = _normalize_tool_call_chunks
        got_any = False
        try:
            async for chunk in super()._astream(*args, **kwargs):
                got_any = True
                _normalize_tool_call_chunks_gen(chunk.message)
                yield chunk
        except TypeError as exc:
            # 典型：langchain_openai/chat_models/base.py _create_chat_result
            # 抛 "Received response with null value for 'choices'..."
            msg = str(exc)
            if "choices" in msg and ("null" in msg or "None" in msg):
                if _IN_FALLBACK.get():
                    raise
                token = _IN_FALLBACK.set(True)
                try:
                    fallback_chunk = await _ainvoke_as_single_chunk(self, *args, **kwargs)
                finally:
                    _IN_FALLBACK.reset(token)
                if fallback_chunk is not None:
                    yield fallback_chunk
                    return
                raise RuntimeError(_empty_stream_error_msg(self)) from exc
            raise
        if got_any:
            return
        # ── 零 chunk 空流 ────────────────────────────────────────
        if _IN_FALLBACK.get():
            # 已经在兜底 ainvoke 内部触发的 astream，再兜底就递归了，直接抛
            raise RuntimeError(_empty_stream_error_msg(self))
        token = _IN_FALLBACK.set(True)
        try:
            fallback_chunk = await _ainvoke_as_single_chunk(self, *args, **kwargs)
        finally:
            _IN_FALLBACK.reset(token)
        if fallback_chunk is None:
            raise RuntimeError(_empty_stream_error_msg(self))
        yield fallback_chunk

    def _stream(self, *args, **kwargs):
        _normalize_tool_call_chunks_gen = _normalize_tool_call_chunks
        got_any = False
        try:
            for chunk in super()._stream(*args, **kwargs):
                got_any = True
                _normalize_tool_call_chunks_gen(chunk.message)
                yield chunk
        except TypeError as exc:
            msg = str(exc)
            if "choices" in msg and ("null" in msg or "None" in msg):
                if _IN_FALLBACK.get():
                    raise
                token = _IN_FALLBACK.set(True)
                try:
                    fallback_chunk = _invoke_as_single_chunk(self, *args, **kwargs)
                finally:
                    _IN_FALLBACK.reset(token)
                if fallback_chunk is not None:
                    yield fallback_chunk
                    return
                raise RuntimeError(_empty_stream_error_msg(self)) from exc
            raise
        if got_any:
            return
        if _IN_FALLBACK.get():
            raise RuntimeError(_empty_stream_error_msg(self))
        token = _IN_FALLBACK.set(True)
        try:
            fallback_chunk = _invoke_as_single_chunk(self, *args, **kwargs)
        finally:
            _IN_FALLBACK.reset(token)
        if fallback_chunk is None:
            raise RuntimeError(_empty_stream_error_msg(self))
        yield fallback_chunk

    async def _agenerate(self, *args, **kwargs):
        if _IN_FALLBACK.get():
            return await ChatOpenAI._agenerate(self, *args, **kwargs)
        try:
            return await ChatOpenAI._agenerate(self, *args, **kwargs)
        except TypeError as exc:
            msg = str(exc)
            if not ("choices" in msg and ("null" in msg or "None" in msg)):
                raise
            # 网关返回 choices=null，尝试非流式兜底
            token = _IN_FALLBACK.set(True)
            try:
                result = await _ainvoke_as_single_chunk(self, *args, **kwargs)
            finally:
                _IN_FALLBACK.reset(token)
            if result is None:
                raise RuntimeError(_empty_stream_error_msg(self)) from exc
            from langchain_core.outputs import ChatResult
            return _chat_result_from_aimessage(result)

    def _generate(self, *args, **kwargs):
        if _IN_FALLBACK.get():
            return ChatOpenAI._generate(self, *args, **kwargs)
        try:
            return ChatOpenAI._generate(self, *args, **kwargs)
        except TypeError as exc:
            msg = str(exc)
            if not ("choices" in msg and ("null" in msg or "None" in msg)):
                raise
            token = _IN_FALLBACK.set(True)
            try:
                result = _invoke_as_single_chunk(self, *args, **kwargs)
            finally:
                _IN_FALLBACK.reset(token)
            if result is None:
                raise RuntimeError(_empty_stream_error_msg(self)) from exc
            return _chat_result_from_aimessage(result)


async def _ainvoke_as_single_chunk(instance, *args, **kwargs):
    """把 0-chunk 空流 / choices=null 退化到 ainvoke 非流式，返回 AIMessageChunk。

    五步兜底（从上到下优先级递减）：
      ① `ainvoke(instance, messages, invoke_kwargs)`：instance 本身可能已绑定
         tools，正常走模型层。若失败（典型：PydanticSerializationError 因为
         kwargs 透传了 BaseTool 对象 / 或 choices=null），继续②。
      ② 尝试 `ainvoke(instance, messages, 不带显式 tools/response_format 等 kwarg)`。
         注意：如果 instance 本身是绑定过 tools 的，self.tools 仍会被序列化；
         所以这一步只保证「显式」不再追加 tools 参数。若仍失败继续③。
      ③ 构造一个**从未绑定过 tools** 的同配置 ChatOpenAI 裸实例再试。
      ④ 用③的裸实例执行最小调用 `ainvoke(bare, messages)`（连 stop/callbacks 也
         不传），这是 v0.6.2 时代的最简路径。
      ⑤ 终极兜底：绕过 LangChain，用 raw httpx 发 POST，自己解析 JSON，
         网关返回业务错误体（code/message）时抛出明确的 RuntimeError，
         否则提取首个 choice 的 content 返回 AIMessage。
    """
    try:
        messages = args[0] if args else kwargs.get("messages")
        if messages is None:
            return None
        invoke_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in ("callbacks", "config", "tags", "metadata", "stop", "response_format")
        }
        result: object = None
        # ── Step ①：instance 自身已绑定 tools，正常 ainvoke ──────────
        try:
            result = await ChatOpenAI.ainvoke(instance, messages, **invoke_kwargs)
        except Exception as exc1:  # noqa: BLE001
            import sys as _sys1
            import traceback as _tb1
            _sys1.stderr.write(
                "\n[FALLBACK STEP ① FAILED] "
                f"type={type(exc1).__module__}.{type(exc1).__name__} "
                f"msg={str(exc1)[:300]!r}\n{_tb1.format_exc()}\n\n"
            )
            _sys1.stderr.flush()
        # ── Step ②：移除 tools/tool_choice/response_format 等高风险字段
        if result is None:
            logger.warning(
                "ainvoke 兜底 step ②：不依赖 instance.tools，退化为纯聊天请求，model=%s",
                getattr(instance, "model", "?"),
            )
            stripped_kwargs: dict = {
                k: v
                for k, v in invoke_kwargs.items()
                if k not in ("tools", "tool_choice", "parallel_tool_calls", "response_format")
            }
            try:
                result = await ChatOpenAI.ainvoke(instance, messages, **stripped_kwargs)
            except Exception as exc2:  # noqa: BLE001
                import sys as _sys2
                import traceback as _tb2
                _sys2.stderr.write(
                    "\n[FALLBACK STEP ② FAILED] "
                    f"type={type(exc2).__module__}.{type(exc2).__name__} "
                    f"msg={str(exc2)[:300]!r}\n{_tb2.format_exc()}\n\n"
                )
                _sys2.stderr.flush()
        # ── Step ③：构造一个完全没绑定 tools 的同配置 ChatOpenAI ──────
        if result is None:
            logger.warning(
                "ainvoke 兜底 step ③：构造无 tools 绑定的模型实例再试，model=%s",
                getattr(instance, "model", "?"),
            )
            bare = _clone_chat_openai_without_tools(instance)
            bare_kwargs: dict = {
                k: v
                for k, v in invoke_kwargs.items()
                if k not in ("tools", "tool_choice", "parallel_tool_calls", "response_format")
            }
            try:
                result = await ChatOpenAI.ainvoke(bare, messages, **bare_kwargs)
            except Exception as exc3:  # noqa: BLE001
                import sys as _sys3
                import traceback as _tb3
                _sys3.stderr.write(
                    "\n[FALLBACK STEP ③ FAILED] "
                    f"type={type(exc3).__module__}.{type(exc3).__name__} "
                    f"msg={str(exc3)[:300]!r}\n{_tb3.format_exc()}\n"
                    f"  bare type={type(bare).__module__}.{type(bare).__name__}\n"
                    f"  bare.model={getattr(bare, 'model', None)!r}\n"
                    f"  bare.base_url={getattr(bare, 'openai_api_base', None)!r}\n"
                    f"  bare_kwargs keys={list(bare_kwargs.keys())!r}\n\n"
                )
                _sys3.stderr.flush()
                # ── Step ④：「裸 ChatOpenAI(messages)」最小调用路径
                try:
                    result = await ChatOpenAI.ainvoke(bare, messages)
                except Exception as exc4:  # noqa: BLE001
                    import traceback as _tb4
                    _sys3.stderr.write(
                        "\n[FALLBACK STEP ④ FAILED] "
                        f"type={type(exc4).__module__}.{type(exc4).__name__} "
                        f"msg={str(exc4)[:300]!r}\n{_tb4.format_exc()}\n"
                        f"  clone_kwargs(api_key?): "
                        f"has={getattr(bare, 'openai_api_key', None) is not None} "
                        f"type={type(getattr(bare, 'openai_api_key', None)).__name__}\n\n"
                    )
                    _sys3.stderr.flush()
                    # ── Step ⑤（终极兜底）：raw httpx POST ──────────
                    try:
                        result = await _raw_httpx_chat_as_aimessage(bare, messages)
                    except Exception as exc5:  # noqa: BLE001
                        import traceback as _tb5
                        _sys3.stderr.write(
                            "\n[FALLBACK STEP ⑤ FAILED — FATAL, WILL RETURN NONE] "
                            f"type={type(exc5).__module__}.{type(exc5).__name__} "
                            f"msg={str(exc5)[:500]!r}\n{_tb5.format_exc()}\n\n"
                        )
                        _sys3.stderr.flush()
                        raise
    except Exception as exc:
        import traceback as _tb
        logger.warning(
            "流式零 chunk 且 ainvoke 兜底（五步）全部失败，model=%s: %s: %s\n%s",
            getattr(instance, "model", "?"),
            type(exc).__name__,
            str(exc)[:200],
            _tb.format_exc(),
        )
        return None
    content = result.content if isinstance(result.content, str) else str(result.content)
    if not content and not getattr(result, "tool_calls", None):
        return None
    from langchain_core.messages import AIMessageChunk
    tool_calls = getattr(result, "tool_calls", None) or []
    tcc = []
    for tc in tool_calls:
        tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
        if isinstance(args, dict):
            import json as _json
            args_str = _json.dumps(args, ensure_ascii=False)
        elif args is None:
            args_str = ""
        else:
            args_str = str(args)
        tcc.append({"name": name, "args": args_str, "id": tid, "index": 0})
    chunk = AIMessageChunk(
        content=content,
        tool_call_chunks=tcc,
    )
    return chunk


def _invoke_as_single_chunk(instance, *args, **kwargs):
    """同步版 _ainvoke_as_single_chunk（_stream / _generate 兜底使用）。"""
    try:
        messages = args[0] if args else kwargs.get("messages")
        if messages is None:
            return None
        invoke_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in ("callbacks", "config", "tags", "metadata", "stop", "response_format")
        }
        result: object = None
        try:
            result = ChatOpenAI.invoke(instance, messages, **invoke_kwargs)
        except Exception:  # noqa: BLE001
            pass
        if result is None:
            stripped_kwargs: dict = {
                k: v
                for k, v in invoke_kwargs.items()
                if k not in ("tools", "tool_choice", "parallel_tool_calls", "response_format")
            }
            try:
                result = ChatOpenAI.invoke(instance, messages, **stripped_kwargs)
            except Exception:  # noqa: BLE001
                pass
        if result is None:
            bare = _clone_chat_openai_without_tools(instance)
            bare_kwargs: dict = {
                k: v
                for k, v in invoke_kwargs.items()
                if k not in ("tools", "tool_choice", "parallel_tool_calls", "response_format")
            }
            try:
                result = ChatOpenAI.invoke(bare, messages, **bare_kwargs)
            except Exception:  # noqa: BLE001
                try:
                    result = ChatOpenAI.invoke(bare, messages)
                except Exception:  # noqa: BLE001
                    pass
        if result is None:
            # 同步版 Step ⑤ 用 asyncio.run 执行 raw httpx 兜底（httpx 异步
            # API 更完整，同步版只是在这里借用事件循环跑一次）
            import asyncio as _asyncio
            bare = _clone_chat_openai_without_tools(instance)
            try:
                result = _asyncio.run(_raw_httpx_chat_as_aimessage(bare, messages))
            except Exception as exc:  # noqa: BLE001
                import traceback as _tb
                logger.warning(
                    "同步版 invoke 兜底五步全部失败，model=%s: %s: %s\n%s",
                    getattr(instance, "model", "?"),
                    type(exc).__name__,
                    str(exc)[:200],
                    _tb.format_exc(),
                )
                return None
    except Exception as exc:
        import traceback as _tb
        logger.warning(
            "同步版零 chunk 兜底异常 model=%s: %s: %s\n%s",
            getattr(instance, "model", "?"),
            type(exc).__name__,
            str(exc)[:200],
            _tb.format_exc(),
        )
        return None
    content = result.content if isinstance(result.content, str) else str(result.content)
    if not content and not getattr(result, "tool_calls", None):
        return None
    from langchain_core.messages import AIMessageChunk
    tool_calls = getattr(result, "tool_calls", None) or []
    tcc = []
    for tc in tool_calls:
        tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
        if isinstance(args, dict):
            import json as _json
            args_str = _json.dumps(args, ensure_ascii=False)
        elif args is None:
            args_str = ""
        else:
            args_str = str(args)
        tcc.append({"name": name, "args": args_str, "id": tid, "index": 0})
    chunk = AIMessageChunk(content=content, tool_call_chunks=tcc)
    return chunk


def _chat_result_from_aimessage(aimsg):
    """把 AIMessage / AIMessageChunk 封装成 ChatResult（供 _agenerate/_generate 返回）。"""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    if isinstance(aimsg, AIMessage):
        gen_msg = aimsg
    else:
        gen_msg = AIMessage(
            content=aimsg.content if isinstance(aimsg.content, str) else str(aimsg.content),
            tool_calls=list(getattr(aimsg, "tool_calls", None) or []),
        )
    return ChatResult(
        generations=[ChatGeneration(message=gen_msg)],
        llm_output={
            "token_usage": {},
            "model_name": getattr(aimsg, "model", "") or "",
        },
    )


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是"后值覆盖"：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在"进行中"。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    if not message:
        return
    chunks = getattr(message, "tool_call_chunks", None) or []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None


def _resolve_openai_base_url(instance: ChatOpenAI) -> str | None:
    """从 ChatOpenAI 实例解析 base_url（兼容 langchain-openai 多版本字段名）。"""
    base_url = None
    for attr in ("openai_api_base", "base_url", "_base_url"):
        base_url = getattr(instance, attr, None)
        if base_url is not None:
            break
    if base_url is None:
        for client_attr in ("root_client", "client"):
            client = getattr(instance, client_attr, None)
            if client is None:
                continue
            for sub_attr in ("_base_url", "base_url"):
                v = getattr(client, sub_attr, None)
                if v is not None:
                    base_url = v
                    break
            if base_url is not None:
                break
            inner = getattr(client, "_client", None)
            if inner is not None:
                v = getattr(inner, "base_url", None)
                if v is not None:
                    base_url = v
                    break
    if base_url is None:
        return None
    if hasattr(base_url, "get_secret_value"):
        base_url = base_url.get_secret_value()
    return str(base_url)


def _empty_stream_error_msg(instance: ChatOpenAI) -> str:
    """构造零 chunk 空流的明确错误信息。"""
    model = getattr(instance, "model", None) or getattr(instance, "model_name", None) or "unknown"
    base_url_str = _resolve_openai_base_url(instance) or "unknown"
    provider_hint = "unknown"
    try:
        import re as _re
        m = _re.search(r"://([^/]+)", base_url_str)
        if m:
            provider_hint = m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return (
        f"模型流式返回空响应（零 chunk），model={model}, "
        f"provider={provider_hint}, base_url={base_url_str}. "
        "常见原因：网关/提供商偶发返回 200 OK 但 SSE body 为空、"
        "限流/鉴权失败时未返回错误码、服务端异常吞掉了响应体。"
        f"若高频出现请联系网关方 ({provider_hint}) 排查。"
    )


def _clone_chat_openai_without_tools(instance: ChatOpenAI) -> ChatOpenAI:
    """克隆一个同配置但**绝对不绑定 tools** 的 ChatOpenAI 原生实例。

    - 优先通过 model_dump(by_alias=True) 拿字段（能正确处理 SecretStr / 别名）；
    - 如果 model_dump() 因 BaseTool 等不可序列化对象抛异常，则 fallback 到
      手工挑取若干「绝对安全」的字段，保证至少可以发起纯文本请求。
    - 返回值是 langchain_openai.ChatOpenAI **原生实例**（不是我们的子类），
      避免再次触发兜底递归。
    """
    used_method = "hand-pick fields"
    clone_kwargs: dict = {}
    try:
        dump = instance.model_dump(by_alias=True, warnings=False)
        if isinstance(dump, dict):
            used_method = "model_dump(by_alias=True)"
            # 移除所有与 tools 绑定相关的字段
            for bad_key in (
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "model_kwargs",  # 里面可能塞了 tools
            ):
                dump.pop(bad_key, None)
            # 只保留 ChatOpenAI 构造函数实际可接受的标量/简单字段
            allowed = {
                "model",
                "model_name",
                "api_key",
                "openai_api_key",
                "base_url",
                "openai_api_base",
                "temperature",
                "max_tokens",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "timeout",
                "max_retries",
                "stop",
                "stream_usage",
                "streaming",
                "organization",
                "openai_organization",
                "default_headers",
                "default_query",
                "http_client",
                "http_async_client",
                "response_format",
            }
            clone_kwargs = {k: v for k, v in dump.items() if k in allowed and v is not None}
            # 确保至少有 api_key 字段（别名优先）
            if "api_key" not in clone_kwargs and "openai_api_key" in clone_kwargs:
                clone_kwargs["api_key"] = clone_kwargs.pop("openai_api_key")
            if "base_url" not in clone_kwargs and "openai_api_base" in clone_kwargs:
                clone_kwargs["base_url"] = clone_kwargs.pop("openai_api_base")
    except Exception:  # noqa: BLE001 — model_dump 因不可序列化 BaseTool 等失败
        # —— fallback：手挑字段 ——
        def _get_first(*attrs):
            for a in attrs:
                v = getattr(instance, a, None)
                if v is not None:
                    return v
            return None

        model_val = _get_first("model", "model_name")
        api_key = _get_first("openai_api_key", "api_key")
        base_url = _resolve_openai_base_url(instance)
        clone_kwargs = {}
        if model_val is not None:
            clone_kwargs["model"] = model_val
        if api_key is not None:
            clone_kwargs["api_key"] = api_key
        if base_url:
            clone_kwargs["base_url"] = base_url
        for attr, default in (
            ("temperature", None),
            ("max_tokens", None),
            ("top_p", None),
            ("frequency_penalty", None),
            ("presence_penalty", None),
            ("timeout", None),
            ("max_retries", None),
            ("stop", None),
        ):
            v = getattr(instance, attr, None)
            if v is not None and v != default:
                clone_kwargs[attr] = v

    # 强制确保绝对不包含 tools，并强制禁用 streaming，避免 ainvoke 内部走 astream 路径
    clone_kwargs.pop("tools", None)
    clone_kwargs.pop("tool_choice", None)
    clone_kwargs.pop("parallel_tool_calls", None)
    clone_kwargs["streaming"] = False
    clone_kwargs["stream_usage"] = False

    logger.debug(
        "_clone_chat_openai_without_tools: 使用 %s 构造裸 ChatOpenAI(model=%s, base_url=%s, has_api_key=%s)",
        used_method,
        clone_kwargs.get("model"),
        clone_kwargs.get("base_url") or clone_kwargs.get("openai_api_base"),
        (clone_kwargs.get("api_key") or clone_kwargs.get("openai_api_key")) is not None,
    )
    return ChatOpenAI(**clone_kwargs)


def _messages_to_openai_payload(messages) -> list[dict]:
    """把 LangChain messages 序列化为 OpenAI /chat/completions 的 messages 数组。"""
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        FunctionMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, BaseMessage):
            continue
        role_map = {
            SystemMessage: "system",
            HumanMessage: "user",
            AIMessage: "assistant",
            ToolMessage: "tool",
            FunctionMessage: "function",
        }
        role = None
        for cls, r in role_map.items():
            if isinstance(msg, cls):
                role = r
                break
        if role is None:
            role = getattr(msg, "type", "user")
        content = msg.content
        if not isinstance(content, str):
            try:
                content = str(content)
            except Exception:  # noqa: BLE001
                content = ""
        entry: dict = {"role": role, "content": content}
        if isinstance(msg, ToolMessage):
            tid = getattr(msg, "tool_call_id", None)
            if isinstance(tid, str) and tid:
                entry["tool_call_id"] = tid
        if isinstance(msg, FunctionMessage):
            nm = getattr(msg, "name", None)
            if isinstance(nm, str) and nm:
                entry["name"] = nm
        out.append(entry)
    return out


async def _raw_httpx_chat_as_aimessage(bare_model: ChatOpenAI, messages):
    """终极兜底：绕过 LangChain，直接用 httpx 发 /chat/completions 请求。"""
    import json as _json_dbg
    import sys as _sys_dbg

    import httpx
    from langchain_core.messages import AIMessage

    base_url = _resolve_openai_base_url(bare_model) or ""
    if not base_url:
        raise RuntimeError("无法解析模型 base_url，无法执行 raw httpx 兜底")
    if not base_url.endswith("/"):
        base_url += "/"
    chat_endpoint = f"{base_url}chat/completions"

    api_key_obj = (
        getattr(bare_model, "openai_api_key", None)
        or getattr(bare_model, "api_key", None)
    )
    api_key: str | None = None
    if api_key_obj is not None and hasattr(api_key_obj, "get_secret_value"):
        api_key = api_key_obj.get_secret_value()
    elif isinstance(api_key_obj, str):
        api_key = api_key_obj

    model_name = getattr(bare_model, "model", None) or ""
    payload_messages = _messages_to_openai_payload(messages)
    payload: dict = {
        "model": model_name,
        "messages": payload_messages,
        "stream": False,
    }
    temperature = getattr(bare_model, "temperature", None)
    if temperature is not None:
        payload["temperature"] = temperature

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(getattr(bare_model, "timeout", 180) or 180)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(chat_endpoint, headers=headers, json=payload)
        raw_text = resp.text or ""
        try:
            data = resp.json()
        except Exception as je:
            snippet = raw_text[:1200]
            _sys_dbg.stderr.write(
                "\n[STEP ⑤ RAW HTTPX] 响应非 JSON: "
                f"HTTP {resp.status_code} len={len(raw_text)} body={snippet!r}\n\n"
            )
            _sys_dbg.stderr.flush()
            raise RuntimeError(
                f"raw httpx 兜底响应非 JSON (HTTP {resp.status_code}): {snippet!r}"
            ) from je

    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and len(choices) > 0:
        first = choices[0]
        if isinstance(first, dict):
            msg_obj = first.get("message")
            if isinstance(msg_obj, dict):
                content = msg_obj.get("content") or ""
                if isinstance(content, str):
                    content_str = content
                else:
                    content_str = str(content) if content is not None else ""
                return AIMessage(content=content_str)

    code = data.get("code") if isinstance(data, dict) else None
    message = data.get("message") if isinstance(data, dict) else None
    extra = data.get("data") if isinstance(data, dict) else None
    provider = ""
    try:
        import re as _re
        m = _re.search(r"://([^/]+)", base_url)
        if m:
            provider = m.group(1)
    except Exception:  # noqa: BLE001
        pass

    # 把完整错误响应 dump 到 stderr 便于排查网关真实返回了什么业务错误
    try:
        _sys_dbg.stderr.write(
            "\n[STEP ⑤ RAW HTTPX] 网关返回业务错误体/choices 非法：\n"
            + f"  HTTP {resp.status_code} len={len(raw_text)}\n"
            + f"  code={code!r}  message={message!r}\n"
            + f"  choices={str(choices)[:500]!r}\n"
            + f"  data keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}\n"
            + f"  payload keys: model={payload['model']!r} num_messages={len(payload_messages)} "
            + f"total_chars(prompt)={sum(len(str(m.get('content',''))) for m in payload_messages)}\n"
            + "  full JSON dump:\n"
            + _json_dbg.dumps(data, ensure_ascii=False, indent=2)[:5000]
            + "\n\n"
        )
        _sys_dbg.stderr.flush()
    except Exception:  # noqa: BLE001
        pass

    if code is not None or message is not None:
        err_hint = (
            f"网关返回业务错误：code={code!r} message={message!r}"
            + (f" data={str(extra)[:400]!r}" if extra else "")
            + (f" provider={provider!r}" if provider else "")
        )
        raise RuntimeError(err_hint)

    raise TypeError(
        f"raw httpx 兜底仍然无法解析响应：choices={choices!r} "
        f"keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}"
    )
