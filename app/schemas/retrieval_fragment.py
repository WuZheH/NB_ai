from __future__ import annotations

from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RETRIEVAL_FRAGMENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://notebook-ai.local/retrieval-fragment/v1",
)


RetrievalSourceType = Literal[
    "pdf_chunk",
    "zotero_highlight",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
    "personal_note",
    "markdown_note",
]

RetrievalOriginKind = Literal[
    "native",
    "plugin",
    "synthetic_seed",
    "manual_import",
    "local_file",
]


class RetrievalFragment(BaseModel):
    """Read-only, source-traceable retrieval unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fragment_id: str
    display_id: str = Field(..., min_length=1)
    source_type: RetrievalSourceType
    origin_kind: RetrievalOriginKind
    source_record_id: str = Field(..., min_length=1)
    canonical_source_locator: str = Field(..., min_length=1)

    document_id: int | None = Field(default=None, ge=1)
    zotero_library_id: int | None = Field(default=None, ge=0)
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None

    parent_fragment_id: str | None = None
    source_group_id: str | None = None
    duplicate_group_id: str | None = None
    duplicate_candidate: bool = False

    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    page_number: int | None = Field(default=None, ge=1)
    page_label: str | None = None
    section: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_order: int | None = Field(default=None, ge=0)
    position: dict[str, Any] | None = None
    bbox: dict[str, Any] | None = None

    text: str = Field(..., min_length=1)
    note_comment: str | None = None
    context_before: str | None = None
    context_after: str | None = None
    context_status: str = Field(..., min_length=1)
    context_method: str | None = None

    original_file_path: str | None = None
    zotero_uri: str | None = None

    language: str | None = None
    index_text: str = Field(..., min_length=1)
    content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    source_created_at: str | None = None
    source_updated_at: str | None = None
    adapter_version: str = Field(..., min_length=1)

    provenance: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_canonical_identity(self) -> "RetrievalFragment":
        expected = str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, self.canonical_source_locator))
        if self.fragment_id != expected:
            raise ValueError("fragment_id must derive from canonical_source_locator")
        return self

    @field_validator("fragment_id", "parent_fragment_id", "source_group_id", "duplicate_group_id")
    @classmethod
    def validate_uuidv5(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = UUID(value)
        if parsed.version != 5:
            raise ValueError("retrieval identities must be UUIDv5 values")
        return str(parsed)

    @field_validator(
        "zotero_item_key",
        "zotero_attachment_key",
        "zotero_annotation_key",
        "title",
        "page_label",
        "section",
        "note_comment",
        "context_before",
        "context_after",
        "original_file_path",
        "zotero_uri",
        "language",
        "source_created_at",
        "source_updated_at",
        "context_method",
    )
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
