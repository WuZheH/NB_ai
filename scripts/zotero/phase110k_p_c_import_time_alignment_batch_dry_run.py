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
from scripts.phase110k_p_b_alignment_writeback_plan import build_writeback_plan
from scripts.phase110k_p_inspiration_match_readiness_dry_run import (
    build_readiness_dry_run_report,
)


MODE = "phase110k_p_c_import_time_alignment_batch_dry_run_v1"
ALIGNMENT_MODE = "zotero_note_import_time_evidence_alignment_batch_plan"
TABLE_NAME = "zotero_inspiration_notes"
PROCESSABLE_STATUSES = {None, "", "not_attempted", "unmatched", "ambiguous"}
ALREADY_ALIGNED_STATUSES = {"matched", "span_matched"}
SUMMARY_FIELDS = (
    "total_notes_seen",
    "planned_count",
    "skipped_already_aligned_count",
    "matched_count",
    "span_matched_count",
    "ambiguous_count",
    "unmatched_count",
    "blocked_count",
    "ready_for_prompt_count",
    "needs_review_count",
)


def build_batch_dry_run_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    attachment_key: str | None = None,
    document_id: int | None = None,
    client_note_id: str | None = None,
    server_note_id: str | None = None,
    limit: int | None = None,
    include_already_aligned: bool = False,
) -> dict[str, Any]:
    db = Path(db_path)
    filters = {
        "attachment_key": attachment_key,
        "document_id": document_id,
        "client_note_id": client_note_id,
        "server_note_id": server_note_id,
        "limit": limit,
        "include_already_aligned": include_already_aligned,
        "plan_only": True,
    }
    notes = _load_note_rows(
        db,
        attachment_key=attachment_key,
        client_note_id=client_note_id,
        server_note_id=server_note_id,
        limit=limit,
    )
    items: list[dict[str, Any]] = []
    skipped_already_aligned_count = 0
    skipped_document_filter_count = 0

    for note in notes:
        current_status = _normalize_status(note.get("evidence_alignment_status"))
        if current_status in ALREADY_ALIGNED_STATUSES and not include_already_aligned:
            skipped_already_aligned_count += 1
            continue

        dry_run_report = build_readiness_dry_run_report(
            db,
            client_note_id=str(note.get("client_note_id") or ""),
            limit=1,
        )
        dry_run_item = (dry_run_report.get("items") or [{}])[0]
        if document_id is not None and not _dry_run_matches_document(
            dry_run_item,
            document_id,
        ):
            skipped_document_filter_count += 1
            continue

        plan = build_writeback_plan(
            db,
            client_note_id=str(note.get("client_note_id") or ""),
            dry_run_report={
                "status": "OK",
                "alignment_mode": "zotero_note_import_time_evidence_alignment_dry_run",
                "count": 1 if dry_run_item else 0,
                "items": [dry_run_item] if dry_run_item else [],
                "db_write_performed": False,
                "mechanism_generated": False,
                "llm_called": False,
            },
        )
        plan_item = (plan.get("items") or [{}])[0]
        items.append(_batch_item(note, dry_run_item, plan_item))

    summary = _summary(
        total_notes_seen=len(notes),
        skipped_already_aligned_count=skipped_already_aligned_count,
        skipped_document_filter_count=skipped_document_filter_count,
        items=items,
    )
    return {
        "status": "OK",
        "mode": MODE,
        "alignment_mode": ALIGNMENT_MODE,
        "db_path": str(db),
        "filters": filters,
        "count": len(items),
        "items": items,
        "summary": summary,
        "db_write_performed": False,
        "matched_fields_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "mechanism_draft_candidates_write_performed": False,
    }


def _batch_item(
    note: Mapping[str, Any],
    dry_run_item: Mapping[str, Any],
    plan_item: Mapping[str, Any],
) -> dict[str, Any]:
    selected_text_length = int(note.get("selected_text_length") or 0)
    blockers = list(
        dict.fromkeys(
            list(dry_run_item.get("blockers") or [])
            + list(plan_item.get("blockers") or [])
        )
    )
    warnings = list(
        dict.fromkeys(
            list(dry_run_item.get("alignment_warnings") or [])
            + list(dry_run_item.get("warnings") or [])
            + list(plan_item.get("warnings") or [])
        )
    )
    if selected_text_length <= 0 and "selected_text_empty" not in blockers:
        blockers.append("selected_text_empty")
    if note.get("pdf_page") is None and selected_text_length > 0 and "page_missing" not in warnings:
        warnings.append("page_missing")

    proposed_writeback = dict(plan_item.get("proposed_writeback") or {})
    if selected_text_length <= 0:
        proposed_writeback = {}

    return {
        "server_note_id": note.get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "zotero_attachment_key": note.get("zotero_attachment_key"),
        "zotero_item_key": note.get("zotero_item_key"),
        "pdf_page": note.get("pdf_page"),
        "page_label": note.get("page_label"),
        "selected_text_length": selected_text_length,
        "note_text_preview": _preview(note.get("note_text")),
        "current_alignment_status": _normalize_status(
            note.get("evidence_alignment_status")
        ) or "not_attempted",
        "dry_run_result_summary": _dry_run_summary(dry_run_item),
        "proposed_writeback": proposed_writeback,
        "writeback_allowed": False,
        "apply_supported": False,
        "blockers": blockers,
        "warnings": warnings,
        "recommended_next_action": _recommended_next_action(
            dry_run_item,
            proposed_writeback,
            blockers,
        ),
    }


def _dry_run_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "document_match_status": item.get("document_match_status"),
        "chunk_match_status": item.get("chunk_match_status"),
        "evidence_alignment_status": item.get("evidence_alignment_status"),
        "alignment_confidence": item.get("alignment_confidence"),
        "alignment_method": item.get("alignment_method"),
        "object_readiness_status": item.get("object_readiness_status"),
        "mechanism_readiness_status": item.get("mechanism_readiness_status"),
        "recommended_next_action": item.get("recommended_next_action"),
    }


def _summary(
    *,
    total_notes_seen: int,
    skipped_already_aligned_count: int,
    skipped_document_filter_count: int,
    items: list[Mapping[str, Any]],
) -> dict[str, int]:
    summary = {field: 0 for field in SUMMARY_FIELDS}
    summary["total_notes_seen"] = total_notes_seen
    summary["skipped_already_aligned_count"] = skipped_already_aligned_count
    summary["skipped_document_filter_count"] = skipped_document_filter_count
    for item in items:
        proposed = item.get("proposed_writeback") or {}
        dry = item.get("dry_run_result_summary") or {}
        status = _summary_alignment_status(proposed, dry)
        if proposed:
            summary["planned_count"] += 1
        if status == "span_matched":
            summary["span_matched_count"] += 1
        elif status == "matched":
            summary["matched_count"] += 1
        elif status == "ambiguous":
            summary["ambiguous_count"] += 1
        else:
            summary["unmatched_count"] += 1
        if item.get("blockers"):
            summary["blocked_count"] += 1
        if dry.get("mechanism_readiness_status") == "ready_for_prompt":
            summary["ready_for_prompt_count"] += 1
        if not proposed or status in {"ambiguous", "unmatched"}:
            summary["needs_review_count"] += 1
    return summary


def _summary_alignment_status(
    proposed: Mapping[str, Any],
    dry: Mapping[str, Any],
) -> str:
    proposed_status = proposed.get("evidence_alignment_status")
    if proposed_status:
        return str(proposed_status)
    dry_statuses = {
        str(dry.get("document_match_status") or ""),
        str(dry.get("chunk_match_status") or ""),
        str(dry.get("evidence_alignment_status") or ""),
    }
    if "ambiguous" in dry_statuses:
        return "ambiguous"
    return str(dry.get("evidence_alignment_status") or "unmatched")


def _recommended_next_action(
    dry_run_item: Mapping[str, Any],
    proposed_writeback: Mapping[str, Any],
    blockers: list[str],
) -> str:
    if "selected_text_empty" in blockers:
        return "capture_selection_required"
    if not proposed_writeback:
        return "needs_evidence_alignment_review"
    if dry_run_item.get("mechanism_readiness_status") == "ready_for_prompt":
        return "ready_for_manual_prompt_preview"
    return "evidence_alignment_writeback_plan_ready_mechanism_blocked"


def _load_note_rows(
    db_path: Path,
    *,
    attachment_key: str | None,
    client_note_id: str | None,
    server_note_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    with sqlite3.connect(_sqlite_uri(db_path), uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, TABLE_NAME):
            return []
        columns = set(_columns(connection, TABLE_NAME))
        selected = [
            "id",
            _select_expr(columns, "server_note_id"),
            _select_expr(columns, "client_note_id"),
            _select_expr(columns, "zotero_attachment_key"),
            _select_expr(columns, "zotero_item_key"),
            _select_expr(columns, "pdf_page"),
            _select_expr(columns, "page_label"),
            _select_expr(columns, "note_text"),
            _select_expr(columns, "evidence_alignment_status"),
            "LENGTH(COALESCE(selected_text, '')) AS selected_text_length"
            if "selected_text" in columns
            else "0 AS selected_text_length",
        ]
        where = ["1 = 1"]
        params: list[Any] = []
        if attachment_key:
            where.append("zotero_attachment_key = ?")
            params.append(attachment_key)
        if client_note_id:
            where.append("client_note_id = ?")
            params.append(client_note_id)
        if server_note_id:
            where.append("server_note_id = ?")
            params.append(server_note_id)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(0, int(limit)))
        rows = connection.execute(
            f"""
            SELECT {', '.join(selected)}
            FROM {TABLE_NAME}
            WHERE {' AND '.join(where)}
            ORDER BY id
            {limit_sql}
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def _dry_run_matches_document(item: Mapping[str, Any], document_id: int) -> bool:
    proposed_doc_ids = {
        candidate.get("document_id")
        for candidate in item.get("document_candidates") or []
    }
    chunk_doc_ids = {
        chunk.get("document_id")
        for chunk in item.get("chunk_candidates") or []
    }
    span_doc_ids = {
        span.get("document_id")
        for span in item.get("chunk_span_candidates") or []
    }
    return document_id in {
        int(value)
        for value in proposed_doc_ids | chunk_doc_ids | span_doc_ids
        if _optional_int(value) is not None
    }


def _preview(value: Any, *, max_length: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(connection, table):
        return []
    return [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _select_expr(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode=ro"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only K-P-C Zotero note import-time evidence alignment "
            "batch dry-run."
        )
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--attachment-key")
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--client-note-id")
    parser.add_argument("--server-note-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-already-aligned", action="store_true")
    parser.add_argument("--plan-only", action="store_true", default=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_batch_dry_run_report(
        args.db_path,
        attachment_key=args.attachment_key,
        document_id=args.document_id,
        client_note_id=args.client_note_id,
        server_note_id=args.server_note_id,
        limit=args.limit,
        include_already_aligned=args.include_already_aligned,
    )
    _emit(report, as_json=args.json)
    return 0


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
