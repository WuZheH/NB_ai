from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.services.human_review_queue_builder import validate_human_review_queue


ACCEPT_DECISIONS = {"accept", "accept_critic", "accept_as_draft_plan"}
REJECT_DECISIONS = {"reject", "reject_critic"}
DEFER_DECISIONS = {"defer"}


def apply_human_review_decisions(
    queue: dict[str, Any],
    decisions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply explicit user decisions to a human review queue in memory.

    The input queue is never mutated. If any decision is invalid, the function
    fails closed and returns validation errors without partially applying safe
    or unsafe state transitions.
    """
    validation_errors = _validate_inputs(queue, decisions or [])
    if validation_errors:
        return _empty_result(queue, validation_errors)

    queue_copy = deepcopy(queue)
    items_by_id = {item["review_item_id"]: item for item in queue_copy.get("items", [])}
    applied_decisions: list[dict[str, Any]] = []
    accepted_items: list[dict[str, Any]] = []
    rejected_items: list[dict[str, Any]] = []
    deferred_items: list[dict[str, Any]] = []

    for decision in decisions or []:
        item = items_by_id[decision["review_item_id"]]
        applied = _apply_single_decision(item, decision)
        applied_decisions.append(applied)
        if item["status"] == "accepted_by_user":
            accepted_items.append(_item_result(item))
        elif item["status"] == "rejected_by_user":
            rejected_items.append(_item_result(item))
        elif item["status"] == "deferred":
            deferred_items.append(_item_result(item))

    missing_decision_items = [
        _item_result(item)
        for item in queue_copy.get("items", [])
        if item.get("review_decision") is None and item.get("status") == "pending_review"
    ]

    return {
        "ok": True,
        "review_queue_id": queue.get("review_queue_id"),
        "applied_decisions": applied_decisions,
        "deferred_items": deferred_items,
        "rejected_items": rejected_items,
        "accepted_items": accepted_items,
        "missing_decision_items": missing_decision_items,
        "validation_errors": [],
        "summary": _summary(queue_copy, applied_decisions, accepted_items, rejected_items, deferred_items),
        "safety_flags": _safety_flags(queue_copy),
    }


def _validate_inputs(queue: dict[str, Any], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    try:
        validate_human_review_queue(queue)
    except ValueError as exc:
        errors.append({"error_code": "invalid_review_queue", "error_message": str(exc)})
        return errors

    items_by_id = {item["review_item_id"]: item for item in queue.get("items", [])}
    seen_ids: set[str] = set()
    for index, decision in enumerate(decisions):
        review_item_id = decision.get("review_item_id")
        decision_value = decision.get("decision")
        if not review_item_id:
            errors.append(
                {
                    "error_code": "missing_review_item_id",
                    "error_message": f"decision at index {index} is missing review_item_id",
                }
            )
            continue
        if review_item_id in seen_ids:
            errors.append(
                {
                    "error_code": "duplicate_decision_for_item",
                    "review_item_id": review_item_id,
                    "error_message": f"duplicate decision for review item {review_item_id}",
                }
            )
            continue
        seen_ids.add(review_item_id)
        item = items_by_id.get(review_item_id)
        if item is None:
            errors.append(
                {
                    "error_code": "unknown_review_item_id",
                    "review_item_id": review_item_id,
                    "error_message": f"review item {review_item_id} does not exist",
                }
            )
            continue
        if decision_value not in item.get("allowed_decisions", []):
            errors.append(
                {
                    "error_code": "decision_not_allowed",
                    "review_item_id": review_item_id,
                    "decision": decision_value,
                    "error_message": f"decision {decision_value!r} is not allowed for {review_item_id}",
                }
            )
            continue
        if decision_value in ACCEPT_DECISIONS and decision.get("created_by") != "user":
            errors.append(
                {
                    "error_code": "accept_requires_user_actor",
                    "review_item_id": review_item_id,
                    "decision": decision_value,
                    "error_message": "accept-type decisions require created_by='user'",
                }
            )
    return errors


def _apply_single_decision(item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    decision_record = {
        "review_item_id": item["review_item_id"],
        "decision": decision["decision"],
        "reviewer_note": decision.get("reviewer_note"),
        "edited_payload": deepcopy(decision.get("edited_payload") or {}),
        "evidence_refs_added": list(decision.get("evidence_refs_added") or []),
        "evidence_refs_removed": list(decision.get("evidence_refs_removed") or []),
        "created_by": decision.get("created_by") or "user",
        "created_at": decision.get("created_at") or _utc_now(),
    }
    item["review_decision"] = decision_record
    decision_value = decision["decision"]
    if decision_value in ACCEPT_DECISIONS:
        item["status"] = "accepted_by_user"
    elif decision_value in REJECT_DECISIONS:
        item["status"] = "rejected_by_user"
    elif decision_value in DEFER_DECISIONS:
        item["status"] = "deferred"
    else:
        item["status"] = "pending_review"
    return {
        "review_item_id": item["review_item_id"],
        "item_type": item.get("item_type"),
        "decision": decision_value,
        "result_status": item["status"],
        "source_id": item.get("source_id"),
    }


def _item_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_item_id": item.get("review_item_id"),
        "item_type": item.get("item_type"),
        "source_id": item.get("source_id"),
        "status": item.get("status"),
        "payload": deepcopy(item.get("payload") or {}),
        "evidence_refs": list(item.get("evidence_refs") or []),
        "source_trace": deepcopy(item.get("source_trace") or {}),
        "review_decision": deepcopy(item.get("review_decision")),
    }


def _empty_result(queue: dict[str, Any], validation_errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "review_queue_id": queue.get("review_queue_id") if isinstance(queue, dict) else None,
        "applied_decisions": [],
        "deferred_items": [],
        "rejected_items": [],
        "accepted_items": [],
        "missing_decision_items": [],
        "validation_errors": validation_errors,
        "summary": {
            "total_decisions_requested": 0,
            "applied_decision_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "deferred_count": 0,
            "pending_count": 0,
            "validation_error_count": len(validation_errors),
        },
        "safety_flags": _base_safety_flags(),
    }


def _summary(
    queue: dict[str, Any],
    applied_decisions: list[dict[str, Any]],
    accepted_items: list[dict[str, Any]],
    rejected_items: list[dict[str, Any]],
    deferred_items: list[dict[str, Any]],
) -> dict[str, int]:
    items = list(queue.get("items") or [])
    return {
        "total_items": len(items),
        "total_decisions_requested": len(applied_decisions),
        "applied_decision_count": len(applied_decisions),
        "accepted_count": len(accepted_items),
        "rejected_count": len(rejected_items),
        "deferred_count": len(deferred_items),
        "pending_count": sum(1 for item in items if item.get("status") == "pending_review"),
        "validation_error_count": 0,
    }


def _safety_flags(queue: dict[str, Any]) -> dict[str, bool]:
    flags = _base_safety_flags()
    if _contains_key_recursive(queue, "final_hypothesis"):
        flags["final_hypothesis_created"] = True
    for item in queue.get("items", []):
        if item.get("status") == "active_candidate":
            flags["active_candidate_created"] = True
        if item.get("status") == "accepted_by_user":
            decision = item.get("review_decision") or {}
            if decision.get("decision") not in ACCEPT_DECISIONS or decision.get("created_by") != "user":
                flags["accepted_without_explicit_user_accept"] = True
        if item.get("status") in {"deferred", "rejected_by_user"}:
            flags["defer_or_reject_created_acceptance"] = flags["defer_or_reject_created_acceptance"] or False
    return flags


def _base_safety_flags() -> dict[str, bool]:
    return {
        "dry_run": True,
        "production_db_written": False,
        "schema_changed": False,
        "llm_called": False,
        "api_called": False,
        "network_called": False,
        "final_hypothesis_created": False,
        "active_candidate_created": False,
        "accepted_without_explicit_user_accept": False,
        "defer_or_reject_created_acceptance": False,
        "input_queue_mutated": False,
    }


def _contains_key_recursive(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_contains_key_recursive(child, target_key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key_recursive(child, target_key) for child in value)
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
