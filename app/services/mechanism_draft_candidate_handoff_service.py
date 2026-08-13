from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from typing import Any, Mapping

from app.services import mechanism_draft_candidate_service
from app.services import mechanism_draft_review_service
from app.services import mechanism_prompt_export_service


HANDOFF_SCHEMA_VERSION = "mechanism_draft_review_candidate_handoff_a_v1"
HANDOFF_MODE = "persisted_candidate_read_only_review_preview"
REVIEWABLE_STATUSES = {"pending", "needs_edit", "deferred"}
PERSISTENCE_SCOPES = {"tempdb", "production"}


def build_candidate_review_handoff(
    conn: sqlite3.Connection,
    draft_id: str,
    *,
    persistence_scope: str = "tempdb",
    production_persistence_enabled: bool = False,
) -> dict[str, Any]:
    """Revalidate a stored P0 candidate and expose a read-only review packet."""

    if persistence_scope not in PERSISTENCE_SCOPES:
        raise mechanism_draft_candidate_service.MechanismDraftCandidateError(
            "invalid_persistence_scope"
        )
    detail = mechanism_draft_candidate_service.get_mechanism_draft_candidate(
        conn,
        draft_id,
        persistence_scope=persistence_scope,
        production_persistence_enabled=production_persistence_enabled,
    )
    candidate = _mapping(detail.get("candidate"))
    reference = _candidate_reference(candidate)
    blockers: list[str] = []

    if candidate.get("candidate_contract_status") != "p0_compliant":
        blockers.append("legacy_candidate_not_p0_compliant")
    review_status = str(candidate.get("review_status") or "")
    if review_status not in REVIEWABLE_STATUSES:
        blockers.append("candidate_review_status_not_reviewable")

    readiness = _mapping(candidate.get("paste_back_readiness_context"))
    source_pack = _source_pack(readiness)
    if not readiness:
        blockers.append("candidate_readiness_context_missing")
    if not source_pack:
        blockers.append("candidate_mechanism_source_pack_missing")

    revalidated: dict[str, Any] = {}
    if readiness and source_pack:
        revalidated = mechanism_prompt_export_service.validate_pasted_mechanism_draft_json(
            readiness,
            _mapping(candidate.get("draft_json")),
        )
        blockers.extend(_stored_candidate_mismatch_blockers(candidate, revalidated))

    if blockers:
        return _blocked_result(
            reference,
            blockers,
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        )

    review_result = mechanism_draft_review_service.build_mechanism_draft_review_ui_packet(
        revalidated,
        source_pack_result={"mechanism_source_pack": source_pack},
    )
    if review_result.get("review_ready") is not True:
        return _blocked_result(
            reference,
            list(review_result.get("blockers") or ["review_packet_not_ready"]),
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        )

    review_packet = deepcopy(_mapping(review_result.get("review_packet")))
    review_packet["review_status"] = review_status
    review_packet["persistence_status"] = "persisted_candidate_read_only"
    review_packet["candidate_reference"] = deepcopy(reference)
    return {
        "status": "OK",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_mode": HANDOFF_MODE,
        "review_ready": True,
        "candidate_reference": reference,
        "review_packet": review_packet,
        "blockers": [],
        **_safety_flags(
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        ),
    }


def _stored_candidate_mismatch_blockers(
    candidate: Mapping[str, Any],
    revalidated: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if revalidated.get("validator_passed") is not True:
        blockers.append("stored_candidate_failed_authoritative_revalidation")
        return blockers
    pending = _mapping(revalidated.get("pending_draft_preview"))
    if not pending:
        blockers.append("authoritative_pending_draft_preview_missing")
        return blockers
    comparisons = {
        "draft_json": (candidate.get("draft_json"), pending.get("draft_json")),
        "validation_report": (
            candidate.get("validation_report_json"),
            revalidated.get("validation_report"),
        ),
        "source_inspiration_note_ids": (
            _canonical_values(candidate.get("source_inspiration_note_ids")),
            _canonical_values(pending.get("source_inspiration_note_ids")),
        ),
        "evidence_chunk_ids": (
            _canonical_ints(candidate.get("evidence_chunk_ids")),
            _canonical_ints(pending.get("evidence_chunk_ids")),
        ),
    }
    for field, (stored, authoritative) in comparisons.items():
        if _json_text(stored) != _json_text(authoritative):
            blockers.append(f"stored_candidate_revalidation_mismatch:{field}")
    return blockers


def _candidate_reference(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "draft_id": candidate.get("draft_id"),
        "review_status": candidate.get("review_status"),
        "candidate_contract_status": candidate.get("candidate_contract_status"),
        "source_scope_fingerprint": candidate.get("source_scope_fingerprint"),
        "candidate_content_fingerprint": candidate.get("candidate_content_fingerprint"),
        "matched_document_id": candidate.get("matched_document_id"),
        "source_inspiration_note_count": candidate.get("source_inspiration_note_count"),
        "evidence_chunk_count": candidate.get("evidence_chunk_count"),
    }


def _blocked_result(
    reference: Mapping[str, Any],
    blockers: list[str],
    *,
    persistence_scope: str,
    production_persistence_enabled: bool,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_mode": HANDOFF_MODE,
        "review_ready": False,
        "candidate_reference": deepcopy(dict(reference)),
        "review_packet": None,
        "blockers": list(dict.fromkeys(blockers)),
        **_safety_flags(
            persistence_scope=persistence_scope,
            production_persistence_enabled=production_persistence_enabled,
        ),
    }


def _source_pack(readiness: Mapping[str, Any]) -> dict[str, Any]:
    pack = readiness.get("source_mechanism_source_pack")
    if isinstance(pack, Mapping):
        return deepcopy(dict(pack))
    payload = _mapping(readiness.get("mechanism_prompt_payload_preview"))
    pack = payload.get("mechanism_source_pack")
    return deepcopy(dict(pack)) if isinstance(pack, Mapping) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_values(values: Any) -> list[Any]:
    items = list(values or [])
    by_text = {_json_text(value): value for value in items}
    return [by_text[key] for key in sorted(by_text)]


def _canonical_ints(values: Any) -> list[int]:
    return sorted({int(value) for value in list(values or []) if value is not None})


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safety_flags(
    *,
    persistence_scope: str,
    production_persistence_enabled: bool,
) -> dict[str, Any]:
    return {
        "db_read_performed": True,
        "db_write_performed": False,
        "persistence_scope": persistence_scope,
        "connection_is_production": persistence_scope == "production",
        "production_persistence_enabled": production_persistence_enabled,
        "production_db_write_allowed": False,
        "llm_called": False,
        "external_model_called": False,
        "external_api_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "mechanism_draft_persisted": False,
        "mechanism_card_created": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
