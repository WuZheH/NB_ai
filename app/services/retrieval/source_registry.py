from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.core.database import connect_immutable_readonly_sqlite
from app.core.paths import DATA_PROJECT_ROOT, DEFAULT_DB_PATH, NOTES_DIR, ZOTERO_SNAPSHOT_PATH
from app.schemas.retrieval_fragment import RetrievalFragment, RetrievalSourceType
from app.services.retrieval.context_builder import build_fragment_contexts
from app.services.retrieval.metadata_resolver import RetrievalMetadataResolver
from app.services.retrieval.sources.markdown_note_adapter import read_markdown_note_fragments
from app.services.retrieval.sources.pdf_chunk_adapter import read_pdf_chunk_fragments
from app.services.retrieval.sources.personal_note_adapter import read_personal_note_fragments
from app.services.retrieval.sources.zotero_child_note_adapter import read_zotero_child_note_fragments
from app.services.retrieval.sources.zotero_inspiration_note_adapter import (
    read_zotero_inspiration_note_fragments,
)
from app.services.retrieval.sources.zotero_native_annotation_adapter import (
    read_zotero_native_annotation_fragments,
)


DEFAULT_ZOTERO_SNAPSHOT_PATH = ZOTERO_SNAPSHOT_PATH
DEFAULT_NOTES_ROOT = NOTES_DIR
SOURCE_REGISTRY_VERSION = "retrieval_source_registry.v1"
ALL_SOURCE_TYPES: tuple[RetrievalSourceType, ...] = (
    "pdf_chunk",
    "zotero_highlight",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
    "personal_note",
    "markdown_note",
)


class RetrievalIdentityCollisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalRegistryResult:
    fragments: tuple[RetrievalFragment, ...]
    source_counts: dict[str, int]
    origin_counts: dict[str, int]
    source_record_counts: dict[str, int]
    warnings: tuple[str, ...]
    read_only: bool = True

    def to_summary(self) -> dict[str, object]:
        return {
            "fragment_count": len(self.fragments),
            "source_counts": self.source_counts,
            "origin_counts": self.origin_counts,
            "source_record_counts": self.source_record_counts,
            "warnings": list(self.warnings),
            "read_only": self.read_only,
        }


class RetrievalSourceRegistry:
    def __init__(
        self,
        *,
        research_db_path: str | Path = DEFAULT_DB_PATH,
        zotero_snapshot_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
        notes_root: str | Path = DEFAULT_NOTES_ROOT,
        project_root: str | Path = DATA_PROJECT_ROOT,
    ) -> None:
        self.research_db_path = Path(research_db_path)
        self.zotero_snapshot_path = Path(zotero_snapshot_path)
        self.notes_root = Path(notes_root)
        self.project_root = Path(project_root)

    def read(
        self,
        *,
        source_types: Iterable[RetrievalSourceType] | None = None,
        document_ids: Iterable[int] | None = None,
        limit: int | None = None,
    ) -> RetrievalRegistryResult:
        requested = set(source_types or ALL_SOURCE_TYPES)
        unknown = requested.difference(ALL_SOURCE_TYPES)
        if unknown:
            raise ValueError(f"unknown retrieval source types: {sorted(unknown)}")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        selected_document_ids = (
            tuple(sorted({int(value) for value in document_ids}))
            if document_ids is not None else None
        )

        with ExitStack() as stack:
            research_conn = stack.enter_context(connect_readonly_sqlite(self.research_db_path))
            zotero_conn = (
                stack.enter_context(connect_readonly_sqlite(self.zotero_snapshot_path))
                if self.zotero_snapshot_path.is_file()
                else None
            )
            resolver = RetrievalMetadataResolver(research_conn, zotero_conn)
            fragments: list[RetrievalFragment] = []

            if "pdf_chunk" in requested:
                fragments.extend(
                    read_pdf_chunk_fragments(
                        research_conn,
                        resolver,
                        document_ids=selected_document_ids,
                    )
                )
            if requested.intersection({"zotero_highlight", "zotero_annotation_comment"}):
                if zotero_conn is not None:
                    native = read_zotero_native_annotation_fragments(
                        zotero_conn,
                        resolver,
                        document_ids=selected_document_ids,
                    )
                    fragments.extend(item for item in native if item.source_type in requested)
            if "zotero_child_note" in requested and zotero_conn is not None:
                fragments.extend(
                    read_zotero_child_note_fragments(
                        zotero_conn,
                        resolver,
                        document_ids=selected_document_ids,
                    )
                )
            if "zotero_inspiration_note" in requested:
                fragments.extend(
                    read_zotero_inspiration_note_fragments(
                        research_conn,
                        resolver,
                        document_ids=selected_document_ids,
                    )
                )
            if "personal_note" in requested:
                fragments.extend(
                    read_personal_note_fragments(
                        research_conn,
                        resolver,
                        document_ids=selected_document_ids,
                    )
                )
            if "markdown_note" in requested:
                fragments.extend(
                    read_markdown_note_fragments(
                        self.notes_root,
                        resolver,
                        project_root=self.project_root,
                        document_ids=selected_document_ids,
                    )
                )

            fragments = _mark_duplicate_candidates(fragments)
            fragments = build_fragment_contexts(fragments)
            _assert_unique_fragment_ids(fragments)
            source_record_counts = _source_record_counts(
                research_conn,
                zotero_conn,
                self.notes_root,
            )

        ordered = sorted(
            fragments,
            key=lambda item: (
                ALL_SOURCE_TYPES.index(item.source_type),
                item.document_id if item.document_id is not None else 2**31,
                item.source_order if item.source_order is not None else 2**31,
                item.fragment_id,
            ),
        )
        if limit is not None:
            ordered = ordered[:limit]
        source_counts = dict(sorted(Counter(item.source_type for item in ordered).items()))
        origin_counts = dict(sorted(Counter(item.origin_kind for item in ordered).items()))
        warnings = list(
            dict.fromkeys(
                warning
                for item in ordered
                for warning in item.warnings
            )
        )
        if selected_document_ids is None and limit is None:
            _append_source_parity_warnings(warnings, ordered, source_record_counts, requested)
        return RetrievalRegistryResult(
            fragments=tuple(ordered),
            source_counts=source_counts,
            origin_counts=origin_counts,
            source_record_counts=source_record_counts,
            warnings=tuple(warnings),
        )


def connect_readonly_sqlite(path: str | Path) -> sqlite3.Connection:
    return connect_immutable_readonly_sqlite(path)


def _mark_duplicate_candidates(
    fragments: list[RetrievalFragment],
) -> list[RetrievalFragment]:
    grouped: dict[str, list[RetrievalFragment]] = {}
    for fragment in fragments:
        if fragment.duplicate_group_id:
            grouped.setdefault(fragment.duplicate_group_id, []).append(fragment)

    updated: dict[str, RetrievalFragment] = {}
    for group in grouped.values():
        comparable = [
            item
            for item in group
            if item.source_type in {"zotero_highlight", "zotero_inspiration_note"}
        ]
        source_types = {item.source_type for item in comparable}
        if source_types != {"zotero_highlight", "zotero_inspiration_note"}:
            continue
        peer_ids = [item.fragment_id for item in comparable]
        for item in comparable:
            raw_metadata = {
                **item.raw_metadata,
                "duplicate_peer_fragment_ids": [
                    peer_id for peer_id in peer_ids if peer_id != item.fragment_id
                ],
            }
            updated[item.fragment_id] = item.model_copy(
                update={
                    "duplicate_candidate": True,
                    "raw_metadata": raw_metadata,
                }
            )
    return [updated.get(item.fragment_id, item) for item in fragments]


def _assert_unique_fragment_ids(fragments: list[RetrievalFragment]) -> None:
    counts = Counter(item.fragment_id for item in fragments)
    collisions = [fragment_id for fragment_id, count in counts.items() if count > 1]
    if collisions:
        raise RetrievalIdentityCollisionError(
            f"retrieval fragment identity collision: {collisions[:5]}"
        )


def _source_record_counts(
    research_conn: sqlite3.Connection,
    zotero_conn: sqlite3.Connection | None,
    notes_root: Path,
) -> dict[str, int]:
    counts = {
        "knowledge_chunks": _count(research_conn, "knowledge_chunks"),
        "zotero_inspiration_notes": _count(research_conn, "zotero_inspiration_notes"),
        "personal_notes": _count(research_conn, "personal_notes"),
        "zotero_native_annotations": 0,
        "zotero_child_notes": 0,
        "markdown_files": len(list(notes_root.rglob("*.md"))) if notes_root.is_dir() else 0,
    }
    if zotero_conn is not None:
        counts["zotero_native_annotations"] = int(
            zotero_conn.execute(
                """
                SELECT COUNT(*)
                FROM itemAnnotations AS annotation
                LEFT JOIN deletedItems AS deleted ON deleted.itemID = annotation.itemID
                WHERE deleted.itemID IS NULL
                """
            ).fetchone()[0]
        )
        counts["zotero_child_notes"] = int(
            zotero_conn.execute(
                """
                SELECT COUNT(*)
                FROM itemNotes AS note
                LEFT JOIN deletedItems AS deleted ON deleted.itemID = note.itemID
                WHERE note.parentItemID IS NOT NULL
                  AND deleted.itemID IS NULL
                """
            ).fetchone()[0]
        )
    return counts


def _count(conn: sqlite3.Connection, table: str) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else 0


def _append_source_parity_warnings(
    warnings: list[str],
    fragments: list[RetrievalFragment],
    source_records: dict[str, int],
    requested: set[RetrievalSourceType],
) -> None:
    annotation_keys = {
        item.zotero_annotation_key
        for item in fragments
        if item.source_type == "zotero_highlight"
        and item.zotero_annotation_key
    }
    annotation_count = source_records.get("zotero_native_annotations", 0)
    if "zotero_highlight" in requested and annotation_count and len(annotation_keys) < annotation_count:
        warnings.append(
            f"native_annotation_records_without_fragment:{annotation_count - len(annotation_keys)}"
        )
    child_count = sum(1 for item in fragments if item.source_type == "zotero_child_note")
    if "zotero_child_note" in requested and child_count < source_records.get("zotero_child_notes", 0):
        warnings.append(
            f"zotero_child_notes_without_fragment:{source_records['zotero_child_notes'] - child_count}"
        )
