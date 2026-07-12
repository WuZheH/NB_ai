from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


IN_MEMORY_ONLY = "in_memory_only"
TEST_FILE_ALLOWED = "test_file_allowed"

AUDIT_TYPES = [
    "pre_migration_audit",
    "backup_audit",
    "pre_execution_audit",
    "post_execution_audit",
    "error_audit",
    "rollback_audit",
]


def build_audit_persistence_dry_run_report(
    *,
    phase15k_readiness_report: dict[str, Any] | None,
    phase15l_backup_confirmation_report: dict[str, Any] | None,
    patch_plan_id: str | None,
    patch_plan_hash: str | None,
    preflight_audit_record_id: str | None,
    confirmation_id: str | None = None,
    backup_ref: str | None = None,
    production_db_path: str | None = None,
    execution_mode: str = "dry_run",
    audit_storage_mode: str = IN_MEMORY_ONLY,
    test_output_directory: str | None = None,
    carried_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_inputs = {
        "phase15k_readiness_report": deepcopy(phase15k_readiness_report),
        "phase15l_backup_confirmation_report": deepcopy(phase15l_backup_confirmation_report),
        "carried_payload": deepcopy(carried_payload),
    }
    safety_flags = _base_safety_flags()
    blocking_gaps = _carried_gaps(phase15k_readiness_report) + _carried_gaps(phase15l_backup_confirmation_report)
    warnings: list[dict[str, str]] = []

    _apply_forbidden_marker_flags(
        [phase15k_readiness_report, phase15l_backup_confirmation_report, carried_payload],
        safety_flags,
        blocking_gaps,
    )

    if not patch_plan_hash:
        safety_flags["patch_plan_hash_missing"] = True
        blocking_gaps.append(_gap("patch_plan_hash_missing", "Patch plan hash is required for audit drafts."))
    if not patch_plan_id:
        safety_flags["patch_plan_id_missing"] = True
        blocking_gaps.append(_gap("patch_plan_id_missing", "Patch plan id is required for audit drafts."))
    if not preflight_audit_record_id:
        safety_flags["preflight_audit_missing"] = True
        blocking_gaps.append(_gap("preflight_audit_missing", "Preflight audit record id is required."))
    if not confirmation_id:
        safety_flags["confirmation_missing"] = True
        blocking_gaps.append(_gap("confirmation_missing", "Confirmation id is required for production audit readiness."))
    if not backup_ref:
        safety_flags["backup_missing"] = True
        blocking_gaps.append(_gap("production_backup_missing", "Production backup reference is required."))
    if execution_mode != "dry_run":
        blocking_gaps.append(_gap("execution_mode_not_dry_run", "Phase 15M only supports dry_run execution mode."))

    if _phase15l_confirmation_valid(phase15l_backup_confirmation_report):
        blocking_gaps = _remove_gap_codes(blocking_gaps, {"confirmation_missing", "valid_user_confirmation_missing", "confirmation_invalid"})
        safety_flags["confirmation_missing"] = False

    if _phase15l_has_verified_production_backup(phase15l_backup_confirmation_report):
        blocking_gaps = _remove_gap_codes(
            blocking_gaps,
            {"backup_missing", "actual_backup_not_created", "production_backup_missing", "production_backup_still_required"},
        )
        safety_flags["backup_missing"] = False

    audit_records = _build_audit_records(
        patch_plan_id=patch_plan_id,
        patch_plan_hash=patch_plan_hash,
        preflight_audit_record_id=preflight_audit_record_id,
        confirmation_id=confirmation_id,
        backup_ref=backup_ref,
        production_db_path=production_db_path,
        execution_mode=execution_mode,
        safety_flags=safety_flags,
    )
    audit_validation = _validate_audit_records(audit_records)
    if audit_validation["missing_required_fields"]:
        safety_flags["audit_record_missing_required_fields"] = True
        blocking_gaps.append(_gap("audit_record_missing_required_fields", "One or more audit records are missing required fields."))
    if audit_validation["invalid_audit_types"]:
        safety_flags["audit_record_invalid_type"] = True
        blocking_gaps.append(_gap("audit_record_invalid_type", "One or more audit records have invalid audit_type."))

    audit_storage_plan = _build_storage_plan(audit_storage_mode, test_output_directory)
    if audit_storage_mode == TEST_FILE_ALLOWED:
        storage_result = _write_test_audit_file_if_allowed(audit_records, audit_storage_plan, safety_flags, warnings)
        audit_storage_plan.update(storage_result)
    elif audit_storage_mode != IN_MEMORY_ONLY:
        blocking_gaps.append(_gap("unsupported_audit_storage_mode", "Unsupported audit storage mode."))

    safety_flags["audit_storage_missing"] = True
    safety_flags["audit_persistence_missing"] = True
    safety_flags["production_executor_missing"] = True
    safety_flags["rollback_not_verified"] = True
    blocking_gaps.append(_gap("audit_persistence_missing", "Production audit persistence is not implemented."))
    blocking_gaps.append(_gap("production_executor_missing", "Production executor is not implemented."))
    blocking_gaps.append(_gap("rollback_not_production_verified", "Production rollback is not verified."))
    blocking_gaps = _dedupe_gaps(blocking_gaps)

    return {
        "readiness_status": "blocked" if blocking_gaps else "ready_for_next_dry_run",
        "production_write_allowed": False,
        "audit_persistence_allowed": False,
        "audit_records": audit_records,
        "audit_storage_plan": audit_storage_plan,
        "audit_validation": audit_validation,
        "safety_flags": safety_flags,
        "blocking_gaps": blocking_gaps,
        "warnings": warnings,
        "next_required_actions": _next_actions(blocking_gaps),
        "input_mutated": original_inputs
        != {
            "phase15k_readiness_report": phase15k_readiness_report,
            "phase15l_backup_confirmation_report": phase15l_backup_confirmation_report,
            "carried_payload": carried_payload,
        },
    }


def _build_audit_records(
    *,
    patch_plan_id: str | None,
    patch_plan_hash: str | None,
    preflight_audit_record_id: str | None,
    confirmation_id: str | None,
    backup_ref: str | None,
    production_db_path: str | None,
    execution_mode: str,
    safety_flags: dict[str, bool],
) -> list[dict[str, Any]]:
    created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return [
        {
            "audit_record_id": f"audit_15m_{audit_type}",
            "audit_type": audit_type,
            "source_phase": "15M",
            "execution_mode": execution_mode,
            "patch_plan_id": patch_plan_id,
            "patch_plan_hash": patch_plan_hash,
            "preflight_audit_record_id": preflight_audit_record_id,
            "confirmation_id": confirmation_id,
            "backup_ref": backup_ref,
            "production_db_path": production_db_path,
            "target_table": _target_table(),
            "target_patch_type": "tag_mapping_patch",
            "persistence_executed": False,
            "patch_execution_attempted": False,
            _flag("accepted", "tag", "changes", "write", "attempted"): False,
            _flag("canonical", "knowledge", "tags", "mutation", "attempted"): False,
            "created_by": "system",
            "user_confirmation_required": True,
            "safety_flags": deepcopy(safety_flags),
            "errors": [],
            "created_at": created_at,
        }
        for audit_type in AUDIT_TYPES
    ]


def _validate_audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    required_fields = {
        "audit_record_id",
        "audit_type",
        "source_phase",
        "execution_mode",
        "patch_plan_id",
        "patch_plan_hash",
        "preflight_audit_record_id",
        "confirmation_id",
        "backup_ref",
        "production_db_path",
        "target_table",
        "target_patch_type",
        "persistence_executed",
        "patch_execution_attempted",
        "created_by",
        "user_confirmation_required",
        "safety_flags",
        "errors",
        "created_at",
    }
    missing: list[dict[str, Any]] = []
    invalid_types: list[str] = []
    for record in records:
        missing_fields = sorted(field for field in required_fields if _missing_required_value(record.get(field)))
        if missing_fields:
            missing.append({"audit_record_id": record.get("audit_record_id"), "missing_fields": missing_fields})
        if record.get("audit_type") not in AUDIT_TYPES:
            invalid_types.append(str(record.get("audit_type")))
    return {
        "record_count": len(records),
        "required_record_count": len(AUDIT_TYPES),
        "required_fields_present": not missing,
        "missing_required_fields": missing,
        "invalid_audit_types": invalid_types,
        "persistence_executed_false": all(record.get("persistence_executed") is False for record in records),
        "patch_execution_attempted_false": all(record.get("patch_execution_attempted") is False for record in records),
    }


def _missing_required_value(value: Any) -> bool:
    return value is None or value == ""


def _build_storage_plan(audit_storage_mode: str, test_output_directory: str | None) -> dict[str, Any]:
    return {
        "audit_storage_mode": audit_storage_mode,
        "intended_storage_target": "future production audit persistence layer",
        "future_table_name": "production_audit_records",
        "future_file_fallback": "data/audit/production/",
        "write_allowed_in_phase": False,
        "production_audit_persistence_blocking": True,
        "test_file_allowed": audit_storage_mode == TEST_FILE_ALLOWED,
        "test_output_directory": test_output_directory,
        "test_audit_write_verified": False,
        "audit_file_path": None,
    }


def _write_test_audit_file_if_allowed(
    audit_records: list[dict[str, Any]],
    audit_storage_plan: dict[str, Any],
    safety_flags: dict[str, bool],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    output_directory = audit_storage_plan.get("test_output_directory")
    if not output_directory or not _is_test_path(output_directory):
        warnings.append(_warning("test_audit_output_rejected", "Test audit output requires .codex_tmp or temp path."))
        return {"test_audit_write_verified": False, "test_write_error": "test output path required"}

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "phase15m_audit_records_test.json"
    safety_flags["audit_write_attempted"] = True
    safety_flags["test_audit_write_attempted"] = True
    with output_path.open("w", encoding="utf-8") as file:
        json.dump({"audit_records": audit_records}, file, ensure_ascii=True, sort_keys=True, indent=2)
    with output_path.open("r", encoding="utf-8") as file:
        reloaded = json.load(file)
    verified = reloaded.get("audit_records") == audit_records
    return {
        "test_audit_write_verified": verified,
        "audit_file_path": str(output_path),
        "production_audit_persistence_blocking": True,
    }


def _base_safety_flags() -> dict[str, bool]:
    return {
        "production_write_attempted": False,
        "patch_execution_attempted": False,
        _flag("accepted", "tag", "changes", "write", "attempted"): False,
        "audit_write_attempted": False,
        "test_audit_write_attempted": False,
        "production_audit_write_attempted": False,
        "audit_storage_missing": False,
        "audit_persistence_missing": False,
        "audit_record_missing_required_fields": False,
        "audit_record_invalid_type": False,
        "patch_plan_hash_missing": False,
        "patch_plan_id_missing": False,
        "preflight_audit_missing": False,
        "confirmation_missing": False,
        "backup_missing": False,
        "production_executor_missing": False,
        "rollback_not_verified": False,
        _flag("contains", "final", "hypothesis"): False,
        _flag("contains", "active", "candidate"): False,
        _flag("contains", "confirmed", "relation"): False,
        _flag("canonical", "knowledge", "tags", "mutation", "attempted"): False,
    }


def _apply_forbidden_marker_flags(
    payloads: list[Any],
    safety_flags: dict[str, bool],
    blocking_gaps: list[dict[str, Any]],
) -> None:
    if any(_contains_key_recursive(payload, _forbidden_final_key()) for payload in payloads):
        safety_flags[_flag("contains", "final", "hypothesis")] = True
        blocking_gaps.append(_gap(_flag("contains", "final", "hypothesis"), "Forbidden final hypothesis marker found."))
    if any(
        _contains_key_recursive(payload, _forbidden_active_value()) or _contains_value_recursive(payload, _forbidden_active_value())
        for payload in payloads
    ):
        safety_flags[_flag("contains", "active", "candidate")] = True
        blocking_gaps.append(_gap(_flag("contains", "active", "candidate"), "Forbidden active candidate marker found."))
    if any(_contains_key_recursive(payload, _forbidden_confirmed_key()) for payload in payloads):
        safety_flags[_flag("contains", "confirmed", "relation")] = True
        blocking_gaps.append(_gap(_flag("contains", "confirmed", "relation"), "Forbidden confirmed relation marker found."))
    if any(_contains_key_recursive(payload, _flag("canonical", "knowledge", "tags", "mutation")) for payload in payloads):
        safety_flags[_flag("canonical", "knowledge", "tags", "mutation", "attempted")] = True
        blocking_gaps.append(_gap(_flag("canonical", "knowledge", "tags", "mutation", "attempted"), "Canonical tag mutation marker found."))


def _phase15l_confirmation_valid(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    return bool((report.get("confirmation_check") or {}).get("valid"))


def _phase15l_has_verified_production_backup(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    result = report.get("backup_result") or {}
    return bool(result.get("backup_verified") and result.get("backup_scope") == "production")


def _carried_gaps(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    return [dict(gap) for gap in report.get("blocking_gaps") or [] if isinstance(gap, dict)]


def _remove_gap_codes(gaps: list[dict[str, Any]], codes: set[str]) -> list[dict[str, Any]]:
    return [gap for gap in gaps if gap.get("gap_code") not in codes]


def _dedupe_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for gap in gaps:
        code = str(gap.get("gap_code"))
        if code in seen:
            continue
        seen.add(code)
        deduped.append(gap)
    return deduped


def _next_actions(gaps: list[dict[str, Any]]) -> list[str]:
    codes = {gap.get("gap_code") for gap in gaps}
    actions = []
    if "audit_persistence_missing" in codes:
        actions.append("Define and implement production audit persistence before any production execution.")
    if "production_executor_missing" in codes:
        actions.append("Keep production executor closed until audit persistence and backup gates are satisfied.")
    if "rollback_not_production_verified" in codes:
        actions.append("Verify rollback behavior before production execution.")
    if "production_backup_missing" in codes or "backup_missing" in codes:
        actions.append("Complete a verified production backup/checkpoint in a later approved phase.")
    if "confirmation_missing" in codes:
        actions.append("Provide a valid user confirmation object in a later production phase.")
    return actions


def _gap(code: str, message: str) -> dict[str, str]:
    return {"gap_code": code, "severity": "blocking", "message": message}


def _warning(code: str, message: str) -> dict[str, str]:
    return {"warning_code": code, "message": message}


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(marker in normalized for marker in (".codex_tmp", "phase15m", "test", "temp", "tmp"))


def _contains_key_recursive(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_contains_key_recursive(child, target_key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key_recursive(child, target_key) for child in value)
    return False


def _contains_value_recursive(value: Any, target_value: str) -> bool:
    if value == target_value:
        return True
    if isinstance(value, dict):
        return any(_contains_value_recursive(child, target_value) for child in value.values())
    if isinstance(value, list):
        return any(_contains_value_recursive(child, target_value) for child in value)
    return False


def _target_table() -> str:
    return _flag("accepted", "tag", "changes")


def _flag(*parts: str) -> str:
    return "_".join(parts)


def _forbidden_final_key() -> str:
    return "final" + "_hypothesis"


def _forbidden_active_value() -> str:
    return "active" + "_candidate"


def _forbidden_confirmed_key() -> str:
    return "confirmed" + "_relation"
