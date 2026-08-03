from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services import vector_store_service
from scripts.import_book_ocr_layout_first import correction_quality_metrics, detect_quality_issues


PROMOTE_SOURCE_BACKEND = "ocr_layout_first_promoted"
PROMOTE_OBJECT_IMPORT_MODE = "ocr_layout_first"
PROMOTE_APPLY_ENABLED = True
AFFECTED_ONLY_VECTOR_SYNC_AVAILABLE = True
SNAPSHOT_TABLE = "ocr_first_promote_snapshots"
ALLOWED_APPLY_SCOPE = {"document_id": 3, "page_start": 390, "page_end": 390}
ALLOWED_APPLY_CHUNK_IDS = [1842, 1843, 1844, 1845, 1846, 1847]
ALLOWED_APPLY_SOURCE_IDS = [f"chunk:3:{chunk_id}" for chunk_id in ALLOWED_APPLY_CHUNK_IDS]
FORMAL_STATUS_COLUMNS = frozenset(
    {"status", "active", "is_active", "superseded", "superseded_at", "superseded_by_chunk_id", "hidden", "is_hidden"}
)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.prepare_snapshot_schema:
        report = run_prepare_snapshot_schema(
            db_path=Path(args.db_path),
            document_id=args.document_id,
            page_start=args.page_start,
            page_end=args.page_end,
            dry_run=args.dry_run,
            apply=args.apply,
            backup_dir=Path(args.backup_dir) if args.backup_dir else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else str(report))
        return 0
    report = run_promote_plan(
        db_path=Path(args.db_path),
        document_id=args.document_id,
        page_start=args.page_start,
        page_end=args.page_end,
        dry_run=args.dry_run,
        apply=args.apply,
        explain_medium_mapping=args.explain_medium_mapping,
        confirm_medium_mapping=args.confirm_medium_mapping,
        promote_run_id=args.promote_run_id,
        backup_dir=Path(args.backup_dir) if args.backup_dir else None,
        vector_store_path=Path(args.vector_store_path) if args.vector_store_path else None,
        vector_manifest_path=Path(args.vector_manifest_path) if args.vector_manifest_path else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _text_report(report))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan local OCR-first candidate promotion without mutating the canonical store.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--page-start", type=int, required=True)
    parser.add_argument("--page-end", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--explain-medium-mapping", action="store_true")
    parser.add_argument("--confirm-medium-mapping", action="store_true")
    parser.add_argument("--promote-run-id")
    parser.add_argument("--backup-dir")
    parser.add_argument("--vector-store-path")
    parser.add_argument("--vector-manifest-path")
    parser.add_argument("--prepare-snapshot-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run_promote_plan(
    *,
    db_path: Path,
    document_id: int,
    page_start: int,
    page_end: int,
    dry_run: bool,
    apply: bool,
    explain_medium_mapping: bool = False,
    confirm_medium_mapping: bool = False,
    promote_run_id: str | None = None,
    backup_dir: Path | None = None,
    vector_store_path: Path | None = None,
    vector_manifest_path: Path | None = None,
    affected_only_vector_sync_available: bool = AFFECTED_ONLY_VECTOR_SYNC_AVAILABLE,
    vector_sync_runner: Any | None = None,
) -> dict[str, Any]:
    _validate_request(document_id=document_id, page_start=page_start, page_end=page_end, dry_run=dry_run, apply=apply)
    run_id = promote_run_id or (
        f"promote_doc{document_id}_page{page_start}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        if apply
        else f"dry_run_doc{document_id}_page{page_start}"
    )
    with open_read_only_connection(db_path) as connection:
        schema = inspect_schema(connection)
        old_chunks = load_old_chunks(connection, document_id=document_id, page_start=page_start, page_end=page_end)
        candidates = load_corrected_candidates(connection, document_id=document_id, page_start=page_start, page_end=page_end)
        mapping = build_old_candidate_mapping(old_chunks, candidates)
        recommendation = recommend_strategy(schema)
        proposed_writes = build_proposed_db_writes(
            schema=schema,
            recommendation=recommendation,
            mapping=mapping,
            old_chunks=old_chunks,
            candidates=candidates,
            promote_run_id=run_id,
        )
        vector_sync_plan = build_vector_sync_plan(document_id=document_id, mapping=mapping)
        medium_mapping_explanations = (
            build_medium_mapping_explanations(connection, old_chunks=old_chunks, candidates=candidates, mapping=mapping)
            if explain_medium_mapping
            else []
        )
    safety_gate = build_apply_safety_gate(
        schema=schema,
        mapping=mapping,
        confirm_medium_mapping=confirm_medium_mapping,
        affected_only_vector_sync_available=affected_only_vector_sync_available,
        document_id=document_id,
        page_start=page_start,
        page_end=page_end,
    )
    if apply:
        if safety_gate["blockers"]:
            raise ValueError("promote apply blocked: " + "; ".join(safety_gate["blockers"]))
        backup_path = backup_sqlite_for_promote(db_path, run_id=run_id, backup_dir=backup_dir)
        apply_result = apply_promote_transaction(
            db_path=db_path,
            document_id=document_id,
            page_start=page_start,
            page_end=page_end,
            mapping=mapping,
            candidates=candidates,
            proposed_writes=proposed_writes,
            promote_run_id=run_id,
            backup_path=backup_path,
        )
        runner = vector_sync_runner or vector_store_service.sync_affected_passage_embeddings
        try:
            vector_result = runner(
                ALLOWED_APPLY_SOURCE_IDS,
                dry_run=False,
                apply=True,
                store_path=vector_store_path,
                manifest_path=vector_manifest_path,
            )
        except Exception as exc:
            raise RuntimeError(
                f"canonical promote committed but affected vector sync failed; restore from {backup_path}: {exc}"
            ) from exc
        return {
            "status": "APPLIED",
            "mode": "ocr_first_promote",
            "document_id": document_id,
            "page_start": page_start,
            "page_end": page_end,
            "promote_run_id": run_id,
            "read_only_sqlite_connection": False,
            "no_database_writes_performed": False,
            "knowledge_chunks_written": True,
            "candidate_or_correction_rows_written": True,
            "lancedb_writes_performed": bool(vector_result.get("lancedb_writes_performed")),
            "apply_safety_gate": safety_gate,
            "backup_path": str(backup_path),
            "apply_result": apply_result,
            "affected_vector_sync": vector_result,
        }
    return {
        "status": "DRY_RUN",
        "mode": "ocr_first_promote_plan",
        "document_id": document_id,
        "page_start": page_start,
        "page_end": page_end,
        "promote_run_id": run_id,
        "read_only_sqlite_connection": True,
        "no_database_writes_performed": True,
        "knowledge_chunks_written": False,
        "candidate_or_correction_rows_written": False,
        "lancedb_writes_performed": False,
        "ocr_run_performed": False,
        "pdf_import_performed": False,
        "llm_calls_performed": False,
        "apply_requires_explicit_apply": True,
        "apply_enabled": PROMOTE_APPLY_ENABLED,
        "apply_safety_gate": safety_gate,
        "schema": schema,
        "recommendation": recommendation,
        "old_chunks": old_chunks,
        "corrected_candidates": candidates,
        "mapping": mapping,
        "medium_mapping_explanations": medium_mapping_explanations,
        "proposed_db_writes": proposed_writes,
        "vector_sync_plan": vector_sync_plan,
        "risk_report": build_risk_report(
            mapping=mapping,
            proposed_writes=proposed_writes,
            vector_sync_plan=vector_sync_plan,
            safety_gate=safety_gate,
        ),
    }


def _validate_request(*, document_id: int, page_start: int, page_end: int, dry_run: bool, apply: bool) -> None:
    if document_id < 1:
        raise ValueError("--document-id must be positive")
    if page_start != page_end:
        raise ValueError("Fix5P local promote smoke plan is limited to one specified PDF page")
    if dry_run == apply:
        raise ValueError("specify exactly one of --dry-run or --apply")
    if apply:
        _validate_allowed_apply_scope(document_id=document_id, page_start=page_start, page_end=page_end)


def _validate_allowed_apply_scope(*, document_id: int, page_start: int, page_end: int) -> None:
    requested = {"document_id": document_id, "page_start": page_start, "page_end": page_end}
    if requested != ALLOWED_APPLY_SCOPE:
        raise ValueError("promote --apply is limited to document_id=3 page_start=390 page_end=390")


def open_read_only_connection(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    if not resolved.exists():
        raise ValueError(f"database does not exist: {resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def inspect_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    chunk_columns = _table_columns(connection, "knowledge_chunks")
    candidate_columns = _table_columns(connection, "ocr_first_chunk_candidates")
    correction_columns = _table_columns(connection, "ocr_first_candidate_corrections")
    line_link_columns = _table_columns(connection, "chunk_layout_line_links")
    status_columns = sorted(FORMAL_STATUS_COLUMNS.intersection(chunk_columns))
    snapshot_tables = [
        name
        for name in _table_names(connection)
        if name == SNAPSHOT_TABLE
        or ("chunk" in name.lower() and any(label in name.lower() for label in ("snapshot", "backup", "audit")))
    ]
    promote_metadata_columns = [
        column for column in ("search_text", "display_text", "source_backend", "object_import_mode") if column in chunk_columns
    ]
    return {
        "knowledge_chunks_columns": sorted(chunk_columns),
        "candidate_columns": sorted(candidate_columns),
        "correction_columns": sorted(correction_columns),
        "chunk_layout_line_links_columns": sorted(line_link_columns),
        "knowledge_chunk_status_columns": status_columns,
        "supports_superseded_or_inactive_chunks": bool(status_columns),
        "snapshot_or_audit_tables": snapshot_tables,
        "supports_old_chunk_snapshot": bool(snapshot_tables),
        "available_promote_metadata_columns": promote_metadata_columns,
        "schema_gaps": [
            description
            for description, missing in (
                ("knowledge_chunks has no mature active/superseded/hidden status field", not status_columns),
                ("knowledge_chunks has no search_text column", "search_text" not in chunk_columns),
                ("knowledge_chunks has no display_text column", "display_text" not in chunk_columns),
                ("knowledge_chunks has no source_backend column", "source_backend" not in chunk_columns),
                ("knowledge_chunks has no object_import_mode column", "object_import_mode" not in chunk_columns),
                ("no old chunk snapshot/audit table exists", not snapshot_tables),
            )
            if missing
        ],
        "snapshot_schema_helper_available": True,
        "planned_snapshot_table": SNAPSHOT_TABLE,
    }


def create_ocr_first_promote_snapshot_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            promote_run_id TEXT NOT NULL,
            chunk_id INTEGER NOT NULL,
            old_chunk_text TEXT NOT NULL,
            old_content_hash TEXT,
            old_char_count INTEGER,
            old_token_count INTEGER,
            old_updated_at TEXT,
            old_line_links_json TEXT NOT NULL,
            old_candidate_id INTEGER,
            proposed_candidate_id INTEGER NOT NULL,
            proposed_text_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{SNAPSHOT_TABLE}_run_chunk ON {SNAPSHOT_TABLE}(promote_run_id, chunk_id)"
    )


def insert_promote_snapshot_rows(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> list[int]:
    inserted_ids: list[int] = []
    for row in rows:
        cursor = connection.execute(
            f"""
            INSERT INTO {SNAPSHOT_TABLE} (
                document_id, page_start, page_end, promote_run_id, chunk_id,
                old_chunk_text, old_content_hash, old_char_count, old_token_count,
                old_updated_at, old_line_links_json, old_candidate_id,
                proposed_candidate_id, proposed_text_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["document_id"],
                row["page_start"],
                row["page_end"],
                row["promote_run_id"],
                row["chunk_id"],
                row["old_chunk_text"],
                row.get("old_content_hash"),
                row.get("old_char_count"),
                row.get("old_token_count"),
                row.get("old_updated_at"),
                row["old_line_links_json"],
                row.get("old_candidate_id"),
                row["proposed_candidate_id"],
                row["proposed_text_hash"],
                row["created_at"],
            ),
        )
        inserted_ids.append(int(cursor.lastrowid))
    return inserted_ids


def run_prepare_snapshot_schema(
    *,
    db_path: Path,
    document_id: int,
    page_start: int,
    page_end: int,
    dry_run: bool,
    apply: bool,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    _validate_request(document_id=document_id, page_start=page_start, page_end=page_end, dry_run=dry_run, apply=apply)
    if dry_run:
        with open_read_only_connection(db_path) as connection:
            existing = SNAPSHOT_TABLE in _table_names(connection)
        return {
            "status": "DRY_RUN",
            "mode": "prepare_ocr_first_promote_snapshot_schema",
            "snapshot_table": SNAPSHOT_TABLE,
            "snapshot_table_exists": existing,
            "planned_create": not existing,
            "no_database_writes_performed": True,
        }
    run_id = f"prepare_snapshot_doc{document_id}_page{page_start}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    backup_path = backup_sqlite_for_promote(db_path, run_id=run_id, backup_dir=backup_dir)
    with sqlite3.connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        create_ocr_first_promote_snapshot_schema(connection)
        connection.commit()
    return {
        "status": "APPLIED",
        "mode": "prepare_ocr_first_promote_snapshot_schema",
        "snapshot_table": SNAPSHOT_TABLE,
        "backup_path": str(backup_path),
        "knowledge_chunks_written": False,
        "chunk_layout_line_links_written": False,
    }


def backup_sqlite_for_promote(db_path: Path, *, run_id: str, backup_dir: Path | None = None) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    destination_dir = backup_dir or db_path.parent / "backups"
    destination_dir.mkdir(parents=True, exist_ok=True)
    backup_path = destination_dir / f"{db_path.stem}_before_ocr_first_promote_{run_id}.db"
    if backup_path.exists():
        raise FileExistsError(f"backup already exists for promote run: {backup_path}")
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"SQLite backup verification failed: {integrity}")
    return backup_path


def apply_promote_transaction(
    *,
    db_path: Path,
    document_id: int,
    page_start: int,
    page_end: int,
    mapping: dict[str, Any],
    candidates: list[dict[str, Any]],
    proposed_writes: dict[str, Any],
    promote_run_id: str,
    backup_path: Path,
) -> dict[str, Any]:
    _validate_apply_mapping(mapping)
    candidate_by_id = {int(item["candidate_id"]): item for item in candidates}
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            create_ocr_first_promote_snapshot_schema(connection)
            candidate_columns = _table_columns(connection, "ocr_first_chunk_candidates")
            existing = connection.execute(
                f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE} WHERE promote_run_id = ?",
                (promote_run_id,),
            ).fetchone()[0]
            if existing:
                raise ValueError(f"promote_run_id already has snapshot rows: {promote_run_id}")
            snapshot_ids = insert_promote_snapshot_rows(connection, proposed_writes["planned_snapshots"])
            if len(snapshot_ids) != len(mapping["mappings"]):
                raise RuntimeError("snapshot row count does not match promote mapping; refusing canonical update")
            updated_chunks = []
            inserted_line_links = 0
            promoted_candidates = []
            for item in mapping["mappings"]:
                chunk_id = int(item["old_chunk_id"])
                candidate = candidate_by_id[int(item["candidate_id"])]
                text = str(candidate["corrected_text"])
                cursor = connection.execute(
                    """
                    UPDATE knowledge_chunks
                    SET chunk_text = ?, content_hash = ?, char_count = ?, token_count = NULL, updated_at = ?
                    WHERE id = ? AND document_id = ? AND pdf_page_start = ? AND pdf_page_end = ?
                    """,
                    (text, _sha256(text), len(text), now, chunk_id, document_id, page_start, page_end),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"canonical chunk update did not affect exactly one row: {chunk_id}")
                connection.execute(
                    """
                    DELETE FROM chunk_layout_line_links
                    WHERE chunk_id = ? AND document_id = ? AND pdf_page BETWEEN ? AND ?
                    """,
                    (chunk_id, document_id, page_start, page_end),
                )
                for line_id in candidate["source_line_ids"]:
                    connection.execute(
                        """
                        INSERT INTO chunk_layout_line_links (
                            chunk_id, document_id, pdf_page, line_id, match_method,
                            overlap_score, confidence, created_at
                        ) VALUES (?, ?, ?, ?, 'ocr_layout_first_source_lines', 1.0, 'high', ?)
                        """,
                        (chunk_id, document_id, page_start, int(line_id), now),
                    )
                    inserted_line_links += 1
                if "notes" in candidate_columns:
                    promoted = connection.execute(
                        """
                        UPDATE ocr_first_chunk_candidates
                        SET review_status = 'promoted',
                            notes = COALESCE(notes || '; ', '') || ?
                        WHERE id = ? AND review_status = 'pending'
                        """,
                        (f"promoted by {promote_run_id}", int(candidate["candidate_id"])),
                    )
                else:
                    promoted = connection.execute(
                        """
                        UPDATE ocr_first_chunk_candidates
                        SET review_status = 'promoted'
                        WHERE id = ? AND review_status = 'pending'
                        """,
                        (int(candidate["candidate_id"]),),
                    )
                if promoted.rowcount != 1:
                    raise RuntimeError(f"candidate was not pending during promote: {candidate['candidate_id']}")
                updated_chunks.append(chunk_id)
                promoted_candidates.append(int(candidate["candidate_id"]))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "transaction_committed": True,
        "backup_path": str(backup_path),
        "snapshot_table": SNAPSHOT_TABLE,
        "snapshot_rows_written": len(snapshot_ids),
        "snapshot_ids": snapshot_ids,
        "updated_chunk_ids": updated_chunks,
        "promoted_candidate_ids": promoted_candidates,
        "inserted_line_links": inserted_line_links,
        "affected_source_ids": list(ALLOWED_APPLY_SOURCE_IDS),
    }


def _validate_apply_mapping(mapping: dict[str, Any]) -> None:
    chunk_ids = [int(item["old_chunk_id"]) for item in mapping["mappings"]]
    candidate_ids = [int(item["candidate_id"]) for item in mapping["mappings"]]
    if chunk_ids != ALLOWED_APPLY_CHUNK_IDS or candidate_ids != [1, 2, 3, 4, 5, 6]:
        raise ValueError("promote apply requires the reviewed page 390 mapping for chunks 1842-1847 and candidates 1-6")


def recommend_strategy(schema: dict[str, Any]) -> dict[str, Any]:
    if schema["supports_superseded_or_inactive_chunks"]:
        return {
            "strategy": "B",
            "reason": "formal chunk lifecycle columns are available; additive promotion could be designed after search gating is verified",
            "snapshot_required": True,
            "rollback_backup_required": True,
        }
    return {
        "strategy": "A",
        "reason": "knowledge_chunks has no mature active/superseded/hidden schema, so adding parallel formal chunks would leave search visibility undefined",
        "preserve_old_chunk_ids": True,
        "snapshot_required": True,
        "rollback_backup_required": True,
    }


def load_old_chunks(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    page_start: int,
    page_end: int,
) -> list[dict[str, Any]]:
    columns = _table_columns(connection, "knowledge_chunks")
    if not columns:
        raise ValueError("knowledge_chunks table not found")
    chapter_expr = "chapter_id" if "chapter_id" in columns else "NULL AS chapter_id"
    chunk_index_expr = "chunk_index" if "chunk_index" in columns else "id AS chunk_index"
    optional_columns = [
        column for column in ("content_hash", "char_count", "token_count", "updated_at") if column in columns
    ]
    optional_sql = f", {', '.join(optional_columns)}" if optional_columns else ""
    rows = connection.execute(
        f"""
        SELECT id, {chunk_index_expr}, {chapter_expr}, pdf_page_start, pdf_page_end, chunk_text{optional_sql}
        FROM knowledge_chunks
        WHERE document_id = ? AND pdf_page_start <= ? AND pdf_page_end >= ?
        ORDER BY chunk_index, id
        """,
        (document_id, page_end, page_start),
    ).fetchall()
    links_by_chunk = _load_line_links(connection, document_id=document_id, page_start=page_start, page_end=page_end)
    result = []
    for row in rows:
        chunk_id = int(row["id"])
        text = str(row["chunk_text"] or "")
        result.append(
            {
                "chunk_id": chunk_id,
                "chunk_index": int(row["chunk_index"]),
                "pdf_page_start": row["pdf_page_start"],
                "pdf_page_end": row["pdf_page_end"],
                "chapter_id": row["chapter_id"],
                "old_chunk_text": text,
                "old_content_hash": row["content_hash"] if "content_hash" in columns else None,
                "old_char_count": row["char_count"] if "char_count" in columns else None,
                "old_token_count": row["token_count"] if "token_count" in columns else None,
                "old_updated_at": row["updated_at"] if "updated_at" in columns else None,
                "old_chunk_text_summary": _preview(text),
                "old_issues": quality_metrics(text),
                "existing_layout_line_links": links_by_chunk.get(
                    chunk_id, {"count": 0, "line_ids": [], "match_methods": [], "confidence_values": [], "links": []}
                ),
                "vector_indexed_status": {
                    "source_id": f"chunk:{document_id}:{chunk_id}",
                    "status": "not_checked",
                    "reason": "planner does not open LanceDB under the no-vector-write dry-run boundary",
                },
            }
        )
    return result


def load_corrected_candidates(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    page_start: int,
    page_end: int,
) -> list[dict[str, Any]]:
    columns = _table_columns(connection, "ocr_first_chunk_candidates")
    if not columns:
        raise ValueError("ocr_first_chunk_candidates table not found")
    corrections_available = bool(_table_columns(connection, "ocr_first_candidate_corrections"))
    correction_join = ""
    selected_text = "c.chunk_text AS selected_text, NULL AS correction_id"
    if corrections_available:
        correction_join = """
            LEFT JOIN ocr_first_candidate_corrections r ON r.id = (
                SELECT MAX(rc.id)
                FROM ocr_first_candidate_corrections rc
                WHERE rc.candidate_id = c.id AND rc.review_status IN ('pending', 'approved')
            )
        """
        selected_text = "COALESCE(r.corrected_text, c.chunk_text) AS selected_text, r.id AS correction_id"
    rows = connection.execute(
        f"""
        SELECT c.id, c.document_id, c.candidate_index, c.pdf_page_start, c.pdf_page_end, c.chapter_id,
               c.chunk_text, c.source_line_ids_json, c.source_line_keys_json, {selected_text}
        FROM ocr_first_chunk_candidates c
        {correction_join}
        WHERE c.document_id = ? AND c.review_status = 'pending'
          AND c.pdf_page_start <= ? AND c.pdf_page_end >= ?
        ORDER BY c.candidate_index, c.id
        """,
        (document_id, page_end, page_start),
    ).fetchall()
    persisted_line_ids = {
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id FROM pdf_page_layout_lines
            WHERE document_id = ? AND pdf_page BETWEEN ? AND ? AND source_backend = 'surya_ocr'
            """,
            (document_id, page_start, page_end),
        ).fetchall()
    } if _table_columns(connection, "pdf_page_layout_lines") else set()
    result = []
    for row in rows:
        source_line_ids = _json_int_list(row["source_line_ids_json"])
        missing_line_ids = [line_id for line_id in source_line_ids if line_id not in persisted_line_ids]
        selected = str(row["selected_text"] or row["chunk_text"] or "")
        result.append(
            {
                "candidate_id": int(row["id"]),
                "document_id": int(row["document_id"]),
                "candidate_index": int(row["candidate_index"]),
                "pdf_page_start": row["pdf_page_start"],
                "pdf_page_end": row["pdf_page_end"],
                "chapter_id": row["chapter_id"],
                "raw_candidate_text_summary": _preview(str(row["chunk_text"] or "")),
                "corrected_text": selected,
                "corrected_text_summary": _preview(selected),
                "text_source": "correction" if row["correction_id"] is not None else "raw_candidate",
                "correction_id": int(row["correction_id"]) if row["correction_id"] is not None else None,
                "source_line_ids": source_line_ids,
                "source_line_keys": json.loads(row["source_line_keys_json"] or "[]"),
                "heading_metadata": {
                    "available": False,
                    "section_title": None,
                    "reason": "candidate schema does not persist Marker heading metadata",
                },
                "quality_metrics": quality_metrics(selected),
                "location_eligible": bool(source_line_ids) and not missing_line_ids,
                "missing_source_line_ids": missing_line_ids,
            }
        )
    return result


def build_old_candidate_mapping(old_chunks: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    eligible_candidates = [candidate for candidate in candidates if candidate.get("location_eligible")]
    mappings: list[dict[str, Any]] = []
    unmapped_old_chunks: list[int] = []
    unmapped_candidates: list[int] = [
        int(candidate["candidate_id"]) for candidate in candidates if not candidate.get("location_eligible")
    ]
    risks: list[str] = []
    same_count = len(old_chunks) == len(eligible_candidates) and bool(old_chunks)
    if same_count:
        for old, candidate in zip(old_chunks, eligible_candidates):
            old_line_ids = set(old["existing_layout_line_links"]["line_ids"])
            source_line_ids = set(candidate["source_line_ids"])
            overlap = sorted(old_line_ids.intersection(source_line_ids))
            pair_risks = []
            if overlap:
                method = "order+source_line_overlap"
                confidence = "high"
            elif old_line_ids:
                method = "order"
                confidence = "low"
                pair_risks.append("ordered pair has no source-line overlap with existing links")
            else:
                method = "order"
                confidence = "medium"
                pair_risks.append("old chunk has no existing line links; mapping relies on page order")
            mappings.append(
                {
                    "old_chunk_id": int(old["chunk_id"]),
                    "candidate_id": int(candidate["candidate_id"]),
                    "candidate_index": int(candidate["candidate_index"]),
                    "mapping_method": method,
                    "source_line_overlap": overlap,
                    "confidence": confidence,
                    "risks": pair_risks,
                }
            )
            risks.extend(f"chunk {old['chunk_id']}: {risk}" for risk in pair_risks)
    else:
        risks.append("old chunk count and location-eligible candidate count differ; automatic ordered promotion is unsafe")
        unmapped_old_chunks = [int(old["chunk_id"]) for old in old_chunks]
        unmapped_candidates.extend(int(candidate["candidate_id"]) for candidate in eligible_candidates)
    return {
        "mapping_strategy": "order_with_page_and_source_line_sanity_check" if same_count else "not_automatically_mappable",
        "old_chunk_count": len(old_chunks),
        "candidate_count": len(candidates),
        "location_eligible_candidate_count": len(eligible_candidates),
        "mappings": mappings,
        "unmapped_old_chunks": sorted(set(unmapped_old_chunks)),
        "unmapped_candidates": sorted(set(unmapped_candidates)),
        "mapping_risks": risks,
    }


def build_proposed_db_writes(
    *,
    schema: dict[str, Any],
    recommendation: dict[str, Any],
    mapping: dict[str, Any],
    old_chunks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    promote_run_id: str = "dry_run",
) -> dict[str, Any]:
    if recommendation["strategy"] != "A":
        return {"strategy": recommendation["strategy"], "operations": [], "reason": "scheme B requires separate lifecycle design"}
    old_by_id = {int(chunk["chunk_id"]): chunk for chunk in old_chunks}
    candidate_by_id = {int(candidate["candidate_id"]): candidate for candidate in candidates}
    columns = set(schema["knowledge_chunks_columns"])
    update_columns = [
        column for column in ("chunk_text", "content_hash", "char_count", "token_count", "updated_at") if column in columns
    ]
    schema_gaps = [
        field for field in ("search_text", "display_text", "source_backend", "object_import_mode") if field not in columns
    ]
    operations = []
    planned_snapshots = []
    created_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    for item in mapping["mappings"]:
        old = old_by_id[item["old_chunk_id"]]
        candidate = candidate_by_id[item["candidate_id"]]
        planned_snapshots.append(
            {
                "document_id": int(candidate["document_id"]),
                "page_start": int(candidate["pdf_page_start"]),
                "page_end": int(candidate["pdf_page_end"]),
                "promote_run_id": promote_run_id,
                "chunk_id": item["old_chunk_id"],
                "old_chunk_text": old["old_chunk_text"],
                "old_content_hash": old["old_content_hash"],
                "old_char_count": old["old_char_count"],
                "old_token_count": old["old_token_count"],
                "old_updated_at": old["old_updated_at"],
                "old_line_links_json": json.dumps(old["existing_layout_line_links"], ensure_ascii=False, sort_keys=True),
                "old_candidate_id": None,
                "proposed_candidate_id": item["candidate_id"],
                "proposed_text_hash": _sha256(candidate["corrected_text"]),
                "created_at": created_at,
            }
        )
        operations.append(
            {
                "old_chunk_id": item["old_chunk_id"],
                "candidate_id": item["candidate_id"],
                "snapshot": {
                    "required": True,
                    "available_table": None if not schema["supports_old_chunk_snapshot"] else schema["snapshot_or_audit_tables"][0],
                    "old_chunk_text_summary": old["old_chunk_text_summary"],
                },
                "knowledge_chunks_update": {
                    "columns": update_columns,
                    "new_chunk_text_summary": candidate["corrected_text_summary"],
                    "content_hash_recompute_required": "content_hash" in columns,
                    "unavailable_requested_columns": schema_gaps,
                },
                "layout_line_link_replace": {
                    "delete_existing_page_links": True,
                    "insert_source_line_ids": candidate["source_line_ids"],
                    "match_method": "ocr_layout_first_source_lines",
                    "confidence": "high",
                },
                "candidate_review_status_update": {
                    "proposed_only": True,
                    "candidate_id": item["candidate_id"],
                    "new_status": "promoted",
                },
            }
        )
    return {
        "strategy": "A",
        "performed": False,
        "operations": operations,
        "snapshot_table": SNAPSHOT_TABLE,
        "snapshot_write_order": "snapshots are inserted successfully in the same transaction before canonical chunk or line-link mutation",
        "planned_snapshot_rows": len(planned_snapshots),
        "planned_snapshots": planned_snapshots,
        "snapshot_required_before_apply": True,
        "rollback_backup_required_before_apply": True,
        "schema_gaps": schema_gaps,
        "planned_schema_actions": (
            [] if schema["supports_old_chunk_snapshot"] else [f"create {SNAPSHOT_TABLE} within the apply transaction"]
        ),
        "proposed_import_metadata": {
            "source_backend": PROMOTE_SOURCE_BACKEND,
            "object_import_mode": PROMOTE_OBJECT_IMPORT_MODE,
            "writable_to_knowledge_chunks": not any(field in schema_gaps for field in ("source_backend", "object_import_mode")),
        },
        "rollback_plan": {
            "source_table": SNAPSHOT_TABLE,
            "restore_columns": ["chunk_text", "content_hash", "char_count", "token_count", "updated_at"],
            "restore_line_links_from": "old_line_links_json",
            "mark_vector_source_ids_stale": [
                f"chunk:{candidate_by_id[item['candidate_id']]['document_id']}:{item['old_chunk_id']}"
                for item in mapping["mappings"]
            ],
        },
    }


def build_vector_sync_plan(*, document_id: int, mapping: dict[str, Any]) -> dict[str, Any]:
    affected_ids = [int(item["old_chunk_id"]) for item in mapping["mappings"]]
    return {
        "performed": False,
        "lancedb_writes_performed": False,
        "stale_after_apply_chunk_ids": affected_ids,
        "stale_after_apply_source_ids": [f"chunk:{document_id}:{chunk_id}" for chunk_id in affected_ids],
        "existing_sync_entrypoint": "scripts/sync_vector_store.py --kind passages",
        "existing_sync_behavior": "incremental stale detection after collecting the passage source set",
        "affected_only_sync_supported": True,
        "affected_only_command_shape": "scripts/sync_vector_store.py --kind passages --source-id <chunk source id> --dry-run",
        "next_step_required": "run affected-only passage sync dry-run after canonical apply planning is approved",
        "full_lancedb_rebuild_allowed": False,
    }


def build_medium_mapping_explanations(
    connection: sqlite3.Connection,
    *,
    old_chunks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    mapping: dict[str, Any],
) -> list[dict[str, Any]]:
    old_by_id = {int(item["chunk_id"]): item for item in old_chunks}
    candidate_by_id = {int(item["candidate_id"]): item for item in candidates}
    by_index = {int(item["candidate_index"]): item for item in candidates}
    explanations = []
    for mapped in mapping["mappings"]:
        if mapped["confidence"] != "medium":
            continue
        candidate = candidate_by_id[mapped["candidate_id"]]
        old = old_by_id[mapped["old_chunk_id"]]
        source_ids = list(candidate["source_line_ids"])
        line_rows = []
        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            line_rows = connection.execute(
                f"""
                SELECT id, line_index, text
                FROM pdf_page_layout_lines
                WHERE id IN ({placeholders})
                ORDER BY line_index
                """,
                source_ids,
            ).fetchall()
        previous = by_index.get(int(candidate["candidate_index"]) - 1)
        following = by_index.get(int(candidate["candidate_index"]) + 1)
        explanations.append(
            {
                "old_chunk_id": mapped["old_chunk_id"],
                "candidate_id": mapped["candidate_id"],
                "old_chunk_text_summary": old["old_chunk_text_summary"],
                "candidate_text": candidate["corrected_text"],
                "candidate_source_line_ids": source_ids,
                "ocr_line_text": [
                    {"line_id": int(row["id"]), "line_index": int(row["line_index"]), "text": str(row["text"] or "")}
                    for row in line_rows
                ],
                "previous_candidate_boundary": _candidate_boundary(previous),
                "next_candidate_boundary": _candidate_boundary(following),
                "old_quality_metrics": old["old_issues"],
                "candidate_quality_metrics": candidate["quality_metrics"],
                "recommended_mapping": f"{mapped['old_chunk_id']} -> candidate {mapped['candidate_id']}",
                "confidence": mapped["confidence"],
                "confidence_rationale": (
                    "candidate has Surya line anchors and page-order continuity, but the old chunk has no "
                    "prior layout line links for overlap verification"
                ),
            }
        )
    return explanations


def build_apply_safety_gate(
    *,
    schema: dict[str, Any],
    mapping: dict[str, Any],
    confirm_medium_mapping: bool,
    affected_only_vector_sync_available: bool,
    document_id: int = ALLOWED_APPLY_SCOPE["document_id"],
    page_start: int = ALLOWED_APPLY_SCOPE["page_start"],
    page_end: int = ALLOWED_APPLY_SCOPE["page_end"],
) -> dict[str, Any]:
    has_medium = any(item["confidence"] == "medium" for item in mapping["mappings"])
    requested_scope = {
        "document_id": int(document_id),
        "page_start": int(page_start),
        "page_end": int(page_end),
    }
    mapped_chunk_ids = sorted(int(item["old_chunk_id"]) for item in mapping["mappings"])
    mapped_candidate_ids = sorted(int(item["candidate_id"]) for item in mapping["mappings"])
    snapshot_table_exists = bool(schema.get("supports_old_chunk_snapshot"))
    chunk_columns = set(schema.get("knowledge_chunks_columns", []))
    candidate_columns = set(schema.get("candidate_columns", []))
    link_columns = set(schema.get("chunk_layout_line_links_columns", []))
    required_chunk_columns = {"chunk_text", "content_hash", "char_count", "token_count", "updated_at"}
    required_candidate_columns = {"review_status"}
    required_link_columns = {
        "chunk_id",
        "document_id",
        "pdf_page",
        "line_id",
        "match_method",
        "overlap_score",
        "confidence",
        "created_at",
    }
    blockers = []
    if not PROMOTE_APPLY_ENABLED:
        blockers.append("promote apply feature flag is disabled")
    if not schema.get("snapshot_schema_helper_available"):
        blockers.append("snapshot schema helper unavailable")
    if not affected_only_vector_sync_available:
        blockers.append("affected-only passage vector sync unavailable")
    if has_medium and not confirm_medium_mapping:
        blockers.append("missing --confirm-medium-mapping")
    if requested_scope != ALLOWED_APPLY_SCOPE:
        blockers.append("apply scope is outside the reviewed doc=3/page=390 range")
    if mapped_chunk_ids != ALLOWED_APPLY_CHUNK_IDS or mapped_candidate_ids != list(range(1, 7)):
        blockers.append("apply requires the reviewed page 390 six-chunk mapping")
    if missing := sorted(required_chunk_columns - chunk_columns):
        blockers.append("knowledge_chunks missing apply columns: " + ", ".join(missing))
    if missing := sorted(required_candidate_columns - candidate_columns):
        blockers.append("ocr_first_chunk_candidates missing apply columns: " + ", ".join(missing))
    if missing := sorted(required_link_columns - link_columns):
        blockers.append("chunk_layout_line_links missing apply columns: " + ", ".join(missing))
    return {
        "apply_enabled": PROMOTE_APPLY_ENABLED,
        "allowed_apply_scope": ALLOWED_APPLY_SCOPE,
        "scope_allowed": requested_scope == ALLOWED_APPLY_SCOPE,
        "snapshot_required_before_chunk_update": True,
        "snapshot_table_exists": snapshot_table_exists,
        "snapshot_table_will_be_created_on_apply": not snapshot_table_exists,
        "automatic_sqlite_backup_required": True,
        "affected_only_vector_sync_required": True,
        "affected_source_ids": list(ALLOWED_APPLY_SOURCE_IDS),
        "required_apply_columns_present": not any(
            (
                required_chunk_columns - chunk_columns,
                required_candidate_columns - candidate_columns,
                required_link_columns - link_columns,
            )
        ),
        "medium_mapping_confirmation_required": has_medium,
        "medium_mapping_confirmed": bool(confirm_medium_mapping),
        "ready": not blockers,
        "blockers": blockers,
    }


def build_risk_report(
    *,
    mapping: dict[str, Any],
    proposed_writes: dict[str, Any],
    vector_sync_plan: dict[str, Any],
    safety_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_trace_risk": "medium until replacement line links are transactionally written and verified against Surya source_line_ids",
        "old_chunk_reference_risk": "low with strategy A because chunk ids are retained; content consumers still require regression checks",
        "embedding_stale_risk": (
            f"high after apply until {len(vector_sync_plan['stale_after_apply_source_ids'])} affected passage vectors are refreshed"
        ),
        "search_snippet_consistency_risk": "medium because canonical chunk text would change while optional search_text/display_text columns do not exist",
        "mapping_risk": mapping["mapping_risks"],
        "rollback_path": "restore snapshotted knowledge_chunks rows and prior chunk_layout_line_links, then refresh only affected passage vectors",
        "backup_requirements": [
            "durable old knowledge_chunks snapshot including text/hash/count/timestamp fields",
            "snapshot of existing chunk_layout_line_links for mapped chunks on the target page",
            "captured affected vector source ids before post-apply sync",
        ],
        "schema_gaps": proposed_writes.get("schema_gaps", []),
        "apply_blockers": list((safety_gate or {}).get("blockers", [])),
    }


def quality_metrics(text: str) -> dict[str, int]:
    issues = detect_quality_issues(text)
    corrected = correction_quality_metrics(text)
    return {
        "html_tag_count": int(issues["html_tag_count"]),
        "math_noise_count": int(issues["math_noise_count"]),
        "repeated_token_count": int(issues["repeated_token_count"]),
        "page_number_noise_count": int(issues["page_number_noise_count"]),
        "broken_sentence_count": int(issues["broken_sentence_count"]),
        "known_ocr_error_count": int(corrected["known_ocr_error_count"]),
        "suspicious_symbol_count": int(corrected["suspicious_symbol_count"]),
    }


def _load_line_links(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    page_start: int,
    page_end: int,
) -> dict[int, dict[str, Any]]:
    columns = _table_columns(connection, "chunk_layout_line_links")
    if not columns:
        return {}
    overlap_expr = "overlap_score" if "overlap_score" in columns else "NULL AS overlap_score"
    created_expr = "created_at" if "created_at" in columns else "NULL AS created_at"
    rows = connection.execute(
        f"""
        SELECT id, chunk_id, document_id, pdf_page, line_id, match_method, {overlap_expr}, confidence, {created_expr}
        FROM chunk_layout_line_links
        WHERE document_id = ? AND pdf_page BETWEEN ? AND ?
        ORDER BY chunk_id, line_id
        """,
        (document_id, page_start, page_end),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = result.setdefault(
            int(row["chunk_id"]), {"count": 0, "line_ids": [], "match_methods": [], "confidence_values": [], "links": []}
        )
        item["count"] += 1
        item["line_ids"].append(int(row["line_id"]))
        if row["match_method"] not in item["match_methods"]:
            item["match_methods"].append(row["match_method"])
        if row["confidence"] not in item["confidence_values"]:
            item["confidence_values"].append(row["confidence"])
        item["links"].append(
            {
                "document_id": int(row["document_id"]),
                "pdf_page": int(row["pdf_page"]),
                "line_id": int(row["line_id"]),
                "match_method": str(row["match_method"]),
                "overlap_score": row["overlap_score"],
                "confidence": row["confidence"],
                "created_at": row["created_at"],
            }
        )
    return result


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    ]


def _json_int_list(raw: Any) -> list[int]:
    return [int(value) for value in json.loads(raw or "[]")]


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _candidate_boundary(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "candidate_id": int(candidate["candidate_id"]),
        "candidate_index": int(candidate["candidate_index"]),
        "source_line_ids": list(candidate["source_line_ids"]),
        "text_summary": candidate["corrected_text_summary"],
    }


def _preview(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _text_report(report: dict[str, Any]) -> str:
    return (
        f"{report['status']} doc={report['document_id']} page={report['page_start']} "
        f"strategy={report['recommendation']['strategy']} old={len(report['old_chunks'])} "
        f"candidates={len(report['corrected_candidates'])}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
