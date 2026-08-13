from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MechanismDraftReviewPacketPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pasteback_validation_result: dict[str, Any]
    source_pack_result: dict[str, Any]


class MechanismDraftReviewActionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_packet: dict[str, Any]
    action: Literal["accept", "reject", "needs_edit", "defer", "merge_into", "merge"]
    review_notes: str | None = Field(default=None, max_length=4000)
    merge_into_packet_id: str | None = Field(default=None, max_length=200)
