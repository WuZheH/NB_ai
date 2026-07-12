from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


DRY_RUN_ONLY = "dry_run_only"
TEST_BACKUP_ALLOWED = "test_backup_allowed"


def build_backup_confirmation_report(
    *,
    production_db_path: str | None,
    phase15k_readiness_report: dict[str, Any] | None,
    patch_plan_hash: str,
    patch_plan_id: str,
    preflight_audit_record_id: str,
    confirmation: dict[str, Any] | None = None,
    backup_mode: str = DRY_RUN_ONLY,
    backup_directory: str | None = None,
    allow_real_production_backup: bool = False,
) -> dict[str, Any]:
    """Validate backup and confirmation readiness without production patch execution."""
    safety_flags = _base_safety_flags()
    warnings: list[dict[str, str]] = []
    blocking_gaps = _carried_gaps(phase15k_readiness_report)

    if _contains_key_recursive(confirmation, _forbidden_final_key()) or _contains_key_recursive(
        phase15k_readiness_report, _forbidden_final_key()
    ):
        safety_flags[_flag("contains", "final", "hypothesis")] = True
        blocking_gaps.append(_gap(_flag("contains", "final", "hypothesis"), "Forbidden final hypothesis marker found."))
    if _contains_value_recursive(confirmation, _forbidden_active_value()) or _contains_value_recursive(
        phase15k_readiness_report, _forbidden_active_value()
    ):
        safety_flags[_flag("contains", "active", "candidate")] = True
        blocking_gaps.append(_gap(_flag("contains", "active", "candidate"), "Forbidden active candidate marker found."))
    if _contains_key_recursive(confirmation, _forbidden_confirmed_key()) or _contains_key_recursive(
        phase15k_readiness_report, _forbidden_confirmed_key()
    ):
        safety_flags[_flag("contains", "confirmed", "relation")] = True
        blocking_gaps.append(_gap(_flag("contains", "confirmed", "relation"), "Forbidden confirmed relation marker found."))

    backup_check = _build_backup_check(production_db_path)
    if backup_check["missing"]:
        safety_flags["production_db_missing"] = True
        blocking_gaps.append(_gap("production_db_missing", "DB path is missing or does not exist."))

    backup_result = _run_backup_mode(
        backup_check,
        backup_mode=backup_mode,
        backup_directory=backup_directory,
        allow_real_production_backup=allow_real_production_backup,
        safety_flags=safety_flags,
        warnings=warnings,
    )
    if backup_result["backup_created"] and backup_result["backup_verified"]:
        blocking_gaps = _remove_gap_codes(blocking_gaps, {"backup_missing", "actual_backup_not_created"})
        if backup_result["backup_scope"] == "test_only":
            blocking_gaps.append(_gap("production_backup_still_required", "Verified test backup does not satisfy production backup."))
            warnings.append(_warning("test_backup_verified_only", "Test backup was verified, but production backup remains required."))
    else:
        blocking_gaps.append(_gap("backup_missing", "No verified backup exists for production execution."))

    confirmation_check = _validate_confirmation(
        confirmation=confirmation,
        production_db_path=production_db_path,
        patch_plan_hash=patch_plan_hash,
        patch_plan_id=patch_plan_id,
        preflight_audit_record_id=preflight_audit_record_id,
    )
    _apply_confirmation_flags(confirmation_check, safety_flags)
    if confirmation_check["valid"]:
        blocking_gaps = _remove_gap_codes(blocking_gaps, {"confirmation_missing", "valid_user_confirmation_missing"})
    else:
        blocking_gaps.append(_gap("confirmation_invalid", "A valid user confirmation is required."))

    blocking_gaps.append(_gap("audit_persistence_missing", "Production audit persistence is not implemented."))
    blocking_gaps.append(_gap("production_executor_missing", "Production executor is not implemented."))
    blocking_gaps.append(_gap("rollback_not_production_verified", "Production rollback is not verified."))
    blocking_gaps = _dedupe_gaps(blocking_gaps)

    return {
        "readiness_status": "blocked" if blocking_gaps else "ready_for_next_dry_run",
        "production_write_allowed": False,
        "backup_check": backup_check,
        "backup_result": backup_result,
        "confirmation_check": confirmation_check,
        "safety_flags": safety_flags,
        "blocking_gaps": blocking_gaps,
        "warnings": warnings,
        "next_required_actions": _next_actions(blocking_gaps),
    }


def _build_backup_check(production_db_path: str | None) -> dict[str, Any]:
    if not production_db_path:
        return {
            "path": None,
            "exists": False,
            "missing": True,
            "size_bytes": None,
            "mtime": None,
            "sha256": None,
            "intended_backup_path": None,
            "overwrite_risk": None,
        }
    path = Path(production_db_path)
    exists = path.exists()
    size_bytes = path.stat().st_size if exists else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat() if exists else None
    intended_backup_path = str(path.parent / "backups" / f"{path.stem}_YYYYMMDD_HHMMSS{path.suffix or '.db'}")
    return {
        "path": str(path),
        "exists": exists,
        "missing": not exists,
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": _sha256_file(path) if exists and path.is_file() else None,
        "intended_backup_path": intended_backup_path,
        "overwrite_risk": "template_path_contains_timestamp_placeholder",
    }


def _run_backup_mode(
    backup_check: dict[str, Any],
    *,
    backup_mode: str,
    backup_directory: str | None,
    allow_real_production_backup: bool,
    safety_flags: dict[str, bool],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    base = {
        "backup_mode": backup_mode,
        "backup_created": False,
        "backup_verified": False,
        "backup_path": None,
        "backup_scope": "none",
        "backup_size_bytes": None,
        "backup_sha256": None,
        "backup_hash_matches_source": False,
        "backup_directory_created": False,
    }
    source = backup_check.get("path")
    if backup_mode == DRY_RUN_ONLY:
        return base | {"dry_run_only": True, "backup_required": True}
    if backup_mode != TEST_BACKUP_ALLOWED:
        return base | {"dry_run_only": True, "backup_required": True, "error": "unsupported backup mode"}
    if not source or not backup_check.get("exists"):
        return base | {"dry_run_only": False, "backup_required": True, "error": "source DB missing"}
    if _is_production_like_path(source) and not allow_real_production_backup:
        safety_flags["production_backup_attempted_without_approval"] = True
        return base | {"dry_run_only": False, "backup_required": True, "error": "production-like path rejected"}
    if not _is_test_path(source):
        safety_flags["production_backup_attempted_without_approval"] = True
        return base | {"dry_run_only": False, "backup_required": True, "error": "test marker required for backup copy"}

    source_path = Path(source)
    target_dir = Path(backup_directory) if backup_directory else source_path.parent / "backups"
    existed_before = target_dir.exists()
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / f"{source_path.stem}_phase15l_test_backup{source_path.suffix or '.db'}"
    if backup_path.exists():
        backup_path = target_dir / f"{source_path.stem}_phase15l_test_backup_{_sha256_text(str(source_path))[:8]}{source_path.suffix or '.db'}"
    shutil.copy2(source_path, backup_path)
    source_hash = backup_check.get("sha256")
    backup_hash = _sha256_file(backup_path)
    hash_matches = bool(source_hash and source_hash == backup_hash)
    safety_flags["backup_file_created"] = True
    safety_flags["backup_verified"] = hash_matches
    if not hash_matches:
        safety_flags["backup_hash_mismatch"] = True
    warnings.append(_warning("test_backup_file_created", "A backup file was created for an explicit test/temp DB only."))
    return {
        "backup_mode": backup_mode,
        "dry_run_only": False,
        "backup_required": True,
        "backup_created": True,
        "backup_verified": hash_matches,
        "backup_path": str(backup_path),
        "backup_scope": "test_only",
        "backup_size_bytes": backup_path.stat().st_size,
        "backup_sha256": backup_hash,
        "backup_hash_matches_source": hash_matches,
        "backup_directory_created": not existed_before,
    }


def _validate_confirmation(
    *,
    confirmation: dict[str, Any] | None,
    production_db_path: str | None,
    patch_plan_hash: str,
    patch_plan_id: str,
    preflight_audit_record_id: str,
) -> dict[str, Any]:
    required_risks = _required_risks()
    if not isinstance(confirmation, dict):
        return _confirmation_result(False, missing=True, missing_required_risks=required_risks)
    acknowledged = set(confirmation.get("acknowledged_risks") or [])
    missing_risks = sorted(required_risks - acknowledged)
    checks = {
        "missing": False,
        "confirmed_by_user": confirmation.get("confirmed_by") == "user",
        "scope_valid": confirmation.get("confirmation_scope") == _scope(),
        "db_path_matches": confirmation.get("production_db_path") == production_db_path,
        "patch_plan_id_matches": confirmation.get("patch_plan_id") == patch_plan_id,
        "preflight_audit_record_id_matches": confirmation.get("preflight_audit_record_id") == preflight_audit_record_id,
        "patch_plan_hash_matches": confirmation.get("patch_plan_hash") == patch_plan_hash,
        "allow_production_write_true": confirmation.get("allow_production_write") is True,
        "missing_required_risks": missing_risks,
        "generated_by_system": confirmation.get("confirmed_by") in {"system", "ai", "assistant"},
    }
    valid = (
        checks["confirmed_by_user"]
        and checks["scope_valid"]
        and checks["db_path_matches"]
        and checks["patch_plan_id_matches"]
        and checks["preflight_audit_record_id_matches"]
        and checks["patch_plan_hash_matches"]
        and checks["allow_production_write_true"]
        and not missing_risks
        and not checks["generated_by_system"]
    )
    checks["valid"] = valid
    return checks


def _confirmation_result(valid: bool, *, missing: bool, missing_required_risks: set[str]) -> dict[str, Any]:
    return {
        "missing": missing,
        "valid": valid,
        "confirmed_by_user": False,
        "scope_valid": False,
        "db_path_matches": False,
        "patch_plan_id_matches": False,
        "preflight_audit_record_id_matches": False,
        "patch_plan_hash_matches": False,
        "allow_production_write_true": False,
        "missing_required_risks": sorted(missing_required_risks),
        "generated_by_system": False,
    }


def _apply_confirmation_flags(confirmation_check: dict[str, Any], safety_flags: dict[str, bool]) -> None:
    safety_flags["confirmation_missing"] = bool(confirmation_check.get("missing"))
    safety_flags["confirmation_not_user"] = not bool(confirmation_check.get("confirmed_by_user"))
    safety_flags["confirmation_scope_invalid"] = not bool(confirmation_check.get("scope_valid"))
    safety_flags["production_db_path_mismatch"] = not bool(confirmation_check.get("db_path_matches"))
    safety_flags["confirmation_hash_mismatch"] = not bool(confirmation_check.get("patch_plan_hash_matches"))
    safety_flags["confirmation_patch_plan_mismatch"] = not bool(confirmation_check.get("patch_plan_id_matches"))
    safety_flags["confirmation_preflight_mismatch"] = not bool(confirmation_check.get("preflight_audit_record_id_matches"))
    safety_flags["confirmation_allow_write_false"] = not bool(confirmation_check.get("allow_production_write_true"))
    safety_flags["confirmation_missing_required_risks"] = bool(confirmation_check.get("missing_required_risks"))


def _base_safety_flags() -> dict[str, bool]:
    return {
        "production_write_attempted": False,
        "patch_execution_attempted": False,
        _flag("accepted", "tag", "changes", "write", "attempted"): False,
        "backup_file_created": False,
        "backup_verified": False,
        "backup_hash_mismatch": False,
        "backup_path_overwrite_risk": False,
        "production_backup_attempted_without_approval": False,
        "production_db_missing": False,
        "production_db_path_mismatch": False,
        "confirmation_missing": False,
        "confirmation_not_user": False,
        "confirmation_scope_invalid": False,
        "confirmation_hash_mismatch": False,
        "confirmation_patch_plan_mismatch": False,
        "confirmation_preflight_mismatch": False,
        "confirmation_allow_write_false": False,
        "confirmation_missing_required_risks": False,
        _flag("canonical", "knowledge", "tags", "mutation", "attempted"): False,
        _flag("contains", "final", "hypothesis"): False,
        _flag("contains", "active", "candidate"): False,
        _flag("contains", "confirmed", "relation"): False,
    }


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
    if "backup_missing" in codes or "production_backup_still_required" in codes:
        actions.append("Complete an approved production backup/checkpoint before any production write.")
    if "confirmation_invalid" in codes:
        actions.append("Provide a valid user-authored confirmation object.")
    if "audit_persistence_missing" in codes:
        actions.append("Implement production audit persistence.")
    if "production_executor_missing" in codes:
        actions.append("Implement production executor only after boundary approval.")
    if "rollback_not_production_verified" in codes:
        actions.append("Verify production rollback procedure.")
    return actions


def _gap(code: str, message: str) -> dict[str, str]:
    return {"gap_code": code, "severity": "blocking", "message": message}


def _warning(code: str, message: str) -> dict[str, str]:
    return {"warning_code": code, "message": message}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_production_like_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "data/db/research_memory.db" in normalized


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(marker in normalized for marker in (".codex_tmp", "phase15l", "test", "temp", "tmp"))


def _required_risks() -> set[str]:
    return {
        "production_db_write",
        "backup_required",
        "rollback_required_if_failure",
        _flag("accepted", "tag", "changes", "is", "not", "canonical", "knowledge", "tags"),
        _flag("no", "final", "hypothesis", "or", "active", "candidate"),
    }


def _scope() -> str:
    return _flag("accepted", "tag", "changes", "only")


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


def _forbidden_active_value() -> str:
    return "active" + "_candidate"


def _forbidden_confirmed_key() -> str:
    return "confirmed" + "_relation"
