from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.paths import DEFAULT_DB_PATH


ARCHIVE_STATUS = "archived"
ACTIVE_READ_STATUSES = {"read", "mastered"}
ARCHIVE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS library_archive_states (
    document_id INTEGER PRIMARY KEY,
    previous_read_status VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1,
    archived_at DATETIME NOT NULL,
    restored_at DATETIME,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE NO ACTION
)
"""


class ArchiveError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


def archive_documents(
    document_ids: list[int],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    ids = _validated_ids(document_ids)
    with _write_connection(db_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(ARCHIVE_TABLE_SQL)
            rows = _document_rows(connection, ids)
            missing = sorted(set(ids) - set(rows))
            if missing:
                raise ArchiveError("archive_document_not_found", "所选文档不存在。", status_code=404)
            invalid = [doc_id for doc_id, row in rows.items() if row["read_status"] not in ACTIVE_READ_STATUSES]
            if invalid:
                raise ArchiveError("archive_document_status_invalid", "所选文档不在活动书架中。")
            now = _utc_now()
            for document_id in ids:
                previous = str(rows[document_id]["read_status"])
                connection.execute(
                    """
                    INSERT INTO library_archive_states (
                        document_id, previous_read_status, active, archived_at, restored_at
                    ) VALUES (?, ?, 1, ?, NULL)
                    ON CONFLICT(document_id) DO UPDATE SET
                        previous_read_status = excluded.previous_read_status,
                        active = 1,
                        archived_at = excluded.archived_at,
                        restored_at = NULL
                    """,
                    (document_id, previous, now),
                )
                connection.execute(
                    "UPDATE documents SET read_status = ?, updated_at = ? WHERE id = ?",
                    (ARCHIVE_STATUS, now, document_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "status": "ok",
        "action": "archived",
        "document_ids": ids,
        "count": len(ids),
        "search_includes_archived": False,
    }


def restore_documents(
    document_ids: list[int],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    ids = _validated_ids(document_ids)
    with _write_connection(db_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not _table_exists(connection, "library_archive_states"):
                raise ArchiveError("archive_state_not_found", "没有可恢复的归档状态。", status_code=404)
            placeholders = ",".join("?" for _ in ids)
            rows = {
                int(row["document_id"]): row
                for row in connection.execute(
                    f"""
                    SELECT state.document_id, state.previous_read_status, state.active,
                           documents.read_status
                    FROM library_archive_states AS state
                    JOIN documents ON documents.id = state.document_id
                    WHERE state.document_id IN ({placeholders})
                    """,
                    ids,
                )
            }
            invalid = [
                document_id
                for document_id in ids
                if document_id not in rows
                or not bool(rows[document_id]["active"])
                or rows[document_id]["read_status"] != ARCHIVE_STATUS
            ]
            if invalid:
                raise ArchiveError("archive_state_not_found", "所选文档没有活动归档状态。", status_code=404)
            now = _utc_now()
            for document_id in ids:
                previous = str(rows[document_id]["previous_read_status"])
                if previous not in ACTIVE_READ_STATUSES:
                    raise ArchiveError("archive_previous_status_invalid", "归档前状态无法安全恢复。")
                connection.execute(
                    "UPDATE documents SET read_status = ?, updated_at = ? WHERE id = ?",
                    (previous, now, document_id),
                )
                connection.execute(
                    "UPDATE library_archive_states SET active = 0, restored_at = ? WHERE document_id = ?",
                    (now, document_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "status": "ok",
        "action": "restored",
        "document_ids": ids,
        "count": len(ids),
    }


def list_archived_documents(
    *,
    limit: int = 100,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[SimpleNamespace]:
    safe_limit = max(1, min(int(limit), 500))
    path = Path(db_path)
    if not path.is_file():
        return []
    with _read_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT documents.id, documents.title, documents.document_type,
                   documents.read_status, documents.object_import_mode,
                   documents.object_import_status, documents.pdf_path,
                   documents.zotero_key, documents.updated_at,
                   COUNT(DISTINCT knowledge_chunks.id) AS chunk_count,
                   COUNT(DISTINCT book_chapters.id) AS chapter_count
            FROM documents
            LEFT JOIN knowledge_chunks ON knowledge_chunks.document_id = documents.id
            LEFT JOIN book_chapters ON book_chapters.document_id = documents.id
            WHERE documents.read_status = ?
            GROUP BY documents.id
            ORDER BY documents.updated_at DESC, documents.id DESC
            LIMIT ?
            """,
            (ARCHIVE_STATUS, safe_limit),
        ).fetchall()
    return [
        SimpleNamespace(
            item_type="document",
            item_id=int(row["id"]),
            source_document_id=int(row["id"]),
            title=row["title"],
            document_type=row["document_type"],
            read_status=row["read_status"],
            object_import_mode=row["object_import_mode"],
            object_import_status=row["object_import_status"],
            chapter_count=int(row["chapter_count"] or 0),
            chunk_count=int(row["chunk_count"] or 0),
            pdf_path=row["pdf_path"],
            zotero_key=row["zotero_key"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def _validated_ids(document_ids: list[int]) -> list[int]:
    ids = [int(value) for value in document_ids]
    if not ids or len(ids) > 5 or any(value < 1 for value in ids) or len(set(ids)) != len(ids):
        raise ArchiveError("archive_document_ids_invalid", "一次只能处理 1 到 5 个不同文档。", status_code=422)
    return ids


def _document_rows(connection: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    placeholders = ",".join("?" for _ in ids)
    return {
        int(row["id"]): row
        for row in connection.execute(
            f"SELECT id, read_status FROM documents WHERE id IN ({placeholders})",
            ids,
        )
    }


def _write_connection(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(db_path), timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def _read_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
