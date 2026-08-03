from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DocumentSource(Base):
    __tablename__ = "document_sources"
    __table_args__ = (UniqueConstraint("document_id", "source_type", "zotero_attachment_key", name="uq_document_sources_zotero_attachment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    zotero_item_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zotero_attachment_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    zotero_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zotero_select_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    zotero_open_pdf_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_trace_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
