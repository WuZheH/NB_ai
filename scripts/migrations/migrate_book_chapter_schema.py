from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "db" / "research_memory.db"


MIGRATION_STATEMENTS = (
    (
        "documents",
        "object_import_mode",
        "ALTER TABLE documents ADD COLUMN object_import_mode TEXT DEFAULT 'full_document'",
    ),
    (
        "documents",
        "object_import_status",
        "ALTER TABLE documents ADD COLUMN object_import_status TEXT DEFAULT 'open'",
    ),
    (
        "knowledge_chunks",
        "chapter_id",
        "ALTER TABLE knowledge_chunks ADD COLUMN chapter_id INTEGER",
    ),
    (
        "object_candidates",
        "chapter_id",
        "ALTER TABLE object_candidates ADD COLUMN chapter_id INTEGER",
    ),
)

BOOK_CHAPTERS_SQL = """
CREATE TABLE IF NOT EXISTS book_chapters (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    title VARCHAR(512) NOT NULL,
    heading_path TEXT,
    pdf_page_start INTEGER,
    pdf_page_end INTEGER,
    object_import_status VARCHAR(64) NOT NULL DEFAULT 'not_started',
    object_bundle_job_id VARCHAR(255),
    object_committed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(document_id) REFERENCES documents(id),
    CONSTRAINT uq_book_chapters_document_chapter_index UNIQUE (document_id, chapter_index)
)
"""

INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_book_chapters_document_id ON book_chapters (document_id)",
    "CREATE INDEX IF NOT EXISTS ix_book_chapters_status ON book_chapters (object_import_status)",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_chapter_id ON knowledge_chunks (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_object_candidates_chapter_id ON object_candidates (chapter_id)",
)


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not table_exists(connection, table_name):
        return False
    return any(row[1] == column_name for row in connection.execute(f"PRAGMA table_info({table_name})"))


def plan_book_schema_migration(connection: sqlite3.Connection) -> list[str]:
    operations: list[str] = []
    if not table_exists(connection, "book_chapters"):
        operations.append("create_table:book_chapters")
    for table_name, column_name, _sql in MIGRATION_STATEMENTS:
        if not column_exists(connection, table_name, column_name):
            operations.append(f"add_column:{table_name}.{column_name}")
    for index_sql in INDEX_SQL:
        index_name = index_sql.split(" IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
        operations.append(f"ensure_index:{index_name}")
    return operations


def apply_book_schema_migration(
    connection: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> dict[str, object]:
    operations = plan_book_schema_migration(connection)
    if dry_run:
        return {"dry_run": True, "operations": operations, "applied_count": 0}

    applied: list[str] = []
    if not table_exists(connection, "book_chapters"):
        connection.execute(BOOK_CHAPTERS_SQL)
        applied.append("create_table:book_chapters")

    for table_name, column_name, sql in MIGRATION_STATEMENTS:
        if not column_exists(connection, table_name, column_name):
            connection.execute(sql)
            applied.append(f"add_column:{table_name}.{column_name}")

    for sql in INDEX_SQL:
        connection.execute(sql)
    connection.commit()
    return {"dry_run": False, "operations": operations, "applied": applied, "applied_count": len(applied)}


def _format_lines(result: dict[str, object]) -> Iterable[str]:
    yield f"dry_run={result['dry_run']}"
    yield f"applied_count={result['applied_count']}"
    for operation in result.get("operations", []):
        yield f"operation={operation}"
    for operation in result.get("applied", []):
        yield f"applied={operation}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare book chapter schema migration.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--apply", action="store_true", default=False)
    args = parser.parse_args()

    if args.apply == args.dry_run:
        raise SystemExit("Choose exactly one of --dry-run or --apply.")

    db_path = Path(args.db_path)
    with sqlite3.connect(db_path) as connection:
        result = apply_book_schema_migration(connection, dry_run=args.dry_run)
    for line in _format_lines(result):
        print(line)


if __name__ == "__main__":
    main()
