from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictChatToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListLibraryRequest(StrictChatToolRequest):
    query: str | None = Field(default=None, max_length=256)
    document_type: str | None = Field(default=None, max_length=64)
    status: str = Field(default="active", pattern=r"^(active|archived|all)$")
    limit: int = Field(default=20, ge=1, le=50)


class ImportPreviewRequest(StrictChatToolRequest):
    inbox_filename: str | None = Field(default=None, max_length=255)


class ImportDocumentRequest(StrictChatToolRequest):
    confirmation_token: str = Field(..., min_length=32, max_length=256)
    confirmed: bool


class DeletePreviewRequest(StrictChatToolRequest):
    document_id: int = Field(..., ge=1)


class DeleteDocumentRequest(StrictChatToolRequest):
    confirmation_token: str = Field(..., min_length=32, max_length=256)
    confirmed: bool
