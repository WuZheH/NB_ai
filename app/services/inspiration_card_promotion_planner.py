from __future__ import annotations

from copy import deepcopy
from typing import Any


ALLOWED_TARGET_TYPES = {
    "research_note",
    "draft_note",
    "evidence_linked_note",
    "follow_up_task",
}
FORBIDDEN_TARGET_ERROR_CODES = {
    "final_hypothesis": "FORBIDDEN_FINAL_HYPOTHESIS_TARGET",
    "relation": "FORBIDDEN_RELATION_TARGET",
    "automatic_evidence_object": "FORBIDDEN_AUTOMATIC_EVIDENCE_OBJECT_TARGET",
    "automatic_tag_expansion": "FORBIDDEN_AUTOMATIC_TAG_EXPANSION_TARGET",
}
KNOWN_CARD_STATUSES = {
    "candidate",
    "confirmed",
    "user-confirmed",
    "rejected",
    "archived",
    "superseded",
    "deleted",
    "raw",
    "promising",
    "tested",
}
PROMOTABLE_STATUSES = {"confirmed", "user-confirmed"}
TRACE_STATUSES = {
    "evidence_backed",
    "source_gap_preserved",
    "unsupported_personal_idea",
}
SOURCE_ID_KEYS = {
    "source_doc_ids",
    "source_chunk_ids",
    "source_note_ids",
    "source_relation_ids",
}


def plan_inspiration_card_promotion(
    card: object | dict[str, Any] | None,
    target_type: str,
    actor: str | None,
    promotion_reason: str | None,
    target_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a dry-run InspirationCard promotion plan without side effects."""
    if card is None:
        return _failure("CARD_NOT_FOUND", "card is required.")

    clean_actor, actor_error = _clean_required_text(actor, "actor")
    if actor_error is not None:
        return _failure(actor_error, "actor is required.")

    clean_reason, reason_error = _clean_required_text(promotion_reason, "promotion_reason")
    if reason_error is not None:
        return _failure(reason_error, "promotion_reason is required.")

    target_failure = _validate_target_type(target_type)
    if target_failure is not None:
        return target_failure

    status = _get_field(card, "status")
    if status not in KNOWN_CARD_STATUSES:
        return _failure("UNKNOWN_CARD_STATUS", f"unknown card status: {status!r}.")
    if status not in PROMOTABLE_STATUSES:
        return _failure("CARD_STATUS_NOT_CONFIRMED", f"card status is not confirmed: {status}.")

    source_trace = _get_field(card, "source_trace")
    source_gap_reason = _get_field(card, "source_gap_reason")
    if source_trace is None:
        source_trace = _source_trace_from_card_sources(card, source_gap_reason)
    else:
        source_trace = deepcopy(source_trace)

    trace_error = _validate_source_trace(source_trace)
    if trace_error is not None:
        return trace_error

    if target_type == "evidence_linked_note" and not _has_concrete_source(source_trace):
        return _failure(
            "TARGET_REQUIRES_SOURCE_TRACE",
            "evidence_linked_note requires source_trace with at least one source document or chunk.",
        )

    origin_card_id = _get_field(card, "id")
    if origin_card_id is None:
        origin_card_id = _get_field(card, "card_id")
    transferred_fields = {
        "origin_card_id": origin_card_id,
        "title": _get_field(card, "title"),
        "content": _get_field(card, "content"),
        "tags": deepcopy(_get_field(card, "tags") or []),
        "source_trace": deepcopy(source_trace),
        "source_gap_reason": source_gap_reason,
        "created_at": _get_field(card, "created_at"),
        "confirmed_at": _confirmed_at(card),
        "target_metadata": deepcopy(target_metadata or {}),
    }

    return {
        "ok": True,
        "dry_run": True,
        "writes_planned": False,
        "source_card_mutated": False,
        "final_hypothesis_created": False,
        "relation_created": False,
        "note_evidence_links_written": False,
        "target_type": target_type,
        "origin_card_id": origin_card_id,
        "promotion_actor": clean_actor,
        "promotion_reason": clean_reason,
        "transferred_fields": transferred_fields,
        "evidence_boundary": {
            "confirmed_means_selected_for_follow_up_not_proven_true": True,
            "source_trace_copied_only": True,
            "source_trace_strengthened": False,
            "unsupported_inspiration_converted_to_evidence_backed": False,
            "source_gap_reason_preserved": source_gap_reason is None
            or transferred_fields["source_gap_reason"] == source_gap_reason,
        },
        "target_preview": {
            "target_type": target_type,
            "title": _get_field(card, "title"),
            "body": _get_field(card, "content"),
            "origin_card_id": origin_card_id,
            "source_trace": deepcopy(source_trace),
            "source_gap_reason": source_gap_reason,
            "promotion_reason": clean_reason,
            "target_metadata": deepcopy(target_metadata or {}),
        },
    }


def _failure(error_code: str, error_message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": error_code,
        "error_message": error_message,
        "writes_planned": False,
        "source_card_mutated": False,
        "final_hypothesis_created": False,
        "relation_created": False,
        "note_evidence_links_written": False,
    }


def _clean_required_text(value: str | None, field_name: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, f"MISSING_{field_name.upper()}"
    clean = value.strip()
    if not clean:
        return None, f"EMPTY_{field_name.upper()}"
    return clean, None


def _validate_target_type(target_type: str) -> dict[str, Any] | None:
    if target_type in FORBIDDEN_TARGET_ERROR_CODES:
        return _failure(FORBIDDEN_TARGET_ERROR_CODES[target_type], f"forbidden target_type: {target_type}.")
    if target_type not in ALLOWED_TARGET_TYPES:
        return _failure("UNSUPPORTED_TARGET_TYPE", f"unsupported target_type: {target_type}.")
    return None


def _validate_source_trace(source_trace: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(source_trace, dict):
        return _failure("MALFORMED_SOURCE_TRACE", "source_trace must be a dict.")
    trace_status = source_trace.get("trace_status")
    if trace_status not in TRACE_STATUSES:
        return _failure("MALFORMED_SOURCE_TRACE", "source_trace.trace_status is invalid.")
    for key in SOURCE_ID_KEYS:
        value = source_trace.get(key, [])
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(item, int) for item in value):
            return _failure("MALFORMED_SOURCE_TRACE", f"source_trace.{key} must be a list of ints.")
    gap_reason = source_trace.get("source_gap_reason")
    if gap_reason is not None and not isinstance(gap_reason, str):
        return _failure("MALFORMED_SOURCE_TRACE", "source_trace.source_gap_reason must be a string when present.")
    return None


def _has_concrete_source(source_trace: dict[str, Any]) -> bool:
    return bool(source_trace.get("source_doc_ids") or source_trace.get("source_chunk_ids"))


def _source_trace_from_card_sources(card: object | dict[str, Any], source_gap_reason: str | None) -> dict[str, Any]:
    source_doc_ids: list[int] = []
    source_chunk_ids: list[int] = []
    for source in _get_field(card, "sources") or []:
        doc_id = _get_field(source, "source_doc_id")
        chunk_id = _get_field(source, "source_chunk_id")
        if isinstance(doc_id, int):
            source_doc_ids.append(doc_id)
        if isinstance(chunk_id, int):
            source_chunk_ids.append(chunk_id)

    if source_doc_ids or source_chunk_ids:
        trace_status = "evidence_backed"
    elif source_gap_reason == "unsupported_personal_idea":
        trace_status = "unsupported_personal_idea"
    else:
        trace_status = "source_gap_preserved"

    return {
        "source_doc_ids": source_doc_ids,
        "source_chunk_ids": source_chunk_ids,
        "source_note_ids": [],
        "source_relation_ids": [],
        "source_gap_reason": source_gap_reason,
        "evidence_strength": None,
        "trace_status": trace_status,
    }


def _confirmed_at(card: object | dict[str, Any]) -> Any:
    for event in _get_field(card, "events") or []:
        to_status = _get_field(event, "to_status")
        if to_status in PROMOTABLE_STATUSES:
            return _get_field(event, "created_at")
    return None


def _get_field(value: object | dict[str, Any], name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
