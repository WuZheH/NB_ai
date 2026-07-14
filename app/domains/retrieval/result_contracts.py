from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


NotebookSourceType = Literal[
    "pdf_chunk",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
]

NOTEBOOK_SOURCE_TYPES: tuple[NotebookSourceType, ...] = (
    "pdf_chunk",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
)
NOTE_SOURCE_TYPES: tuple[NotebookSourceType, ...] = (
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
)


class OpenTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_url: str | None = None
    zotero_url: str | None = None
    can_open_pdf: bool = False
    can_open_zotero: bool = False
    pdf_disabled_reason: str | None = None
    zotero_disabled_reason: str | None = None


class PdfHighlightRect(BaseModel):
    """A rectangle in PDF user-space coordinates.

    ``pdf_page`` is a one-based physical PDF page number.  Zotero annotation
    positions are retained as PDF user-space rectangles (bottom-left origin),
    so PDF.js converts them directly through its active viewport.
    """

    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float


class NotebookFragmentLocator(BaseModel):
    """Read-only PDF-preview metadata for a notebook retrieval fragment."""

    model_config = ConfigDict(extra="forbid")

    fragment_id: str
    source_type: NotebookSourceType
    document_id: int | None = None
    document_title: str | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    # Physical PDF page number (one-based).  PDF.js getPage uses the same
    # convention; page_label is display-only and must not be used for lookup.
    pdf_page: int | None = Field(default=None, ge=1)
    page_index: int | None = Field(default=None, ge=0)
    page_label: str | None = None
    bbox: dict[str, Any] | None = None
    rects: list[PdfHighlightRect] = Field(default_factory=list)
    selected_text: str | None = None
    locator_strategy: Literal["bbox", "text", "page", "none"] = "none"
    pdf_available: bool = False
    pdf_endpoint: str | None = None
    warnings: list[str] = Field(default_factory=list)


class NotebookFragment(BaseModel):
    """Stable, source-separated fragment returned by NOTEBOOK_AI retrieval."""

    model_config = ConfigDict(extra="forbid")

    fragment_id: str
    source_type: NotebookSourceType
    server_note_id: str | None = None
    client_note_id: str | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    document_id: int | None = None
    document_title: str | None = None
    document_type: str | None = None
    chunk_id: int | None = None
    pdf_page: int | None = None
    page_label: str | None = None
    text: str | None = None
    selected_text: str | None = None
    note_text: str | None = None
    context_before: str | None = None
    context_after: str | None = None
    tags: list[str] = Field(default_factory=list)
    content_hash: str
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    open_target: OpenTarget


class NotebookSearchResult(NotebookFragment):
    final_rank: int = Field(..., ge=1)
    final_score: float
    reranker_score: float | None = None
    semantic_score: float | None = None
    raw_rank: int | None = Field(default=None, ge=1)


class NotebookSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    query: str
    mode: Literal["high_quality_notebook_search_v1"] = "high_quality_notebook_search_v1"
    embedding_model: str
    reranker_model: str
    backend: str
    result_count: int = Field(..., ge=0)
    results: list[NotebookSearchResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency: dict[str, float] = Field(default_factory=dict)
    db_write_performed: bool = False
    production_db_write_performed: bool = False
    zotero_db_write_performed: bool = False
    vector_write_performed: bool = False
    llm_called: bool = False
