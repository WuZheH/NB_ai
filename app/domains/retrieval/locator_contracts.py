from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.retrieval.result_contracts import NotebookSourceType


LocatorStrategy = Literal["bbox", "annotation", "text", "page", "note"]


class FragmentLocator(BaseModel):
    """Minimal, read-only instructions for locating a Search fragment in Zotero."""

    model_config = ConfigDict(extra="forbid")

    fragment_id: str
    source_type: NotebookSourceType
    document_id: int | None = None
    document_title: str | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    zotero_note_key: str | None = None
    pdf_page: int | None = Field(default=None, ge=1)
    page_label: str | None = None
    bbox: dict[str, Any] | None = None
    selected_text: str | None = Field(default=None, max_length=512)
    locator_strategy: LocatorStrategy
    locator_confidence: float = Field(..., ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
