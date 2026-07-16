from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class KnowledgeTag(Base):
    __tablename__ = "knowledge_tags"
    __table_args__ = (UniqueConstraint("name", "tag_type", name="uq_knowledge_tags_name_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tag_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    chunk_tags = relationship("ChunkTag", back_populates="tag")
    note_tags = relationship("NoteTag", back_populates="tag")


class ChunkTag(Base):
    __tablename__ = "chunk_tags"
    __table_args__ = (UniqueConstraint("chunk_id", "tag_id", name="uq_chunk_tags_chunk_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=False, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("knowledge_tags.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    chunk = relationship("KnowledgeChunk")
    tag = relationship("KnowledgeTag", back_populates="chunk_tags")


class NoteTag(Base):
    __tablename__ = "note_tags"
    __table_args__ = (UniqueConstraint("note_id", "tag_id", name="uq_note_tags_note_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("personal_notes.id"), nullable=False, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("knowledge_tags.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    note = relationship("PersonalNote")
    tag = relationship("KnowledgeTag", back_populates="note_tags")
