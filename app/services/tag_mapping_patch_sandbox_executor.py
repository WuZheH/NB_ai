from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.accepted_tag_change_sandbox_repository import AcceptedTagChangeSandboxRepository


TAG_MAPPING_PATCH = "tag_mapping_patch"


def execute_tag_mapping_patches_in_sandbox(
    preflight_package: dict[str, Any] | None,
    patch_plan: dict[str, Any],
    repository: AcceptedTagChangeSandboxRepository,
    *,
    executor_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write tag_mapping_patch rows to a sandbox accepted_tag_changes table only."""
    original_preflight = deepcopy(preflight_package)
    original_patch_plan = deepcopy(patch_plan)
    safety_flags = _base_safety_flags()
    validation_errors = _validate_preflight_gate(preflight_package, safety_flags)
    patch_entries = deepcopy(patch_plan.get("patch_entries") or []) if isinstance(patch_plan, dict) else []
    blocked_entries: list[dict[str, Any]] = []
    eligible_entries: list[dict[str, Any]] = []

    if repository is None:
        validation_errors.append({"error_code": "missing_sandbox_repository", "error_message": "repository is required"})
        safety_flags["target_table_not_allowlisted"] = True

    if not validation_errors:
        for entry in patch_entries:
            if not isinstance(entry, dict) or entry.get("patch_type") != TAG_MAPPING_PATCH:
                safety_flags["non_tag_mapping_patch_blocked"] = True
                blocked_entries.append(_blocked_entry(entry, "non_tag_mapping_patch_blocked"))
                continue
            eligible_entries.append(entry)

    accepted_tag_change_ids: list[str] = []
    rollback_record = _rollback_record(False, "not_needed", 0)
    execution_status = "sandbox_failed" if validation_errors else "sandbox_success"
    execution_audit_record_id = _audit_record_id(preflight_package, patch_entries)

    if not validation_errors and eligible_entries:
        try:
            with repository.session.begin():
                for entry in eligible_entries:
                    entry_errors = _validate_tag_mapping_entry(entry, safety_flags)
                    if entry_errors:
                        validation_errors.extend(entry_errors)
                        raise ValueError("tag_mapping_patch validation failed")
                    record = repository.create_accepted_tag_change_from_patch(
                        entry,
                        patch_plan=patch_plan,
                        preflight_package=preflight_package or {},
                        execution_audit_record_id=execution_audit_record_id,
                    )
                    accepted_tag_change_ids.append(record.accepted_tag_change_id)
            execution_status = "sandbox_success"
        except Exception as exc:
            safety_flags["transaction_failed"] = True
            safety_flags["rollback_performed"] = True
            accepted_tag_change_ids = []
            rollback_record = _rollback_record(True, "rolled_back_sandbox_transaction", len(eligible_entries))
            if not validation_errors:
                validation_errors.append(
                    {
                        "error_code": "sandbox_transaction_failed",
                        "error_message": str(exc) or "sandbox transaction failed",
                    }
                )
            execution_status = "sandbox_failed"

    sandbox_records_created = len(accepted_tag_change_ids) if execution_status == "sandbox_success" else 0
    if execution_status == "sandbox_failed" and accepted_tag_change_ids:
        safety_flags["partial_execution_detected"] = True

    audit_record = {
        "audit_record_id": execution_audit_record_id,
        "source_phase": "15H",
        "created_by": "system",
        "sandbox_only": True,
        "production_db_touched": False,
        "persistence_executed": False,
        "sandbox_records_created": sandbox_records_created,
        "patch_entry_refs": _patch_entry_refs(patch_entries),
        "accepted_tag_change_ids": list(accepted_tag_change_ids),
        "rollback_record": deepcopy(rollback_record),
        "safety_flags": deepcopy(safety_flags),
        "errors": deepcopy(validation_errors),
        "executor_metadata": deepcopy(executor_metadata or {}),
    }
    return {
        "execution_status": execution_status,
        "sandbox_only": True,
        "production_db_touched": False,
        "persistence_executed": False,
        "sandbox_persistence_executed": sandbox_records_created > 0,
        "sandbox_records_created": sandbox_records_created,
        "blocked_patch_count": len(blocked_entries),
        "accepted_tag_change_ids": accepted_tag_change_ids,
        "audit_record": audit_record,
        "rollback_record": rollback_record,
        "safety_flags": safety_flags,
        "validation_errors": validation_errors,
        "blocked_patch_entries": blocked_entries,
        "input_preflight_mutated": preflight_package != original_preflight,
        "input_patch_plan_mutated": patch_plan != original_patch_plan,
    }


def _validate_preflight_gate(
    preflight_package: dict[str, Any] | None,
    safety_flags: dict[str, bool],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(preflight_package, dict):
        safety_flags["preflight_not_passed"] = True
        return [{"error_code": "missing_preflight_package", "error_message": "preflight package is required"}]
    if preflight_package.get("preflight_status") != "passed":
        safety_flags["preflight_not_passed"] = True
        errors.append({"error_code": "preflight_not_passed", "error_message": "preflight_status must be passed"})
    audit_record = preflight_package.get("audit_record") or {}
    if audit_record.get("execution_status") != "not_executed":
        safety_flags["preflight_not_passed"] = True
        errors.append(
            {
                "error_code": "preflight_audit_not_not_executed",
                "error_message": "preflight audit execution_status must be not_executed",
            }
        )
    if audit_record.get("persistence_executed") is not False:
        safety_flags["preflight_not_passed"] = True
        errors.append(
            {
                "error_code": "preflight_persistence_already_executed",
                "error_message": "preflight audit must report persistence_executed=false",
            }
        )
    for flag, value in (preflight_package.get("safety_flags") or {}).items():
        if bool(value):
            safety_flags["preflight_not_passed"] = True
            errors.append(
                {
                    "error_code": "blocking_preflight_safety_flag",
                    "safety_flag": flag,
                    "error_message": "Phase 15H sandbox execution requires clean Phase 15C safety flags",
                }
            )
    return errors


def _validate_tag_mapping_entry(entry: dict[str, Any], safety_flags: dict[str, bool]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    payload = entry.get("original_payload") or {}
    if entry.get("execution_status") != "not_executed":
        errors.append({"error_code": "patch_entry_not_not_executed", "review_item_id": entry.get("review_item_id")})
    if entry.get("created_by") != "user" or (entry.get("decision_metadata") or {}).get("created_by") != "user":
        safety_flags["contains_unconfirmed_user_decision"] = True
        errors.append({"error_code": "missing_user_decision", "review_item_id": entry.get("review_item_id")})
    if not entry.get("evidence_refs"):
        safety_flags["missing_evidence_refs"] = True
        errors.append({"error_code": "missing_evidence_refs", "review_item_id": entry.get("review_item_id")})
    if not isinstance(entry.get("source_trace"), dict) or not entry.get("source_trace"):
        safety_flags["missing_source_trace"] = True
        errors.append({"error_code": "missing_source_trace", "review_item_id": entry.get("review_item_id")})
    if _contains_key_recursive(entry, _forbidden_final_key()):
        safety_flags["contains_final_hypothesis"] = True
        errors.append({"error_code": "forbidden_final_hypothesis", "review_item_id": entry.get("review_item_id")})
    if _contains_value_recursive(entry, _forbidden_active_state()) or _contains_key_recursive(entry, _forbidden_active_state()):
        safety_flags["contains_active_candidate"] = True
        errors.append({"error_code": "forbidden_active_candidate", "review_item_id": entry.get("review_item_id")})
    if _contains_key_recursive(entry, "confirmed_relation"):
        safety_flags["contains_confirmed_relation"] = True
        errors.append({"error_code": "confirmed_relation_forbidden", "review_item_id": entry.get("review_item_id")})
    if not (payload.get("target_bucket") or payload.get("suggested_bucket")):
        safety_flags["accepted_tag_change_validation_failed"] = True
        errors.append({"error_code": "missing_target_bucket", "review_item_id": entry.get("review_item_id")})
    if not (payload.get("mapped_tag_name") or payload.get("name") or payload.get("tag_name")):
        safety_flags["accepted_tag_change_validation_failed"] = True
        errors.append({"error_code": "missing_mapped_tag_name", "review_item_id": entry.get("review_item_id")})
    return errors


def _blocked_entry(entry: Any, reason: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"patch_id": None, "review_item_id": None, "patch_type": None, "block_reason": reason}
    return {
        "patch_id": entry.get("patch_id"),
        "review_item_id": entry.get("review_item_id"),
        "patch_type": entry.get("patch_type"),
        "execution_status": entry.get("execution_status"),
        "block_reason": reason,
    }


def _rollback_record(rollback_performed: bool, reason: str, attempted_patch_count: int) -> dict[str, Any]:
    return {
        "rollback_performed": rollback_performed,
        "reason": reason,
        "attempted_patch_count": attempted_patch_count,
        "persistence_executed": False,
        "production_db_touched": False,
    }


def _patch_entry_refs(patch_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "patch_id": entry.get("patch_id"),
            "review_item_id": entry.get("review_item_id"),
            "patch_type": entry.get("patch_type"),
            "execution_status": "not_executed",
        }
        for entry in patch_entries
        if isinstance(entry, dict)
    ]


def _audit_record_id(preflight_package: dict[str, Any] | None, patch_entries: list[dict[str, Any]]) -> str:
    preflight_id = None
    if isinstance(preflight_package, dict):
        preflight_id = (preflight_package.get("audit_record") or {}).get("audit_record_id")
    return f"audit_15h_{preflight_id or 'unknown'}_{len(patch_entries):03d}"


def _base_safety_flags() -> dict[str, bool]:
    return {
        "preflight_not_passed": False,
        "contains_final_hypothesis": False,
        "contains_active_candidate": False,
        "contains_confirmed_relation": False,
        "contains_unconfirmed_user_decision": False,
        "missing_evidence_refs": False,
        "missing_source_trace": False,
        "unknown_patch_type": False,
        "non_tag_mapping_patch_blocked": False,
        "transaction_failed": False,
        "rollback_performed": False,
        "partial_execution_detected": False,
        "production_db_touched": False,
        "canonical_knowledge_tags_mutated": False,
        "target_table_not_allowlisted": False,
        "accepted_tag_change_validation_failed": False,
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
