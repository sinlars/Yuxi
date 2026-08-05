from __future__ import annotations

import asyncio
from typing import Any

import json_repair

from yuxi.models.chat import select_model
from yuxi.utils import logger

from .base import GraphExtractor

DEFAULT_TRIPLE_EXTRACTION_PROMPT = """请从下面文本中抽取实体和实体关系，返回严格 JSON，不要输出解释。
JSON 格式：
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""

DEFAULT_REQUEST_TIMEOUT_SECONDS = 180
DEFAULT_TIMEOUT_RETRIES = 1


class LLMGraphExtractor(GraphExtractor):
    extractor_type = "llm"

    def validate_options(self) -> None:
        if not self.options.get("model_spec"):
            raise ValueError("LLM 抽取器需要 model_spec")
        if self.options.get("prompt"):
            raise ValueError("LLM 图谱抽取器不支持自定义完整 Prompt，请使用 schema 配置抽取约束")
        concurrency_count = self.options.get("concurrency_count", 1)
        try:
            concurrency_count = int(concurrency_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 concurrency_count 必须是整数") from exc
        if concurrency_count < 1 or concurrency_count > 1000:
            raise ValueError("LLM 抽取器 concurrency_count 必须在 1 到 1000 之间")
        try:
            request_timeout_seconds = float(
                self.options.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 request_timeout_seconds 必须是数字") from exc
        if request_timeout_seconds < 30 or request_timeout_seconds > 600:
            raise ValueError("LLM 抽取器 request_timeout_seconds 必须在 30 到 600 秒之间")
        try:
            timeout_retries = int(self.options.get("timeout_retries", DEFAULT_TIMEOUT_RETRIES))
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 timeout_retries 必须是整数") from exc
        if timeout_retries < 0 or timeout_retries > 3:
            raise ValueError("LLM 抽取器 timeout_retries 必须在 0 到 3 之间")
        if self.options.get("model_params") is not None and not isinstance(self.options["model_params"], dict):
            raise ValueError("LLM 抽取器 model_params 必须是对象")

    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.validate_options()
        request_timeout_seconds = float(
            self.options.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
        )
        timeout_retries = int(self.options.get("timeout_retries", DEFAULT_TIMEOUT_RETRIES))
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=request_timeout_seconds,
            model_params=self.options.get("model_params") or {},
        )
        prompt = self._build_prompt(text)
        for attempt in range(timeout_retries + 1):
            try:
                response = await model.call(prompt, stream=False)
                break
            except Exception as exc:
                if attempt >= timeout_retries or not _is_timeout_error(exc):
                    raise
                retry_number = attempt + 1
                logger.warning(
                    "图谱模型请求超时，准备重试 {}/{} chunk_id={}",
                    retry_number,
                    timeout_retries,
                    (chunk_metadata or {}).get("chunk_id", ""),
                )
                await asyncio.sleep(min(2**attempt, 5))
        parsed = json_repair.loads(response.content if response else "")
        return parsed

    def _build_prompt(self, text: str) -> str:
        extraction_prompt = DEFAULT_TRIPLE_EXTRACTION_PROMPT
        schema = str(self.options.get("schema") or "").strip()
        if schema:
            extraction_prompt = f"{extraction_prompt}\n{SCHEMA_INSTRUCTION.format(schema=schema)}"
        return f"{extraction_prompt}\n\n文本：\n{text}"


def _is_timeout_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        error_name = type(current).__name__.lower()
        error_message = str(current).lower()
        if "timeout" in error_name or "timed out" in error_message or "timeout" in error_message:
            return True
        current = current.__cause__ or current.__context__
    return False
