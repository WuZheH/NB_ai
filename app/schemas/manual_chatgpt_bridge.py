from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MechanismSourcePackPromptExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_pack_result: dict[str, Any]
    include_expected_schema: bool = True
    include_prompt_payload: bool = True
    chapter_id: int | None = None
    import_batch_id: str | None = None


class MechanismSourcePackPastebackValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_pack_result: dict[str, Any]
    pasted_chatgpt_response_json: dict[str, Any]

class WorkspaceSelectionSourcePackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: int = Field(..., gt=0)
    chapter_id: int = Field(..., gt=0)
    chunk_id: int | None = Field(default=None, gt=0)
    server_note_id: str | None = None
    client_note_id: str | None = None
    object_candidate_ids: list[int] = Field(default_factory=list)
    reviewed_object_refs: list[str] = Field(default_factory=list)
