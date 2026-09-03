from __future__ import annotations

import re
from typing import Any

from yuxi.knowledge.chunking.ragflow_like import nlp


def _unescape_delimiter(delimiter: str) -> str:
    return delimiter.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t").replace("\\\\", "\\")


def _iter_sections(markdown_content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    for line in (markdown_content or "").splitlines():
        text = line.strip()
        if not text:
            continue
        sections.append((text, ""))

    if not sections and markdown_content and markdown_content.strip():
        sections.append((markdown_content.strip(), ""))

    return sections


def _ensure_chunk_token_limit(chunks: list[str], chunk_token_num: int) -> list[str]:
    max_tokens = int(chunk_token_num or 0)
    normalized = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if max_tokens <= 0:
        return normalized

    protected: list[str] = []
    for chunk in normalized:
        if nlp.count_tokens(chunk) <= max_tokens:
            protected.append(chunk)
        else:
            protected.extend(nlp.hard_split_by_token_limit(chunk, max_tokens))
    return protected


def _split_heading_context(section: str, bullet_category: int) -> tuple[list[str], list[str]]:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    heading_lines: list[str] = []

    for line in lines:
        is_heading = any(re.match(pattern, line) for pattern in nlp.BULLET_PATTERN[bullet_category])
        if not is_heading or not nlp.is_probable_heading_line(line):
            break
        heading_lines.append(line)

    return heading_lines, lines[len(heading_lines) :]


def _chunk_section(
    section: str,
    bullet_category: int,
    chunk_token_num: int,
    overlapped_percent: int,
) -> list[str]:
    headings, body_lines = _split_heading_context(section, bullet_category)
    if not body_lines:
        return _ensure_chunk_token_limit([section], chunk_token_num)

    heading_text = "\n".join(headings)
    heading_tokens = nlp.count_tokens(heading_text)
    body_token_limit = chunk_token_num - heading_tokens
    if body_token_limit <= 0:
        return nlp.hard_split_by_token_limit(section, chunk_token_num)

    body_parts: list[str] = []
    for line in body_lines:
        if nlp.count_tokens(line) <= body_token_limit:
            body_parts.append(line)
        else:
            body_parts.extend(nlp.hard_split_by_token_limit(line, body_token_limit))

    overlap_target = int(body_token_limit * max(0, min(overlapped_percent, 99)) / 100)
    chunks: list[str] = []
    current_lines: list[str] = []
    current_tokens = 0

    for part in body_parts:
        part_tokens = nlp.count_tokens(part)
        if current_lines and current_tokens + part_tokens > body_token_limit:
            chunks.append("\n".join([*headings, *current_lines]))

            overlap_lines: list[str] = []
            overlap_tokens = 0
            for previous_line in reversed(current_lines):
                previous_tokens = nlp.count_tokens(previous_line)
                if overlap_tokens + previous_tokens > overlap_target:
                    break
                overlap_lines.insert(0, previous_line)
                overlap_tokens += previous_tokens

            current_lines = overlap_lines
            current_tokens = overlap_tokens

            while current_lines and current_tokens + part_tokens > body_token_limit:
                current_tokens -= nlp.count_tokens(current_lines.pop(0))

        current_lines.append(part)
        current_tokens += part_tokens

    if current_lines:
        chunks.append("\n".join([*headings, *current_lines]))

    return _ensure_chunk_token_limit(chunks, chunk_token_num)


def chunk_markdown(markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    parser_config = parser_config or {}

    delimiter = _unescape_delimiter(str(parser_config.get("delimiter", "\n") or "\n"))
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    overlapped_percent = int(parser_config.get("overlapped_percent", 0) or 0)

    sections = _iter_sections(markdown_content)
    if not sections:
        return []

    section_texts = [text for text, _ in sections]
    nlp.remove_contents_table(
        sections,
        eng=nlp.is_english(section_texts),
        max_scan_lines=512,
    )
    nlp.make_colon_as_title(sections)

    bull = nlp.bullets_category([text for text, _ in sections])

    if bull >= 0:
        hierarchical_sections = nlp.tree_merge(bull, sections, depth=5)
        chunks = [
            chunk
            for section in hierarchical_sections
            for chunk in _chunk_section(
                section,
                bull,
                chunk_token_num,
                overlapped_percent,
            )
        ]
        if chunks:
            return chunks

    return _ensure_chunk_token_limit(
        nlp.naive_merge(
            sections,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            overlapped_percent=overlapped_percent,
        ),
        chunk_token_num,
    )
