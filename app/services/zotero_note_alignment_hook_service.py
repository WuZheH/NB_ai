from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from scripts.phase110k_p_d_import_alignment_hook_dry_run import (
    build_import_alignment_hook_report,
)
from scripts.phase110k_p_f_batch_alignment_writeback_apply import (
    build_batch_alignment_writeback_report,
)

SERVICE_MODE = "zotero_note_import_time_evidence_alignment_hook_service_v1"

NO_WRITE_FLAGS: dict[str, bool] = {
    "db_write_performed": False,
    "matched_fields_write_performed": False,
    "mechanism_generated": False,
    "llm_called": False,
    "vector_store_write_performed": False,
    "mechanism_draft_candidates_write_performed": False,
    "import_performed": False,
    "marker_or_ocr_performed": False,
}


def run_import_time_alignment_hook_dry_run(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    document_id: int | None = None,
    attachment_key: str | None = None,
    zotero_item_key: str | None = None,
    source_path: str | None = None,
    limit: int | None = None,
    auto_apply_alignment: bool = False,
    allow_medium_confidence: bool = False,
    max_apply_count: int | None = None,
    include_already_aligned: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """Return a Zotero note evidence alignment hook report.

    Auto apply is an explicit opt-in gate. The default remains plan-only.
    """
    try:
        report = build_import_alignment_hook_report(
            db_path,
            document_id=document_id,
            attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            source_path=source_path,
            limit=limit,
        )
        normalized = _with_no_write_flags(report)
        return _with_auto_apply_gate(
            normalized,
            db_path=db_path,
            document_id=document_id,
            attachment_key=attachment_key,
            client_note_id=None,
            server_note_id=None,
            limit=limit,
            auto_apply_alignment=auto_apply_alignment,
            allow_medium_confidence=allow_medium_confidence,
            max_apply_count=max_apply_count,
            include_already_aligned=include_already_aligned,
        )
    except Exception as exc:
        if strict:
            raise
        return _error_report(
            db_path=db_path,
            document_id=document_id,
            attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            source_path=source_path,
            limit=limit,
            auto_apply_alignment=auto_apply_alignment,
            exc=exc,
        )


def skipped_import_time_alignment_hook_report(
    *,
    reason: str,
    document_id: int | None = None,
    attachment_key: str | None = None,
    zotero_item_key: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "mode": SERVICE_MODE,
        "service_mode": SERVICE_MODE,
        "reason": reason,
        "document_id": document_id,
        "attachment_key": attachment_key,
        "zotero_item_key": zotero_item_key,
        "source_path": source_path,
        "batch_result": None,
        "auto_apply_requested": False,
        "auto_apply_performed": False,
        "alignment_apply_report": None,
        "hook_recommended_next_action": "alignment_hook_skipped",
        **NO_WRITE_FLAGS,
    }


def _with_no_write_flags(report: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(report)
    normalized["service_mode"] = SERVICE_MODE
    input_payload = normalized.get("input") or {}
    if isinstance(input_payload, dict):
        normalized.setdefault("document_id", input_payload.get("document_id"))
        normalized.setdefault("attachment_key", input_payload.get("attachment_key"))
        normalized.setdefault("zotero_item_key", input_payload.get("zotero_item_key"))
        normalized.setdefault("source_path", input_payload.get("source_path"))
    for key, value in NO_WRITE_FLAGS.items():
        normalized[key] = value
    batch_result = normalized.get("batch_result")
    if isinstance(batch_result, dict):
        normalized["batch_result"] = {
            **batch_result,
            "db_write_performed": False,
            "matched_fields_write_performed": False,
            "mechanism_generated": False,
            "llm_called": False,
            "vector_store_write_performed": False,
            "mechanism_draft_candidates_write_performed": False,
        }
    return normalized


def _with_auto_apply_gate(
    report: dict[str, Any],
    *,
    db_path: str | Path,
    document_id: int | None,
    attachment_key: str | None,
    client_note_id: str | None,
    server_note_id: str | None,
    limit: int | None,
    auto_apply_alignment: bool,
    allow_medium_confidence: bool,
    max_apply_count: int | None,
    include_already_aligned: bool,
) -> dict[str, Any]:
    report["auto_apply_requested"] = bool(auto_apply_alignment)
    report["auto_apply_performed"] = False
    report["alignment_apply_report"] = None
    if not auto_apply_alignment:
        return report

    batch_result = report.get("batch_result")
    if not isinstance(batch_result, dict):
        report["alignment_apply_report"] = _apply_skipped_report(
            reason="batch_result_missing"
        )
        return report
    if int(batch_result.get("count") or 0) <= 0:
        report["alignment_apply_report"] = _apply_skipped_report(
            reason="no_zotero_notes_to_align"
        )
        return report

    try:
        apply_report = build_batch_alignment_writeback_report(
            db_path,
            batch_dry_run_report=batch_result,
            attachment_key=attachment_key,
            document_id=document_id,
            client_note_id=client_note_id,
            server_note_id=server_note_id,
            limit=limit,
            include_already_aligned=include_already_aligned,
            allow_medium_confidence=allow_medium_confidence,
            apply_batch_alignment_writeback=True,
            max_apply_count=max_apply_count,
        )
    except Exception as exc:
        report["status"] = "WARN"
        report.setdefault("warnings", []).append("alignment_auto_apply_error")
        report.setdefault("blockers", []).append("alignment_auto_apply_error")
        report["alignment_apply_report"] = _apply_error_report(exc)
        return report

    report["alignment_apply_report"] = apply_report
    report["auto_apply_performed"] = bool(apply_report.get("db_write_performed"))
    report["db_write_performed"] = bool(apply_report.get("db_write_performed"))
    report["matched_fields_write_performed"] = bool(
        apply_report.get("matched_fields_write_performed")
    )
    report["schema_write_performed"] = False
    report["mechanism_generated"] = False
    report["llm_called"] = False
    report["vector_store_write_performed"] = False
    report["mechanism_draft_candidates_write_performed"] = False
    if apply_report.get("status") == "ERROR":
        report["status"] = "WARN"
        report.setdefault("warnings", []).append("alignment_auto_apply_error")
        report.setdefault("blockers", []).append("alignment_auto_apply_error")
    return report


def _apply_skipped_report(*, reason: str) -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "reason": reason,
        "count": 0,
        "summary": {
            "total_items": 0,
            "eligible_count": 0,
            "applied_count": 0,
            "skipped_count": 0,
            "blocked_count": 0,
            "already_aligned_skipped_count": 0,
            "ambiguous_or_unmatched_count": 0,
            "selected_text_empty_count": 0,
            "would_apply_count": 0,
            "max_apply_count": None,
            "errors_count": 0,
        },
        **NO_WRITE_FLAGS,
        "schema_write_performed": False,
    }


def _apply_error_report(exc: Exception) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "summary": {
            "total_items": 0,
            "eligible_count": 0,
            "applied_count": 0,
            "skipped_count": 0,
            "blocked_count": 0,
            "already_aligned_skipped_count": 0,
            "ambiguous_or_unmatched_count": 0,
            "selected_text_empty_count": 0,
            "would_apply_count": 0,
            "max_apply_count": None,
            "errors_count": 1,
        },
        **NO_WRITE_FLAGS,
        "schema_write_performed": False,
    }


def _error_report(
    *,
    db_path: str | Path,
    document_id: int | None,
    attachment_key: str | None,
    zotero_item_key: str | None,
    source_path: str | None,
    limit: int | None,
    auto_apply_alignment: bool,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "status": "WARN",
        "mode": SERVICE_MODE,
        "service_mode": SERVICE_MODE,
        "db_path": str(db_path),
        "document_id": document_id,
        "attachment_key": attachment_key,
        "zotero_item_key": zotero_item_key,
        "source_path": source_path,
        "input": {
            "document_id": document_id,
            "attachment_key": attachment_key,
            "zotero_item_key": zotero_item_key,
            "source_path": source_path,
            "limit": limit,
            "plan_only": True,
        },
        "resolved_document": None,
        "resolved_attachment": None,
        "batch_result": None,
        "blockers": ["alignment_hook_error"],
        "warnings": ["alignment_hook_error"],
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "auto_apply_requested": bool(auto_apply_alignment),
        "auto_apply_performed": False,
        "alignment_apply_report": None,
        "hook_recommended_next_action": "inspect_alignment_hook_error",
        **NO_WRITE_FLAGS,
    }
