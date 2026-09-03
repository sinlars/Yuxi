import re

from langchain_core.messages import convert_to_messages

from yuxi.agents.models import load_chat_model
from yuxi.models.providers.cache import model_cache
from yuxi.utils import logger


class GeneralResponse:
    def __init__(self, content):
        self.content = content
        self.is_full = False


# 匹配 "Thinking Process:" 前缀及后续编号步骤段落（Qwen3 等模型将思维链
# 放在 content 而非 reasoning_content 时使用）。
_THINKING_SECTION_RE = re.compile(r"^Thinking Process:\s*\n", re.IGNORECASE)
_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s")


def _strip_thinking(text: str) -> str:
    """剥离 Qwen3 等模型在 content 中输出的 "Thinking Process:" 思维链前缀。

    部分 OpenAI 兼容端点（如 uni-api）不使用标准 reasoning_content 字段，
    而是将思维链作为普通文本放在 content 开头。这会导致简单调用（标题生成、
    内容审查等）返回的文本包含 "Thinking Process:\\n\\n1. **Analy...**" 前缀。
    """
    if not text or not _THINKING_SECTION_RE.match(text):
        return text
    # 思维链由 "Thinking Process:\n\n" 开头，后跟若干编号步骤段落，
    # 最终答案在最后一个空行分隔的段落。从后往前找第一个非编号段落。
    for part in reversed(text.split("\n\n")):
        stripped = part.strip()
        if stripped and not _NUMBERED_ITEM_RE.match(stripped):
            return stripped
    # 兜底：返回最后一个非空段落
    for part in reversed(text.split("\n\n")):
        stripped = part.strip()
        if stripped:
            return stripped
    return text


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
            return GeneralResponse(_strip_thinking(response.text))
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


async def test_chat_model_status_by_spec(spec: str) -> dict:
    try:
        logger.debug(f"Testing model status by spec: {spec}")
        model = select_model(model_spec=spec)

        test_messages = [{"role": "user", "content": "Say 1"}]
        response = await model.call(test_messages, stream=False)

        if response and response.content:
            return {"spec": spec, "status": "available", "message": "连接正常"}
        return {"spec": spec, "status": "unavailable", "message": "响应无效"}

    except Exception as e:
        logger.error(f"测试模型状态失败 {spec}: {e}")
        return {"spec": spec, "status": "error", "message": str(e)}


if __name__ == "__main__":
    pass
