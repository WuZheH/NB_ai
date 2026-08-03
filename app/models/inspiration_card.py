from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class InspirationCard(Base):
    __tablename__ = "inspiration_cards"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'user-confirmed', 'rejected', 'archived', 'superseded', 'deleted')",
            name="ck_inspiration_cards_status",
        ),
        CheckConstraint(
            "source_gap_reason IS NULL OR source_gap_reason IN ("
            "'user_free_form_idea', "
            "'book_level_insight', "
            "'cross_document_synthesis', "
            "'source_not_yet_imported', "
            "'unsupported_personal_idea'"
            ")",
            name="ck_inspiration_cards_source_gap_reason",
        ),
        Index("ix_inspiration_cards_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="candidate")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    source_gap_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    sources = relationship("InspirationCardSource", back_populates="card")
    tags = relationship("InspirationCardTag", back_populates="card")
    events = relationship("InspirationCardEvent", back_populates="card")


class InspirationCardSource(Base):
    __tablename__ = "inspiration_card_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("inspiration_cards.id"), nullable=False, index=True)
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    source_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_chunks.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    card = relationship("InspirationCard", back_populates="sources")
    document = relationship("Document")
    chunk = relationship("KnowledgeChunk")


class InspirationCardTag(Base):
    __tablename__ = "inspiration_card_tags"
    __table_args__ = (UniqueConstraint("card_id", "tag_id", name="uq_inspiration_card_tags_card_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("inspiration_cards.id"), nullable=False, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("knowledge_tags.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    card = relationship("InspirationCard", back_populates="tags")
    tag = relationship("KnowledgeTag")


class InspirationCardEvent(Base):
    __tablename__ = "inspiration_card_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("inspiration_cards.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    card = relationship("InspirationCard", back_populates="events")
