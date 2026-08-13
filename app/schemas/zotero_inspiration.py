from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


InspirationSource = Literal["zotero_plugin", "zotero_annotation", "zotero_native_annotation", "manual"]
SelectionType = Literal["sentence", "paragraph", "section_title", "chapter_title", "manual"]
DedupAction = Literal["inserted", "updated", "unchanged", "conflict"]
MatchMethod = Literal[
    "attachment_page_exact_text",
    "page_exact_text",
    "page_fuzzy_text",
    "attachment_only",
    "unmatched",
]
MatchConfidence = Literal["high", "medium", "low", "none"]
MechanismReadinessStatus = Literal[
    "ready_for_mechanism_prompt",
    "blocked_by_unmatched_note",
    "blocked_by_low_confidence_match",
    "blocked_by_object_review",
    "blocked_by_missing_evidence",
    "not_inspiration_note",
    "missing_object_layer",
    "needs_manual_review",
]


class ZoteroInspirationNoteUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_note_id: str = Field(..., min_length=1)
    source: InspirationSource
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_annotation_key: str | None = None
    pdf_page: int | None = Field(default=None, ge=1)
    page_label: str | None = None
    selected_text: str
    selected_text_hash: str = Field(..., min_length=1)
    note_text: str
    user_tags: list[str] = Field(default_factory=list)
    selection_type: SelectionType
    context_before: str | None = None
    context_after: str | None = None
    bbox: dict[str, Any] | None = None
    created_at: str = Field(..., min_length=1)
    updated_at: str = Field(..., min_length=1)
    sync_status: str | None = None

class ZoteroInspirationNoteBatchUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: list[ZoteroInspirationNoteUpsertRequest] = Field(default_factory=list)


class ZoteroInspirationNoteUpsertResponse(BaseModel):
    status: Literal["OK", "CONFLICT"]
    mode: str | None = None
    production_write_endpoint: bool | None = None
    recommended_primary_flow: str | None = None
    server_note_id: str
    client_note_id: str
    sync_status: str
    matched_document_id: int | None = None
    matched_chunk_id: int | None = None
    matched_object_ids: list[int] = Field(default_factory=list)
    dedup_action: DedupAction
    db_write_performed: bool
    mechanism_generated: bool = False
    llm_called: bool = False
    match_status: str = "unmatched"
    warnings: list[str] = Field(default_factory=list)
    selected_text_hash_diagnostic: dict[str, Any] | None = None


class ZoteroInspirationNoteBatchUpsertResponse(BaseModel):
    status: Literal["OK"]
    mode: str | None = None
    production_write_endpoint: bool | None = None
    recommended_primary_flow: str | None = None
    results: list[ZoteroInspirationNoteUpsertResponse]
    count: int
    db_write_performed: bool
    mechanism_generated: bool = False
    llm_called: bool = False


class ZoteroInspirationMatchNote(ZoteroInspirationNoteUpsertRequest):
    server_note_id: str | None = None


class ZoteroInspirationMatchDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: ZoteroInspirationMatchNote
    document_id: int | None = Field(default=None, ge=1)
    max_candidates: int = Field(default=5, ge=1, le=20)


class ZoteroInspirationBatchMatchDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: list[ZoteroInspirationMatchNote] = Field(default_factory=list)
    document_id: int | None = Field(default=None, ge=1)
    max_candidates: int = Field(default=5, ge=1, le=20)


class InspirationChunkMatchCandidate(BaseModel):
    chunk_id: int
    document_id: int
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    score: float
    reason: str


class ZoteroInspirationMatchDryRunResponse(BaseModel):
    status: Literal["OK"]
    client_note_id: str
    server_note_id: str | None = None
    matched_document_id: int | None = None
    matched_chunk_id: int | None = None
    matched_pdf_page: int | None = None
    match_method: MatchMethod
    match_confidence: MatchConfidence
    selected_text_preserved: bool
    note_text_preserved: bool
    user_tags_preserved: bool
    candidate_chunks: list[InspirationChunkMatchCandidate] = Field(default_factory=list)
    evidence_context: dict[str, Any] | None = None
    raw_note: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    db_write_performed: bool = False
    mechanism_generated: bool = False
    llm_called: bool = False


class ZoteroInspirationBatchMatchDryRunResponse(BaseModel):
    status: Literal["OK"]
    reports: list[ZoteroInspirationMatchDryRunResponse]
    count: int
    db_write_performed: bool = False
    mechanism_generated: bool = False
    llm_called: bool = False


class ZoteroMechanismReadinessDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: ZoteroInspirationMatchNote
    match_report: ZoteroInspirationMatchDryRunResponse | None = None
    max_neighbor_chunks: int = Field(default=2, ge=0, le=20)


class ZoteroMechanismReadinessBatchDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: list[ZoteroInspirationMatchNote] = Field(default_factory=list)
    match_reports: list[ZoteroInspirationMatchDryRunResponse] = Field(default_factory=list)
    max_neighbor_chunks: int = Field(default=2, ge=0, le=20)


class MechanismReadinessObjectSummary(BaseModel):
    object_id: int | str
    object_key: str | None = None
    object_name: str
    object_type: str
    review_status: Literal["approved", "pending", "rejected", "unknown"]
    source_review_status: str | None = None
    evidence_chunk_ids: list[int] = Field(default_factory=list)
    link_reason: Literal["same_chunk", "nearby_chunk", "same_page", "tag_relation"]


class ZoteroMechanismReadinessDryRunResponse(BaseModel):
    status: Literal["OK"]
    client_note_id: str
    server_note_id: str | None = None
    readiness_status: MechanismReadinessStatus
    readiness_blockers: list[str] = Field(default_factory=list)
    matched_chunk_evidence: dict[str, Any] | None = None
    linked_approved_objects: list[MechanismReadinessObjectSummary] = Field(default_factory=list)
    linked_candidate_objects: list[MechanismReadinessObjectSummary] = Field(default_factory=list)
    object_review_required: bool
    mechanism_prompt_payload_preview: dict[str, Any]
    evidence_completeness_score: float = Field(..., ge=0, le=1)
    selected_text_preserved: bool = True
    note_text_preserved: bool = True
    user_tags_preserved: bool = True
    warnings: list[str] = Field(default_factory=list)
    db_write_performed: bool = False
    mechanism_generated: bool = False
    llm_called: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False


class ZoteroMechanismReadinessBatchDryRunResponse(BaseModel):
    status: Literal["OK"]
    reports: list[ZoteroMechanismReadinessDryRunResponse] = Field(default_factory=list)
    count: int
    db_write_performed: bool = False
    mechanism_generated: bool = False
    llm_called: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False
