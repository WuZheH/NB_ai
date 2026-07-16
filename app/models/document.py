from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_layer: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    zotero_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    read_status: Mapped[str] = mapped_column(String(64), nullable=False, default="read")
    research_direction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_import_mode: Mapped[str | None] = mapped_column(String(64), nullable=True, deferred=True)
    object_import_status: Mapped[str | None] = mapped_column(String(64), nullable=True, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    markdown_nodes = relationship("MarkdownNode", back_populates="document")
    knowledge_chunks = relationship("KnowledgeChunk", back_populates="document")
    personal_notes = relationship("PersonalNote", back_populates="document")
    book_chapters = relationship("BookChapter", back_populates="document")
