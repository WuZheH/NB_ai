from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.retrieval_fragment import (
    RetrievalOriginKind,
    RetrievalSourceType,
)


RetrievalSearchMode = Literal["precision", "coverage"]


class RetrievalSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RetrievalSourceType | list[RetrievalSourceType] | None = None
    origin_kind: RetrievalOriginKind | list[RetrievalOriginKind] | None = None
    document_id: int | list[int] | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    collection: str | None = None
    tag: str | None = None
    year: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    has_note_comment: bool | None = None
    has_zotero_uri: bool | None = None


class RetrievalSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    mode: RetrievalSearchMode = "precision"
    limit: int | None = Field(default=None, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10_000)
    collapse_duplicates: bool = True
    include_context: bool = True
    filters: RetrievalSearchFilters = Field(default_factory=RetrievalSearchFilters)


class RetrievalSearchResult(BaseModel):
    fragment_id: str
    display_id: str
    source_type: RetrievalSourceType
    origin_kind: RetrievalOriginKind
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    document_id: int | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    page_number: int | None = None
    page_label: str | None = None
    section: str | None = None
    text: str
    context_before: str | None = None
    context_after: str | None = None
    note_comment: str | None = None
    original_file_path: str | None = None
    zotero_uri: str | None = None
    score: float
    base_bm25_score: float
    base_bm25_rank: int
    final_rank: int
    match_reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    retrieval_channels: list[str] = Field(default_factory=list)
    duplicate_count: int = 1
    duplicate_fragment_ids: list[str] = Field(default_factory=list)
    duplicate_source_types: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RetrievalSearchResponse(BaseModel):
    status: Literal["ok"]
    mode: RetrievalSearchMode
    query: str
    query_plan: dict[str, Any]
    results: list[RetrievalSearchResult]
    counts: dict[str, Any]
    timing_ms: dict[str, float]
    index_status: dict[str, Any]
    db_write_performed: bool = False
    production_db_write_performed: bool = False
    zotero_db_write_performed: bool = False
    vector_write_performed: bool = False
    llm_called: bool = False
