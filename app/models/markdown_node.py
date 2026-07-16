from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class MarkdownNode(Base):
    __tablename__ = "markdown_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("markdown_nodes.id"), nullable=True)
    heading_level: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_title: Mapped[str] = mapped_column(String(512), nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    document = relationship("Document", back_populates="markdown_nodes")
    parent = relationship("MarkdownNode", remote_side=[id])
    knowledge_chunks = relationship("KnowledgeChunk", back_populates="node")

