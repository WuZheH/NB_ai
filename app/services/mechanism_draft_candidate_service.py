from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.services import mechanism_prompt_export_service


REVIEW_STATUSES = {"pending", "accepted", "rejected", "merged", "needs_edit", "deferred"}
PERSISTENCE_SCOPES = {"disabled", "tempdb", "production"}
PERSISTABLE_SOURCE_MODES = {"note_led", "source_led", "joint_led"}
REVIEWED_OBJECT_STATUSES = {"accepted", "approved", "edited", "reviewed", "committed"}
IDENTITY_UNORDERED_LIST_FIELDS = {
    "citation_tokens",
    "evidence_chunk_ids",
    "source_chunk_ids",
    "source_inspiration_note_ids",
    "source_object_ids",
}
REQUIRED_INDEX_NAMES = {
    "idx_mechanism_draft_candidates_draft_id",
    "idx_mechanism_draft_candidates_review_status",
    "idx_mechanism_draft_candidates_mechanism_key",
    "idx_mechanism_draft_candidates_mechanism_type",
    "idx_mechanism_draft_candidates_source",
    "idx_mechanism_draft_candidates_created_at",
    "idx_mechanism_draft_candidates_matched_document_id",
}
REVIEW_ACTION_TO_STATUS = {
    "accept": "accepted",
    "reject": "rejected",
    "merge": "merged",
    "needs_edit": "needs_edit",
    "defer": "deferred",
}


class MechanismDraftCandidateError(ValueError):
    pass


class MechanismDraftCandidateNotFoundError(MechanismDraftCandidateError):
    pass


def ensure_mechanism_draft_candidate_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mechanism_draft_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'pasted_chatgpt_json',
            source_inspiration_note_ids_json TEXT NOT NULL,
            bound_inspiration_note_ids_json TEXT NOT NULL,
            evidence_chunk_ids_json TEXT NOT NULL,
            matched_document_id INTEGER NULL,
            pdf_pages_json TEXT,
            mechanism_key TEXT,
            mechanism_name_cn TEXT,
            mechanism_name_en TEXT,
            mechanism_type TEXT,
            confidence TEXT,
            draft_json TEXT NOT NULL,
            validation_report_json TEXT NOT NULL,
            prompt_export_metadata_json TEXT,
            paste_back_readiness_context_json TEXT,
            review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (review_status IN ('pending', 'accepted', 'rejected', 'merged', 'needs_edit', 'deferred')),
            review_decision TEXT NULL,
            review_notes TEXT NULL,
            merged_into_draft_id TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_draft_id
            ON mechanism_draft_candidates (draft_id);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_review_status
            ON mechanism_draft_candidates (review_status);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_mechanism_key
            ON mechanism_draft_candidates (mechanism_key);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_mechanism_type
            ON mechanism_draft_candidates (mechanism_type);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_source
            ON mechanism_draft_candidates (source);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_created_at
            ON mechanism_draft_candidates (created_at);
        CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_matched_document_id
            ON mechanism_draft_candidates (matched_document_id);
        """
    )
    conn.commit()


def get_schema_status(
    conn: sqlite3.Connection | None,
    *,
    production_persistence_enabled: bool = False,
    integrity_check_ok: bool | None = None,
    persistence_scope: str = "disabled",
) -> dict[str, Any]:
    schema_present = False
    missing_indexes = sorted(REQUIRED_INDEX_NAMES)
    if conn is not None:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mechanism_draft_candidates'"
        ).fetchone()
        schema_present = row is not None
        if schema_present:
            missing_indexes = _missing_indexes(conn)
    missing_tables = [] if schema_present else ["mechanism_draft_candidates"]
    schema_ready = schema_present and not missing_indexes
    available = schema_ready and integrity_check_ok is not False
    return {
        **_safety_flags(
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        ),
        "available": available,
        "schema_present": schema_present,
        "schema_ready": schema_ready,
        "missing_tables": missing_tables,
        "missing_indexes": missing_indexes,
        "integrity_check_ok": integrity_check_ok,
        "write_available": bool(production_persistence_enabled and available),
        "production_persistence_enabled": production_persistence_enabled,
    }


def persist_validated_pending_draft_candidate(
    conn: sqlite3.Connection,
    pending_draft_preview: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    source_context: Mapping[str, Any],
    *,
    persistence_scope: str = "tempdb",
) -> dict[str, Any]:
    _assert_persistence_scope(persistence_scope)
    if persistence_scope != "tempdb":
        raise MechanismDraftCandidateError("production_candidate_persistence_not_authorized")
    assert_pending_draft_was_validated(pending_draft_preview)
    _assert_submitted_validation_report(validation_report)
    readiness_context = _extract_readiness_context(source_context)

    # Persistence must cross the existing K-G/K-F gate again; caller flags alone are insufficient.
    revalidated = mechanism_prompt_export_service.validate_pasted_mechanism_draft_json(
        readiness_context,
        _require_mapping(pending_draft_preview.get("draft_json"), "draft_json"),
    )
    if not revalidated.get("validator_passed") or revalidated.get("pending_draft_preview") is None:
        raise MechanismDraftCandidateError("pending_draft_preview_failed_k_g_revalidation")
    authoritative_preview = revalidated["pending_draft_preview"]
    _assert_preview_matches_revalidation(pending_draft_preview, authoritative_preview)
    if _json_text(validation_report) != _json_text(revalidated["validation_report"]):
        raise MechanismDraftCandidateError("validation_report_does_not_match_k_g_revalidation")

    draft_json = _require_mapping(authoritative_preview["draft_json"], "draft_json")
    source_note_ids = _canonical_values(authoritative_preview["source_inspiration_note_ids"])
    evidence_ids = _canonical_ints(authoritative_preview["evidence_chunk_ids"])
    source_pack = _assert_p0_source_provenance(
        readiness_context,
        draft_json,
        source_note_ids=source_note_ids,
        evidence_ids=evidence_ids,
    )
    metadata = _mapping_value(source_context.get("prompt_export_metadata"))
    if metadata.get("binding_mode") != "mechanism_source_pack":
        raise MechanismDraftCandidateError("mechanism_source_pack_binding_required")
    bound_note_ids = list(metadata.get("bound_inspiration_note_ids") or source_note_ids)
    if _canonical_values(bound_note_ids) != source_note_ids:
        raise MechanismDraftCandidateError("bound_inspiration_note_ids_do_not_match_validated_sources")

    identity = _candidate_identity(draft_json, source_note_ids, evidence_ids, source_pack)
    draft_id = f"mdraft_{identity['candidate_content_fingerprint'][:24]}"

    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = _load_row(conn, draft_id)
        if existing is not None:
            conn.commit()
            return _persist_result(
                existing,
                "unchanged",
                db_write_performed=False,
                persistence_scope=persistence_scope,
            )
        conflict = _find_candidate_identity_conflict(conn, identity, draft_id)
        if conflict is not None:
            raise MechanismDraftCandidateError(
                f"candidate_scope_content_conflict:{conflict['draft_id']}"
            )

        now = _utc_timestamp()
        conn.execute(
            """
            INSERT INTO mechanism_draft_candidates (
                draft_id, source, source_inspiration_note_ids_json, bound_inspiration_note_ids_json,
                evidence_chunk_ids_json, matched_document_id, pdf_pages_json, mechanism_key,
                mechanism_name_cn, mechanism_name_en, mechanism_type, confidence, draft_json,
                validation_report_json, prompt_export_metadata_json, paste_back_readiness_context_json,
                review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                draft_id,
                str(authoritative_preview.get("source") or "pasted_chatgpt_json"),
                _json_text(source_note_ids),
                _json_text(source_note_ids),
                _json_text(evidence_ids),
                _matched_document_id(readiness_context),
                _json_text(_pdf_pages(readiness_context)),
                draft_json.get("mechanism_key"),
                draft_json.get("mechanism_name_cn"),
                draft_json.get("mechanism_name_en"),
                draft_json.get("mechanism_type"),
                draft_json.get("confidence"),
                _json_text(draft_json),
                _json_text(validation_report),
                _json_text(metadata),
                _json_text(readiness_context),
                now,
                now,
            ),
        )
        row = _load_row(conn, draft_id)
        if row is None:
            raise MechanismDraftCandidateError("candidate_insert_failed")
        conn.commit()
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        if isinstance(exc, MechanismDraftCandidateError):
            raise
        raise MechanismDraftCandidateError(f"candidate_persistence_failed:{exc}") from exc
    return _persist_result(
        row,
        "inserted",
        db_write_performed=True,
        persistence_scope=persistence_scope,
    )


def list_mechanism_draft_candidates(
    conn: sqlite3.Connection,
    status: str = "pending",
    document_id: int | None = None,
    mechanism_type: str | None = None,
    *,
    persistence_scope: str = "tempdb",
    production_persistence_enabled: bool = False,
) -> dict[str, Any]:
    _assert_persistence_scope(persistence_scope)
    if status not in REVIEW_STATUSES:
        raise MechanismDraftCandidateError("invalid_review_status_filter")
    conditions = ["review_status = ?"]
    params: list[Any] = [status]
    if document_id is not None:
        conditions.append("matched_document_id = ?")
        params.append(document_id)
    if mechanism_type is not None:
        conditions.append("mechanism_type = ?")
        params.append(mechanism_type)
    query = (
        "SELECT * FROM mechanism_draft_candidates WHERE "
        + " AND ".join(conditions)
        + " ORDER BY created_at DESC, id DESC"
    )
    rows = [
        row
        for row in (_row_dict(cursor_row) for cursor_row in _execute_rows(conn, query, params))
        if _candidate_contract_status(row) == "p0_compliant"
    ]
    return {
        "status": "OK",
        "items": [build_mechanism_review_queue_item(row) for row in rows],
        "total": len(rows),
        **_safety_flags(
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        ),
    }


def get_mechanism_draft_candidate(
    conn: sqlite3.Connection,
    draft_id: str,
    *,
    persistence_scope: str = "tempdb",
    production_persistence_enabled: bool = False,
) -> dict[str, Any]:
    _assert_persistence_scope(persistence_scope)
    row = _load_row(conn, draft_id)
    if row is None:
        raise MechanismDraftCandidateNotFoundError("mechanism_draft_candidate_not_found")
    return {
        "status": "OK",
        "candidate": _candidate_detail(row),
        **_safety_flags(
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        ),
    }


def update_mechanism_draft_candidate_review_status(
    conn: sqlite3.Connection,
    draft_id: str,
    action: str,
    review_notes: str | None = None,
    merge_target_draft_id: str | None = None,
    *,
    persistence_scope: str = "tempdb",
) -> dict[str, Any]:
    _assert_persistence_scope(persistence_scope)
    if persistence_scope != "tempdb":
        raise MechanismDraftCandidateError("production_candidate_review_write_not_authorized")
    status = REVIEW_ACTION_TO_STATUS.get(action)
    if status is None:
        raise MechanismDraftCandidateError("invalid_review_action")
    if _load_row(conn, draft_id) is None:
        raise MechanismDraftCandidateNotFoundError("mechanism_draft_candidate_not_found")
    if action == "merge":
        if not merge_target_draft_id:
            raise MechanismDraftCandidateError("merge_requires_merge_target_draft_id")
        if merge_target_draft_id == draft_id:
            raise MechanismDraftCandidateError("merge_target_must_be_different_candidate")
        if _load_row(conn, merge_target_draft_id) is None:
            raise MechanismDraftCandidateNotFoundError("merge_target_draft_candidate_not_found")
    now = _utc_timestamp()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE mechanism_draft_candidates
            SET review_status = ?, review_decision = ?, review_notes = ?,
                merged_into_draft_id = ?, updated_at = ?, reviewed_at = ?
            WHERE draft_id = ?
            """,
            (
                status,
                action,
                review_notes,
                merge_target_draft_id if action == "merge" else None,
                now,
                now,
                draft_id,
            ),
        )
        conn.commit()
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        if isinstance(exc, MechanismDraftCandidateError):
            raise
        raise MechanismDraftCandidateError(f"candidate_review_update_failed:{exc}") from exc
    row = _load_row(conn, draft_id)
    return {
        "status": "OK",
        "action": action,
        "candidate": _candidate_detail(row),
        **_safety_flags(
            db_write_performed=True,
            persistence_scope=persistence_scope,
        ),
    }


def build_mechanism_review_queue_item(row: Mapping[str, Any]) -> dict[str, Any]:
    draft = _json_object(row.get("draft_json"))
    source_ids = _json_list(row.get("source_inspiration_note_ids_json"))
    evidence_ids = _json_list(row.get("evidence_chunk_ids_json"))
    readiness = _json_object(row.get("paste_back_readiness_context_json"))
    payload = _mapping_value(readiness.get("mechanism_prompt_payload_preview"))
    source_notes = list(payload.get("source_inspiration_notes") or [])
    note_preview = ""
    if source_notes and isinstance(source_notes[0], Mapping):
        note_preview = str(source_notes[0].get("user_note_text") or "")
    return {
        "draft_id": row["draft_id"],
        "mechanism_name_cn": row.get("mechanism_name_cn"),
        "mechanism_name_en": row.get("mechanism_name_en"),
        "mechanism_type": row.get("mechanism_type"),
        "confidence": row.get("confidence"),
        "review_status": row.get("review_status"),
        "candidate_contract_status": _candidate_contract_status(row),
        "source_inspiration_note_count": len(source_ids),
        "evidence_chunk_count": len(evidence_ids),
        "source_note_preview": note_preview,
        "mechanism_summary": draft.get("short_explanation"),
        "transfer_directions_summary": [
            item.get("target_domain")
            for item in draft.get("transfer_directions") or []
            if isinstance(item, Mapping)
        ],
        "failure_modes_count": len(draft.get("failure_modes") or []),
        "writing_angles_count": len(draft.get("writing_angles") or []),
        "created_at": row.get("created_at"),
    }


def assert_pending_draft_was_validated(pending_draft_preview: Mapping[str, Any]) -> None:
    if not isinstance(pending_draft_preview, Mapping):
        raise MechanismDraftCandidateError("pending_draft_preview_required")
    if pending_draft_preview.get("draft_status") != "pending":
        raise MechanismDraftCandidateError("draft_status_must_be_pending")
    if pending_draft_preview.get("review_status") != "pending":
        raise MechanismDraftCandidateError("review_status_must_be_pending")
    if pending_draft_preview.get("mechanism_card_created") is not False:
        raise MechanismDraftCandidateError("mechanism_card_created_must_be_false")
    _require_mapping(pending_draft_preview.get("draft_json"), "draft_json")


def _candidate_detail(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise MechanismDraftCandidateNotFoundError("mechanism_draft_candidate_not_found")
    review_status = str(row.get("review_status") or "")
    draft_json = _json_object(row.get("draft_json"))
    readiness_context = _json_object(row.get("paste_back_readiness_context_json"))
    contract_status = _candidate_contract_status(row)
    identity: dict[str, str] = {}
    try:
        identity = _candidate_identity(
            draft_json,
            _json_list(row.get("source_inspiration_note_ids_json")),
            _canonical_ints(_json_list(row.get("evidence_chunk_ids_json"))),
            _source_pack_from_readiness(readiness_context),
        )
    except MechanismDraftCandidateError:
        pass
    return {
        **build_mechanism_review_queue_item(row),
        "source": row.get("source"),
        "source_inspiration_note_ids": _json_list(row.get("source_inspiration_note_ids_json")),
        "bound_inspiration_note_ids": _json_list(row.get("bound_inspiration_note_ids_json")),
        "evidence_chunk_ids": _json_list(row.get("evidence_chunk_ids_json")),
        "matched_document_id": row.get("matched_document_id"),
        "pdf_pages": _json_list(row.get("pdf_pages_json")),
        "draft_json": draft_json,
        "validation_report_json": _json_object(row.get("validation_report_json")),
        "prompt_export_metadata": _json_object(row.get("prompt_export_metadata_json")),
        "paste_back_readiness_context": readiness_context,
        "source_scope_fingerprint": identity.get("source_scope_fingerprint"),
        "candidate_content_fingerprint": identity.get("candidate_content_fingerprint"),
        "candidate_contract_status": contract_status,
        "review_decision": row.get("review_decision"),
        "review_notes": row.get("review_notes"),
        "merged_into_draft_id": row.get("merged_into_draft_id"),
        "updated_at": row.get("updated_at"),
        "reviewed_at": row.get("reviewed_at"),
        "review_controls_available": (
            ["accept", "reject", "needs_edit", "defer", "merge"]
            if review_status in {"pending", "needs_edit", "deferred"}
            else []
        ),
        "warning": (
            "legacy candidate is excluded from the P0 review queue"
            if contract_status != "p0_compliant"
            else "mechanism_card not yet created; candidate review is not final activation."
        ),
    }


def _persist_result(
    row: Mapping[str, Any],
    dedup_action: str,
    *,
    db_write_performed: bool,
    persistence_scope: str,
) -> dict[str, Any]:
    return {
        "status": "OK",
        "draft_id": row["draft_id"],
        "review_status": row["review_status"],
        "dedup_action": dedup_action,
        "candidate": _candidate_detail(row),
        **_safety_flags(
            db_write_performed=db_write_performed,
            persistence_scope=persistence_scope,
        ),
    }


def _assert_submitted_validation_report(validation_report: Mapping[str, Any]) -> None:
    if not isinstance(validation_report, Mapping):
        raise MechanismDraftCandidateError("validation_report_required")
    if validation_report.get("is_valid") is not True or validation_report.get("blocked") is True:
        raise MechanismDraftCandidateError("validation_report_must_pass_before_persistence")


def _assert_preview_matches_revalidation(
    submitted: Mapping[str, Any],
    authoritative: Mapping[str, Any],
) -> None:
    for field in (
        "draft_status",
        "review_status",
        "source",
        "draft_json",
        "source_inspiration_note_ids",
        "evidence_chunk_ids",
        "immutable_inspiration_provenance",
        "mechanism_card_created",
    ):
        if _json_text(submitted.get(field)) != _json_text(authoritative.get(field)):
            raise MechanismDraftCandidateError(f"pending_draft_preview_not_validator_issued:{field}")


def _extract_readiness_context(source_context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(source_context, Mapping):
        raise MechanismDraftCandidateError("source_context_required")
    nested = source_context.get("paste_back_readiness_context") or source_context.get("readiness_report")
    context = nested if isinstance(nested, Mapping) else source_context
    if context.get("readiness_status") != "ready_for_mechanism_prompt":
        raise MechanismDraftCandidateError("source_context_requires_ready_readiness_report")
    return dict(context)


def _assert_p0_source_provenance(
    readiness_context: Mapping[str, Any],
    draft_json: Mapping[str, Any],
    *,
    source_note_ids: list[Any],
    evidence_ids: list[int],
) -> dict[str, Any]:
    if draft_json.get("should_generate_mechanism") is not True:
        raise MechanismDraftCandidateError(
            "should_generate_mechanism_must_be_true_for_persistence"
        )
    pack = _source_pack_from_readiness(readiness_context)
    if not pack:
        raise MechanismDraftCandidateError("mechanism_source_pack_provenance_required")
    payload = _mapping_value(readiness_context.get("mechanism_prompt_payload_preview"))
    payload_pack = _mapping_value(payload.get("mechanism_source_pack"))
    if payload_pack and _json_text(payload_pack) != _json_text(pack):
        raise MechanismDraftCandidateError("mechanism_source_pack_provenance_mismatch")

    source_mode = str(pack.get("source_mode") or "unknown")
    if source_mode not in PERSISTABLE_SOURCE_MODES:
        raise MechanismDraftCandidateError("source_mode_not_persistable")
    if str(draft_json.get("source_mode") or "unknown") != source_mode:
        raise MechanismDraftCandidateError("draft_source_mode_does_not_match_source_pack")

    primary_note = _mapping_value(pack.get("primary_user_note"))
    primary_excerpt = _mapping_value(pack.get("primary_source_excerpt"))
    note_present = bool(str(primary_note.get("note_text") or "").strip())
    excerpt_present = bool(
        str(primary_excerpt.get("selected_text") or "").strip()
        or str(primary_excerpt.get("chunk_text") or "").strip()
    )
    if source_mode in {"note_led", "joint_led"}:
        if not note_present or not source_note_ids:
            raise MechanismDraftCandidateError("source_inspiration_note_ids_required")
        note_id = primary_note.get("note_id")
        if note_id is not None and str(note_id) not in {str(value) for value in source_note_ids}:
            raise MechanismDraftCandidateError("primary_user_note_id_not_in_validated_sources")
    if source_mode in {"source_led", "joint_led"}:
        if not excerpt_present or not evidence_ids:
            raise MechanismDraftCandidateError("evidence_chunk_ids_required")
        chunk_id = primary_excerpt.get("chunk_id")
        if chunk_id is not None and int(chunk_id) not in evidence_ids:
            raise MechanismDraftCandidateError("primary_source_chunk_not_in_validated_evidence")

    if primary_note.get("role") != "primary_source":
        raise MechanismDraftCandidateError("primary_user_note_role_must_be_primary_source")
    if primary_excerpt.get("role") != "primary_source":
        raise MechanismDraftCandidateError("primary_source_excerpt_role_must_be_primary_source")
    if not list(pack.get("citation_tokens") or []):
        raise MechanismDraftCandidateError("citation_tokens_required")

    policy = _mapping_value(pack.get("source_balance_policy"))
    for field in (
        "treat_user_note_as_primary",
        "treat_source_excerpt_as_primary",
        "do_not_force_note_to_dominate_source",
        "do_not_reduce_source_to_citation_only",
        "preserve_original_note_text",
    ):
        if policy.get(field) is not True:
            raise MechanismDraftCandidateError(f"source_balance_policy_required:{field}")

    if note_present and not str(draft_json.get("user_note_contribution") or "").strip():
        raise MechanismDraftCandidateError("missing_user_note_contribution")
    if excerpt_present and not str(draft_json.get("source_excerpt_contribution") or "").strip():
        raise MechanismDraftCandidateError("missing_source_excerpt_contribution")
    if not str(draft_json.get("evidence_alignment") or "").strip():
        raise MechanismDraftCandidateError("source_conflict_unresolved")
    if not isinstance(draft_json.get("source_balance_warnings"), list):
        raise MechanismDraftCandidateError("source_balance_warnings_must_be_list")

    objects = _mapping_list(_mapping_value(pack.get("linked_knowledge")).get("objects"))
    for item in objects:
        status = str(item.get("review_status") or item.get("status") or "").casefold()
        if status not in REVIEWED_OBJECT_STATUSES:
            raise MechanismDraftCandidateError("linked_object_not_reviewed")
    if objects and not str(draft_json.get("linked_object_contribution") or "").strip():
        raise MechanismDraftCandidateError("missing_linked_object_contribution")
    return pack


def _source_pack_from_readiness(readiness_context: Mapping[str, Any]) -> dict[str, Any]:
    pack = _mapping_value(readiness_context.get("source_mechanism_source_pack"))
    if pack:
        return pack
    payload = _mapping_value(readiness_context.get("mechanism_prompt_payload_preview"))
    return _mapping_value(payload.get("mechanism_source_pack"))


def _matched_document_id(readiness_context: Mapping[str, Any]) -> int | None:
    evidence = _mapping_value(readiness_context.get("matched_chunk_evidence"))
    payload = _mapping_value(readiness_context.get("mechanism_prompt_payload_preview"))
    primary_excerpt = _mapping_value(payload.get("primary_source_excerpt"))
    value = evidence.get("document_id") or primary_excerpt.get("document_id")
    if value is None:
        for item in _mapping_list(payload.get("evidence")):
            if item.get("document_id") is not None:
                value = item["document_id"]
                break
    return int(value) if value is not None else None


def _pdf_pages(readiness_context: Mapping[str, Any]) -> list[Any]:
    payload = _mapping_value(readiness_context.get("mechanism_prompt_payload_preview"))
    values = [payload.get("pdf_page")]
    values.extend(
        note.get("pdf_page")
        for note in payload.get("source_inspiration_notes") or []
        if isinstance(note, Mapping)
    )
    return list(dict.fromkeys(value for value in values if value is not None))


def _candidate_identity(
    draft_json: Mapping[str, Any],
    source_note_ids: list[Any],
    evidence_ids: list[int],
    source_pack: Mapping[str, Any],
) -> dict[str, str]:
    scope_material = {
        "source_mode": str(source_pack.get("source_mode") or "unknown"),
        "source_inspiration_note_ids": _canonical_values(source_note_ids),
        "evidence_chunk_ids": _canonical_ints(evidence_ids),
        "citation_tokens": _canonical_values(source_pack.get("citation_tokens") or []),
    }
    source_anchor_fingerprint = _sha256(
        {
            "source_inspiration_note_ids": scope_material["source_inspiration_note_ids"],
            "evidence_chunk_ids": scope_material["evidence_chunk_ids"],
        }
    )
    source_scope_fingerprint = _sha256(scope_material)
    mechanism_identity = _mechanism_identity(draft_json)
    content_material = {
        "source_scope_fingerprint": source_scope_fingerprint,
        "mechanism_identity": mechanism_identity,
        "draft_json": _canonicalize_identity_value(draft_json),
    }
    return {
        "source_scope_fingerprint": source_scope_fingerprint,
        "source_anchor_fingerprint": source_anchor_fingerprint,
        "candidate_content_fingerprint": _sha256(content_material),
        "mechanism_identity": mechanism_identity,
    }


def _mechanism_identity(draft_json: Mapping[str, Any]) -> str:
    mechanism_key = str(draft_json.get("mechanism_key") or "").strip().casefold()
    if mechanism_key:
        return f"key:{mechanism_key}"
    names = [
        str(draft_json.get("mechanism_name_cn") or "").strip().casefold(),
        str(draft_json.get("mechanism_name_en") or "").strip().casefold(),
        str(draft_json.get("mechanism_type") or "").strip().casefold(),
    ]
    identity = "|".join(value for value in names if value)
    if not identity:
        raise MechanismDraftCandidateError("mechanism_identity_required")
    return f"name:{identity}"


def _find_candidate_identity_conflict(
    conn: sqlite3.Connection,
    identity: Mapping[str, str],
    draft_id: str,
) -> dict[str, Any] | None:
    rows = _execute_rows(conn, "SELECT * FROM mechanism_draft_candidates", [])
    for row in rows:
        if str(row.get("draft_id")) == draft_id:
            continue
        draft = _json_object(row.get("draft_json"))
        try:
            row_mechanism_identity = _mechanism_identity(draft)
        except MechanismDraftCandidateError:
            continue
        if row_mechanism_identity != identity["mechanism_identity"]:
            continue
        readiness = _json_object(row.get("paste_back_readiness_context_json"))
        row_identity = _candidate_identity(
            draft,
            _json_list(row.get("source_inspiration_note_ids_json")),
            _canonical_ints(_json_list(row.get("evidence_chunk_ids_json"))),
            _source_pack_from_readiness(readiness),
        )
        if row_identity["source_anchor_fingerprint"] == identity["source_anchor_fingerprint"]:
            return row
    return None


def _candidate_contract_status(row: Mapping[str, Any]) -> str:
    draft = _json_object(row.get("draft_json"))
    readiness = _json_object(row.get("paste_back_readiness_context_json"))
    try:
        _assert_p0_source_provenance(
            readiness,
            draft,
            source_note_ids=_canonical_values(
                _json_list(row.get("source_inspiration_note_ids_json"))
            ),
            evidence_ids=_canonical_ints(_json_list(row.get("evidence_chunk_ids_json"))),
        )
    except (MechanismDraftCandidateError, TypeError, ValueError):
        return "legacy_unverified"
    return "p0_compliant"


def _canonicalize_identity_value(value: Any, *, field: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_identity_value(item, field=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        items = [_canonicalize_identity_value(item) for item in value]
        if field in IDENTITY_UNORDERED_LIST_FIELDS:
            by_text = {_json_text(item): item for item in items}
            return [by_text[key] for key in sorted(by_text)]
        return items
    return value


def _canonical_values(values: Any) -> list[Any]:
    items = list(values or [])
    by_text = {_json_text(value): value for value in items}
    return [by_text[key] for key in sorted(by_text)]


def _canonical_ints(values: Any) -> list[int]:
    return sorted({int(value) for value in list(values or []) if value is not None})


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _load_row(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any] | None:
    cursor = conn.execute(
        "SELECT * FROM mechanism_draft_candidates WHERE draft_id = ?",
        (draft_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip([column[0] for column in cursor.description], row))


def _execute_rows(
    conn: sqlite3.Connection,
    query: str,
    params: list[Any],
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, tuple(params))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MechanismDraftCandidateError(f"{field}_required")
    return dict(value)


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(str(value))
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(str(value))
    return list(parsed) if isinstance(parsed, list) else []


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _missing_indexes(conn: sqlite3.Connection) -> list[str]:
    placeholders = ", ".join(["?"] * len(REQUIRED_INDEX_NAMES))
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ({placeholders})",
        tuple(REQUIRED_INDEX_NAMES),
    ).fetchall()
    present = {str(row[0]) for row in rows}
    return sorted(REQUIRED_INDEX_NAMES - present)


def _assert_persistence_scope(persistence_scope: str) -> None:
    if persistence_scope not in PERSISTENCE_SCOPES:
        raise MechanismDraftCandidateError("invalid_persistence_scope")


def _safety_flags(
    *,
    db_write_performed: bool = False,
    persistence_scope: str = "disabled",
    production_persistence_enabled: bool = False,
) -> dict[str, Any]:
    _assert_persistence_scope(persistence_scope)
    connection_is_production = persistence_scope == "production"
    return {
        "db_write_performed": db_write_performed,
        "persistence_scope": persistence_scope,
        "connection_is_production": connection_is_production,
        "mechanism_card_created": False,
        "llm_called": False,
        "external_model_called": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
        "production_persistence_enabled": production_persistence_enabled,
    }
