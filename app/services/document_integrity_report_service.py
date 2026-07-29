from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.database import connect_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR
from app.services import vector_store_service
from app.services.retrieval import fts_status_service


class IntegrityReportError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class IntegrityReportRuntime:
    db_path: Path
    fts_index_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path

    @classmethod
    def production(cls) -> "IntegrityReportRuntime":
        return cls(
            db_path=DEFAULT_DB_PATH,
            fts_index_path=FTS_DB_PATH,
            fts_manifest_path=FTS_MANIFEST_PATH,
            vector_store_path=LANCEDB_DIR,
            vector_manifest_path=vector_store_service.MANIFEST_PATH,
        )


def build_integrity_report(
    *,
    document_id: int,
    runtime: IntegrityReportRuntime | None = None,
) -> dict[str, Any]:
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id < 1:
        raise IntegrityReportError(
            "integrity_report_document_id_invalid",
            "document_id must be a positive integer.",
            status_code=422,
        )
    actual = runtime or IntegrityReportRuntime.production()
    document, chunk_ids, note_ids, lifecycle, source = _read_document_state(
        actual.db_path,
        document_id,
    )
    fts = _read_fts_state(actual, document_id)
    passage_source_ids = [
        vector_store_service.make_passage_source_id(document_id, chunk_id)
        for chunk_id in chunk_ids
    ]
    note_source_ids = [
        vector_store_service.make_note_source_id(note_id)
        for note_id in note_ids
    ]
    try:
        vector_impact = vector_store_service.inspect_document_vector_impact(
            passage_source_ids=passage_source_ids,
            object_keys=[],
            store_path=actual.vector_store_path,
        )
        note_impact = vector_store_service.inspect_note_vector_impact(
            note_source_ids=note_source_ids,
            store_path=actual.vector_store_path,
        )
        vector_status = "ready"
    except (
        vector_store_service.VectorStoreUnavailable,
        vector_store_service.VectorStoreSchemaMismatch,
    ):
        vector_impact = {"passage_vector_count": 0}
        note_impact = {"note_vector_count": 0}
        vector_status = "unavailable"
    return {
        "status": "ok",
        "read_only": True,
        "document_id": document_id,
        "document": document,
        "source": source,
        "database": {
            "document_count": 1,
            "chunk_count": len(chunk_ids),
            **lifecycle,
        },
        "fts": fts,
        "vectors": {
            "status": vector_status,
            "passage_expected_count": len(passage_source_ids),
            "passage_indexed_count": int(vector_impact.get("passage_vector_count") or 0),
            "note_expected_count": len(note_source_ids),
            "note_indexed_count": int(note_impact.get("note_vector_count") or 0),
        },
        "history": {
            "confirmation_token_fingerprint": "not_recorded",
            "previewed_at": "not_recorded",
            "confirmed_at": "not_recorded",
            "lifecycle_events": "not_recorded",
        },
        "writes_performed": {
            "production_db": False,
            "fts": False,
            "vector_store": False,
            "zotero": False,
        },
    }


def _read_document_state(
    db_path: Path,
    document_id: int,
) -> tuple[dict[str, Any], list[int], list[int], dict[str, int], dict[str, Any]]:
    try:
        with connect_readonly_sqlite(
            db_path,
            resolve_strict=True,
            row_factory=sqlite3.Row,
            query_only=True,
            temp_store="MEMORY",
        ) as connection:
            document = connection.execute(
                """
                SELECT id, title, document_type, read_status, created_at
                FROM documents
                WHERE id = ?
                """,
                (document_id,),
            ).fetchone()
            if document is None:
                raise IntegrityReportError(
                    "integrity_report_document_not_found",
                    "The requested Search document was not found.",
                    status_code=404,
                    details={"document_id": document_id},
                )
            chunk_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM knowledge_chunks WHERE document_id = ? ORDER BY id",
                    (document_id,),
                )
            ]
            note_ids = _ids_if_table(
                connection,
                "personal_notes",
                "id",
                document_id,
            )
            lifecycle = {
                "chapter_count": _count_if_table(connection, "chapters", document_id),
                "source_binding_count": _count_if_table(connection, "document_sources", document_id),
                "personal_note_count": len(note_ids),
                "evidence_link_count": _count_if_table(connection, "note_evidence_links", document_id),
            }
            source_row = _source_row(connection, document_id)
    except IntegrityReportError:
        raise
    except sqlite3.Error as exc:
        raise IntegrityReportError(
            "integrity_report_database_unavailable",
            "Search document integrity could not be read.",
            status_code=503,
        ) from exc
    return (
        {
            "title": str(document["title"] or ""),
            "document_type": str(document["document_type"] or "other"),
            "status": "archived" if str(document["read_status"] or "") == "archived" else "active",
            "imported_at": str(document["created_at"] or ""),
        },
        chunk_ids,
        note_ids,
        lifecycle,
        source_row,
    )


def _count_if_table(connection: sqlite3.Connection, table: str, document_id: int) -> int:
    if not _table_exists(connection, table):
        return 0
    columns = _columns(connection, table)
    if "document_id" not in columns:
        return 0
    return int(
        connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE document_id = ?',
            (document_id,),
        ).fetchone()[0]
    )


def _ids_if_table(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    document_id: int,
) -> list[int]:
    if not _table_exists(connection, table):
        return []
    columns = _columns(connection, table)
    if id_column not in columns or "document_id" not in columns:
        return []
    return [
        int(row[0])
        for row in connection.execute(
            f'SELECT "{id_column}" FROM "{table}" WHERE document_id = ? ORDER BY "{id_column}"',
            (document_id,),
        )
    ]


def _source_row(connection: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    if not _table_exists(connection, "document_sources"):
        return {"recorded": False}
    columns = _columns(connection, "document_sources")
    selected = [
        name
        for name in (
            "source_type",
            "zotero_item_key",
            "zotero_attachment_key",
            "source_sha256",
            "source_revision_fingerprint",
            "source_trace_json",
        )
        if name in columns
    ]
    if not selected:
        return {"recorded": False}
    row = connection.execute(
        f'SELECT {", ".join(selected)} FROM document_sources WHERE document_id = ? ORDER BY rowid LIMIT 1',
        (document_id,),
    ).fetchone()
    if row is None:
        return {"recorded": False}
    values = dict(row)
    trace: dict[str, Any] = {}
    raw_trace = values.pop("source_trace_json", None)
    if isinstance(raw_trace, str):
        try:
            parsed = json.loads(raw_trace)
            if isinstance(parsed, dict):
                for key in (
                    "zotero_item_key",
                    "zotero_attachment_key",
                    "source_sha256",
                    "source_revision_fingerprint",
                ):
                    if key in parsed and key not in values:
                        trace[key] = parsed[key]
        except json.JSONDecodeError:
            pass
    return {
        "recorded": True,
        **{key: value for key, value in values.items() if value not in (None, "")},
        **{key: value for key, value in trace.items() if value not in (None, "")},
    }


def _read_fts_state(
    runtime: IntegrityReportRuntime,
    document_id: int,
) -> dict[str, Any]:
    status = fts_status_service.get_index_status(
        index_path=runtime.fts_index_path,
        manifest_path=runtime.fts_manifest_path,
        production_db_path=runtime.db_path,
    )
    fragment_count = 0
    source_types: dict[str, int] = {}
    if runtime.fts_index_path.is_file():
        try:
            with closing(
                fts_status_service.connect_readonly_index(runtime.fts_index_path)
            ) as connection:
                for row in connection.execute(
                    """
                    SELECT source_type, COUNT(*)
                    FROM retrieval_fragments
                    WHERE document_id = ?
                    GROUP BY source_type
                    ORDER BY source_type
                    """,
                    (document_id,),
                ):
                    source_types[str(row[0])] = int(row[1])
                fragment_count = sum(source_types.values())
        except sqlite3.Error:
            pass
    return {
        "status": str(status.get("status") or "unknown"),
        "ready": bool(status.get("ready")),
        "fragment_count": fragment_count,
        "source_type_counts": source_types,
        "reasons": [str(value) for value in status.get("reasons", [])],
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }
