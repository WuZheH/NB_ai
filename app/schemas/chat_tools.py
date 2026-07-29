from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictChatToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListLibraryRequest(StrictChatToolRequest):
    scope: Literal["imported", "catalog", "zotero"] = "imported"
    query: str | None = Field(default=None, max_length=256)
    document_type: str | None = Field(default=None, max_length=64)
    status: str = Field(
        default="active",
        pattern=r"^(active|archived|available|imported|all)$",
    )
    limit: int = Field(default=20, ge=1, le=50)


class ImportPreviewRequest(StrictChatToolRequest):
    source_type: Literal["local_pdf", "zotero_selected_book"] = "local_pdf"
    inbox_filename: str | None = Field(default=None, max_length=255)
    zotero_item_key: str | None = Field(default=None, min_length=1, max_length=64)
    zotero_attachment_key: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def normalize_optional_strings(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for field in (
                "inbox_filename",
                "zotero_item_key",
                "zotero_attachment_key",
            ):
                raw = value.get(field)
                if isinstance(raw, str):
                    value[field] = raw.strip() or None
        return value

    @model_validator(mode="after")
    def validate_source_fields(self):
        if self.source_type == "local_pdf":
            if self.zotero_item_key is not None or self.zotero_attachment_key is not None:
                raise ValueError("local_pdf does not accept Zotero keys")
        else:
            if self.zotero_item_key is None:
                raise ValueError("zotero_item_key is required for zotero_selected_book")
            if self.inbox_filename is not None:
                raise ValueError("zotero_selected_book does not accept inbox_filename")
        return self


class ImportDocumentRequest(StrictChatToolRequest):
    confirmation_token: str = Field(..., min_length=32, max_length=256)
    confirmed: bool


class DeletePreviewRequest(StrictChatToolRequest):
    document_id: int = Field(..., ge=1)


class DeleteDocumentRequest(StrictChatToolRequest):
    confirmation_token: str = Field(..., min_length=32, max_length=256)
    confirmed: bool


class IntegrityReportRequest(StrictChatToolRequest):
    document_id: int = Field(..., ge=1)
