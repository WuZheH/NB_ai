"""Pure contract helpers for future book/chapter object import flows.

This module intentionally has no database dependency. It documents and tests
the minimal schema semantics needed before a real migration is designed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


DOCUMENT_TYPE_PAPER = "paper"
DOCUMENT_TYPE_BOOK = "book"
DOCUMENT_TYPES = frozenset({DOCUMENT_TYPE_PAPER, DOCUMENT_TYPE_BOOK})

OBJECT_IMPORT_MODE_FULL_DOCUMENT = "full_document"
OBJECT_IMPORT_MODE_CHAPTERED = "chaptered"
OBJECT_IMPORT_MODE_OCR_LAYOUT_FIRST = "ocr_layout_first"
OBJECT_IMPORT_MODES = frozenset(
    {OBJECT_IMPORT_MODE_FULL_DOCUMENT, OBJECT_IMPORT_MODE_CHAPTERED, OBJECT_IMPORT_MODE_OCR_LAYOUT_FIRST}
)

DOCUMENT_OBJECT_IMPORT_STATUS_OPEN = "open"
DOCUMENT_OBJECT_IMPORT_STATUS_LOCKED = "locked"
DOCUMENT_OBJECT_IMPORT_STATUSES = frozenset(
    {DOCUMENT_OBJECT_IMPORT_STATUS_OPEN, DOCUMENT_OBJECT_IMPORT_STATUS_LOCKED}
)

CHAPTER_STATUS_NOT_STARTED = "not_started"
CHAPTER_STATUS_BUNDLE_GENERATED = "bundle_generated"
CHAPTER_STATUS_JSON_PASTED = "json_pasted"
CHAPTER_STATUS_COMMITTED = "committed"
CHAPTER_STATUS_SKIPPED = "skipped"
BOOK_CHAPTER_STATUSES = frozenset(
    {
        CHAPTER_STATUS_NOT_STARTED,
        CHAPTER_STATUS_BUNDLE_GENERATED,
        CHAPTER_STATUS_JSON_PASTED,
        CHAPTER_STATUS_COMMITTED,
        CHAPTER_STATUS_SKIPPED,
    }
)
BOOK_CHAPTER_TERMINAL_STATUSES = frozenset(
    {CHAPTER_STATUS_COMMITTED, CHAPTER_STATUS_SKIPPED}
)


@dataclass(frozen=True)
class BookChapterContract:
    chapter_id: int | str
    chapter_index: int
    title: str
    object_import_status: str = CHAPTER_STATUS_NOT_STARTED
    heading_path: str | None = None
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    object_count: int = 0
    evidence_count: int = 0

    def __post_init__(self) -> None:
        validate_chapter_status(self.object_import_status)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PdfLayoutBlock:
    pdf_page: int
    block_index: int
    block_type: str
    text: str
    normalized_text: str
    bbox: dict[str, float]
    source_backend: str
    document_id: int | None = None
    page_width: float | None = None
    page_height: float | None = None
    backend_version: str | None = None
    polygon: list[dict[str, float]] | None = None
    confidence: float | None = None
    text_hash: str | None = None
    source_block_id: str | None = None
    source_coordinate_space: str = "pdf_page"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PdfLayoutLine:
    pdf_page: int
    block_index: int
    line_index: int
    text: str
    normalized_text: str
    bbox: dict[str, float]
    source_backend: str
    document_id: int | None = None
    page_width: float | None = None
    page_height: float | None = None
    block_id: int | None = None
    confidence: float | None = None
    text_hash: str | None = None
    source_block_id: str | None = None
    source_coordinate_space: str = "pdf_page"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PdfLayoutSpan:
    pdf_page: int
    block_index: int
    line_index: int | None
    span_index: int
    text: str
    normalized_text: str
    bbox: dict[str, float]
    source_backend: str
    document_id: int | None = None
    block_id: int | None = None
    line_id: int | None = None
    confidence: float | None = None
    text_hash: str | None = None
    source_block_id: str | None = None
    source_coordinate_space: str = "pdf_page"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkLayoutLink:
    chunk_id: int
    document_id: int
    pdf_page: int
    block_id: int
    match_method: str
    overlap_score: float
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkLayoutLineLink:
    chunk_id: int
    document_id: int
    pdf_page: int
    line_id: int
    match_method: str
    overlap_score: float
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_document_type(document_type: str) -> str:
    if document_type not in DOCUMENT_TYPES:
        raise ValueError(f"unsupported document_type: {document_type!r}")
    return document_type


def validate_object_import_mode(import_mode: str) -> str:
    if import_mode not in OBJECT_IMPORT_MODES:
        raise ValueError(f"unsupported object_import_mode: {import_mode!r}")
    return import_mode


def validate_chapter_status(status: str) -> str:
    if status not in BOOK_CHAPTER_STATUSES:
        raise ValueError(f"unsupported book chapter status: {status!r}")
    return status


def requires_chapter_binding(document_type: str, object_import_mode: str | None = None) -> bool:
    validate_document_type(document_type)
    if object_import_mode is not None:
        validate_object_import_mode(object_import_mode)
    return document_type == DOCUMENT_TYPE_BOOK or object_import_mode == OBJECT_IMPORT_MODE_CHAPTERED


def is_valid_chapter_binding(
    *,
    document_type: str,
    chapter_id: int | str | None,
    object_import_mode: str | None = None,
) -> bool:
    if not requires_chapter_binding(document_type, object_import_mode):
        return True
    return chapter_id is not None and str(chapter_id).strip() != ""


def is_chapter_terminal(chapter: BookChapterContract | dict[str, Any]) -> bool:
    return _chapter_status(chapter) in BOOK_CHAPTER_TERMINAL_STATUSES


def select_next_chapter(
    chapters: Iterable[BookChapterContract | dict[str, Any]],
) -> BookChapterContract | dict[str, Any] | None:
    ordered = _ordered_chapters(chapters)
    if not ordered:
        return None

    for status in (
        CHAPTER_STATUS_JSON_PASTED,
        CHAPTER_STATUS_BUNDLE_GENERATED,
        CHAPTER_STATUS_NOT_STARTED,
    ):
        matches = [chapter for chapter in ordered if _chapter_status(chapter) == status]
        if matches:
            return matches[0]
    return None


def calculate_book_object_import_progress(
    chapters: Iterable[BookChapterContract | dict[str, Any]],
) -> dict[str, Any]:
    ordered = _ordered_chapters(chapters)
    counts = {status: 0 for status in BOOK_CHAPTER_STATUSES}
    for chapter in ordered:
        counts[_chapter_status(chapter)] += 1

    next_chapter = select_next_chapter(ordered)
    completed_count = (
        counts[CHAPTER_STATUS_COMMITTED] + counts[CHAPTER_STATUS_SKIPPED]
    )
    return {
        "total_count": len(ordered),
        "completed_count": completed_count,
        "committed_count": counts[CHAPTER_STATUS_COMMITTED],
        "skipped_count": counts[CHAPTER_STATUS_SKIPPED],
        "not_started_count": counts[CHAPTER_STATUS_NOT_STARTED],
        "bundle_generated_count": counts[CHAPTER_STATUS_BUNDLE_GENERATED],
        "json_pasted_count": counts[CHAPTER_STATUS_JSON_PASTED],
        "done": bool(ordered) and completed_count == len(ordered),
        "next_chapter": next_chapter,
    }


def _ordered_chapters(
    chapters: Iterable[BookChapterContract | dict[str, Any]],
) -> list[BookChapterContract | dict[str, Any]]:
    ordered = list(chapters)
    for chapter in ordered:
        validate_chapter_status(_chapter_status(chapter))
    return sorted(ordered, key=_chapter_index)


def _chapter_status(chapter: BookChapterContract | dict[str, Any]) -> str:
    if isinstance(chapter, BookChapterContract):
        return chapter.object_import_status
    return str(chapter.get("object_import_status", CHAPTER_STATUS_NOT_STARTED))


def _chapter_index(chapter: BookChapterContract | dict[str, Any]) -> int:
    if isinstance(chapter, BookChapterContract):
        return chapter.chapter_index
    return int(chapter.get("chapter_index", 0))
