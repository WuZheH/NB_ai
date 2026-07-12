from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoteroMarkdownExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zotero_attachment_key: str = Field(..., min_length=1)
    zotero_item_key: str | None = None
    save_to_file: bool = False
