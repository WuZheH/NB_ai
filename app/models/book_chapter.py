from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class BookChapter(Base):
    __tablename__ = "book_chapters"
    __table_args__ = (
        UniqueConstraint("document_id", "chapter_index", name="uq_book_chapters_document_chapter_index"),
        Index("ix_book_chapters_document_id", "document_id"),
        Index("ix_book_chapters_status", "object_import_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdf_page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    object_import_status: Mapped[str] = mapped_column(String(64), nullable=False, default="not_started")
    object_bundle_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    document = relationship("Document", back_populates="book_chapters")
