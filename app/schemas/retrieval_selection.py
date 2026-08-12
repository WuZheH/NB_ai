from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.retrieval_fragment import RetrievalOriginKind, RetrievalSourceType
from app.schemas.retrieval_search import RetrievalSearchRequest


DEFAULT_DOCUMENT_NOTE_TYPES: list[RetrievalSourceType] = [
    "zotero_highlight",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
    "personal_note",
    "markdown_note",
]


class ExplicitSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["explicit"]
    fragment_ids: list[str] = Field(default_factory=list)


class SearchResultsSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["search_results"]
    search_request: RetrievalSearchRequest
    max_items: int = Field(default=500, ge=1, le=500)


class DocumentScopeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["document_scope"]
    document_id: int = Field(..., ge=1)
    source_types: list[RetrievalSourceType] = Field(
        default_factory=lambda: list(DEFAULT_DOCUMENT_NOTE_TYPES)
    )
    max_items: int = Field(default=1000, ge=1, le=1000)

    @field_validator("source_types")
    @classmethod
    def unique_source_types(
        cls,
        value: list[RetrievalSourceType],
    ) -> list[RetrievalSourceType]:
        return list(dict.fromkeys(value))


RetrievalSelectionSelector = Annotated[
    ExplicitSelection | SearchResultsSelection | DocumentScopeSelection,
    Field(discriminator="type"),
]


class EvidenceBasketItem(BaseModel):
    fragment_id: str
    display_id: str
    source_type: RetrievalSourceType
    origin_kind: RetrievalOriginKind
    document_id: int | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    page_number: int | None = None
    page_label: str | None = None
    section: str | None = None
    selected_order: int = Field(..., ge=1)
    duplicate_count: int = Field(default=1, ge=1)
    warnings: list[str] = Field(default_factory=list)


class RetrievalSelectionResponse(BaseModel):
    status: Literal["OK"]
    selection_type: Literal["explicit", "search_results", "document_scope"]
    resolved_fragment_ids: list[str]
    resolved_count: int = Field(..., ge=0)
    items: list[EvidenceBasketItem]
    warnings: list[str] = Field(default_factory=list)
    source_index_hash: str
    source_manifest_hash: str
    db_write_performed: bool = False
    production_db_write_performed: bool = False
    zotero_db_write_performed: bool = False
    vector_write_performed: bool = False
    llm_called: bool = False
