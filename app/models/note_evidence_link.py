from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class NoteEvidenceLink(Base):
    __tablename__ = "note_evidence_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("personal_notes.id"), nullable=False, index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=False, index=True)
    link_type: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    note = relationship("PersonalNote", back_populates="evidence_links")
    chunk = relationship("KnowledgeChunk", back_populates="note_links")

