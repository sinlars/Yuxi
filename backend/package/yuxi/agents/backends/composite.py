from __future__ import annotations

import json
import re
from dataclasses import dataclass

from deepagents.backends import CompositeBackend
from deepagents.middleware.filesystem import (
    TOOLS_EXCLUDED_FROM_EVICTION,
    FilesystemMiddleware,
    FsToolName,
)
from langchain_core.messages import ToolMessage

from yuxi.agents.backends.paths import runtime_workdir_path
from yuxi.agents.skills.service import refresh_user_skill_projection_async

from .sandbox import ProvisionerSandboxBackend

# Yuxi 在 DeepAgents 内建排除集之上额外豁免知识库文档工具结果，
# 避免 read_file/offload 循环：该工具自带分页与引用语义。
_TOOL_RESULT_EVICTION_EXEMPT_TOOLS = frozenset(TOOLS_EXCLUDED_FROM_EVICTION) | {"open_kb_document"}

# 文件工具 allowlist：显式排除 destructive delete。Yuxi backend 未实现 delete，
# 且删除语义需要审批与审计设计，开放前不应让模型看到该工具。
_AGENT_FS_TOOLS: tuple[FsToolName, ...] = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
)

_CITATION_MANIFEST_START = "<yuxi-citation-manifest-v1>"
_CITATION_MANIFEST_END = "</yuxi-citation-manifest-v1>"
_CITATION_EXCERPT_CHARS = 3000
_CITATION_IMAGE_LIMIT = 4


def _parse_tool_message_content(message: ToolMessage):
    content = message.content
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def _attach_inline_citation_sources(tool_name: str, message: ToolMessage) -> ToolMessage:
    """直接在普通大小的网络搜索结果中补齐稳定来源 URL，避免正文引用缺失。"""
    if "tavily_search" not in str(tool_name or "").lower():
        return message

    payload = _parse_tool_message_content(message)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return message

    changed = False
    results = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            results.append(item)
            continue
        url = str(item.get("url") or "").strip()
        if not url or item.get("citation_source") == url:
            results.append(item)
            continue
        results.append({**item, "citation_source": url})
        changed = True

    if not changed:
        return message
    normalized_payload = {**payload, "results": results}
    content = (
        json.dumps(normalized_payload, ensure_ascii=False)
        if isinstance(message.content, str)
        else normalized_payload
    )
    return message.model_copy(update={"content": content})


def _compact_citation_item(item: dict, *, kb_id: str = "", file_id: str = "") -> dict | None:
    citation_source = str(item.get("citation_source") or "").strip()
    content = str(item.get("content") or "").strip()
    if not citation_source or not content:
        return None

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    compact_metadata = {
        key: metadata[key]
        for key in (
            "source",
            "file_name",
            "filename",
            "file_id",
            "chunk_id",
            "chunk_index",
            "score",
            "start_line",
            "end_line",
        )
        if metadata.get(key) is not None
    }
    excerpt = content[:_CITATION_EXCERPT_CHARS]
    # 图片可能位于长片段尾部；单独保留少量 Markdown 图片，供引用气泡恢复图文来源。
    for image_markdown in re.findall(r"!\[[^\]]*\]\([^\n)]+\)", content)[:_CITATION_IMAGE_LIMIT]:
        if image_markdown not in excerpt:
            excerpt = f"{excerpt}\n\n{image_markdown}"

    compact = {
        "id": str(item.get("id") or metadata.get("chunk_id") or ""),
        "kb_id": str(item.get("kb_id") or kb_id or ""),
        "file_id": str(item.get("file_id") or metadata.get("file_id") or file_id or ""),
        "citation_source": citation_source,
        "content": excerpt,
        "metadata": compact_metadata,
    }
    if isinstance(item.get("score"), (int, float)):
        compact["score"] = item["score"]
    for key in ("start_line", "end_line"):
        if isinstance(item.get(key), int):
            compact[key] = item[key]
    return compact


def _build_citation_manifest(tool_name: str, message: ToolMessage) -> dict | None:
    payload = _parse_tool_message_content(message)
    if not isinstance(payload, dict):
        return None

    normalized_name = str(tool_name or "").lower()
    if normalized_name in {"query_kb", "find_kb_document", "open_kb_document"}:
        kb_id = str(payload.get("kb_id") or "")
        file_id = str(payload.get("file_id") or "")
        if isinstance(payload.get("results"), list):
            raw_items = payload["results"]
        elif isinstance(payload.get("windows"), list):
            raw_items = payload["windows"]
        else:
            raw_items = [payload]
        chunks = [
            compact
            for item in raw_items
            if isinstance(item, dict)
            and (compact := _compact_citation_item(item, kb_id=kb_id, file_id=file_id)) is not None
        ]
        return {"version": 1, "knowledge_chunks": chunks} if chunks else None

    if "tavily_search" in normalized_name and isinstance(payload.get("results"), list):
        sources = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url:
                continue
            source = {
                "title": title,
                "url": url,
                "citation_source": url,
                "content": str(item.get("content") or "")[:_CITATION_EXCERPT_CHARS],
            }
            for key in ("score", "published_date"):
                if item.get(key) is not None:
                    source[key] = item[key]
            sources.append(source)
        return {"version": 1, "web_sources": sources} if sources else None
    return None


def _attach_citation_manifest(message: ToolMessage, manifest: dict | None) -> ToolMessage:
    if not manifest or not isinstance(message.content, str):
        return message
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    content = f"{message.content}\n\n{_CITATION_MANIFEST_START}{encoded}{_CITATION_MANIFEST_END}"
    return message.model_copy(update={"content": content})


class YuxiFilesystemMiddleware(FilesystemMiddleware):
    """Filesystem middleware that budgets large tool outputs before they hit model context.

    注意：这里不使用 @dataclass，是因为 FilesystemMiddleware.__init__ 有大量
    keyword-only 参数（backend、tools、tool_token_limit_before_evict 等），
    使用 @dataclass 继承会使 dataclass 生成的 __init__ 遮蔽父类构造函数，
    导致 TypeError: got an unexpected keyword argument 'backend'。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def wrap_tool_call(self, request, handler):
        tool_result = handler(request)
        normalized_result = (
            _attach_inline_citation_sources(request.tool_call["name"], tool_result)
            if isinstance(tool_result, ToolMessage)
            else tool_result
        )

        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return normalized_result
        if self._tool_token_limit_before_evict is None:
            return normalized_result

        processed_result = self._intercept_large_tool_result(normalized_result)
        if isinstance(normalized_result, ToolMessage) and processed_result is not normalized_result:
            manifest = _build_citation_manifest(request.tool_call["name"], normalized_result)
            return _attach_citation_manifest(processed_result, manifest)
        return processed_result

    async def awrap_tool_call(self, request, handler):
        tool_result = await handler(request)
        normalized_result = (
            _attach_inline_citation_sources(request.tool_call["name"], tool_result)
            if isinstance(tool_result, ToolMessage)
            else tool_result
        )

        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return normalized_result
        if self._tool_token_limit_before_evict is None:
            return normalized_result

        processed_result = await self._aintercept_large_tool_result(normalized_result)
        if isinstance(normalized_result, ToolMessage) and processed_result is not normalized_result:
            manifest = _build_citation_manifest(request.tool_call["name"], normalized_result)
            return _attach_citation_manifest(processed_result, manifest)
        return processed_result


@dataclass(frozen=True)
class _BackendScope:
    runtime_scope_id: str
    workdir_relative_path: str
    uid: str

    @property
    def workdir_path(self) -> str:
        return runtime_workdir_path(self.workdir_relative_path)

    @classmethod
    def from_sources(cls, *sources, error_context: str) -> _BackendScope:
        def string_value(key: str) -> str | None:
            for source in sources:
                value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        thread_id = string_value("thread_id")
        if not thread_id:
            raise ValueError(f"thread_id is required in {error_context}")

        uid = string_value("uid")
        if not uid:
            raise ValueError(f"uid is required in {error_context}")

        runtime_scope_id = string_value("runtime_scope_id") or thread_id
        relative_path = string_value("workdir_relative_path") or ""
        return cls(
            runtime_scope_id=runtime_scope_id,
            workdir_relative_path=relative_path,
            uid=uid,
        )

    def create_backend(self) -> CompositeBackend:
        if not self.workdir_relative_path:
            raise ValueError("workdir path is required in runtime context")
        # artifacts_root 指向 outputs 目录：Filesystem/Summarization middleware 由此
        # 派生 large_tool_results 与 conversation_history 前缀，与 Yuxi 契约一致。
        return CompositeBackend(
            default=ProvisionerSandboxBackend(
                thread_id=self.runtime_scope_id,
                uid=self.uid,
                workdir_path=self.workdir_relative_path,
                create_if_missing=True,
            ),
            routes={},
            artifacts_root=f"{self.workdir_path.rstrip('/')}/outputs",
        )


async def sync_agent_context_skills(context) -> None:
    """在 Agent Run 初始化时同步当前用户获授权的共享 Skill 投影。"""
    scope = _BackendScope.from_sources(context, error_context="runtime context")
    await refresh_user_skill_projection_async(scope.uid)


def create_agent_composite_backend(context) -> CompositeBackend:
    """按已准备的 Agent context 构造本 Run 独享的 CompositeBackend 实例。

    DeepAgents 0.7 移除了 backend factory：每次 graph 构造时基于 context 创建
    具体实例，并由 filesystem 与 summary middleware 共用同一实例，保持
    user/thread/file_thread 的隔离边界。
    """
    return _BackendScope.from_sources(context, error_context="agent context").create_backend()


def create_agent_filesystem_middleware(
    tool_token_limit_before_evict: int | None = None,
    *,
    backend: CompositeBackend,
) -> FilesystemMiddleware:
    return YuxiFilesystemMiddleware(
        backend=backend,
        tool_token_limit_before_evict=tool_token_limit_before_evict,
        tools=list(_AGENT_FS_TOOLS),
    )
