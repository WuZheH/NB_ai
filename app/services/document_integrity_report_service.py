from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.database import connect_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR
from app.services import vector_store_service
from app.services.retrieval import fts_status_service
from app.services.retrieval.source_registry import RetrievalSourceRegistry
from app.services.retrieval.sources.personal_note_adapter import (
    personal_note_exclusion_reason,
)


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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
    (
        document,
        chunk_ids,
        note_rows,
        lifecycle,
        source,
        database_validation,
    ) = _read_document_state(
        actual.db_path,
        document_id,
    )
    pdf_sha256, pdf_warning = _resolve_pdf_sha256(source)
    expected_fts, exclusions = _expected_fts_fragments(
        actual.db_path,
        document_id,
        note_rows,
    )
    fts = _read_fts_state(
        actual,
        document_id,
        expected_fts=expected_fts,
        exclusions=exclusions,
    )
    passage_sources = vector_store_service.collect_passage_sources(
        source_ids=[
            vector_store_service.make_passage_source_id(document_id, chunk_id)
            for chunk_id in chunk_ids
        ],
        source_db_path=actual.db_path,
    )
    note_sources = vector_store_service.collect_personal_note_sources(
        document_id=document_id,
        source_db_path=actual.db_path,
    )
    passage_source_ids = [
        str(source["source_id"])
        for source in passage_sources
    ]
    note_source_ids = [
        str(source["source_id"])
        for source in note_sources
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
        indexed_passage_ids = {
            str(value)
            for value in vector_impact.get("passage_source_ids", [])
        }
        indexed_note_ids = {
            str(value)
            for value in note_impact.get("note_source_ids", [])
        }
        passage_missing_count = len(set(passage_source_ids) - indexed_passage_ids)
        note_missing_count = len(set(note_source_ids) - indexed_note_ids)
        vector_status = (
            "ready"
            if passage_missing_count == 0 and note_missing_count == 0
            else "drift"
        )
    except (
        vector_store_service.VectorStoreUnavailable,
        vector_store_service.VectorStoreSchemaMismatch,
    ):
        indexed_passage_ids = set()
        indexed_note_ids = set()
        passage_missing_count = len(passage_source_ids)
        note_missing_count = len(note_source_ids)
        vector_status = "unavailable"
    vectors: dict[str, Any] = {
        "status": vector_status,
        "passage_expected_count": len(passage_source_ids),
        "passage_indexed_count": len(indexed_passage_ids),
        "passage_missing_count": passage_missing_count,
        "passage_orphan_count": "not_available",
        "note_expected_count": len(note_source_ids),
        "note_indexed_count": len(indexed_note_ids),
        "note_missing_count": note_missing_count,
        "note_orphan_count": "not_available",
    }
    history = {
        "confirmation_token_fingerprint": "not_recorded",
        "previewed_at": "not_recorded",
        "confirmed_at": "not_recorded",
        "lifecycle_events": "not_recorded",
    }
    writes_performed = {
        "production_db": False,
        "fts": False,
        "vector_store": False,
        "zotero": False,
    }
    database = {
        "document_count": 1,
        "chunk_count": len(chunk_ids),
        **lifecycle,
        **database_validation,
    }
    verdict, warnings = _evaluate_verdict(
        source=source,
        database=database,
        fts=fts,
        vectors=vectors,
        history=history,
        writes_performed=writes_performed,
        pdf_warning=pdf_warning,
    )
    return {
        "status": "ok",
        "read_only": True,
        "verdict": verdict,
        "warnings": warnings,
        "document_id": document_id,
        "pdf_sha256": pdf_sha256,
        "document": document,
        "source": source,
        "database": database,
        "fts": fts,
        "vectors": vectors,
        "history": history,
        "writes_performed": writes_performed,
    }


def _read_document_state(
    db_path: Path,
    document_id: int,
) -> tuple[
    dict[str, Any],
    list[int],
    list[dict[str, Any]],
    dict[str, int],
    dict[str, Any],
    dict[str, Any],
]:
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
            note_rows = _rows_if_table(
                connection,
                "personal_notes",
                document_id,
            )
            lifecycle = {
                "chapter_count": _count_if_table(connection, "chapters", document_id),
                "source_binding_count": _count_if_table(connection, "document_sources", document_id),
                "personal_note_count": len(note_rows),
                "evidence_link_count": _count_if_table(connection, "note_evidence_links", document_id),
            }
            source_row = _source_row(connection, document_id)
            integrity_row = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()
            database_validation = {
                "integrity_check": (
                    str(integrity_row[0])
                    if integrity_row is not None
                    else "unavailable"
                ),
                "foreign_key_issue_count": len(
                    connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                ),
            }
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
        note_rows,
        lifecycle,
        source_row,
        database_validation,
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


def _rows_if_table(
    connection: sqlite3.Connection,
    table: str,
    document_id: int,
) -> list[dict[str, Any]]:
    if not _table_exists(connection, table):
        return []
    columns = _columns(connection, table)
    if "id" not in columns or "document_id" not in columns:
        return []
    return [
        dict(row)
        for row in connection.execute(
            f'SELECT * FROM "{table}" WHERE document_id = ? ORDER BY id',
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
    recorded_source_hash = values.pop(
        "source_sha256",
        None,
    )
    if isinstance(raw_trace, str):
        try:
            parsed = json.loads(raw_trace)
            if isinstance(parsed, dict):
                for key in (
                    "zotero_item_key",
                    "zotero_attachment_key",
                    "source_revision_fingerprint",
                ):
                    if key in parsed and key not in values:
                        trace[key] = parsed[key]
                trace["_recorded_pdf_sha256"] = (
                    recorded_source_hash
                    or parsed.get("source_pdf_sha256")
                    or parsed.get("source_sha256")
                )
                trace["_recorded_pdf_path"] = (
                    parsed.get("source_pdf_path")
                    or parsed.get("managed_pdf_path")
                )
        except json.JSONDecodeError:
            pass
    else:
        trace["_recorded_pdf_sha256"] = (
            recorded_source_hash
        )
    return {
        "recorded": True,
        **{key: value for key, value in values.items() if value not in (None, "")},
        **{key: value for key, value in trace.items() if value not in (None, "")},
    }


def _resolve_pdf_sha256(
    source: dict[str, Any],
) -> tuple[str, str | None]:
    recorded_hash = source.pop(
        "_recorded_pdf_sha256",
        None,
    )
    recorded_path = source.pop(
        "_recorded_pdf_path",
        None,
    )
    if isinstance(recorded_hash, str):
        cleaned = recorded_hash.strip()
        if _SHA256_RE.fullmatch(cleaned):
            return cleaned.lower(), None
        if cleaned:
            return (
                "not_available",
                "pdf_sha256_record_invalid",
            )
    if isinstance(recorded_path, str) and recorded_path.strip():
        try:
            path = Path(recorded_path)
            if path.is_file():
                return _sha256_file(path), None
            return (
                "not_available",
                "pdf_sha256_source_file_unavailable",
            )
        except OSError:
            return (
                "not_available",
                "pdf_sha256_source_file_unavailable",
            )
    return "not_recorded", "pdf_sha256_not_recorded"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)
    return digest.hexdigest()


def _expected_fts_fragments(
    db_path: Path,
    document_id: int,
    note_rows: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, int]]:
    registry = RetrievalSourceRegistry(
        research_db_path=db_path,
        zotero_snapshot_path=db_path.with_name(
            ".integrity-zotero-absent.sqlite"
        ),
        notes_root=db_path.with_name(
            ".integrity-notes-absent"
        ),
        project_root=db_path.parent,
    )
    catalog = registry.read(
        source_types=("pdf_chunk", "personal_note"),
        document_ids=(document_id,),
    )
    expected = {
        source_type: {
            fragment.fragment_id
            for fragment in catalog.fragments
            if fragment.source_type == source_type
        }
        for source_type in ("pdf_chunk", "personal_note")
    }
    exclusion_counts = Counter(
        reason
        for row in note_rows
        for reason in [
            personal_note_exclusion_reason(row)
        ]
        if reason is not None
    )
    return (
        expected,
        dict(sorted(exclusion_counts.items())),
    )


def _read_fts_state(
    runtime: IntegrityReportRuntime,
    document_id: int,
    *,
    expected_fts: dict[str, set[str]],
    exclusions: dict[str, int],
) -> dict[str, Any]:
    status = fts_status_service.get_index_status(
        index_path=runtime.fts_index_path,
        manifest_path=runtime.fts_manifest_path,
        production_db_path=runtime.db_path,
    )
    fragment_count = 0
    source_types: dict[str, int] = {}
    indexed = {
        "pdf_chunk": set(),
        "personal_note": set(),
    }
    scan_failed = False
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
                for row in connection.execute(
                    """
                    SELECT source_type, fragment_id
                    FROM retrieval_fragments
                    WHERE document_id = ?
                      AND source_type IN ('pdf_chunk', 'personal_note')
                    ORDER BY source_type, fragment_id
                    """,
                    (document_id,),
                ):
                    indexed[str(row[0])].add(
                        str(row[1])
                    )
        except sqlite3.Error:
            scan_failed = True
    expected_pdf = expected_fts["pdf_chunk"]
    expected_notes = expected_fts["personal_note"]
    indexed_pdf = indexed["pdf_chunk"]
    indexed_notes = indexed["personal_note"]
    reasons = [
        str(value)
        for value in status.get("reasons", [])
    ]
    if scan_failed:
        reasons.append("document_fragment_scan_failed")
    return {
        "status": str(status.get("status") or "unknown"),
        "ready": bool(status.get("ready")),
        "expected_pdf_chunk_count": len(expected_pdf),
        "indexed_pdf_chunk_count": len(indexed_pdf),
        "missing_pdf_chunk_count": len(
            expected_pdf - indexed_pdf
        ),
        "orphan_pdf_chunk_count": len(
            indexed_pdf - expected_pdf
        ),
        "eligible_personal_note_count": len(
            expected_notes
        ),
        "indexed_personal_note_count": len(
            indexed_notes
        ),
        "missing_personal_note_count": len(
            expected_notes - indexed_notes
        ),
        "orphan_personal_note_count": len(
            indexed_notes - expected_notes
        ),
        "excluded_personal_note_count": sum(
            exclusions.values()
        ),
        "exclusion_reasons": exclusions,
        "fragment_count": fragment_count,
        "source_type_counts": source_types,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _evaluate_verdict(
    *,
    source: dict[str, Any],
    database: dict[str, Any],
    fts: dict[str, Any],
    vectors: dict[str, Any],
    history: dict[str, str],
    writes_performed: dict[str, bool],
    pdf_warning: str | None,
) -> tuple[str, list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    if not source.get("recorded"):
        failures.append("document_source_binding_missing")
    if database.get("integrity_check") != "ok":
        failures.append("database_integrity_check_failed")
    if int(
        database.get("foreign_key_issue_count") or 0
    ) > 0:
        failures.append("database_foreign_key_issues")
    if fts.get("ready") is not True:
        failures.append("fts_not_ready")
    for field in (
        "missing_pdf_chunk_count",
        "orphan_pdf_chunk_count",
        "missing_personal_note_count",
        "orphan_personal_note_count",
    ):
        if int(fts.get(field) or 0) > 0:
            failures.append(f"fts_{field}")
    if vectors.get("status") == "unavailable":
        failures.append("vector_store_unavailable")
    for field in (
        "passage_missing_count",
        "note_missing_count",
    ):
        if int(vectors.get(field) or 0) > 0:
            failures.append(f"vector_{field}")
    for field in (
        "passage_orphan_count",
        "note_orphan_count",
    ):
        value = vectors.get(field)
        if isinstance(value, int) and value > 0:
            failures.append(f"vector_{field}")
        elif value == "not_available":
            warnings.append(
                f"vector_{field}_not_available"
            )
    if any(writes_performed.values()):
        failures.append("read_only_contract_violated")

    if pdf_warning:
        warnings.append(pdf_warning)
    if int(
        fts.get("excluded_personal_note_count") or 0
    ) > 0:
        warnings.append(
            "personal_notes_excluded_from_fts:"
            f"{fts['excluded_personal_note_count']}"
        )
    if any(
        value == "not_recorded"
        for value in history.values()
    ):
        warnings.append("historical_events_not_recorded")
    warnings.extend(failures)
    normalized = list(dict.fromkeys(warnings))
    if failures:
        return "fail", normalized
    if normalized:
        return "warn", normalized
    return "pass", []


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
