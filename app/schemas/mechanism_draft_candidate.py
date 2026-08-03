from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MechanismDraftCandidatePersistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending_draft_preview: dict[str, Any]
    validation_report: dict[str, Any]
    source_context: dict[str, Any]


class MechanismDraftCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "reject", "needs_edit", "defer", "merge"]
    review_notes: str | None = None
    merge_target_draft_id: str | None = None


class MechanismDraftCandidateSafetyResponse(BaseModel):
    db_write_performed: bool = False
    persistence_scope: Literal["disabled", "tempdb", "production"] = "disabled"
    connection_is_production: bool = False
    mechanism_card_created: bool = False
    llm_called: bool = False
    external_model_called: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False
    production_persistence_enabled: bool = False


class MechanismDraftCandidatePersistResponse(MechanismDraftCandidateSafetyResponse):
    status: Literal["OK"]
    draft_id: str
    review_status: str
    dedup_action: Literal["inserted", "unchanged"]
    candidate: dict[str, Any]


class MechanismDraftCandidateQueueResponse(MechanismDraftCandidateSafetyResponse):
    status: Literal["OK"]
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int


class MechanismDraftCandidateDetailResponse(MechanismDraftCandidateSafetyResponse):
    status: Literal["OK"]
    candidate: dict[str, Any]


class MechanismDraftCandidateReviewResponse(MechanismDraftCandidateSafetyResponse):
    status: Literal["OK"]
    action: str
    candidate: dict[str, Any]


class MechanismDraftCandidateReviewHandoffResponse(BaseModel):
    status: Literal["OK", "BLOCKED"]
    schema_version: Literal["mechanism_draft_review_candidate_handoff_a_v1"]
    handoff_mode: Literal["persisted_candidate_read_only_review_preview"]
    review_ready: bool
    candidate_reference: dict[str, Any]
    review_packet: dict[str, Any] | None = None
    blockers: list[str] = Field(default_factory=list)
    db_read_performed: bool = True
    db_write_performed: bool = False
    persistence_scope: Literal["tempdb", "production"]
    connection_is_production: bool = False
    production_persistence_enabled: bool = False
    production_db_write_allowed: bool = False
    llm_called: bool = False
    external_model_called: bool = False
    external_api_called: bool = False
    relation_generated: bool = False
    mechanism_generated: bool = False
    mechanism_draft_persisted: bool = False
    mechanism_card_created: bool = False
    zotero_write_performed: bool = False
    vector_store_write_performed: bool = False
