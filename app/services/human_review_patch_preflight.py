from __future__ import annotations

from copy import deepcopy
from typing import Any


VALID_PATCH_TYPES = {
    "tag_mapping_patch",
    "candidate_method_patch",
    "hypothesis_patch",
    "critic_entry_patch",
    "experiment_plan_patch",
    "unsupported_claim_patch",
    "gap_item_patch",
}

REQUIRED_PATCH_ENTRY_FIELDS = {
    "patch_id",
    "patch_type",
    "execution_status",
    "review_item_id",
    "item_type",
    "source_trace",
    "evidence_refs",
    "created_by",
    "decision_metadata",
    "original_payload",
}


def build_persistence_patch_preflight(patch_plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and describe a Phase 15B patch plan without executing it."""
    original_snapshot = deepcopy(patch_plan)
    validation_errors, safety_flags = _validate_patch_plan(patch_plan)
    patch_entries = deepcopy(patch_plan.get("patch_entries") or []) if isinstance(patch_plan, dict) else []
    preflight_status = "failed" if validation_errors else "passed"
    executable_patch_count = len(patch_entries) if preflight_status == "passed" else 0
    blocked_patch_count = 0 if preflight_status == "passed" else len(patch_entries)
    patch_plan_summary = deepcopy(patch_plan.get("summary") or {}) if isinstance(patch_plan, dict) else {}

    package = {
        "preflight_status": preflight_status,
        "validation_errors": validation_errors,
        "patch_plan_summary": patch_plan_summary,
        "dry_run_diff": _build_dry_run_diff(patch_entries, preflight_status),
        "audit_record": _build_audit_record(
            patch_plan,
            preflight_status,
            validation_errors,
            safety_flags,
            patch_entries,
        ),
        "executable_patch_count": executable_patch_count,
        "blocked_patch_count": blocked_patch_count,
        "safety_flags": safety_flags,
    }
    package["safety_flags"]["input_patch_plan_mutated"] = patch_plan != original_snapshot
    return package


def _validate_patch_plan(patch_plan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    safety_flags = _base_safety_flags()
    validation_errors: list[dict[str, Any]] = []
    if not isinstance(patch_plan, dict):
        safety_flags["contains_missing_required_fields"] = True
        return (
            [{"error_code": "invalid_patch_plan", "error_message": "patch_plan must be a dict"}],
            safety_flags,
        )

    if patch_plan.get("validation_errors"):
        validation_errors.append(
            {
                "error_code": "input_patch_plan_has_validation_errors",
                "error_message": "input patch plan already contains validation errors",
            }
        )

    patch_entries = patch_plan.get("patch_entries")
    if not isinstance(patch_entries, list):
        safety_flags["contains_missing_required_fields"] = True
        validation_errors.append(
            {"error_code": "missing_patch_entries", "error_message": "patch_entries must be a list"}
        )
        patch_entries = []

    seen_review_item_ids: set[str] = set()
    for index, entry in enumerate(patch_entries):
        entry_errors = _validate_patch_entry(entry, index, safety_flags)
        validation_errors.extend(entry_errors)
        if isinstance(entry, dict):
            review_item_id = entry.get("review_item_id")
            if review_item_id:
                if review_item_id in seen_review_item_ids:
                    safety_flags["contains_duplicate_review_item_patch"] = True
                    validation_errors.append(
                        {
                            "error_code": "duplicate_review_item_id",
                            "review_item_id": review_item_id,
                            "error_message": "duplicate review_item_id patch entry",
                        }
                    )
                seen_review_item_ids.add(review_item_id)

    validation_errors.extend(_validate_summary_counts(patch_plan, patch_entries, safety_flags))
    return validation_errors, safety_flags


def _validate_patch_entry(
    entry: Any,
    index: int,
    safety_flags: dict[str, bool],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(entry, dict):
        safety_flags["contains_missing_required_fields"] = True
        return [
            {
                "error_code": "malformed_patch_entry",
                "patch_index": index,
                "error_message": "patch entry must be a dict",
            }
        ]

    missing_fields = sorted(field for field in REQUIRED_PATCH_ENTRY_FIELDS if field not in entry)
    if missing_fields:
        safety_flags["contains_missing_required_fields"] = True
        errors.append(
            {
                "error_code": "missing_required_patch_entry_fields",
                "patch_index": index,
                "review_item_id": entry.get("review_item_id"),
                "missing_fields": missing_fields,
                "error_message": "patch entry is missing required fields",
            }
        )

    if entry.get("execution_status") != "not_executed":
        safety_flags["contains_executed_patch_entry"] = True
        errors.append(
            {
                "error_code": "patch_entry_already_executed",
                "review_item_id": entry.get("review_item_id"),
                "execution_status": entry.get("execution_status"),
                "error_message": "patch entry execution_status must remain not_executed",
            }
        )

    if entry.get("patch_type") not in VALID_PATCH_TYPES:
        safety_flags["contains_unknown_patch_type"] = True
        errors.append(
            {
                "error_code": "unknown_patch_type",
                "review_item_id": entry.get("review_item_id"),
                "patch_type": entry.get("patch_type"),
                "error_message": "unknown patch_type",
            }
        )

    if _contains_key_recursive(entry, _forbidden_final_key()):
        safety_flags["contains_final_hypothesis"] = True
        errors.append(
            {
                "error_code": "forbidden_final_hypothesis",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry contains forbidden final hypothesis key",
            }
        )

    if _contains_value_recursive(entry, _forbidden_active_state()) or _contains_key_recursive(
        entry, _forbidden_active_state()
    ):
        safety_flags["contains_active_candidate"] = True
        errors.append(
            {
                "error_code": "forbidden_active_candidate",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry contains forbidden active candidate marker",
            }
        )

    if _contains_key_recursive(entry, "persistence_execution_result") or _contains_key_recursive(
        entry, "persistence_result"
    ):
        safety_flags["contains_persistence_execution_result"] = True
        errors.append(
            {
                "error_code": "persistence_execution_result_forbidden",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry must not contain persistence execution result",
            }
        )

    if bool(entry.get("db_write_intent")):
        safety_flags["contains_db_write_intent"] = True
        errors.append(
            {
                "error_code": "db_write_intent_forbidden",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry must not contain DB write intent",
            }
        )

    if bool(entry.get("external_call_intent")):
        safety_flags["contains_external_call_intent"] = True
        errors.append(
            {
                "error_code": "external_call_intent_forbidden",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry must not contain external call intent",
            }
        )

    if not isinstance(entry.get("evidence_refs"), list) or not entry.get("evidence_refs"):
        safety_flags["missing_evidence_refs_for_committable_patch"] = True
        errors.append(
            {
                "error_code": "missing_evidence_refs_for_patch",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "committable patch entry must preserve non-empty evidence_refs",
            }
        )

    decision_metadata = entry.get("decision_metadata")
    if not isinstance(decision_metadata, dict) or decision_metadata.get("created_by") != "user":
        safety_flags["missing_user_decision_for_committable_patch"] = True
        errors.append(
            {
                "error_code": "missing_user_decision_for_patch",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "committable patch entry must preserve explicit user decision metadata",
            }
        )

    if not isinstance(entry.get("source_trace"), dict):
        safety_flags["contains_missing_required_fields"] = True
        errors.append(
            {
                "error_code": "invalid_source_trace",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "source_trace must be preserved as a dict",
            }
        )

    if not isinstance(entry.get("original_payload"), dict) or not entry.get("original_payload"):
        safety_flags["contains_missing_required_fields"] = True
        errors.append(
            {
                "error_code": "invalid_original_payload",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "original_payload must be preserved as a non-empty dict",
            }
        )

    if entry.get("created_by") != "user":
        safety_flags["missing_user_decision_for_committable_patch"] = True
        errors.append(
            {
                "error_code": "patch_created_by_not_user",
                "review_item_id": entry.get("review_item_id"),
                "error_message": "patch entry must be traceable to an explicit user decision",
            }
        )

    return errors


def _validate_summary_counts(
    patch_plan: dict[str, Any],
    patch_entries: list[Any],
    safety_flags: dict[str, bool],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    summary = patch_plan.get("summary")
    if not isinstance(summary, dict):
        safety_flags["contains_missing_required_fields"] = True
        return [{"error_code": "missing_summary", "error_message": "patch plan summary must be a dict"}]

    actual_patch_count = len(patch_entries)
    if summary.get("patch_entry_count") != actual_patch_count:
        safety_flags["summary_count_mismatch"] = True
        errors.append(
            {
                "error_code": "patch_entry_count_mismatch",
                "expected": actual_patch_count,
                "actual": summary.get("patch_entry_count"),
                "error_message": "summary.patch_entry_count must match actual patch_entries length",
            }
        )

    counts_by_type: dict[str, int] = {}
    for entry in patch_entries:
        if isinstance(entry, dict):
            patch_type = str(entry.get("patch_type") or "unknown")
            counts_by_type[patch_type] = counts_by_type.get(patch_type, 0) + 1

    for patch_type in VALID_PATCH_TYPES:
        summary_key = f"{patch_type}_count"
        if summary_key in summary and summary.get(summary_key) != counts_by_type.get(patch_type, 0):
            safety_flags["summary_count_mismatch"] = True
            errors.append(
                {
                    "error_code": "patch_type_count_mismatch",
                    "summary_key": summary_key,
                    "expected": counts_by_type.get(patch_type, 0),
                    "actual": summary.get(summary_key),
                    "error_message": "summary patch type count must match actual patch entries",
                }
            )

    return errors


def _build_dry_run_diff(patch_entries: list[dict[str, Any]], preflight_status: str) -> dict[str, Any]:
    if preflight_status != "passed":
        return {
            "mode": "dry_run",
            "status": "blocked",
            "entries": [],
            "summary": {
                "planned_change_count": 0,
                "persistence_executed": False,
                "description": "No changes would be applied because preflight failed.",
            },
        }

    entries = []
    for entry in patch_entries:
        entries.append(
            {
                "patch_id": entry.get("patch_id"),
                "review_item_id": entry.get("review_item_id"),
                "patch_type": entry.get("patch_type"),
                "operation": "would_prepare_for_persistence",
                "description": (
                    f"Would prepare {entry.get('patch_type')} for review item "
                    f"{entry.get('review_item_id')} without executing persistence."
                ),
                "execution_status": "not_executed",
                "persistence_executed": False,
            }
        )
    return {
        "mode": "dry_run",
        "status": "descriptive_only",
        "entries": entries,
        "summary": {
            "planned_change_count": len(entries),
            "persistence_executed": False,
            "description": "Dry-run diff is descriptive only and does not inspect live application state.",
        },
    }


def _build_audit_record(
    patch_plan: Any,
    preflight_status: str,
    validation_errors: list[dict[str, Any]],
    safety_flags: dict[str, bool],
    patch_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    review_queue_id = patch_plan.get("review_queue_id") if isinstance(patch_plan, dict) else None
    patch_entry_refs = [
        {
            "patch_id": entry.get("patch_id"),
            "review_item_id": entry.get("review_item_id"),
            "patch_type": entry.get("patch_type"),
            "execution_status": "not_executed",
        }
        for entry in patch_entries
        if isinstance(entry, dict)
    ]
    return {
        "audit_record_id": _audit_record_id(review_queue_id, patch_entry_refs),
        "created_by": "system",
        "source_phase": "15C",
        "input_patch_plan_summary": deepcopy(patch_plan.get("summary") or {}) if isinstance(patch_plan, dict) else {},
        "preflight_status": preflight_status,
        "validation_errors": deepcopy(validation_errors),
        "safety_flags": deepcopy(safety_flags),
        "patch_entry_refs": patch_entry_refs,
        "execution_status": "not_executed",
        "persistence_executed": False,
    }


def _audit_record_id(review_queue_id: Any, patch_entry_refs: list[dict[str, Any]]) -> str:
    queue_part = str(review_queue_id or "unknown").replace(" ", "_")
    return f"audit_15c_{queue_part}_{len(patch_entry_refs):03d}"


def _base_safety_flags() -> dict[str, bool]:
    return {
        "contains_final_hypothesis": False,
        "contains_active_candidate": False,
        "contains_executed_patch_entry": False,
        "contains_unknown_patch_type": False,
        "contains_missing_required_fields": False,
        "contains_duplicate_review_item_patch": False,
        "contains_persistence_execution_result": False,
        "contains_db_write_intent": False,
        "contains_external_call_intent": False,
        "summary_count_mismatch": False,
        "missing_evidence_refs_for_committable_patch": False,
        "missing_user_decision_for_committable_patch": False,
        "input_patch_plan_mutated": False,
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
