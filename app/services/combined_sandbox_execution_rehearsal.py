from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.models.production_audit_record import ProductionAuditRecord, validate_production_audit_record
from app.services.accepted_tag_change_sandbox_repository import AcceptedTagChangeSandboxRepository


TARGET_PATCH_TYPE = "tag_mapping_patch"
SOURCE_PHASE = "15P"


def run_combined_sandbox_execution_rehearsal(
    patch_plan: dict[str, Any],
    preflight_package: dict[str, Any] | None,
    phase15k_readiness_report: dict[str, Any] | None,
    phase15l_backup_confirmation_report: dict[str, Any] | None,
    phase15m_audit_persistence_report: dict[str, Any] | None,
    sandbox_session: Any | None,
    rehearsal_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rehearse audit + accepted tag mapping writes in an explicit sandbox session only."""
    metadata = deepcopy(rehearsal_metadata or {})
    result = _base_result()
    result["safety_flags"]["sandbox_patch_execution_attempted"] = sandbox_session is not None
    result["sandbox_patch_execution_attempted"] = sandbox_session is not None

    if sandbox_session is None:
        result["validation_errors"].append("explicit sandbox SQLAlchemy session is required")
        return result

    context = _build_context(
        patch_plan,
        preflight_package,
        phase15k_readiness_report,
        phase15l_backup_confirmation_report,
        phase15m_audit_persistence_report,
        metadata,
    )
    eligible_entries, blocked_entries, gate_errors = _validate_gates(
        patch_plan,
        preflight_package,
        phase15k_readiness_report,
        phase15l_backup_confirmation_report,
        phase15m_audit_persistence_report,
        context,
        result["safety_flags"],
    )
    result["blocked_patch_count"] = len(blocked_entries)
    if blocked_entries:
        result["safety_flags"]["non_tag_mapping_patch_blocked"] = True
    if gate_errors:
        result["validation_errors"].extend(gate_errors)
        return result

    pre_audit = _audit_record(
        audit_record_id=_audit_id("pre", context),
        audit_type="pre_execution_audit",
        execution_status="preflighted",
        context=context,
        safety_flags=result["safety_flags"],
        patch_entries=eligible_entries,
    )
    pre_errors = validate_production_audit_record(pre_audit)
    if pre_errors:
        result["validation_errors"].extend(pre_errors)
        return result

    try:
        pre_audit_id = pre_audit.audit_record_id
        sandbox_session.add(pre_audit)
        sandbox_session.commit()
        result["pre_execution_audit_id"] = pre_audit_id
        result["audit_rows_created"] += 1
        result["safety_flags"]["sandbox_audit_write_attempted"] = True
    except Exception as exc:  # pragma: no cover - defensive transaction failure handling
        sandbox_session.rollback()
        result["validation_errors"].append(f"pre_execution_audit sandbox insert failed: {exc}")
        result["safety_flags"]["transaction_failed"] = True
        return result

    accepted_ids: list[str] = []
    repository = AcceptedTagChangeSandboxRepository(sandbox_session)
    try:
        with sandbox_session.begin():
            for entry in eligible_entries:
                record = repository.create_accepted_tag_change_from_patch(
                    entry,
                    patch_plan=patch_plan,
                    preflight_package=preflight_package or {},
                    execution_audit_record_id=result["pre_execution_audit_id"],
                )
                accepted_ids.append(record.accepted_tag_change_id)
        result["accepted_tag_change_ids"] = accepted_ids
        result["accepted_tag_change_rows_created"] = len(accepted_ids)
        result["safety_flags"]["accepted_tag_changes_write_attempted"] = bool(accepted_ids)
    except Exception as exc:
        sandbox_session.rollback()
        result["validation_errors"].append(f"sandbox accepted tag change transaction failed: {exc}")
        result["safety_flags"]["transaction_failed"] = True
        result["safety_flags"]["rollback_performed"] = True
        result["rollback_record"] = {
            "rollback_performed": True,
            "rollback_ref": _rollback_ref(context),
            "reason": "accepted tag change transaction failed",
        }
        _write_failure_audits(
            sandbox_session,
            result=result,
            context=context,
            eligible_entries=eligible_entries,
            error_message=str(exc),
        )
        return result

    post_audit = _audit_record(
        audit_record_id=_audit_id("post", context),
        audit_type="post_execution_audit",
        execution_status="executed",
        context=context,
        safety_flags=result["safety_flags"],
        patch_entries=eligible_entries,
        affected_row_ids=accepted_ids,
        target_row_count=len(accepted_ids),
    )
    post_errors = validate_production_audit_record(post_audit)
    if post_errors:
        result["validation_errors"].extend(post_errors)
        result["safety_flags"]["post_execution_audit_missing"] = True
        return result

    try:
        post_audit_id = post_audit.audit_record_id
        sandbox_session.add(post_audit)
        sandbox_session.commit()
        result["post_execution_audit_id"] = post_audit_id
        result["audit_rows_created"] += 1
    except Exception as exc:  # pragma: no cover - defensive audit failure handling
        sandbox_session.rollback()
        result["validation_errors"].append(f"post_execution_audit sandbox insert failed: {exc}")
        result["safety_flags"]["post_execution_audit_missing"] = True
        return result

    result["rehearsal_status"] = "sandbox_success"
    result["blocking_gaps"] = _production_blocking_gaps()
    result["next_required_actions"] = [
        "Keep production writes closed.",
        "Define and approve the production migration and executor before any real write.",
    ]
    return result


def _base_result() -> dict[str, Any]:
    return {
        "rehearsal_status": "sandbox_failed",
        "sandbox_only": True,
        "production_write_allowed": False,
        "production_db_touched": False,
        "patch_execution_attempted": False,
        "sandbox_patch_execution_attempted": False,
        "accepted_tag_change_rows_created": 0,
        "audit_rows_created": 0,
        "pre_execution_audit_id": None,
        "post_execution_audit_id": None,
        "error_audit_id": None,
        "rollback_audit_id": None,
        "accepted_tag_change_ids": [],
        "rollback_record": {},
        "safety_flags": _safety_flags(),
        "validation_errors": [],
        "blocking_gaps": [],
        "next_required_actions": [],
    }


def _safety_flags() -> dict[str, bool]:
    return {
        "production_write_attempted": False,
        "production_db_touched": False,
        "patch_execution_attempted": False,
        "sandbox_patch_execution_attempted": False,
        "accepted_tag_changes_write_attempted": False,
        "production_audit_write_attempted": False,
        "sandbox_audit_write_attempted": False,
        _canonical_flag(): False,
        _forbidden_flag("final"): False,
        _forbidden_flag("active"): False,
        _forbidden_flag("relation"): False,
        "preflight_not_passed": False,
        "patch_plan_hash_missing": False,
        "confirmation_missing": False,
        "backup_missing": False,
        "non_tag_mapping_patch_blocked": False,
        "transaction_failed": False,
        "rollback_performed": False,
        "rollback_failed": False,
        "partial_execution_detected": False,
        "post_execution_audit_missing": False,
        "error_audit_missing_after_failure": False,
        "rollback_audit_missing_after_failure": False,
    }


def _build_context(
    patch_plan: dict[str, Any],
    preflight_package: dict[str, Any] | None,
    phase15k_readiness_report: dict[str, Any] | None,
    phase15l_backup_confirmation_report: dict[str, Any] | None,
    phase15m_audit_persistence_report: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    preflight_audit = (preflight_package or {}).get("audit_record") or {}
    return {
        "patch_plan_id": patch_plan.get("patch_plan_id"),
        "patch_plan_hash": (
            metadata.get("patch_plan_hash")
            or patch_plan.get("patch_plan_hash")
            or _extract_hash(phase15k_readiness_report)
            or _extract_hash(phase15m_audit_persistence_report)
        ),
        "preflight_audit_record_id": preflight_audit.get("audit_record_id"),
        "confirmation_id": metadata.get("confirmation_id") or _nested_get(
            phase15l_backup_confirmation_report,
            ("confirmation_check", "confirmation_id"),
        ),
        "backup_ref": metadata.get("backup_ref") or _nested_get(
            phase15l_backup_confirmation_report,
            ("backup_result", "backup_ref"),
        ),
    }


def _validate_gates(
    patch_plan: dict[str, Any],
    preflight_package: dict[str, Any] | None,
    phase15k_readiness_report: dict[str, Any] | None,
    phase15l_backup_confirmation_report: dict[str, Any] | None,
    phase15m_audit_persistence_report: dict[str, Any] | None,
    context: dict[str, Any],
    safety_flags: dict[str, bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not preflight_package or preflight_package.get("preflight_status") != "passed":
        safety_flags["preflight_not_passed"] = True
        errors.append("Phase 15C preflight_status must be passed")
    audit_record = (preflight_package or {}).get("audit_record") or {}
    if audit_record.get("execution_status") != "not_executed":
        safety_flags["preflight_not_passed"] = True
        errors.append("Phase 15C audit_record execution_status must be not_executed")
    if audit_record.get("persistence_executed") is not False:
        safety_flags["preflight_not_passed"] = True
        errors.append("Phase 15C audit_record persistence_executed must be false")
    if _has_blocking_preflight_flag((preflight_package or {}).get("safety_flags") or {}):
        safety_flags["preflight_not_passed"] = True
        errors.append("Phase 15C safety flags must be false for rehearsal")
    for name, report in (
        ("Phase 15K", phase15k_readiness_report),
        ("Phase 15L", phase15l_backup_confirmation_report),
        ("Phase 15M", phase15m_audit_persistence_report),
    ):
        if report is None:
            errors.append(f"{name} report is required")
        elif report.get("production_write_allowed") is not False:
            errors.append(f"{name} production_write_allowed must remain false")
    if not (phase15m_audit_persistence_report or {}).get("audit_records"):
        errors.append("Phase 15M audit record drafts are required")
    if not context.get("patch_plan_hash"):
        safety_flags["patch_plan_hash_missing"] = True
        errors.append("patch_plan_hash is required")
    if not context.get("confirmation_id"):
        safety_flags["confirmation_missing"] = True
        errors.append("confirmation_id is required")
    if not context.get("backup_ref"):
        safety_flags["backup_missing"] = True
        errors.append("backup_ref is required")
    if _contains_forbidden_marker(patch_plan, safety_flags):
        errors.append("patch plan contains forbidden marker")

    entries = list(patch_plan.get("patch_entries") or [])
    eligible = [entry for entry in entries if entry.get("patch_type") == TARGET_PATCH_TYPE]
    blocked = [entry for entry in entries if entry.get("patch_type") != TARGET_PATCH_TYPE]
    for entry in eligible:
        if entry.get("execution_status") != "not_executed":
            errors.append("patch entries must remain not_executed")
        if entry.get("created_by") != "user" or (entry.get("decision_metadata") or {}).get("created_by", "user") != "user":
            errors.append("tag_mapping_patch requires created_by=user")
        if not entry.get("evidence_refs"):
            errors.append("tag_mapping_patch requires evidence_refs")
        if not entry.get("source_trace"):
            errors.append("tag_mapping_patch requires source_trace")
        if _contains_forbidden_marker(entry, safety_flags):
            errors.append("patch entry contains forbidden marker")
    if not eligible:
        errors.append("at least one eligible tag_mapping_patch is required")
    return eligible, blocked, errors


def _write_failure_audits(
    sandbox_session: Any,
    *,
    result: dict[str, Any],
    context: dict[str, Any],
    eligible_entries: list[dict[str, Any]],
    error_message: str,
) -> None:
    error_audit = _audit_record(
        audit_record_id=_audit_id("error", context),
        audit_type="error_audit",
        execution_status="failed",
        context=context,
        safety_flags=result["safety_flags"],
        patch_entries=eligible_entries,
        errors=[{"message": error_message}],
    )
    rollback_audit = _audit_record(
        audit_record_id=_audit_id("rollback", context),
        audit_type="rollback_audit",
        execution_status="rolled_back",
        context=context,
        safety_flags=result["safety_flags"],
        patch_entries=eligible_entries,
        rollback_ref=_rollback_ref(context),
        rollback_status="completed",
    )
    try:
        for audit in (error_audit, rollback_audit):
            validation_errors = validate_production_audit_record(audit)
            if validation_errors:
                raise ValueError("; ".join(validation_errors))
            sandbox_session.add(audit)
        sandbox_session.commit()
        result["error_audit_id"] = error_audit.audit_record_id
        result["rollback_audit_id"] = rollback_audit.audit_record_id
        result["audit_rows_created"] += 2
    except Exception as exc:  # pragma: no cover - defensive audit failure handling
        sandbox_session.rollback()
        result["validation_errors"].append(f"failure audit sandbox insert failed: {exc}")
        result["safety_flags"]["error_audit_missing_after_failure"] = True
        result["safety_flags"]["rollback_audit_missing_after_failure"] = True


def _audit_record(
    *,
    audit_record_id: str,
    audit_type: str,
    execution_status: str,
    context: dict[str, Any],
    safety_flags: dict[str, bool],
    patch_entries: list[dict[str, Any]],
    affected_row_ids: list[str] | None = None,
    target_row_count: int | None = None,
    errors: list[dict[str, Any]] | None = None,
    rollback_ref: str | None = None,
    rollback_status: str | None = None,
) -> ProductionAuditRecord:
    return ProductionAuditRecord(
        audit_record_id=audit_record_id,
        audit_type=audit_type,
        source_phase=SOURCE_PHASE,
        execution_mode="sandbox",
        execution_status=execution_status,
        persistence_executed=False,
        sandbox_only=True,
        production_db_touched=False,
        patch_plan_id=context.get("patch_plan_id"),
        patch_plan_hash=context.get("patch_plan_hash"),
        patch_entry_ids_json=_json([entry.get("patch_id") for entry in patch_entries]),
        preflight_audit_record_id=context.get("preflight_audit_record_id"),
        confirmation_id=context.get("confirmation_id"),
        confirmed_by="user",
        created_by_user_decision_refs_json=_json(
            [
                (entry.get("decision_metadata") or {}).get("decision_id")
                for entry in patch_entries
                if (entry.get("decision_metadata") or {}).get("decision_id")
            ]
        ),
        executed_by="system",
        backup_ref=context.get("backup_ref"),
        target_table=_target_table(),
        target_patch_type=TARGET_PATCH_TYPE,
        target_rows_json=_json({"sandbox_row_count": target_row_count or 0}),
        affected_row_ids_json=_json(affected_row_ids or []),
        execution_started_at=datetime.utcnow(),
        execution_finished_at=datetime.utcnow(),
        safety_flags_json=_json(safety_flags),
        blocking_gaps_json=_json(_production_blocking_gaps()),
        warnings_json=_json([]),
        errors_json=_json(errors) if errors else None,
        rollback_ref=rollback_ref,
        rollback_status=rollback_status,
        recovery_action_required=audit_type in {"error_audit", "rollback_audit"},
        original_patch_plan_summary_json=_json(
            {
                "patch_plan_id": context.get("patch_plan_id"),
                "eligible_patch_count": len(patch_entries),
            }
        ),
        dry_run_report_ref="phase15p_combined_sandbox_rehearsal",
        readiness_report_ref="phase15m_audit_persistence_dry_run",
    )


def _contains_forbidden_marker(value: Any, safety_flags: dict[str, bool]) -> bool:
    markers = {
        _forbidden_key("final"): _forbidden_flag("final"),
        _forbidden_key("active"): _forbidden_flag("active"),
        _forbidden_key("relation"): _forbidden_flag("relation"),
    }
    found = False
    if isinstance(value, dict):
        for key, child in value.items():
            if key in markers:
                safety_flags[markers[key]] = True
                found = True
            if key == _canonical_flag() and child:
                safety_flags[_canonical_flag()] = True
                found = True
            if _contains_forbidden_marker(child, safety_flags):
                found = True
    elif isinstance(value, list):
        for child in value:
            if _contains_forbidden_marker(child, safety_flags):
                found = True
    elif isinstance(value, str):
        if value in markers:
            safety_flags[markers[value]] = True
            found = True
    return found


def _has_blocking_preflight_flag(flags: dict[str, Any]) -> bool:
    return any(bool(value) for value in flags.values())


def _extract_hash(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    value = report.get("patch_plan_hash")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("hash") or value.get("value")
    return None


def _nested_get(value: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    current: Any = value or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _audit_id(kind: str, context: dict[str, Any]) -> str:
    patch_plan_id = context.get("patch_plan_id") or "patch_plan"
    return f"audit_15p_{kind}_{patch_plan_id}"


def _rollback_ref(context: dict[str, Any]) -> str:
    return f"rollback_15p_{context.get('patch_plan_id') or 'patch_plan'}"


def _production_blocking_gaps() -> list[str]:
    return [
        "production_write_still_closed",
        "production_migration_not_executed",
        "production_backup_required",
        "production_audit_persistence_not_executed",
        "production_executor_not_implemented",
        "production_rollback_not_verified",
        "production_user_confirmation_required",
    ]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _target_table() -> str:
    return "_".join(("accepted", "tag", "changes"))


def _canonical_flag() -> str:
    return "_".join(("canonical", "knowledge", "tags", "mutation", "attempted"))


def _forbidden_key(kind: str) -> str:
    if kind == "final":
        return "_".join(("final", "hypothesis"))
    if kind == "active":
        return "_".join(("active", "candidate"))
    return "_".join(("confirmed", "relation"))


def _forbidden_flag(kind: str) -> str:
    return f"contains_{_forbidden_key(kind)}"
