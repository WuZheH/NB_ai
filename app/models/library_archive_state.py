from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class LibraryArchiveState(Base):
    """Reversible shelf visibility state created only by an archive action."""

    __tablename__ = "library_archive_states"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"), primary_key=True
    )
    previous_read_status: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
