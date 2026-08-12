from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from scripts.phase110k_p_inspiration_match_readiness_dry_run import (
    build_readiness_dry_run_report,
)


MODE = "phase110k_p_b_alignment_writeback_plan_v1"
ALIGNMENT_MODE = "zotero_note_import_time_evidence_alignment_writeback_plan"
TABLE_NAME = "zotero_inspiration_notes"
AUDITED_FIELDS = (
    "matched_document_id",
    "matched_chunk_id",
    "matched_object_ids_json",
    "matched_object_ids",
    "matched_chunk_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
)
CURRENT_FIELD_CANDIDATES = (
    "matched_document_id",
    "matched_chunk_id",
    "matched_object_ids_json",
    "matched_object_ids",
)
FUTURE_WRITEBACK_FIELDS = (
    "matched_document_id",
    "matched_chunk_id",
    "matched_chunk_ids_json",
    "matched_object_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
)
RECOMMENDED_FUTURE_MIGRATION = (
    "add matched_chunk_ids_json TEXT",
    "add evidence_alignment_status TEXT",
    "add alignment_confidence TEXT",
    "add alignment_method TEXT",
    "add alignment_warnings_json TEXT",
)
ALIGNMENT_WRITEBACK_COLUMNS = (
    "matched_document_id",
    "matched_chunk_id",
    "matched_chunk_ids_json",
    "matched_object_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
)
DEFAULT_JSON_ARRAY_VALUES = (None, "", "[]")
DEFAULT_ALIGNMENT_STATUS_VALUES = (None, "", "not_attempted")


class ApplyRefused(RuntimeError):
    """Raised when an explicit alignment writeback apply request is unsafe."""


def build_writeback_plan(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    client_note_id: str | None = None,
    server_note_id: str | None = None,
    attachment_key: str | None = None,
    limit: int | None = None,
    include_schema_audit_only: bool = False,
    dry_run_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    db = Path(db_path)
    schema_audit = _schema_audit(db)
    items: list[dict[str, Any]] = []
    if not include_schema_audit_only:
        dry_run = dry_run_report or build_readiness_dry_run_report(
            db,
            client_note_id=client_note_id,
            server_note_id=server_note_id,
            attachment_key=attachment_key,
            limit=limit,
        )
        current_fields = _current_fields_by_note_id(
            db,
            client_note_id=client_note_id,
            server_note_id=server_note_id,
            attachment_key=attachment_key,
            limit=limit,
        )
        dry_run_items = _filtered_dry_run_items(
            dry_run["items"],
            client_note_id=client_note_id,
            server_note_id=server_note_id,
            attachment_key=attachment_key,
            limit=limit,
        )
        items = [
            _plan_item(item, current_fields.get(item.get("note_id")), schema_audit)
            for item in dry_run_items
        ]
    return {
        "status": "OK",
        "mode": MODE,
        "alignment_mode": ALIGNMENT_MODE,
        "input_source": "dry_run_json" if dry_run_report is not None else "live_dry_run",
        "db_path": str(db),
        "count": len(items),
        "items": items,
        "schema_audit": schema_audit,
        "db_write_performed": False,
        "schema_write_performed": False,
        "matched_fields_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
    }


def apply_alignment_writeback(
    db_path: str | Path,
    report: dict[str, Any],
    *,
    client_note_id: str | None,
    expected_server_note_id: str | None,
    expected_attachment_key: str | None,
) -> dict[str, Any]:
    if not client_note_id:
        raise ApplyRefused("client_note_id_required")
    if not expected_server_note_id:
        raise ApplyRefused("expected_server_note_id_required")
    if not expected_attachment_key:
        raise ApplyRefused("expected_attachment_key_required")
    if report.get("input_source") != "dry_run_json":
        raise ApplyRefused("apply_requires_from_dry_run_json")

    item = _single_apply_item(report, client_note_id)
    proposed = dict(item.get("proposed_writeback") or {})
    if not proposed:
        raise ApplyRefused("proposed_writeback_empty")
    selected_text_length = item.get("selected_text_length")
    if selected_text_length is not None and int(selected_text_length or 0) <= 0:
        raise ApplyRefused("selected_text_empty")
    if proposed.get("evidence_alignment_status") not in {"matched", "span_matched"}:
        raise ApplyRefused("evidence_alignment_not_matched")
    if proposed.get("alignment_confidence") not in {"high", "medium"}:
        raise ApplyRefused("alignment_confidence_too_low")

    schema_fields = set(report.get("schema_audit", {}).get("existing_fields") or [])
    missing_writeback_fields = [
        field for field in ALIGNMENT_WRITEBACK_COLUMNS
        if field not in schema_fields
    ]
    if missing_writeback_fields:
        raise ApplyRefused(
            "schema_missing_writeback_fields:" + ",".join(missing_writeback_fields)
        )

    db = Path(db_path)
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, server_note_id, client_note_id, source,
                   zotero_attachment_key, LENGTH(COALESCE(selected_text, '')) AS selected_text_length,
                   matched_document_id, matched_chunk_id, matched_chunk_ids_json,
                   matched_object_ids_json, evidence_alignment_status,
                   alignment_confidence, alignment_method, alignment_warnings_json
            FROM zotero_inspiration_notes
            WHERE client_note_id = ?
            """,
            (client_note_id,),
        ).fetchone()
        if row is None:
            raise ApplyRefused("target_note_not_found")
        _validate_apply_target_row(
            row,
            expected_server_note_id=expected_server_note_id,
            expected_attachment_key=expected_attachment_key,
        )
        cursor = connection.execute(
            """
            UPDATE zotero_inspiration_notes
            SET matched_document_id = ?,
                matched_chunk_id = ?,
                matched_chunk_ids_json = ?,
                matched_object_ids_json = ?,
                evidence_alignment_status = ?,
                alignment_confidence = ?,
                alignment_method = ?,
                alignment_warnings_json = ?
            WHERE client_note_id = ?
              AND server_note_id = ?
              AND zotero_attachment_key = ?
              AND source = 'zotero_plugin'
              AND LENGTH(COALESCE(selected_text, '')) > 0
              AND matched_document_id IS NULL
              AND matched_chunk_id IS NULL
              AND COALESCE(matched_chunk_ids_json, '[]') = '[]'
              AND COALESCE(matched_object_ids_json, '[]') = '[]'
              AND COALESCE(evidence_alignment_status, 'not_attempted') = 'not_attempted'
              AND alignment_confidence IS NULL
              AND alignment_method IS NULL
              AND COALESCE(alignment_warnings_json, '[]') = '[]'
            """,
            (
                proposed["matched_document_id"],
                proposed["matched_chunk_id"],
                proposed["matched_chunk_ids_json"],
                proposed["matched_object_ids_json"],
                proposed["evidence_alignment_status"],
                proposed["alignment_confidence"],
                proposed["alignment_method"],
                proposed["alignment_warnings_json"],
                client_note_id,
                expected_server_note_id,
                expected_attachment_key,
            ),
        )
        affected_rows = cursor.rowcount
        if affected_rows != 1:
            raise ApplyRefused(f"affected_rows_not_one:{affected_rows}")

    return {
        "status": "applied",
        "client_note_id": client_note_id,
        "server_note_id": expected_server_note_id,
        "zotero_attachment_key": expected_attachment_key,
        "affected_rows": affected_rows,
        "applied_fields": proposed,
        "db_write_performed": True,
        "schema_write_performed": False,
        "matched_fields_write_performed": True,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
    }


def _plan_item(
    item: Mapping[str, Any],
    current_fields: Mapping[str, Any] | None,
    schema_audit: Mapping[str, Any],
) -> dict[str, Any]:
    proposed_writeback, blockers, warnings, rationale = _proposed_writeback(item)
    return {
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_attachment_key": item.get("zotero_attachment_key"),
        "zotero_item_key": item.get("zotero_item_key"),
        "pdf_page": item.get("pdf_page"),
        "page_label": item.get("page_label"),
        "selected_text_length": len(str(item.get("selected_text") or "")),
        "current_fields": dict(current_fields or _empty_current_fields()),
        "proposed_writeback": proposed_writeback,
        "schema_gap": _schema_gap(proposed_writeback, schema_audit),
        "writeback_allowed": False,
        "apply_supported": False,
        "blockers": blockers,
        "warnings": warnings,
        "rationale": rationale,
    }


def _filtered_dry_run_items(
    items: list[Mapping[str, Any]],
    *,
    client_note_id: str | None,
    server_note_id: str | None,
    attachment_key: str | None,
    limit: int | None,
) -> list[Mapping[str, Any]]:
    filtered = []
    for item in items:
        if client_note_id and item.get("client_note_id") != client_note_id:
            continue
        if server_note_id and item.get("server_note_id") != server_note_id:
            continue
        if attachment_key and item.get("zotero_attachment_key") != attachment_key:
            continue
        filtered.append(item)
    if limit is not None:
        return filtered[: max(0, int(limit))]
    return filtered


def _single_apply_item(
    report: Mapping[str, Any],
    client_note_id: str,
) -> Mapping[str, Any]:
    matches = [
        item for item in report.get("items") or []
        if item.get("client_note_id") == client_note_id
    ]
    if not matches:
        raise ApplyRefused("plan_item_not_found")
    if len(matches) > 1:
        raise ApplyRefused("multiple_plan_items_for_client_note_id")
    return matches[0]


def _validate_apply_target_row(
    row: sqlite3.Row,
    *,
    expected_server_note_id: str,
    expected_attachment_key: str,
) -> None:
    if row["server_note_id"] != expected_server_note_id:
        raise ApplyRefused("server_note_id_mismatch")
    if row["zotero_attachment_key"] != expected_attachment_key:
        raise ApplyRefused("attachment_key_mismatch")
    if row["source"] != "zotero_plugin":
        raise ApplyRefused("source_not_zotero_plugin")
    if int(row["selected_text_length"] or 0) <= 0:
        raise ApplyRefused("selected_text_empty")
    if row["matched_document_id"] is not None:
        raise ApplyRefused("matched_document_id_already_set")
    if row["matched_chunk_id"] is not None:
        raise ApplyRefused("matched_chunk_id_already_set")
    if row["matched_chunk_ids_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        raise ApplyRefused("matched_chunk_ids_json_already_set")
    if row["matched_object_ids_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        raise ApplyRefused("matched_object_ids_json_already_set")
    if row["evidence_alignment_status"] not in DEFAULT_ALIGNMENT_STATUS_VALUES:
        raise ApplyRefused("evidence_alignment_status_already_set")
    if row["alignment_confidence"] is not None:
        raise ApplyRefused("alignment_confidence_already_set")
    if row["alignment_method"] is not None:
        raise ApplyRefused("alignment_method_already_set")
    if row["alignment_warnings_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        raise ApplyRefused("alignment_warnings_json_already_set")


def _proposed_writeback(
    item: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings = list(dict.fromkeys(item.get("alignment_warnings") or item.get("warnings") or []))
    rationale = [
        "writeback plan only; production writes disabled",
        "mechanism prompt readiness is separate from evidence alignment writeback",
    ]
    document_id = _matched_document_id(item)
    if document_id is None:
        blockers.append(f"document_{item.get('document_match_status') or 'unmatched'}")

    alignment_status = _evidence_alignment_status(item)
    chunk_ids = _matched_chunk_ids(item, alignment_status)
    if not chunk_ids:
        blockers.append(f"evidence_alignment_{alignment_status}")

    if blockers:
        return {}, list(dict.fromkeys(blockers)), warnings, rationale

    if alignment_status == "span_matched":
        rationale.append("span evidence preserved in matched_chunk_ids_json")
    else:
        rationale.append("single chunk evidence still writes matched_chunk_ids_json")

    object_ids = [
        int(candidate["object_id"])
        for candidate in item.get("object_candidates") or []
        if candidate.get("object_id") is not None
        and candidate.get("approval_status") == "approved"
    ]
    if not object_ids:
        warnings.append("approved_object_missing_for_writeback_plan")

    proposed = {
        "matched_document_id": document_id,
        "matched_chunk_id": chunk_ids[0],
        "matched_chunk_ids_json": json.dumps(chunk_ids),
        "matched_object_ids_json": json.dumps(object_ids),
        "evidence_alignment_status": alignment_status,
        "alignment_confidence": _alignment_confidence(item, alignment_status),
        "alignment_method": _alignment_method(alignment_status),
        "alignment_warnings_json": json.dumps(list(dict.fromkeys(warnings))),
    }
    return proposed, [], list(dict.fromkeys(warnings)), rationale


def _evidence_alignment_status(item: Mapping[str, Any]) -> str:
    high_spans = [
        span for span in item.get("chunk_span_candidates") or []
        if span.get("confidence") == "high"
    ]
    if high_spans and item.get("chunk_match_status") == "matched":
        return "span_matched"
    status = str(item.get("evidence_alignment_status") or "").strip()
    if status in {"matched", "span_matched", "ambiguous", "unmatched"}:
        return status
    chunk_status = str(item.get("chunk_match_status") or "").strip()
    return chunk_status if chunk_status in {"ambiguous", "unmatched"} else "unmatched"


def _alignment_confidence(item: Mapping[str, Any], alignment_status: str) -> str | None:
    if alignment_status == "span_matched":
        spans = list(item.get("chunk_span_candidates") or [])
        return str(spans[0].get("confidence")) if spans else item.get("alignment_confidence")
    if alignment_status == "matched":
        chunks = [
            chunk for chunk in item.get("chunk_candidates") or []
            if chunk.get("confidence") == "high"
        ]
        if chunks:
            return str(chunks[0].get("confidence"))
    confidence = item.get("alignment_confidence")
    return str(confidence) if confidence is not None else None


def _alignment_method(alignment_status: str) -> str:
    if alignment_status == "span_matched":
        return "import_time_page_text_span_alignment"
    if alignment_status == "matched":
        return "import_time_page_text_single_chunk_alignment"
    return "import_time_page_text_candidate_alignment"


def _matched_document_id(item: Mapping[str, Any]) -> int | None:
    if item.get("document_match_status") != "matched":
        return None
    candidates = list(item.get("document_candidates") or [])
    if not candidates:
        return None
    preferred = [
        candidate for candidate in candidates
        if candidate.get("corroborated_by_chunk") is True
    ]
    chosen = preferred[0] if preferred else candidates[0]
    return _optional_int(chosen.get("document_id"))


def _matched_chunk_ids(item: Mapping[str, Any], alignment_status: str) -> list[int]:
    if alignment_status == "span_matched":
        spans = [
            span for span in item.get("chunk_span_candidates") or []
            if span.get("confidence") == "high"
        ]
        if not spans:
            return []
        return [
            int(chunk_id)
            for chunk_id in spans[0].get("chunk_ids") or []
            if _optional_int(chunk_id) is not None
        ]
    if alignment_status == "matched":
        chunks = [
            chunk for chunk in item.get("chunk_candidates") or []
            if chunk.get("confidence") == "high"
        ]
        if not chunks:
            return []
        return [int(chunks[0]["chunk_id"])]
    return []


def _schema_gap(
    proposed_writeback: Mapping[str, Any],
    schema_audit: Mapping[str, Any],
) -> list[str]:
    if not proposed_writeback:
        return list(schema_audit.get("missing_fields") or [])
    existing = set(schema_audit.get("existing_fields") or [])
    return [
        field for field in FUTURE_WRITEBACK_FIELDS
        if field in proposed_writeback and field not in existing
    ]


def _schema_audit(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
        existing_fields = _columns(connection, TABLE_NAME)
    missing_fields = [
        field for field in AUDITED_FIELDS
        if field not in existing_fields
    ]
    supported_current_fields = [
        field for field in CURRENT_FIELD_CANDIDATES
        if field in existing_fields
    ]
    return {
        "table": TABLE_NAME,
        "existing_fields": existing_fields,
        "missing_fields": missing_fields,
        "supported_current_fields": supported_current_fields,
        "recommended_future_migration": list(RECOMMENDED_FUTURE_MIGRATION),
        "matched_object_ids_compatibility": (
            "Prefer matched_object_ids_json; if only matched_object_ids exists, "
            "read it as a legacy JSON-compatible object id list."
        ),
        "why_not_applying_migration_in_this_task": (
            "K-P-B preparation is read-only. Production schema migration requires "
            "separate user approval and should not run while preparing the plan."
        ),
    }


def _current_fields_by_note_id(
    db_path: Path,
    *,
    client_note_id: str | None,
    server_note_id: str | None,
    attachment_key: str | None,
    limit: int | None,
) -> dict[int, dict[str, Any]]:
    with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
        if not _table_exists(connection, TABLE_NAME):
            return {}
        columns = set(_columns(connection, TABLE_NAME))
        selected = [
            "id AS note_id",
            _select_expr(columns, "matched_document_id"),
            _select_expr(columns, "matched_chunk_id"),
            _select_expr(columns, "matched_object_ids_json"),
            _select_expr(columns, "matched_object_ids"),
        ]
        where = ["1 = 1"]
        params: list[Any] = []
        if client_note_id:
            where.append("client_note_id = ?")
            params.append(client_note_id)
        if server_note_id:
            where.append("server_note_id = ?")
            params.append(server_note_id)
        if attachment_key:
            where.append("zotero_attachment_key = ?")
            params.append(attachment_key)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(0, int(limit)))
        rows = _query_dicts(
            connection,
            f"""
            SELECT {', '.join(selected)}
            FROM {TABLE_NAME}
            WHERE {' AND '.join(where)}
            ORDER BY id
            {limit_sql}
            """,
            tuple(params),
        )
    return {
        int(row["note_id"]): {
            "matched_document_id": row.get("matched_document_id"),
            "matched_chunk_id": row.get("matched_chunk_id"),
            "matched_object_ids": _matched_object_ids(row),
        }
        for row in rows
    }


def _matched_object_ids(row: Mapping[str, Any]) -> list[Any]:
    json_value = row.get("matched_object_ids_json")
    if json_value is not None:
        return _json_list(json_value)
    return _json_list(row.get("matched_object_ids"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only K-P-B Zotero note import-time evidence alignment "
            "writeback plan."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--client-note-id")
    parser.add_argument("--server-note-id")
    parser.add_argument("--attachment-key")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--from-dry-run-json",
        type=Path,
        help="Build the writeback plan from a saved K-P-A dry-run JSON report.",
    )
    parser.add_argument(
        "--include-schema-audit-only",
        action="store_true",
        help="Return only the read-only zotero_inspiration_notes schema audit.",
    )
    parser.add_argument(
        "--apply-alignment-writeback",
        action="store_true",
        help=(
            "Apply one guarded evidence alignment writeback. Requires "
            "--from-dry-run-json, --client-note-id, --expected-server-note-id, "
            "and --expected-attachment-key."
        ),
    )
    parser.add_argument("--expected-server-note-id")
    parser.add_argument("--expected-attachment-key")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dry_run_report = None
    if args.from_dry_run_json is not None:
        dry_run_report = _load_dry_run_json(args.from_dry_run_json)
    report = build_writeback_plan(
        args.db_path,
        client_note_id=args.client_note_id,
        server_note_id=args.server_note_id,
        attachment_key=args.attachment_key,
        limit=args.limit,
        include_schema_audit_only=args.include_schema_audit_only,
        dry_run_report=dry_run_report,
    )
    if args.apply_alignment_writeback:
        try:
            apply_result = apply_alignment_writeback(
                args.db_path,
                report,
                client_note_id=args.client_note_id,
                expected_server_note_id=args.expected_server_note_id,
                expected_attachment_key=args.expected_attachment_key,
            )
        except ApplyRefused as exc:
            report["status"] = "blocked"
            report["apply_requested"] = True
            report["apply_result"] = {
                "status": "refused",
                "reason": str(exc),
                "db_write_performed": False,
                "schema_write_performed": False,
                "matched_fields_write_performed": False,
                "mechanism_generated": False,
                "llm_called": False,
                "vector_store_write_performed": False,
            }
            _emit(report, as_json=args.json)
            return 2
        report["apply_requested"] = True
        report["apply_result"] = apply_result
        report["db_write_performed"] = True
        report["matched_fields_write_performed"] = True
        report["schema_write_performed"] = False
        report["mechanism_generated"] = False
        report["llm_called"] = False
        report["vector_store_write_performed"] = False
    else:
        report["apply_requested"] = False
    _emit(report, as_json=args.json)
    return 0


def _load_dry_run_json(source_path: Path) -> Mapping[str, Any]:
    return json.loads(source_path.read_text(encoding="utf-8"))


def _emit(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print_json(report)
    else:
        print(dict(report))


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(conn, table):
        return []
    return [
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _query_dicts(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(statement, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _select_expr(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _empty_current_fields() -> dict[str, Any]:
    return {
        "matched_document_id": None,
        "matched_chunk_id": None,
        "matched_object_ids": [],
    }


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode=ro"


if __name__ == "__main__":
    raise SystemExit(main())
