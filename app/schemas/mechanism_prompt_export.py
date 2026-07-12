from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.zotero_inspiration import ZoteroMechanismReadinessDryRunResponse


class MechanismPromptExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_report: ZoteroMechanismReadinessDryRunResponse
    include_expected_schema: bool = True
    include_prompt_payload: bool = True
    chapter_id: int | None = None
    import_batch_id: str | None = None


class MechanismPromptBatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_reports: list[ZoteroMechanismReadinessDryRunResponse] = Field(default_factory=list)
    chapter_id: int | None = None
    import_batch_id: str | None = None
    merge_selected_by_user: bool = False
    include_expected_schema: bool = True
    include_prompt_payload: bool = True


class MechanismPromptExportResponse(BaseModel):
    status: Literal["OK", "BLOCKED"]
    export_mode: Literal["manual_chatgpt_prompt"]
    binding_mode: Literal["single_note", "explicit_note_group", "mechanism_source_pack"]
    copy_ready_prompt: str | None = None
    prompt_payload_json: dict[str, Any] | None = None
    expected_response_schema: dict[str, Any] | None = None
    evidence_summary: dict[str, Any] | None = None
    prompt_export_metadata: dict[str, Any]
    paste_back_readiness_context: dict[str, Any] | None = None
    instructions_for_user: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    llm_called: bool = False
    external_model_called: bool = False
    db_write_performed: bool = False
    mechanism_generated: bool = False
    mechanism_draft_persisted: bool = False
    mechanism_card_created: bool = False


class MechanismPromptBatchExportResponse(BaseModel):
    status: Literal["OK", "PARTIAL", "BLOCKED"]
    export_mode: Literal["manual_chatgpt_prompt"]
    batch_mode: Literal["one_note_per_prompt", "explicit_note_group"]
    prompt_packages: list[MechanismPromptExportResponse] = Field(default_factory=list)
    prompt_count: int
    blocked_count: int
    chapter_id: int | None = None
    import_batch_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    llm_called: bool = False
    external_model_called: bool = False
    db_write_performed: bool = False
    mechanism_generated: bool = False
    mechanism_draft_persisted: bool = False
    mechanism_card_created: bool = False


class MechanismPromptValidatePastedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_report: ZoteroMechanismReadinessDryRunResponse
    pasted_chatgpt_response_json: dict[str, Any]


class MechanismPromptValidatePastedResponse(BaseModel):
    status: Literal["OK", "INVALID", "BLOCKED"]
    validator_passed: bool
    validation_report: dict[str, Any]
    pending_draft_preview: dict[str, Any] | None = None
    llm_called: bool = False
    external_model_called: bool = False
    db_write_performed: bool = False
    mechanism_draft_persisted: bool = False
    mechanism_card_created: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False
