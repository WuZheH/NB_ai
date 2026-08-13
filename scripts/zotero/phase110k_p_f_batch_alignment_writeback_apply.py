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
from scripts.phase110k_p_c_import_time_alignment_batch_dry_run import (
    build_batch_dry_run_report,
)


MODE = "phase110k_p_f_batch_alignment_writeback_apply_v1"
ALIGNMENT_MODE = "zotero_note_import_time_evidence_alignment_batch_writeback"
TABLE_NAME = "zotero_inspiration_notes"
ELIGIBLE_STATUSES = {"matched", "span_matched"}
DEFAULT_JSON_ARRAY_VALUES = {None, "", "[]"}
DEFAULT_ALIGNMENT_STATUS_VALUES = {None, "", "not_attempted"}
WRITEBACK_FIELDS = (
    "matched_document_id",
    "matched_chunk_id",
    "matched_chunk_ids_json",
    "matched_object_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
)
SUMMARY_FIELDS = (
    "total_items",
    "eligible_count",
    "applied_count",
    "skipped_count",
    "blocked_count",
    "already_aligned_skipped_count",
    "ambiguous_or_unmatched_count",
    "selected_text_empty_count",
    "would_apply_count",
    "max_apply_count",
    "errors_count",
)


def build_batch_alignment_writeback_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    batch_dry_run_report: Mapping[str, Any] | None = None,
    attachment_key: str | None = None,
    document_id: int | None = None,
    client_note_id: str | None = None,
    server_note_id: str | None = None,
    limit: int | None = None,
    include_already_aligned: bool = False,
    allow_medium_confidence: bool = False,
    apply_batch_alignment_writeback: bool = False,
    max_apply_count: int | None = None,
) -> dict[str, Any]:
    db = Path(db_path)
    input_payload = {
        "from_batch_dry_run_json": batch_dry_run_report is not None,
        "attachment_key": attachment_key,
        "document_id": document_id,
        "client_note_id": client_note_id,
        "server_note_id": server_note_id,
        "limit": limit,
        "apply_batch_alignment_writeback": apply_batch_alignment_writeback,
        "max_apply_count": max_apply_count,
        "allow_medium_confidence": allow_medium_confidence,
        "include_already_aligned": include_already_aligned,
        "plan_only": not apply_batch_alignment_writeback,
    }
    source_report = batch_dry_run_report or build_batch_dry_run_report(
        db,
        attachment_key=attachment_key,
        document_id=document_id,
        client_note_id=client_note_id,
        server_note_id=server_note_id,
        limit=limit,
        include_already_aligned=include_already_aligned,
    )
    raw_items = _filtered_items(
        source_report.get("items") or [],
        attachment_key=attachment_key,
        client_note_id=client_note_id,
        server_note_id=server_note_id,
        limit=limit,
    )
    schema_missing = _missing_writeback_fields(db) if apply_batch_alignment_writeback else []
    items = [
        _plan_item(
            raw_item,
            allow_medium_confidence=allow_medium_confidence,
            include_already_aligned=include_already_aligned,
            schema_missing=schema_missing,
        )
        for raw_item in raw_items
    ]

    db_write_performed = False
    matched_fields_write_performed = False
    if apply_batch_alignment_writeback:
        db_write_performed = _apply_items(
            db,
            items,
            max_apply_count=max_apply_count,
        )
        matched_fields_write_performed = db_write_performed

    summary = _summary(items, max_apply_count=max_apply_count)
    return {
        "status": _status(apply_batch_alignment_writeback, summary),
        "mode": MODE,
        "alignment_mode": ALIGNMENT_MODE,
        "db_path": str(db),
        "input": input_payload,
        "count": len(items),
        "items": items,
        "summary": summary,
        "db_write_performed": db_write_performed,
        "matched_fields_write_performed": matched_fields_write_performed,
        "schema_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "mechanism_draft_candidates_write_performed": False,
    }


def _plan_item(
    raw_item: Mapping[str, Any],
    *,
    allow_medium_confidence: bool,
    include_already_aligned: bool,
    schema_missing: list[str],
) -> dict[str, Any]:
    proposed = dict(raw_item.get("proposed_writeback") or {})
    blockers = list(dict.fromkeys(raw_item.get("blockers") or []))
    warnings = list(dict.fromkeys(raw_item.get("warnings") or []))
    current_status = _clean_status(raw_item.get("current_alignment_status"))
    selected_text_length = _selected_text_length(raw_item)

    if current_status in ELIGIBLE_STATUSES and not include_already_aligned:
        blockers.append("already_aligned")
    if selected_text_length <= 0:
        blockers.append("selected_text_empty")
    if not proposed:
        blockers.append("proposed_writeback_empty")

    evidence_status = str(proposed.get("evidence_alignment_status") or "")
    if proposed and evidence_status not in ELIGIBLE_STATUSES:
        blockers.append("evidence_alignment_not_matched")
    if proposed and evidence_status in {"ambiguous", "unmatched"}:
        blockers.append(f"evidence_alignment_{evidence_status}")

    confidence = str(proposed.get("alignment_confidence") or "")
    if proposed and not _confidence_allowed(
        confidence,
        allow_medium_confidence=allow_medium_confidence,
    ):
        blockers.append("alignment_confidence_not_allowed")

    if proposed and not _json_id_list(proposed.get("matched_chunk_ids_json")):
        blockers.append("matched_chunk_ids_json_empty")
    if schema_missing:
        blockers.append("schema_missing_writeback_fields:" + ",".join(schema_missing))

    blockers = list(dict.fromkeys(blockers))
    eligible = not blockers and bool(proposed)
    return {
        "server_note_id": raw_item.get("server_note_id"),
        "client_note_id": raw_item.get("client_note_id"),
        "zotero_attachment_key": raw_item.get("zotero_attachment_key"),
        "current_alignment_status": current_status or "not_attempted",
        "selected_text_length": selected_text_length,
        "proposed_writeback": proposed,
        "eligible_for_apply": eligible,
        "apply_blockers": blockers,
        "apply_warnings": warnings,
        "applied": False,
        "affected_rows": 0,
    }


def _apply_items(
    db_path: Path,
    items: list[dict[str, Any]],
    *,
    max_apply_count: int | None,
) -> bool:
    applied_count = 0
    write_performed = False
    for item in items:
        if not item["eligible_for_apply"]:
            continue
        if max_apply_count is not None and applied_count >= max(0, int(max_apply_count)):
            item["apply_blockers"].append("max_apply_count_reached")
            item["eligible_for_apply"] = False
            continue
        result = _apply_one(db_path, item)
        item.update(result)
        if item["applied"]:
            applied_count += 1
            write_performed = True
    return write_performed


def _apply_one(db_path: Path, item: Mapping[str, Any]) -> dict[str, Any]:
    proposed = dict(item.get("proposed_writeback") or {})
    client_note_id = item.get("client_note_id")
    server_note_id = item.get("server_note_id")
    attachment_key = item.get("zotero_attachment_key")
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, source, LENGTH(COALESCE(selected_text, '')) AS selected_text_length,
                       matched_document_id, matched_chunk_id, matched_chunk_ids_json,
                       matched_object_ids_json, evidence_alignment_status,
                       alignment_confidence, alignment_method, alignment_warnings_json
                FROM zotero_inspiration_notes
                WHERE client_note_id = ?
                """,
                (client_note_id,),
            ).fetchall()
            if not rows:
                return _apply_error("target_note_not_found")
            if len(rows) != 1:
                return _apply_error(f"target_note_not_unique:{len(rows)}")
            row = rows[0]
            row_blocker = _row_apply_blocker(row)
            if row_blocker:
                return _apply_blocked(row_blocker)

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
                    server_note_id,
                    attachment_key,
                ),
            )
            affected_rows = int(cursor.rowcount)
            if affected_rows != 1:
                return {
                    "applied": False,
                    "affected_rows": affected_rows,
                    "eligible_for_apply": False,
                    "apply_blockers": [f"affected_rows_not_one:{affected_rows}"],
                    "apply_warnings": [],
                    "error": f"affected_rows_not_one:{affected_rows}",
                }
            connection.commit()
    except sqlite3.Error as exc:
        return _apply_error(f"sqlite_error:{exc}")
    return {
        "applied": True,
        "affected_rows": 1,
        "applied_fields": proposed,
        "error": None,
    }


def _row_apply_blocker(row: sqlite3.Row) -> str | None:
    if row["source"] != "zotero_plugin":
        return "source_not_zotero_plugin"
    if int(row["selected_text_length"] or 0) <= 0:
        return "selected_text_empty"
    if row["matched_document_id"] is not None:
        return "matched_document_id_already_set"
    if row["matched_chunk_id"] is not None:
        return "matched_chunk_id_already_set"
    if row["matched_chunk_ids_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        return "matched_chunk_ids_json_already_set"
    if row["matched_object_ids_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        return "matched_object_ids_json_already_set"
    if row["evidence_alignment_status"] not in DEFAULT_ALIGNMENT_STATUS_VALUES:
        return "evidence_alignment_status_already_set"
    if row["alignment_confidence"] is not None:
        return "alignment_confidence_already_set"
    if row["alignment_method"] is not None:
        return "alignment_method_already_set"
    if row["alignment_warnings_json"] not in DEFAULT_JSON_ARRAY_VALUES:
        return "alignment_warnings_json_already_set"
    return None


def _apply_blocked(reason: str) -> dict[str, Any]:
    return {
        "applied": False,
        "affected_rows": 0,
        "eligible_for_apply": False,
        "apply_blockers": [reason],
        "error": None,
    }


def _apply_error(reason: str) -> dict[str, Any]:
    return {
        "applied": False,
        "affected_rows": 0,
        "eligible_for_apply": False,
        "apply_blockers": [reason],
        "error": reason,
    }


def _summary(
    items: list[Mapping[str, Any]],
    *,
    max_apply_count: int | None,
) -> dict[str, int | None]:
    summary: dict[str, int | None] = {field: 0 for field in SUMMARY_FIELDS}
    summary["total_items"] = len(items)
    summary["max_apply_count"] = max_apply_count
    for item in items:
        blockers = set(item.get("apply_blockers") or [])
        proposed = item.get("proposed_writeback") or {}
        evidence_status = proposed.get("evidence_alignment_status")
        if item.get("eligible_for_apply") or "max_apply_count_reached" in blockers:
            summary["eligible_count"] = int(summary["eligible_count"] or 0) + 1
        if item.get("eligible_for_apply") and not item.get("applied"):
            summary["would_apply_count"] = int(summary["would_apply_count"] or 0) + 1
        if item.get("applied"):
            summary["applied_count"] = int(summary["applied_count"] or 0) + 1
        if blockers:
            summary["blocked_count"] = int(summary["blocked_count"] or 0) + 1
            summary["skipped_count"] = int(summary["skipped_count"] or 0) + 1
        if "already_aligned" in blockers or any(
            str(blocker).endswith("_already_set") for blocker in blockers
        ):
            summary["already_aligned_skipped_count"] = (
                int(summary["already_aligned_skipped_count"] or 0) + 1
            )
        if evidence_status in {"ambiguous", "unmatched"} or {
            "evidence_alignment_not_matched",
            "evidence_alignment_ambiguous",
            "evidence_alignment_unmatched",
        } & blockers:
            summary["ambiguous_or_unmatched_count"] = (
                int(summary["ambiguous_or_unmatched_count"] or 0) + 1
            )
        if "selected_text_empty" in blockers:
            summary["selected_text_empty_count"] = (
                int(summary["selected_text_empty_count"] or 0) + 1
            )
        if item.get("error"):
            summary["errors_count"] = int(summary["errors_count"] or 0) + 1
    return summary


def _status(apply_requested: bool, summary: Mapping[str, Any]) -> str:
    if summary.get("errors_count"):
        return "ERROR"
    if apply_requested and summary.get("applied_count"):
        return "applied"
    return "OK"


def _filtered_items(
    items: list[Mapping[str, Any]],
    *,
    attachment_key: str | None,
    client_note_id: str | None,
    server_note_id: str | None,
    limit: int | None,
) -> list[Mapping[str, Any]]:
    filtered: list[Mapping[str, Any]] = []
    for item in items:
        if attachment_key and item.get("zotero_attachment_key") != attachment_key:
            continue
        if client_note_id and item.get("client_note_id") != client_note_id:
            continue
        if server_note_id and item.get("server_note_id") != server_note_id:
            continue
        filtered.append(item)
    if limit is None:
        return filtered
    return filtered[: max(0, int(limit))]


def _missing_writeback_fields(db_path: Path) -> list[str]:
    with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
        columns = set(_columns(connection, TABLE_NAME))
    return [field for field in WRITEBACK_FIELDS if field not in columns]


def _selected_text_length(item: Mapping[str, Any]) -> int:
    if item.get("selected_text_length") is not None:
        return int(item.get("selected_text_length") or 0)
    selected = item.get("selected_text")
    return len(str(selected or ""))


def _confidence_allowed(
    confidence: str,
    *,
    allow_medium_confidence: bool,
) -> bool:
    if confidence == "high":
        return True
    return allow_medium_confidence and confidence == "medium"


def _clean_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_id_list(value: Any) -> list[int]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return []
    return result


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(conn, table):
        return []
    return [
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode=ro"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "K-P-F explicit batch apply layer for Zotero note import-time "
            "evidence alignment writeback."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--from-batch-dry-run-json", type=Path)
    parser.add_argument("--attachment-key")
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--client-note-id")
    parser.add_argument("--server-note-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply-batch-alignment-writeback", action="store_true")
    parser.add_argument("--max-apply-count", type=int)
    parser.add_argument("--allow-medium-confidence", action="store_true")
    parser.add_argument("--include-already-aligned", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    batch_report = None
    if args.from_batch_dry_run_json is not None:
        batch_report = json.loads(
            args.from_batch_dry_run_json.read_text(encoding="utf-8")
        )
    report = build_batch_alignment_writeback_report(
        args.db_path,
        batch_dry_run_report=batch_report,
        attachment_key=args.attachment_key,
        document_id=args.document_id,
        client_note_id=args.client_note_id,
        server_note_id=args.server_note_id,
        limit=args.limit,
        include_already_aligned=args.include_already_aligned,
        allow_medium_confidence=args.allow_medium_confidence,
        apply_batch_alignment_writeback=args.apply_batch_alignment_writeback,
        max_apply_count=args.max_apply_count,
    )
    _emit(report, as_json=args.json)
    return 2 if report["status"] == "ERROR" else 0


def _emit(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            print(output, end="")
            return
        buffer.write(output.encode("utf-8"))
    else:
        print(dict(report))


if __name__ == "__main__":
    raise SystemExit(main())
