from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import canonical_source_locator
from app.services.retrieval.fragment_normalizer import normalize_text
from app.services.retrieval.metadata_resolver import (
    ResolvedSourceMetadata,
    RetrievalMetadataResolver,
)
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "markdown_note_adapter.v1"
_FILENAME_STOPWORDS = {
    "paper",
    "card",
    "seed",
    "note",
    "evidence",
    "chain",
    "gap",
    "log",
    "marker",
    "local",
}


@dataclass(frozen=True)
class MarkdownBlock:
    ordinal: int
    line_start: int
    line_end: int
    heading_path: tuple[str, ...]
    text: str


def read_markdown_note_fragments(
    notes_root: Path,
    resolver: RetrievalMetadataResolver,
    *,
    project_root: Path,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    selected_ids = {int(value) for value in (document_ids or [])}
    fragments: list[RetrievalFragment] = []
    if not notes_root.is_dir():
        return fragments

    for path in sorted(notes_root.rglob("*.md"), key=lambda item: item.as_posix().casefold()):
        raw = path.read_text(encoding="utf-8")
        blocks = parse_markdown_blocks(raw)
        if not blocks:
            continue
        relative_path = path.resolve().relative_to(project_root.resolve()).as_posix()
        first_heading = next(
            (heading for block in blocks for heading in block.heading_path[:1]),
            path.stem,
        )
        metadata, mapping_warnings = _resolve_markdown_metadata(
            resolver,
            path=path,
            raw=raw,
            first_heading=first_heading,
        )
        if selected_ids and metadata.document_id not in selected_ids:
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

        for block in blocks:
            heading_path = list(block.heading_path)
            locator = canonical_source_locator(
                "markdown_note",
                relative_path=relative_path,
                heading_path=" > ".join(heading_path) or "(root)",
                block_ordinal=block.ordinal,
            )
            fragments.append(
                make_fragment(
                    source_type="markdown_note",
                    origin_kind="local_file",
                    source_record_id=f"{relative_path}#{block.ordinal}",
                    canonical_locator=locator,
                    text=block.text,
                    adapter_version=ADAPTER_VERSION,
                    metadata=metadata,
                    title=metadata.title or first_heading,
                    section=heading_path[-1] if heading_path else None,
                    heading_path=heading_path,
                    source_order=block.ordinal,
                    position={
                        "relative_path": relative_path,
                        "line_start": block.line_start,
                        "line_end": block.line_end,
                        "block_ordinal": block.ordinal,
                    },
                    context_status="pending",
                    original_file_path=str(path.resolve()),
                    source_updated_at=modified_at,
                    provenance=[
                        {
                            "store": "local_file",
                            "relative_path": relative_path,
                            "line_start": block.line_start,
                            "line_end": block.line_end,
                        }
                    ],
                    warnings=mapping_warnings,
                    raw_metadata={
                        "relative_path": relative_path,
                        "line_start": block.line_start,
                        "line_end": block.line_end,
                        "block_ordinal": block.ordinal,
                    },
                )
            )
    return fragments


def parse_markdown_blocks(raw: str) -> list[MarkdownBlock]:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heading_stack: list[str] = []
    blocks: list[MarkdownBlock] = []
    buffer: list[str] = []
    buffer_start = 0

    def flush(end_line: int) -> None:
        nonlocal buffer, buffer_start
        text = normalize_text("\n".join(buffer))
        if text:
            blocks.append(
                MarkdownBlock(
                    ordinal=len(blocks),
                    line_start=buffer_start,
                    line_end=end_line,
                    heading_path=tuple(heading_stack),
                    text=text,
                )
            )
        buffer = []
        buffer_start = 0

    for index, line in enumerate(lines, start=1):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush(index - 1)
            level = len(heading.group(1))
            title = normalize_text(heading.group(2), preserve_paragraphs=False)
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(title)
            continue
        list_item = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if list_item:
            flush(index - 1)
            blocks.append(
                MarkdownBlock(
                    ordinal=len(blocks),
                    line_start=index,
                    line_end=index,
                    heading_path=tuple(heading_stack),
                    text=normalize_text(list_item.group(1)),
                )
            )
            continue
        if not line.strip():
            flush(index - 1)
            continue
        if not buffer:
            buffer_start = index
        buffer.append(line)
    flush(len(lines))
    return blocks


def _resolve_markdown_metadata(
    resolver: RetrievalMetadataResolver,
    *,
    path: Path,
    raw: str,
    first_heading: str,
) -> tuple[ResolvedSourceMetadata, list[str]]:
    warnings: list[str] = []
    declared = re.search(r"\bdocument_id\s*=\s*(\d+)\b", raw, flags=re.IGNORECASE)
    if declared:
        document_id = int(declared.group(1))
        metadata = resolver.for_document(document_id)
        if metadata.mapping_status != "document_not_found":
            return metadata, warnings
        warnings.append(f"declared_document_id_not_found:{document_id}")

    aliases = [
        part
        for part in re.split(r"[^A-Za-z0-9]+", path.stem.casefold())
        if len(part) >= 3 and part not in _FILENAME_STOPWORDS
    ]
    acronym_candidates = _title_acronym_candidates(resolver, aliases)
    if len(acronym_candidates) == 1:
        return resolver.for_document(next(iter(acronym_candidates))), warnings
    if len(acronym_candidates) > 1:
        warnings.append("ambiguous_local_markdown_title_acronym")

    candidates: dict[int, int] = {}
    conn = resolver.research_conn
    for alias in aliases:
        rows = conn.execute(
            """
            SELECT document_id, chunk_text
            FROM knowledge_chunks
            WHERE chunk_index <= 12
              AND LOWER(chunk_text) LIKE ?
            """,
            (f"%{alias}%",),
        ).fetchall()
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)
        counts: dict[int, int] = {}
        for row in rows:
            occurrences = len(pattern.findall(str(row["chunk_text"] or "")))
            if occurrences:
                counts[int(row["document_id"])] = counts.get(int(row["document_id"]), 0) + occurrences
        strong = [document_id for document_id, count in counts.items() if count >= 2]
        if len(strong) == 1:
            candidates[strong[0]] = max(candidates.get(strong[0], 0), counts[strong[0]])

    if len(candidates) == 1:
        document_id = next(iter(candidates))
        return resolver.for_document(document_id), warnings
    if len(candidates) > 1:
        warnings.append("ambiguous_local_markdown_document_mapping")
    else:
        warnings.append("local_markdown_document_unmapped")
    return (
        ResolvedSourceMetadata(
            mapping_status="local_file_unmapped",
            title=first_heading,
            warnings=tuple(warnings),
            provenance=(
                {"store": "local_file", "path": str(path.resolve())},
            ),
        ),
        warnings,
    )


def _title_acronym_candidates(
    resolver: RetrievalMetadataResolver,
    aliases: list[str],
) -> set[int]:
    candidates: set[int] = set()
    for row in resolver.research_conn.execute("SELECT id, title FROM documents"):
        words = re.findall(r"[A-Za-z0-9]+", str(row["title"] or ""))
        for alias in aliases:
            if not alias.isalpha() or len(alias) < 3:
                continue
            width = len(alias)
            for start in range(0, len(words) - width + 1):
                initialism = "".join(word[0] for word in words[start : start + width])
                if initialism.casefold() == alias:
                    candidates.add(int(row["id"]))
                    break
            if int(row["id"]) in candidates:
                break
    return candidates
