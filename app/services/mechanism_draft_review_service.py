from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


REVIEW_PACKET_SCHEMA_VERSION = "mechanism_draft_review_a_v1"
ACTION_PREVIEW_MODE = "mechanism_draft_review_action_preview_v1"
REVIEWABLE_STATUSES = {"pending", "needs_edit", "deferred"}
ACTION_TO_STATUS = {
    "accept": "accepted",
    "reject": "rejected",
    "needs_edit": "needs_edit",
    "defer": "deferred",
    "merge_into": "merged",
    "merge": "merged",
}
ACTION_TO_DECISION = {
    "accept": "accepted",
    "reject": "rejected",
    "needs_edit": "needs_edit",
    "defer": "deferred",
    "merge_into": "merged",
    "merge": "merged",
}


class MechanismDraftReviewError(ValueError):
    pass


def build_mechanism_draft_review_packet(
    pasteback_validation_result: Mapping[str, Any],
    *,
    source_pack_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a review packet from validator output without persisting a draft."""

    blockers = _validation_blockers(pasteback_validation_result)
    validation_report = _mapping(pasteback_validation_result.get("validation_report"))
    pending_preview = _mapping(pasteback_validation_result.get("pending_draft_preview"))
    source_pack = _source_pack(source_pack_result)

    if blockers:
        return {
            "status": "BLOCKED",
            "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
            "review_ready": False,
            "review_packet": None,
            "blockers": blockers,
            "validation": _validation_summary(False, validation_report),
            **_safety_flags(),
        }

    draft_json = _mapping(pending_preview.get("draft_json"))
    packet = {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "packet_id": _packet_id(pending_preview, source_pack),
        "draft_status": pending_preview.get("draft_status"),
        "review_status": pending_preview.get("review_status"),
        "persistence_status": pending_preview.get("persistence_status", "preview_only"),
        "mechanism_card_created": False,
        "review_gate": {
            "ready_for_human_review": True,
            "requires_human_review": True,
            "allowed_actions": _review_options(),
            "blocked_actions": [
                "create_formal_mechanism_card",
                "write_relation",
                "write_zotero",
                "write_vector_store",
                "auto_call_llm",
            ],
        },
        "draft_summary": _draft_summary(draft_json),
        "source_parity": _source_parity(draft_json, source_pack),
        "source_material": _source_material(pending_preview, draft_json, source_pack),
        "linked_objects": _linked_objects(draft_json, source_pack),
        "validation": _validation_summary(True, validation_report),
        "review_actions": _review_options(),
        "safety_boundary": _safety_flags(),
    }
    return {
        "status": "OK",
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "review_ready": True,
        "review_packet": packet,
        "blockers": [],
        **_safety_flags(),
    }


def build_mechanism_draft_review_ui_packet(
    pasteback_validation_result: Mapping[str, Any],
    *,
    source_pack_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a UI review packet and require both primary sources to be inspectable."""

    result = build_mechanism_draft_review_packet(
        pasteback_validation_result,
        source_pack_result=source_pack_result,
    )
    if not result.get("review_ready"):
        return result

    packet = _mapping(result.get("review_packet"))
    source_material = _mapping(packet.get("source_material"))
    blockers: list[str] = []
    user_note = _mapping(source_material.get("primary_user_note"))
    source_excerpt = _mapping(source_material.get("primary_source_excerpt"))
    note_text = str(user_note.get("note_text") or user_note.get("user_note_text") or "").strip()
    excerpt_text = str(source_excerpt.get("selected_text") or source_excerpt.get("chunk_text") or "").strip()
    if not note_text:
        blockers.append("primary_user_note_required_for_review_ui")
    if not excerpt_text:
        blockers.append("primary_source_excerpt_required_for_review_ui")
    if blockers:
        return {
            "status": "BLOCKED",
            "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
            "review_ready": False,
            "review_packet": None,
            "blockers": blockers,
            "validation": packet.get("validation"),
            **_safety_flags(),
        }
    return result


def preview_mechanism_draft_review_action(
    review_packet_or_result: Mapping[str, Any],
    *,
    action: str,
    review_notes: str | None = None,
    merge_into_packet_id: str | None = None,
) -> dict[str, Any]:
    packet = _unwrap_packet(review_packet_or_result)
    blockers: list[str] = []
    warnings: list[str] = []
    status_before = str(packet.get("review_status") or "")
    normalized_action = "merge_into" if action == "merge" else action
    proposed_status = ACTION_TO_STATUS.get(action)
    proposed_decision = ACTION_TO_DECISION.get(action)

    if not packet:
        blockers.append("review_packet_required")
    if action not in ACTION_TO_STATUS:
        blockers.append("invalid_review_action")
    if packet and status_before not in REVIEWABLE_STATUSES:
        blockers.append("review_status_not_reviewable")
    if normalized_action == "merge_into" and not merge_into_packet_id:
        blockers.append("merge_into_requires_target_packet_id")
    if not review_notes or not review_notes.strip():
        warnings.append("review_notes_recommended")

    return {
        "status": "BLOCKED" if blockers else "OK",
        "mode": ACTION_PREVIEW_MODE,
        "dry_run": True,
        "apply": False,
        "packet_id": packet.get("packet_id"),
        "requested_action": action,
        "normalized_action": normalized_action,
        "review_status_before": status_before or None,
        "proposed_review_status": proposed_status,
        "proposed_review_decision": proposed_decision,
        "review_notes": review_notes,
        "merge_into_packet_id": merge_into_packet_id,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "creates_formal_mechanism_card": False,
        **_safety_flags(),
    }


def _validation_blockers(validation_result: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not isinstance(validation_result, Mapping):
        return ["pasteback_validation_result_required"]
    if validation_result.get("validator_passed") is not True:
        blockers.append("validator_not_passed")
    pending_preview = validation_result.get("pending_draft_preview")
    if not isinstance(pending_preview, Mapping):
        blockers.append("pending_draft_preview_missing")
    else:
        if pending_preview.get("draft_status") != "pending":
            blockers.append("draft_status_must_be_pending")
        if pending_preview.get("review_status") != "pending":
            blockers.append("review_status_must_be_pending")
        if pending_preview.get("mechanism_card_created") is not False:
            blockers.append("mechanism_card_created_must_be_false")
        if not isinstance(pending_preview.get("draft_json"), Mapping):
            blockers.append("draft_json_missing")
    for key in (
        "db_write_performed",
        "llm_called",
        "external_model_called",
        "api_called",
        "mechanism_draft_persisted",
        "mechanism_card_created",
        "relation_generated",
        "zotero_write_performed",
        "vector_store_write_performed",
    ):
        if validation_result.get(key) is True:
            blockers.append(f"unsafe_flag_true:{key}")
    return list(dict.fromkeys(blockers))


def _draft_summary(draft_json: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "should_generate_mechanism": draft_json.get("should_generate_mechanism"),
        "mechanism_key": draft_json.get("mechanism_key"),
        "mechanism_name_cn": draft_json.get("mechanism_name_cn"),
        "mechanism_name_en": draft_json.get("mechanism_name_en"),
        "mechanism_type": draft_json.get("mechanism_type"),
        "confidence": draft_json.get("confidence"),
        "short_explanation": draft_json.get("short_explanation"),
        "needs_user_review_reason": draft_json.get("needs_user_review_reason"),
    }


def _source_parity(draft_json: Mapping[str, Any], source_pack: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_mode": draft_json.get("source_mode") or source_pack.get("source_mode") or "unknown",
        "primary_source_roles": {
            "user_note": "primary_source",
            "source_excerpt": "primary_source",
            "linked_object": "semantic_support",
        },
        "source_balance_policy": deepcopy(dict(source_pack.get("source_balance_policy") or {})),
        "user_note_contribution": draft_json.get("user_note_contribution"),
        "source_excerpt_contribution": draft_json.get("source_excerpt_contribution"),
        "linked_object_contribution": draft_json.get("linked_object_contribution"),
        "evidence_alignment": draft_json.get("evidence_alignment"),
        "source_balance_warnings": _list(draft_json.get("source_balance_warnings")),
    }


def _source_material(
    pending_preview: Mapping[str, Any],
    draft_json: Mapping[str, Any],
    source_pack: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_inspiration_note_ids": _list(pending_preview.get("source_inspiration_note_ids")),
        "evidence_chunk_ids": _list(pending_preview.get("evidence_chunk_ids")),
        "draft_evidence_chunk_ids": _list(draft_json.get("evidence_chunk_ids")),
        "immutable_inspiration_provenance": deepcopy(
            _list(pending_preview.get("immutable_inspiration_provenance"))
        ),
        "primary_user_note": deepcopy(_mapping(source_pack.get("primary_user_note"))),
        "primary_source_excerpt": deepcopy(_mapping(source_pack.get("primary_source_excerpt"))),
        "citation_tokens": _list(source_pack.get("citation_tokens")),
    }


def _linked_objects(draft_json: Mapping[str, Any], source_pack: Mapping[str, Any]) -> dict[str, Any]:
    linked_knowledge = _mapping(source_pack.get("linked_knowledge"))
    approved_objects = _mapping_list(linked_knowledge.get("objects"))
    return {
        "source_object_ids": _int_list(
            item.get("object_id") or item.get("id") for item in approved_objects
        ),
        "approved_objects": deepcopy(approved_objects),
        "draft_linked_objects": deepcopy(_mapping_list(draft_json.get("linked_objects"))),
        "role": "semantic_support_not_mechanism",
    }


def _validation_summary(validator_passed: bool, validation_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "validator_passed": validator_passed,
        "status": validation_report.get("status"),
        "is_valid": validation_report.get("is_valid"),
        "blocked": validation_report.get("blocked"),
        "errors": _list(validation_report.get("errors")),
        "warnings": _list(validation_report.get("warnings")),
    }


def _review_options() -> list[dict[str, Any]]:
    return [
        {
            "action": "accept",
            "review_status": "accepted",
            "meaning": "Accept this draft only; no formal mechanism card is created.",
        },
        {
            "action": "reject",
            "review_status": "rejected",
            "meaning": "Reject this draft candidate.",
        },
        {
            "action": "needs_edit",
            "review_status": "needs_edit",
            "meaning": "Keep the draft as needing user or prompt revision.",
        },
        {
            "action": "defer",
            "review_status": "deferred",
            "meaning": "Postpone the review decision.",
        },
        {
            "action": "merge_into",
            "review_status": "merged",
            "meaning": "Mark this draft as merged into another reviewed draft; do not merge JSON here.",
        },
    ]


def _unwrap_packet(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("review_packet"), Mapping):
        return deepcopy(dict(value["review_packet"]))
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _source_pack(source_pack_result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source_pack_result, Mapping):
        return {}
    pack = source_pack_result.get("mechanism_source_pack")
    return deepcopy(dict(pack)) if isinstance(pack, Mapping) else {}


def _packet_id(pending_preview: Mapping[str, Any], source_pack: Mapping[str, Any]) -> str:
    material = {
        "draft_json": pending_preview.get("draft_json"),
        "source_inspiration_note_ids": pending_preview.get("source_inspiration_note_ids"),
        "evidence_chunk_ids": pending_preview.get("evidence_chunk_ids"),
        "citation_tokens": source_pack.get("citation_tokens"),
    }
    digest = hashlib.sha256(_json_text(material).encode("utf-8")).hexdigest()
    return f"mdraft_review_{digest[:24]}"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _int_list(values: Any) -> list[int]:
    results: list[int] = []
    for value in values or []:
        try:
            integer = int(value)
        except (TypeError, ValueError):
            continue
        if integer not in results:
            results.append(integer)
    return results


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_db_write_allowed": False,
        "llm_called": False,
        "external_model_called": False,
        "api_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "mechanism_draft_persisted": False,
        "mechanism_card_created": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
