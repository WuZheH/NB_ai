from __future__ import annotations

from sqlalchemy import inspect

from app.core.paths import ensure_db_dir
from app.db.session import Base, engine

# Import models so SQLAlchemy registers all Phase 1 tables.
from app.models import (  # noqa: F401
    ChunkTag,
    Document,
    DocumentSource,
    InspirationCard,
    InspirationCardEvent,
    InspirationCardSource,
    InspirationCardTag,
    KnowledgeChunk,
    KnowledgeRelation,
    KnowledgeTag,
    LibraryArchiveState,
    MarkdownNode,
    NoteTag,
    NoteEvidenceLink,
    ObjectCandidate,
    PersonalNote,
    ZoteroPdfSource,
)


def init_db() -> None:
    ensure_db_dir()
    Base.metadata.create_all(bind=engine)
    _upgrade_personal_notes_columns()


def initialize_database_if_empty() -> bool:
    """Create the application schema only for a database with no tables."""
    ensure_db_dir()
    if inspect(engine).get_table_names():
        return False
    Base.metadata.create_all(bind=engine)
    return True


def _upgrade_personal_notes_columns() -> None:
    inspector = inspect(engine)
    if "personal_notes" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("personal_notes")}
    missing_columns = []
    if "source_path" not in existing_columns:
        missing_columns.append(("source_path", "TEXT"))
    if "content_hash" not in existing_columns:
        missing_columns.append(("content_hash", "VARCHAR(64)"))

    if not missing_columns:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing_columns:
            connection.exec_driver_sql(f"ALTER TABLE personal_notes ADD COLUMN {column_name} {column_type}")


if __name__ == "__main__":
    init_db()
