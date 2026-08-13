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
    _upgrade_personal_notes_columns()
    return True


def ensure_empty_retrieval_baseline() -> bool:
    """Create an empty legacy retrieval baseline for a brand-new data root.

    A new user has no database, index, pointer, or generation history.  This
    builds the minimal empty baseline (schema DB -> empty FTS index and
    manifest bound to the database revision, empty vector/note directories)
    so that search, library listing, and the first import work from zero.

    Runs only when no retrieval state exists at all (no active pointer, no
    generation entries, no legacy FTS manifest).  Idempotent; it never
    touches an existing generation system and never rewrites a manifest.
    """
    from pathlib import Path

    from app.core.paths import (
        DATA_DIR,
        FTS_MANIFEST_PATH,
        LANCEDB_DIR,
        VECTOR_STORE_DIR,
        ZOTERO_NOTE_VECTOR_DIR,
    )
    from app.domains.retrieval.note_vector_index import build_zotero_note_vectors
    from app.services.retrieval.fts_index_service import build_retrieval_fts

    data_root = Path(DATA_DIR).resolve(strict=False)
    pointer = data_root / "active_index.json"
    generation_root = data_root / "index_versions"
    if (pointer.exists() or pointer.is_symlink()) or FTS_MANIFEST_PATH.is_file():
        return False
    if generation_root.exists() or generation_root.is_symlink():
        if any(generation_root.iterdir()):
            return False
    if not (data_root / "db" / "research_memory.db").is_file():
        return False

    build_retrieval_fts()
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    (VECTOR_STORE_DIR / "vector_manifest.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    build_zotero_note_vectors(
        index_dir=ZOTERO_NOTE_VECTOR_DIR,
        fragments=[],
    )
    return True


def _upgrade_personal_notes_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "personal_notes" in inspector.get_table_names():
            existing_columns = {column["name"] for column in inspector.get_columns("personal_notes")}
            missing_columns = [
                (column_name, column_type)
                for column_name, column_type in _PERSONAL_NOTE_EXTRA_COLUMNS
                if column_name not in existing_columns
            ]
            for column_name, column_type in missing_columns:
                connection.exec_driver_sql(f"ALTER TABLE personal_notes ADD COLUMN {column_name} {column_type}")
            for statement in _PERSONAL_NOTE_EXTRA_INDEXES:
                connection.exec_driver_sql(statement)

        if "note_evidence_links" in inspector.get_table_names():
            existing_evidence_columns = {column["name"] for column in inspector.get_columns("note_evidence_links")}
            missing_evidence_columns = [
                (column_name, column_type)
                for column_name, column_type in _EVIDENCE_EXTRA_COLUMNS
                if column_name not in existing_evidence_columns
            ]
            for column_name, column_type in missing_evidence_columns:
                connection.exec_driver_sql(f"ALTER TABLE note_evidence_links ADD COLUMN {column_name} {column_type}")
            for statement in _EVIDENCE_EXTRA_INDEXES:
                connection.exec_driver_sql(statement)


_PERSONAL_NOTE_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_path", "TEXT"),
    ("content_hash", "VARCHAR(64)"),
    ("source_system", "TEXT"),
    ("source_library_id", "INTEGER"),
    ("source_item_key", "TEXT"),
    ("source_parent_item_key", "TEXT"),
    ("source_attachment_key", "TEXT"),
    ("source_annotation_key", "TEXT"),
    ("source_note_key", "TEXT"),
    ("source_record_kind", "TEXT"),
    ("source_identity", "TEXT"),
    ("selected_text", "TEXT"),
    ("source_comment", "TEXT"),
    ("pdf_page", "INTEGER"),
    ("page_label", "TEXT"),
    ("position_json", "TEXT"),
    ("source_uri", "TEXT"),
    ("source_created_at", "TEXT"),
    ("source_updated_at", "TEXT"),
    ("source_version", "INTEGER"),
    ("source_content_hash", "TEXT"),
    ("source_missing", "INTEGER NOT NULL DEFAULT 0"),
)

_PERSONAL_NOTE_EXTRA_INDEXES: tuple[str, ...] = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    ux_personal_notes_source_identity
    ON personal_notes(source_identity)
    WHERE source_identity IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_system
    ON personal_notes(source_system)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_item_key
    ON personal_notes(source_item_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_attachment_key
    ON personal_notes(source_attachment_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_annotation_key
    ON personal_notes(source_annotation_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_note_key
    ON personal_notes(source_note_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_content_hash
    ON personal_notes(source_content_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_updated_at
    ON personal_notes(source_updated_at)
    """,
)

_EVIDENCE_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("document_id", "INTEGER"),
    ("pdf_page", "INTEGER"),
    ("page_label", "VARCHAR(64)"),
    ("source_locator_json", "TEXT"),
    ("alignment_status", "VARCHAR(64)"),
    ("alignment_method", "VARCHAR(128)"),
    ("alignment_warnings_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("source_quote_hash", "VARCHAR(64)"),
)

_EVIDENCE_EXTRA_INDEXES: tuple[str, ...] = (
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_document_id
    ON note_evidence_links(document_id)
    """,
)


if __name__ == "__main__":
    init_db()
