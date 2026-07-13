from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.retrieval_search import RetrievalSearchMode


EvidenceExportFormat = Literal["markdown", "jsonl", "json"]


class EvidenceExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_context_before: bool = True
    include_context_after: bool = True
    include_note_comment: bool = True
    include_match_reasons: bool = True
    include_provenance: bool = True
    include_raw_warnings: bool = False
    group_by_document: bool = False


class EvidenceExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fragment_ids: list[str] = Field(default_factory=list)
    format: EvidenceExportFormat = "markdown"
    query: str | None = Field(default=None, max_length=2000)
    retrieval_mode: RetrievalSearchMode | None = None
    options: EvidenceExportOptions = Field(default_factory=EvidenceExportOptions)
    save_to_file: bool = False


class EvidenceExportResponse(BaseModel):
    status: Literal["OK"]
    format: EvidenceExportFormat
    content: str
    filename: str
    mime_type: str
    evidence_count: int = Field(..., ge=1)
    export_fingerprint: str
    source_index_hash: str
    source_manifest_hash: str
    exported_at: str
    warnings: list[str] = Field(default_factory=list)
    output_path: str | None = None
    db_write_performed: bool = False
    production_db_write_performed: bool = False
    zotero_db_write_performed: bool = False
    vector_write_performed: bool = False
    llm_called: bool = False
    relation_generated: bool = False
    mechanism_generated: bool = False
