from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoteroSelectedBookImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zotero_item_key: str = Field(..., min_length=1)
    zotero_attachment_key: str | None = None
