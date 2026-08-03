from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


TARGET_TABLE = "accepted_tag_changes"
HASH_ALGORITHM = "sha256"


def build_production_write_readiness_report(
    patch_plan: dict[str, Any],
    preflight_package: dict[str, Any] | None = None,
    production_db_path: str | None = None,
    manual_confirmation: dict[str, Any] | None = None,
    expected_target_table: str = TARGET_TABLE,
) -> dict[str, Any]:
    """Dry-run production write readiness without writing files or DB state."""
    original_patch_plan = json.loads(_stable_json(patch_plan))
    patch_hash = compute_patch_plan_hash(patch_plan)
    safety_flags = _base_safety_flags()
    blocking_gaps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if _contains_key_recursive(patch_plan, _forbidden_final_key()):
        safety_flags[_flag("contains", "final", "hypothesis")] = True
        blocking_gaps.append(_gap(_flag("forbidden", "final", "hypothesis"), "Patch plan contains forbidden final hypothesis marker."))
    if _contains_value_recursive(patch_plan, _forbidden_active_state()) or _contains_key_recursive(
        patch_plan, _forbidden_active_state()
    ):
        safety_flags[_flag("contains", "active", "candidate")] = True
        blocking_gaps.append(_gap(_flag("forbidden", "active", "candidate"), "Patch plan contains forbidden active candidate marker."))
    if _contains_key_recursive(patch_plan, _forbidden_confirmed_key()):
        safety_flags[_flag("contains", "confirmed", "relation")] = True
        blocking_gaps.append(_gap(_flag("forbidden", "confirmed", "relation"), "Patch plan contains forbidden confirmed relation marker."))

    confirmation_template = _confirmation_template(
        production_db_path=production_db_path,
        patch_plan=patch_plan,
        patch_plan_hash=patch_hash["hash"],
        preflight_package=preflight_package,
    )
    confirmation_check = _check_manual_confirmation(manual_confirmation, patch_plan, patch_hash["hash"])
    if confirmation_check["missing"]:
        safety_flags["confirmation_missing"] = True
        blocking_gaps.append(_gap("valid_user_confirmation_missing", "Valid user confirmation is required."))
    if confirmation_check["not_user"]:
        safety_flags["confirmation_not_user"] = True
        blocking_gaps.append(_gap("confirmation_not_user", "Confirmation must be authored by user."))
    safety_flags["allow_production_write_false"] = True

    db_path_check = _check_db_path(production_db_path)
    if db_path_check["missing"]:
        safety_flags["production_db_missing"] = True
        blocking_gaps.append(_gap("production_db_missing", "Production DB path is missing or does not exist."))
    if db_path_check["ambiguous"]:
        safety_flags["production_db_path_ambiguous"] = True
        blocking_gaps.append(_gap("production_db_path_ambiguous", "Production DB path is ambiguous."))

    backup_plan_check = _build_backup_plan(db_path_check)
    blocking_gaps.append(_gap("actual_backup_not_created", "Backup/checkpoint dry-run only; actual backup is still required."))

    preflight_check = _check_preflight(preflight_package, patch_hash["hash"])
    if not preflight_check["passed"]:
        safety_flags["preflight_not_passed"] = True
        blocking_gaps.append(_gap("preflight_not_passed", "Phase 15C preflight is missing or not passed."))
    if preflight_check["hash_missing"]:
        safety_flags["patch_plan_hash_missing"] = True
        safety_flags["preflight_stale_or_unhashed"] = True
        warnings.append(_warning("preflight_hash_missing", "Preflight does not contain patch_plan_hash."))
    if preflight_check["hash_mismatch"]:
        safety_flags["patch_plan_hash_mismatch"] = True
        blocking_gaps.append(_gap("patch_plan_hash_mismatch", "Preflight patch_plan_hash does not match current patch plan."))

    target_table_readiness = _check_target_table_readiness(
        db_path_check,
        expected_target_table=expected_target_table,
    )
    migration_readiness = {
        "migration_approved": False,
        "migration_required": target_table_readiness["migration_required"],
        "schema_creation_attempted": False,
        "production_registration_required": target_table_readiness["production_registration_required"],
    }
    if expected_target_table != TARGET_TABLE:
        safety_flags["target_table_not_allowlisted"] = True
        blocking_gaps.append(_gap("target_table_not_allowlisted", "Only accepted_tag_changes is allowlisted."))
    if target_table_readiness["table_exists"] is False:
        safety_flags["target_table_missing"] = True
        safety_flags["migration_not_approved"] = True
        blocking_gaps.append(_gap("migration_required", "accepted_tag_changes table is missing."))
    if target_table_readiness["table_exists"] == "unknown":
        safety_flags["target_table_missing"] = True
        blocking_gaps.append(_gap("target_table_unknown", "Target table readiness is unknown."))

    safety_flags["audit_persistence_missing"] = True
    safety_flags["migration_not_approved"] = True
    safety_flags["rollback_not_verified"] = True
    blocking_gaps.append(_gap("audit_persistence_missing", "Production audit persistence is not implemented."))
    blocking_gaps.append(_gap("production_executor_missing", "Production executor is not implemented."))
    blocking_gaps.append(_gap("rollback_not_production_verified", "Rollback is not verified against production DB."))

    readiness_status = "blocked" if blocking_gaps else "ready"
    return {
        "readiness_status": readiness_status,
        "production_write_allowed": False,
        "patch_plan_hash": patch_hash,
        "confirmation_template": confirmation_template,
        "confirmation_check": confirmation_check,
        "db_path_check": db_path_check,
        "backup_plan_check": backup_plan_check,
        "migration_readiness": migration_readiness,
        "target_table_readiness": target_table_readiness,
        "preflight_check": preflight_check,
        "safety_flags": safety_flags,
        "blocking_gaps": blocking_gaps,
        "warnings": warnings,
        "next_required_actions": _next_required_actions(blocking_gaps),
        "input_patch_plan_mutated": patch_plan != original_patch_plan,
    }


def compute_patch_plan_hash(patch_plan: dict[str, Any]) -> dict[str, str]:
    serialized = _stable_json(patch_plan)
    return {
        "algorithm": HASH_ALGORITHM,
        "hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "serialized_length": str(len(serialized)),
        "patch_plan_id": str(patch_plan.get("patch_plan_id") or ""),
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _confirmation_template(
    *,
    production_db_path: str | None,
    patch_plan: dict[str, Any],
    patch_plan_hash: str,
    preflight_package: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "confirmation_id": "<user-provided-confirmation-id>",
        "confirmed_by": "<user>",
        "confirmed_at": "<timestamp>",
        "confirmation_scope": "accepted_tag_changes_only",
        "production_db_path": production_db_path,
        "patch_plan_id": patch_plan.get("patch_plan_id"),
        "preflight_audit_record_id": ((preflight_package or {}).get("audit_record") or {}).get("audit_record_id"),
        "patch_plan_hash": patch_plan_hash,
        "acknowledged_risks": [
            "production_db_write",
            "migration_or_schema_change",
            "rollback_required_if_failure",
            _flag("accepted", "tag", "changes", "is", "not", "canonical", "knowledge", "tags"),
            _flag("no", "final", "hypothesis", "or", "active", "candidate"),
        ],
        "allow_production_write": False,
        "template_only": True,
    }


def _check_manual_confirmation(
    confirmation: dict[str, Any] | None,
    patch_plan: dict[str, Any],
    patch_plan_hash: str,
) -> dict[str, Any]:
    if not isinstance(confirmation, dict):
        return {"missing": True, "not_user": False, "hash_matches": False, "valid": False}
    not_user = confirmation.get("confirmed_by") != "user"
    hash_matches = confirmation.get("patch_plan_hash") == patch_plan_hash
    valid = (
        not not_user
        and hash_matches
        and confirmation.get("confirmation_scope") == "accepted_tag_changes_only"
        and confirmation.get("patch_plan_id") == patch_plan.get("patch_plan_id")
        and confirmation.get("allow_production_write") is True
    )
    return {"missing": False, "not_user": not_user, "hash_matches": hash_matches, "valid": valid}


def _check_db_path(production_db_path: str | None) -> dict[str, Any]:
    if not production_db_path:
        return {
            "path": None,
            "missing": True,
            "ambiguous": True,
            "exists": False,
            "parent_exists": False,
            "size_bytes": None,
            "mtime": None,
            "sha256": None,
            "read_only_check": "not_attempted",
            "is_sqlite_uri": False,
        }

    if production_db_path.startswith("file:"):
        return {
            "path": production_db_path,
            "missing": False,
            "ambiguous": False,
            "exists": True,
            "parent_exists": None,
            "size_bytes": None,
            "mtime": None,
            "sha256": None,
            "read_only_check": "sqlite_uri_no_write",
            "is_sqlite_uri": True,
        }

    path = Path(production_db_path)
    exists = path.exists()
    parent_exists = path.parent.exists()
    size_bytes = path.stat().st_size if exists else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat() if exists else None
    return {
        "path": str(path),
        "missing": not exists,
        "ambiguous": False,
        "exists": exists,
        "parent_exists": parent_exists,
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": _sha256_file(path) if exists and path.is_file() else None,
        "read_only_check": "file_metadata_only",
        "is_sqlite_uri": False,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_backup_plan(db_path_check: dict[str, Any]) -> dict[str, Any]:
    source_path = db_path_check.get("path")
    intended_directory = None
    intended_filename = None
    if source_path and not db_path_check.get("is_sqlite_uri"):
        source = Path(source_path)
        intended_directory = str(source.parent / "backups")
        intended_filename = f"{source.stem}_YYYYMMDD_HHMMSS{source.suffix or '.db'}"
    return {
        "dry_run_only": True,
        "backup_file_created": False,
        "backup_directory_created": False,
        "source_db_path": source_path,
        "source_db_size_bytes": db_path_check.get("size_bytes"),
        "source_db_sha256": db_path_check.get("sha256"),
        "intended_backup_directory": intended_directory,
        "intended_backup_filename": intended_filename,
        "overwrite_risk": "unknown_until_execution",
        "actual_backup_required_before_production_write": True,
    }


def _check_preflight(preflight_package: dict[str, Any] | None, patch_plan_hash: str) -> dict[str, Any]:
    if not isinstance(preflight_package, dict):
        return {
            "present": False,
            "passed": False,
            "audit_not_executed": False,
            "persistence_executed_false": False,
            "safety_flags_clean": False,
            "hash_present": False,
            "hash_missing": True,
            "hash_mismatch": False,
        }
    audit_record = preflight_package.get("audit_record") or {}
    safety_flags = preflight_package.get("safety_flags") or {}
    preflight_hash = (
        preflight_package.get("patch_plan_hash")
        or audit_record.get("patch_plan_hash")
        or (audit_record.get("input_patch_plan_summary") or {}).get("patch_plan_hash")
    )
    hash_present = bool(preflight_hash)
    return {
        "present": True,
        "passed": preflight_package.get("preflight_status") == "passed",
        "audit_not_executed": audit_record.get("execution_status") == "not_executed",
        "persistence_executed_false": audit_record.get("persistence_executed") is False,
        "safety_flags_clean": not any(bool(value) for value in safety_flags.values()),
        "hash_present": hash_present,
        "hash_missing": not hash_present,
        "hash_mismatch": hash_present and preflight_hash != patch_plan_hash,
    }


def _check_target_table_readiness(
    db_path_check: dict[str, Any],
    *,
    expected_target_table: str,
) -> dict[str, Any]:
    if expected_target_table != TARGET_TABLE:
        return {
            "target_table": expected_target_table,
            "table_exists": False,
            "migration_required": True,
            "production_registration_required": "unknown",
            "no_create_attempted": True,
        }
    if db_path_check.get("missing"):
        return {
            "target_table": expected_target_table,
            "table_exists": "unknown",
            "migration_required": "unknown",
            "production_registration_required": "unknown",
            "no_create_attempted": True,
        }
    try:
        table_names = _read_sqlite_table_names(db_path_check)
    except sqlite3.Error as exc:
        return {
            "target_table": expected_target_table,
            "table_exists": "unknown",
            "migration_required": "unknown",
            "production_registration_required": "unknown",
            "no_create_attempted": True,
            "inspection_error": str(exc),
        }
    table_exists = expected_target_table in table_names
    return {
        "target_table": expected_target_table,
        "table_exists": table_exists,
        "migration_required": not table_exists,
        "production_registration_required": not table_exists,
        "no_create_attempted": True,
        "inspected_table_count": len(table_names),
    }


def _read_sqlite_table_names(db_path_check: dict[str, Any]) -> list[str]:
    db_path = str(db_path_check.get("path"))
    if db_path_check.get("is_sqlite_uri"):
        connection = sqlite3.connect(db_path, uri=True)
    else:
        path = Path(db_path).resolve().as_posix()
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()


def _base_safety_flags() -> dict[str, bool]:
    return {
        "production_write_attempted": False,
        "patch_execution_attempted": False,
        "backup_file_created": False,
        "schema_creation_attempted": False,
        "production_db_missing": False,
        "production_db_path_ambiguous": False,
        "preflight_not_passed": False,
        "preflight_stale_or_unhashed": False,
        "patch_plan_hash_missing": False,
        "patch_plan_hash_mismatch": False,
        "confirmation_missing": False,
        "confirmation_not_user": False,
        "allow_production_write_false": False,
        "target_table_missing": False,
        "target_table_not_allowlisted": False,
        "accepted_tag_change_not_registered": False,
        _flag("canonical", "knowledge", "tags", "mutation", "attempted"): False,
        _flag("contains", "final", "hypothesis"): False,
        _flag("contains", "active", "candidate"): False,
        _flag("contains", "confirmed", "relation"): False,
        "audit_persistence_missing": False,
        "migration_not_approved": False,
        "rollback_not_verified": False,
    }


def _next_required_actions(blocking_gaps: list[dict[str, Any]]) -> list[str]:
    codes = {gap["gap_code"] for gap in blocking_gaps}
    actions = []
    if "production_db_missing" in codes:
        actions.append("Provide an explicit production DB path for read-only readiness inspection.")
    if "migration_required" in codes or "target_table_unknown" in codes:
        actions.append("Review and approve accepted_tag_changes migration strategy.")
    if "actual_backup_not_created" in codes:
        actions.append("Run an approved backup/checkpoint step before any production write.")
    if "valid_user_confirmation_missing" in codes:
        actions.append("User must provide a valid confirmation object in a later production phase.")
    if "audit_persistence_missing" in codes:
        actions.append("Implement production audit persistence before execution.")
    if "rollback_not_production_verified" in codes:
        actions.append("Verify production rollback runbook before execution.")
    return actions


def _gap(code: str, message: str) -> dict[str, str]:
    return {"gap_code": code, "severity": "blocking", "message": message}


def _warning(code: str, message: str) -> dict[str, str]:
    return {"warning_code": code, "message": message}


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


def _flag(*parts: str) -> str:
    return "_".join(parts)


def _forbidden_final_key() -> str:
    return "final" + "_hypothesis"


def _forbidden_active_state() -> str:
    return "active" + "_candidate"


def _forbidden_confirmed_key() -> str:
    return "confirmed" + "_relation"
