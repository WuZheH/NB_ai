from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class KnowledgeRelation(Base):
    __tablename__ = "knowledge_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    evidence_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_chunks.id"),
        nullable=True,
        index=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    note_id: Mapped[int | None] = mapped_column(ForeignKey("personal_notes.id"), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    evidence_chunk = relationship("KnowledgeChunk")
    note = relationship("PersonalNote")


Index(
    "uq_knowledge_relations_identity",
    KnowledgeRelation.source_type,
    KnowledgeRelation.source_id,
    KnowledgeRelation.relation_type,
    KnowledgeRelation.target_type,
    KnowledgeRelation.target_id,
    func.coalesce(KnowledgeRelation.evidence_chunk_id, -1),
    func.coalesce(KnowledgeRelation.note_id, -1),
    unique=True,
)
