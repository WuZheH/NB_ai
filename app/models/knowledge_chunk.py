from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("markdown_nodes.id"), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overlap_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    overlap_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    embedding_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdf_page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, deferred=True)
    zotero_open_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    document = relationship("Document", back_populates="knowledge_chunks")
    node = relationship("MarkdownNode", back_populates="knowledge_chunks")
    note_links = relationship("NoteEvidenceLink", back_populates="chunk")
