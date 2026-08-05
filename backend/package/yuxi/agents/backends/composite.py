from __future__ import annotations

import json
import re
from dataclasses import dataclass

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import FileInfo, GlobResult
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import ToolMessage

from yuxi.agents.skills.service import normalize_string_list
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS, VIRTUAL_PATH_OUTPUTS

from .sandbox import ProvisionerSandboxBackend
from .skills_backend import SelectedSkillsReadonlyBackend

_TOOL_RESULT_EVICTION_EXEMPT_TOOLS = frozenset({"read_file", "open_kb_document"})
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
    """Expose stable source ids directly in normal-sized web search results."""
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


def _coerce_glob_result(result) -> GlobResult:
    if isinstance(result, GlobResult):
        return result
    return GlobResult(matches=result or [])


class CustomCompositeBackend(CompositeBackend):
    """修复 glob 路由逻辑的 CompositeBackend。"""

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(backend.glob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(self.default.glob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(backend.glob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(self.default.glob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(await backend.aglob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(await self.default.aglob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(await backend.aglob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(await self.default.aglob(pattern, path))


class YuxiFilesystemMiddleware(FilesystemMiddleware):
    """Filesystem middleware that budgets large tool outputs before they hit model context."""

    def wrap_tool_call(self, request, handler):
        tool_result = handler(request)
        normalized_result = (
            _attach_inline_citation_sources(request.tool_call["name"], tool_result)
            if isinstance(tool_result, ToolMessage)
            else tool_result
        )

        if self._tool_token_limit_before_evict is None:
            return normalized_result
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return normalized_result

        processed_result = self._intercept_large_tool_result(normalized_result, request.runtime)
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

        if self._tool_token_limit_before_evict is None:
            return normalized_result
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return normalized_result

        processed_result = await self._aintercept_large_tool_result(normalized_result, request.runtime)
        if isinstance(normalized_result, ToolMessage) and processed_result is not normalized_result:
            manifest = _build_citation_manifest(request.tool_call["name"], normalized_result)
            return _attach_citation_manifest(processed_result, manifest)
        return processed_result


@dataclass(frozen=True)
class _BackendScope:
    thread_id: str
    uid: str
    readable_skills: list[str]
    file_thread_id: str
    skills_thread_id: str

    @classmethod
    def from_runtime(cls, runtime) -> _BackendScope:
        config = getattr(runtime, "config", None)
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        context = getattr(runtime, "context", None)
        state = getattr(runtime, "state", None)
        return cls.from_sources(
            configurable if isinstance(configurable, dict) else {},
            context,
            state if isinstance(state, dict) else {},
            readable_skills_source=context,
            error_context="runtime configurable context",
        )

    @classmethod
    def from_sources(cls, *sources, readable_skills_source, error_context: str) -> _BackendScope:
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

        selected = getattr(readable_skills_source, "_readable_skills", [])
        return cls(
            thread_id=thread_id,
            uid=uid,
            readable_skills=normalize_string_list(selected if isinstance(selected, list) else []),
            file_thread_id=string_value("file_thread_id") or thread_id,
            skills_thread_id=string_value("skills_thread_id") or thread_id,
        )

    def create_backend(self) -> CompositeBackend:
        return CustomCompositeBackend(
            default=ProvisionerSandboxBackend(
                thread_id=self.thread_id,
                uid=self.uid,
                readable_skills=self.readable_skills,
                file_thread_id=self.file_thread_id,
                skills_thread_id=self.skills_thread_id,
            ),
            routes={
                "/skills/": SelectedSkillsReadonlyBackend(selected_slugs=self.readable_skills),
            },
            artifacts_root=VIRTUAL_PATH_OUTPUTS,
        )


def create_agent_composite_backend(runtime) -> CompositeBackend:
    return _BackendScope.from_runtime(runtime).create_backend()


def create_agent_filesystem_middleware(
    tool_token_limit_before_evict: int | None = None,
    *,
    context=None,
) -> FilesystemMiddleware:
    backend = create_agent_composite_backend
    if context is not None:
        backend = _BackendScope.from_sources(
            context,
            readable_skills_source=context,
            error_context="runtime context",
        ).create_backend()
    middleware = YuxiFilesystemMiddleware(
        backend=backend,
        tool_token_limit_before_evict=tool_token_limit_before_evict,
    )
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    middleware._conversation_history_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    return middleware
