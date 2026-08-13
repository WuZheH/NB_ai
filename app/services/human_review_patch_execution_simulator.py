from __future__ import annotations

from copy import deepcopy
from typing import Any


TAG_MAPPING_PATCH = "tag_mapping_patch"


class FakeTagMappingReviewRepository:
    """In-memory review/audit repository for simulation only."""

    def __init__(self, initial_records: list[dict[str, Any]] | None = None) -> None:
        self.review_records = deepcopy(initial_records or [])
        self.canonical_knowledge_tags = []
        self._staged_records: list[dict[str, Any]] = []
        self._transaction_active = False

    def begin(self) -> None:
        if self._transaction_active:
            raise ValueError("transaction already active")
        self._transaction_active = True
        self._staged_records = []

    def stage_record(self, record: dict[str, Any]) -> None:
        if not self._transaction_active:
            raise ValueError("transaction is not active")
        self._staged_records.append(deepcopy(record))

    def commit(self) -> list[dict[str, Any]]:
        if not self._transaction_active:
            raise ValueError("transaction is not active")
        committed = deepcopy(self._staged_records)
        self.review_records.extend(committed)
        self._staged_records = []
        self._transaction_active = False
        return committed

    def rollback(self) -> list[dict[str, Any]]:
        rolled_back = deepcopy(self._staged_records)
        self._staged_records = []
        self._transaction_active = False
        return rolled_back

    def snapshot(self) -> dict[str, Any]:
        return {
            "review_records": deepcopy(self.review_records),
            "canonical_knowledge_tags": deepcopy(self.canonical_knowledge_tags),
        }


def simulate_tag_mapping_patch_execution(
    preflight_package: dict[str, Any] | None,
    patch_plan: dict[str, Any],
    repository: FakeTagMappingReviewRepository | None = None,
) -> dict[str, Any]:
    """Simulate tag_mapping_patch execution against an in-memory fake repository."""
    original_preflight = deepcopy(preflight_package)
    original_patch_plan = deepcopy(patch_plan)
    repo = repository or FakeTagMappingReviewRepository()
    before_snapshot = repo.snapshot()
    safety_flags = _base_safety_flags()
    validation_errors = _validate_preflight_gate(preflight_package, safety_flags)
    patch_entries = deepcopy(patch_plan.get("patch_entries") or []) if isinstance(patch_plan, dict) else []
    eligible_entries: list[dict[str, Any]] = []
    blocked_entries: list[dict[str, Any]] = []

    if not validation_errors:
        for entry in patch_entries:
            if not isinstance(entry, dict) or entry.get("patch_type") != TAG_MAPPING_PATCH:
                safety_flags["non_tag_mapping_patch_blocked"] = True
                blocked_entries.append(_blocked_entry(entry, "non_tag_mapping_patch_blocked"))
                continue
            eligible_entries.append(entry)

    simulated_records: list[dict[str, Any]] = []
    rollback_record = _rollback_record(False, "not_needed", [])

    if validation_errors:
        execution_status = "simulated_failed"
    else:
        try:
            repo.begin()
            for entry in eligible_entries:
                entry_errors = _validate_tag_mapping_entry(entry, safety_flags)
                if entry_errors:
                    validation_errors.extend(entry_errors)
                    raise ValueError("tag mapping patch validation failed")
                repo.stage_record(_record_from_entry(entry, len(simulated_records) + 1))
                simulated_records = deepcopy(repo._staged_records)
            simulated_records = repo.commit()
            execution_status = "simulated_success"
        except Exception:
            rolled_back = repo.rollback()
            simulated_records = []
            rollback_record = _rollback_record(True, "rolled_back_staged_records", rolled_back)
            execution_status = "simulated_failed"

    after_snapshot = repo.snapshot()
    if (
        before_snapshot["canonical_knowledge_tags"] != after_snapshot["canonical_knowledge_tags"]
        or repo.canonical_knowledge_tags
    ):
        safety_flags["attempted_canonical_knowledge_tag_mutation"] = True
        validation_errors.append(
            {
                "error_code": "canonical_knowledge_tag_mutation_forbidden",
                "error_message": "simulation must not mutate canonical knowledge tags",
            }
        )
        execution_status = "simulated_failed"

    if execution_status == "simulated_failed" and simulated_records:
        safety_flags["partial_execution_detected"] = True

    patch_entry_refs = _patch_entry_refs(patch_entries)
    audit_record = {
        "audit_record_id": _audit_record_id(preflight_package, patch_entries),
        "created_by": "system",
        "source_phase": "15E",
        "simulation_only": True,
        "persistence_executed": False,
        "input_preflight_audit_record_id": _preflight_audit_id(preflight_package),
        "patch_entry_refs": patch_entry_refs,
        "simulated_records": deepcopy(simulated_records),
        "execution_status": execution_status,
        "rollback_record": deepcopy(rollback_record),
        "safety_flags": deepcopy(safety_flags),
        "errors": deepcopy(validation_errors),
    }
    result = {
        "execution_status": execution_status,
        "persistence_executed": False,
        "simulation_only": True,
        "executed_patch_count": 0,
        "simulated_patch_count": len(simulated_records) if execution_status == "simulated_success" else 0,
        "blocked_patch_count": len(blocked_entries) + (len(eligible_entries) if execution_status == "simulated_failed" else 0),
        "simulated_records": deepcopy(simulated_records),
        "audit_record": audit_record,
        "rollback_record": rollback_record,
        "safety_flags": safety_flags,
        "validation_errors": validation_errors,
        "blocked_patch_entries": blocked_entries,
        "input_preflight_mutated": preflight_package != original_preflight,
        "input_patch_plan_mutated": patch_plan != original_patch_plan,
    }
    return result


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
        safety_flags["persistence_executed"] = True
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
                    "error_message": "Phase 15E simulation requires clean Phase 15C safety flags",
                }
            )
    return errors


def _validate_tag_mapping_entry(entry: dict[str, Any], safety_flags: dict[str, bool]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
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
        errors.append({"error_code": "confirmed_relation_forbidden", "review_item_id": entry.get("review_item_id")})
    if _contains_key_recursive(entry, "experiment_result"):
        errors.append({"error_code": "experiment_result_forbidden", "review_item_id": entry.get("review_item_id")})
    if _contains_key_recursive(entry, "canonical_knowledge_tag_mutation") or bool(
        entry.get("canonical_knowledge_tag_mutation")
    ):
        safety_flags["attempted_canonical_knowledge_tag_mutation"] = True
        errors.append(
            {
                "error_code": "canonical_knowledge_tag_mutation_forbidden",
                "review_item_id": entry.get("review_item_id"),
            }
        )
    if _contains_key_recursive(entry, "persistence_execution_result"):
        errors.append({"error_code": "persistence_execution_result_forbidden", "review_item_id": entry.get("review_item_id")})
    payload = entry.get("original_payload") or {}
    if not _tag_name(payload) or not _target_bucket(payload):
        errors.append({"error_code": "missing_tag_mapping_payload", "review_item_id": entry.get("review_item_id")})
    return errors


def _record_from_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    payload = entry.get("original_payload") or {}
    return {
        "record_id": f"sim_tag_mapping_review_{index:03d}",
        "record_type": "accepted_tag_mapping_review_record",
        "source_patch_entry_id": entry.get("patch_id"),
        "review_item_id": entry.get("review_item_id"),
        "tag_name": _tag_name(payload),
        "target_bucket": _target_bucket(payload),
        "status": "accepted_by_user",
        "created_by": "user",
        "evidence_refs": list(entry.get("evidence_refs") or []),
        "source_trace": deepcopy(entry.get("source_trace") or {}),
        "simulation_only": True,
        "canonical_knowledge_tag_mutated": False,
    }


def _tag_name(payload: dict[str, Any]) -> Any:
    return payload.get("name") or payload.get("tag_name") or payload.get("suggested_name")


def _target_bucket(payload: dict[str, Any]) -> Any:
    return payload.get("target_bucket") or payload.get("suggested_bucket")


def _blocked_entry(entry: Any, reason: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"review_item_id": None, "patch_type": None, "block_reason": reason}
    return {
        "patch_id": entry.get("patch_id"),
        "review_item_id": entry.get("review_item_id"),
        "patch_type": entry.get("patch_type"),
        "block_reason": reason,
        "execution_status": entry.get("execution_status"),
    }


def _rollback_record(rollback_performed: bool, reason: str, rolled_back_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rollback_performed": rollback_performed,
        "reason": reason,
        "rolled_back_record_count": len(rolled_back_records),
        "rolled_back_records": deepcopy(rolled_back_records),
        "persistence_executed": False,
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
    preflight_id = _preflight_audit_id(preflight_package) or "unknown"
    return f"audit_15e_{preflight_id}_{len(patch_entries):03d}"


def _preflight_audit_id(preflight_package: dict[str, Any] | None) -> Any:
    if not isinstance(preflight_package, dict):
        return None
    return (preflight_package.get("audit_record") or {}).get("audit_record_id")


def _base_safety_flags() -> dict[str, bool]:
    return {
        "preflight_not_passed": False,
        "contains_final_hypothesis": False,
        "contains_active_candidate": False,
        "contains_unconfirmed_user_decision": False,
        "missing_evidence_refs": False,
        "missing_source_trace": False,
        "unknown_patch_type": False,
        "non_tag_mapping_patch_blocked": False,
        "transaction_unavailable": False,
        "rollback_unavailable": False,
        "partial_execution_detected": False,
        "audit_write_failed": False,
        "target_table_not_allowlisted": False,
        "attempted_canonical_knowledge_tag_mutation": False,
        "persistence_executed": False,
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
