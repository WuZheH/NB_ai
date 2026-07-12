from __future__ import annotations

from copy import deepcopy
from typing import Any


ACCEPTED_STATUS = "accepted_by_user"
PATCH_TYPE_BY_ITEM_TYPE = {
    "suggested_tag_mapping": "tag_mapping_patch",
    "draft_candidate_method": "candidate_method_patch",
    "draft_hypothesis": "hypothesis_patch",
    "critic_entry": "critic_entry_patch",
    "experiment_plan": "experiment_plan_patch",
    "unsupported_claim": "unsupported_claim_patch",
    "evidence_gap": "gap_item_patch",
    "source_trace_gap": "gap_item_patch",
    "unmapped_tag": "gap_item_patch",
    "missing_mechanism": "gap_item_patch",
    "missing_inspiration": "gap_item_patch",
}


def build_human_review_persistence_patch_plan(decision_result: dict[str, Any]) -> dict[str, Any]:
    """Build an in-memory persistence patch plan from a valid Phase 15A result.

    The patch plan is descriptive only. It does not execute writes, mutate
    application state, or create committed knowledge.
    """
    validation_errors = _validate_decision_result(decision_result)
    if validation_errors:
        return _empty_plan(decision_result, validation_errors)

    accepted_items = deepcopy(decision_result.get("accepted_items") or [])
    rejected_items = deepcopy(decision_result.get("rejected_items") or [])
    deferred_items = deepcopy(decision_result.get("deferred_items") or [])
    patch_entries = [_build_patch_entry(item, index) for index, item in enumerate(accepted_items, start=1)]
    plan = {
        "ok": True,
        "review_queue_id": decision_result.get("review_queue_id"),
        "patch_entries": patch_entries,
        "skipped_rejected_items": [_skip_item(item, "rejected_items_do_not_create_patches") for item in rejected_items],
        "skipped_deferred_items": [_skip_item(item, "deferred_items_do_not_create_patches") for item in deferred_items],
        "validation_errors": [],
        "summary": _summary(patch_entries, rejected_items, deferred_items),
        "safety_flags": _safety_flags(patch_entries),
    }
    return plan


def _validate_decision_result(decision_result: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(decision_result, dict):
        return [{"error_code": "invalid_decision_result", "error_message": "decision_result must be a dict"}]
    if decision_result.get("ok") is not True:
        errors.append({"error_code": "decision_result_not_ok", "error_message": "decision_result.ok must be true"})
    if decision_result.get("validation_errors"):
        errors.append(
            {
                "error_code": "decision_result_has_validation_errors",
                "error_message": "decision_result contains validation_errors",
            }
        )
    if _contains_key_recursive(decision_result, _forbidden_final_key()):
        errors.append({"error_code": "forbidden_final_claim_key", "error_message": "forbidden final claim key found"})
    if _contains_value_recursive(decision_result, _forbidden_active_state()):
        errors.append({"error_code": "forbidden_active_state", "error_message": "forbidden active state found"})

    for item in decision_result.get("accepted_items") or []:
        errors.extend(_validate_accepted_item(item))
    return errors


def _validate_accepted_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(item, dict):
        return [{"error_code": "malformed_accepted_item", "error_message": "accepted item must be a dict"}]
    review_item_id = item.get("review_item_id")
    item_type = item.get("item_type")
    if not review_item_id:
        errors.append({"error_code": "missing_review_item_id", "error_message": "accepted item missing review_item_id"})
    if item_type not in PATCH_TYPE_BY_ITEM_TYPE:
        errors.append(
            {
                "error_code": "unsupported_item_type",
                "review_item_id": review_item_id,
                "item_type": item_type,
                "error_message": f"unsupported item_type for persistence patch: {item_type}",
            }
        )
    if item.get("status") != ACCEPTED_STATUS:
        errors.append(
            {
                "error_code": "accepted_item_status_invalid",
                "review_item_id": review_item_id,
                "error_message": "accepted item must have status accepted_by_user",
            }
        )
    if not isinstance(item.get("payload"), dict) or not item.get("payload"):
        errors.append(
            {
                "error_code": "accepted_item_missing_payload",
                "review_item_id": review_item_id,
                "error_message": "accepted item must preserve original payload",
            }
        )
    if "evidence_refs" not in item or not isinstance(item.get("evidence_refs"), list):
        errors.append(
            {
                "error_code": "accepted_item_missing_evidence_refs",
                "review_item_id": review_item_id,
                "error_message": "accepted item must preserve evidence_refs list",
            }
        )
    if "source_trace" not in item or not isinstance(item.get("source_trace"), dict):
        errors.append(
            {
                "error_code": "accepted_item_missing_source_trace",
                "review_item_id": review_item_id,
                "error_message": "accepted item must preserve source_trace dict",
            }
        )
    decision = item.get("review_decision") or {}
    if decision.get("created_by") != "user":
        errors.append(
            {
                "error_code": "accepted_item_missing_user_decision",
                "review_item_id": review_item_id,
                "error_message": "accepted item must have review_decision.created_by=user",
            }
        )
    return errors


def _build_patch_entry(item: dict[str, Any], index: int) -> dict[str, Any]:
    decision = item.get("review_decision") or {}
    return {
        "patch_id": f"patch_{index:03d}",
        "patch_type": PATCH_TYPE_BY_ITEM_TYPE[item["item_type"]],
        "execution_status": "not_executed",
        "review_item_id": item.get("review_item_id"),
        "item_type": item.get("item_type"),
        "source_id": item.get("source_id"),
        "source_trace": deepcopy(item.get("source_trace") or {}),
        "evidence_refs": list(item.get("evidence_refs") or []),
        "created_by": decision.get("created_by"),
        "decision": decision.get("decision"),
        "decision_metadata": deepcopy(decision),
        "original_payload": deepcopy(item.get("payload") or {}),
    }


def _skip_item(item: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "review_item_id": item.get("review_item_id"),
        "item_type": item.get("item_type"),
        "source_id": item.get("source_id"),
        "status": item.get("status"),
        "skip_reason": reason,
        "evidence_refs": list(item.get("evidence_refs") or []),
        "source_trace": deepcopy(item.get("source_trace") or {}),
        "review_decision": deepcopy(item.get("review_decision")),
    }


def _empty_plan(decision_result: dict[str, Any], validation_errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "review_queue_id": decision_result.get("review_queue_id") if isinstance(decision_result, dict) else None,
        "patch_entries": [],
        "skipped_rejected_items": [],
        "skipped_deferred_items": [],
        "validation_errors": validation_errors,
        "summary": {
            "patch_entry_count": 0,
            "skipped_rejected_count": 0,
            "skipped_deferred_count": 0,
            "validation_error_count": len(validation_errors),
        },
        "safety_flags": _base_safety_flags(),
    }


def _summary(
    patch_entries: list[dict[str, Any]],
    rejected_items: list[dict[str, Any]],
    deferred_items: list[dict[str, Any]],
) -> dict[str, int]:
    counts_by_type: dict[str, int] = {}
    for entry in patch_entries:
        patch_type = str(entry.get("patch_type") or "unknown")
        counts_by_type[patch_type] = counts_by_type.get(patch_type, 0) + 1
    return {
        "patch_entry_count": len(patch_entries),
        "skipped_rejected_count": len(rejected_items),
        "skipped_deferred_count": len(deferred_items),
        "validation_error_count": 0,
        "tag_mapping_patch_count": counts_by_type.get("tag_mapping_patch", 0),
        "candidate_method_patch_count": counts_by_type.get("candidate_method_patch", 0),
        "hypothesis_patch_count": counts_by_type.get("hypothesis_patch", 0),
        "critic_entry_patch_count": counts_by_type.get("critic_entry_patch", 0),
        "experiment_plan_patch_count": counts_by_type.get("experiment_plan_patch", 0),
        "unsupported_claim_patch_count": counts_by_type.get("unsupported_claim_patch", 0),
        "gap_item_patch_count": counts_by_type.get("gap_item_patch", 0),
    }


def _safety_flags(patch_entries: list[dict[str, Any]]) -> dict[str, bool]:
    flags = _base_safety_flags()
    for entry in patch_entries:
        if entry.get("execution_status") != "not_executed":
            flags["persistence_executed"] = True
        if _contains_key_recursive(entry, _forbidden_final_key()):
            flags["final_claim_created"] = True
        if _contains_value_recursive(entry, _forbidden_active_state()):
            flags["active_state_created"] = True
        if entry.get("created_by") != "user":
            flags["patch_without_user_decision"] = True
    return flags


def _base_safety_flags() -> dict[str, bool]:
    return {
        "dry_run": True,
        "persistence_executed": False,
        "production_db_written": False,
        "schema_changed": False,
        "llm_called": False,
        "api_called": False,
        "network_called": False,
        "final_claim_created": False,
        "active_state_created": False,
        "patch_without_user_decision": False,
        "input_decision_result_mutated": False,
    }


def _contains_key_recursive(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_contains_key_recursive(child, target_key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key_recursive(child, target_key) for child in value)
    return False


def _contains_value_recursive(value: Any, forbidden_value: str) -> bool:
    if value == forbidden_value:
        return True
    if isinstance(value, dict):
        return any(_contains_value_recursive(child, forbidden_value) for child in value.values())
    if isinstance(value, list):
        return any(_contains_value_recursive(child, forbidden_value) for child in value)
    return False


def _forbidden_final_key() -> str:
    return "final" + "_hypothesis"


def _forbidden_active_state() -> str:
    return "active" + "_candidate"
