"""Library DTOs and evidence-locator contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


METADATA_CHUNK_MARKERS = (
    "backend:",
    "page_count:",
    "pages_with_text:",
    "headings_detected:",
    "sections_detected:",
)


def normalize_evidence_text(text: str | None) -> str:
    return " ".join(str(text or "").split())


def is_metadata_chunk_text(text: str | None) -> bool:
    normalized = normalize_evidence_text(text).lower()
    if not normalized:
        return False
    if normalized.startswith("- backend:") or normalized.startswith("backend:"):
        return True
    marker_hits = sum(1 for marker in METADATA_CHUNK_MARKERS if marker in normalized)
    return marker_hits >= 3


def is_metadata_chunk(chunk: object) -> bool:
    return is_metadata_chunk_text(getattr(chunk, "chunk_text", None))


def evidence_locator_contract(
    *,
    chunk_text: str | None,
    pdf_page_start: int | None,
    pdf_path: str | None = None,
    is_metadata: bool | None = None,
) -> dict[str, object]:
    metadata = is_metadata_chunk_text(chunk_text) if is_metadata is None else bool(is_metadata)
    has_text = bool(normalize_evidence_text(chunk_text))
    if metadata:
        return {
            "is_metadata_chunk": True,
            "is_locatable": False,
            "locator_status": "metadata_non_locatable",
            "locator_reason": "该片段是抽取元信息，不支持 PDF 定位。",
            "match_method": "not_applicable",
            "highlight_count": 0,
        }
    if not pdf_page_start:
        return {
            "is_metadata_chunk": False,
            "is_locatable": False,
            "locator_status": "no_page",
            "locator_reason": "该片段缺少 PDF 页码，只能打开文档。",
            "match_method": "not_applicable",
            "highlight_count": 0,
        }
    if not has_text:
        return {
            "is_metadata_chunk": False,
            "is_locatable": False,
            "locator_status": "no_text",
            "locator_reason": "该片段缺少正文文本，无法定位。",
            "match_method": "not_applicable",
            "highlight_count": 0,
        }
    if not pdf_path:
        return {
            "is_metadata_chunk": False,
            "is_locatable": False,
            "locator_status": "pdf_missing",
            "locator_reason": "该片段缺少可预览 PDF，只能查看证据文本。",
            "match_method": "not_applicable",
            "highlight_count": 0,
        }
    return {
        "is_metadata_chunk": False,
        "is_locatable": True,
        "locator_status": "page_level_only",
        "locator_reason": "已具备页码和正文，可打开 PDF 页码并尝试精确定位。",
        "match_method": "page_hint",
        "highlight_count": 0,
    }


@dataclass(frozen=True)
class ReadLibraryDocumentSummary:
    document_id: int
    title: str
    document_type: str
    read_status: str
    research_direction: str | None
    pdf_path: str | None
    zotero_key: str | None
    chunk_count: int
    note_count: int


@dataclass(frozen=True)
class LibraryHomeItem:
    item_type: str
    item_id: int
    title: str
    document_type: str | None
    note_type: str | None
    read_status: str | None
    research_direction: str | None
    updated_at: datetime
    has_pdf: bool
    has_zotero: bool
    chunk_count: int
    note_count: int
    tag_count: int
    source_document_id: int | None
    object_import_mode: str | None
    object_import_status: str | None
    chapter_count: int
    pdf_path: str | None = None
    zotero_key: str | None = None


@dataclass(frozen=True)
class LibrarySearchResult:
    result_type: str
    id: int
    title: str
    snippet: str
    document_id: int | None
    document_title: str | None
    document_type: str | None
    note_type: str | None
    heading_path: str | None
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    tags: list[str]
    related_notes: list[str]
    related_relations: list[LibraryRelationItem]
    relation_summary: str | None

    @property
    def source_type(self) -> str:
        return self.result_type

    @property
    def source_id(self) -> int:
        return self.id

    @property
    def related_note_titles(self) -> list[str]:
        return self.related_notes


@dataclass(frozen=True)
class LibraryGroupedSearchChunk:
    chunk_id: int
    document_id: int
    document_title: str
    heading_path: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    section_path: list[str]
    section_label: str
    location_label: str
    heading_level: int | None
    snippet: str
    chunk_text: str
    is_metadata_chunk: bool
    is_locatable: bool
    locator_status: str
    locator_reason: str
    relevance_score: float
    relevance_label: str
    match_reasons: list[str]
    tags: list[str]
    related_relations: list["LibraryRelationItem"]


@dataclass(frozen=True)
class LibraryGroupedSearchDocument:
    document_id: int
    document_title: str
    document_type: str
    document_relevance_score: float
    document_relevance_label: str
    match_reasons: list[str]
    top_chunks: list[LibraryGroupedSearchChunk]


@dataclass(frozen=True)
class LibraryRelationItem:
    relation_id: int
    source_type: str
    source_id: int
    relation_type: str
    target_type: str
    target_id: int
    evidence_chunk_id: int | None
    note_id: int | None
    description: str | None
    confidence: float | None
    source_label: str
    target_label: str
    relation_label_zh: str
    evidence_pdf_page: int | None
    raw_relation: str


@dataclass(frozen=True)
class LibraryRelatedNoteItem:
    note_id: int
    title: str
    note_type: str
    summary: str | None
    snippet: str


@dataclass(frozen=True)
class LibraryLinkedChunkItem:
    chunk_id: int
    document_id: int
    document_title: str
    heading_path: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    snippet: str


@dataclass(frozen=True)
class LibraryDocumentDetail:
    document_id: int
    title: str
    document_type: str
    content_layer: str
    read_status: str
    research_direction: str | None
    source_path: str | None
    pdf_path: str | None
    pdf_open_url: str | None
    zotero_key: str | None
    zotero_open_url: str | None
    object_import_mode: str | None
    object_import_status: str | None
    created_at: datetime
    updated_at: datetime
    chunk_count: int
    note_count: int
    tags: list[str]
    top_headings: list[str]
    related_notes: list[LibraryRelatedNoteItem]
    related_relations: list[LibraryRelationItem]


@dataclass(frozen=True)
class DocumentPdfSource:
    document_id: int
    title: str
    pdf_path: str | None
    source_path: str | None = None


@dataclass(frozen=True)
class LibraryNoteItem:
    note_id: int
    title: str
    note_type: str
    source_path: str | None
    scope_type: str | None
    scope_path: str | None
    summary: str | None
    content_snippet: str
    note_tags: list[str]


@dataclass(frozen=True)
class LibraryEvidenceItem:
    chunk_id: int
    heading_path: str
    pdf_page_start: int | None
    pdf_page_end: int | None
    pdf_open_url: str | None
    snippet: str
    chunk_text: str
    is_metadata_chunk: bool
    is_locatable: bool
    locator_status: str
    locator_reason: str
    related_note_titles: list[str]
    chunk_tags: list[str]


@dataclass(frozen=True)
class LibraryNotePreview:
    note_id: int
    title: str
    note_type: str
    summary: str | None
    source_path: str | None
    document_id: int | None
    scope_type: str | None
    scope_path: str | None
    snippet: str
    linked_chunks: list[LibraryLinkedChunkItem]
    note_tags: list[str]
    related_relations: list[LibraryRelationItem]


@dataclass(frozen=True)
class LibraryChunkPreview:
    chunk_id: int
    document_id: int
    document_title: str
    document_type: str
    heading_path: str
    snippet: str
    chunk_text: str
    is_metadata_chunk: bool
    is_locatable: bool
    locator_status: str
    locator_reason: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_page_end: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    related_notes: list[LibraryRelatedNoteItem]
    chunk_tags: list[str]
    related_relations: list[LibraryRelationItem]


__all__ = [
    "DocumentPdfSource",
    "LibraryChunkPreview",
    "LibraryDocumentDetail",
    "LibraryEvidenceItem",
    "LibraryGroupedSearchChunk",
    "LibraryGroupedSearchDocument",
    "LibraryHomeItem",
    "LibraryLinkedChunkItem",
    "LibraryNoteItem",
    "LibraryNotePreview",
    "LibraryRelatedNoteItem",
    "LibraryRelationItem",
    "LibrarySearchResult",
    "ReadLibraryDocumentSummary",
    "evidence_locator_contract",
    "is_metadata_chunk",
    "is_metadata_chunk_text",
    "normalize_evidence_text",
]
