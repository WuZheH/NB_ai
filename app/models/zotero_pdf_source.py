from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ZoteroPdfSource(Base):
    __tablename__ = "zotero_pdf_sources"
    __table_args__ = (UniqueConstraint("zotero_attachment_key", name="uq_zotero_pdf_sources_attachment_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zotero_item_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zotero_attachment_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    creators_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    year: Mapped[str | None] = mapped_column(String(16), nullable=True)
    publication_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_path_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    path_exists: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    link_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zotero_select_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    zotero_open_pdf_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_snapshot_mtime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_data_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_status: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
