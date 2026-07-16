from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PdfPageLayoutBlock(Base):
    __tablename__ = "pdf_page_layout_blocks"
    __table_args__ = (
        Index("ix_pdf_page_layout_blocks_document_page", "document_id", "pdf_page"),
        Index("ix_pdf_page_layout_blocks_text_hash", "text_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    page_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_backend: Mapped[str] = mapped_column(Text, nullable=False)
    backend_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox_json: Mapped[str] = mapped_column(Text, nullable=False)
    polygon_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ChunkLayoutLink(Base):
    __tablename__ = "chunk_layout_links"
    __table_args__ = (
        Index("ix_chunk_layout_links_chunk_id", "chunk_id"),
        Index("ix_chunk_layout_links_document_page", "document_id", "pdf_page"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[int] = mapped_column(ForeignKey("pdf_page_layout_blocks.id"), nullable=False)
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    overlap_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PdfPageTextLayerCache(Base):
    __tablename__ = "pdf_page_text_layer_cache"
    __table_args__ = (
        Index("ix_pdf_page_text_layer_cache_document_page", "document_id", "pdf_page"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_text_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
