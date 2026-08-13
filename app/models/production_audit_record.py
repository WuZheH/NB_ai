from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


ALLOWED_AUDIT_TYPES = {
    "pre_migration_audit",
    "backup_audit",
    "pre_execution_audit",
    "post_execution_audit",
    "error_audit",
    "rollback_audit",
}
ALLOWED_EXECUTION_MODES = {"dry_run", "sandbox", "production"}
ALLOWED_EXECUTION_STATUSES = {
    "planned",
    "preflighted",
    "blocked",
    "executing",
    "executed",
    "failed",
    "rolled_back",
    "rollback_failed",
}
ALLOWED_TARGET_TABLES = {"accepted_tag_changes"}
ALLOWED_TARGET_PATCH_TYPES = {"tag_mapping_patch"}
EXECUTION_RELATED_AUDIT_TYPES = {"pre_execution_audit", "post_execution_audit", "error_audit", "rollback_audit"}
FORBIDDEN_PAYLOAD_KEYS = {"final_hypothesis", "active_candidate", "confirmed_relation"}
CANONICAL_TAG_MUTATION_FLAG = "canonical_knowledge_tags_mutation_attempted"


class ProductionAuditRecord(Base):
    __tablename__ = "production_audit_records"
    __table_args__ = (
        CheckConstraint(
            "audit_type IN ('pre_migration_audit', 'backup_audit', 'pre_execution_audit', "
            "'post_execution_audit', 'error_audit', 'rollback_audit')",
            name="ck_production_audit_records_audit_type",
        ),
        CheckConstraint(
            "execution_mode IN ('dry_run', 'sandbox', 'production')",
            name="ck_production_audit_records_execution_mode",
        ),
        CheckConstraint(
            "execution_status IN ('planned', 'preflighted', 'blocked', 'executing', "
            "'executed', 'failed', 'rolled_back', 'rollback_failed')",
            name="ck_production_audit_records_execution_status",
        ),
        CheckConstraint(
            "target_table = 'accepted_tag_changes'",
            name="ck_production_audit_records_target_table",
        ),
        CheckConstraint(
            "target_patch_type = 'tag_mapping_patch'",
            name="ck_production_audit_records_target_patch_type",
        ),
        CheckConstraint(
            "persistence_executed = 0 OR (audit_type = 'post_execution_audit' AND execution_status = 'executed')",
            name="ck_production_audit_records_persistence_executed_only_post_execution",
        ),
        Index("ix_production_audit_records_audit_record_id", "audit_record_id", unique=True),
        Index("ix_production_audit_records_patch_plan_id", "patch_plan_id"),
        Index("ix_production_audit_records_patch_plan_hash", "patch_plan_hash"),
        Index("ix_production_audit_records_audit_type", "audit_type"),
        Index("ix_production_audit_records_execution_status", "execution_status"),
        Index("ix_production_audit_records_target_table", "target_table"),
        Index("ix_production_audit_records_confirmation_id", "confirmation_id"),
        Index("ix_production_audit_records_backup_ref", "backup_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    audit_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    audit_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_phase: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(64), nullable=False)
    persistence_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sandbox_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    production_db_touched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    patch_plan_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    patch_plan_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    patch_entry_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    preflight_audit_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_audit_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    confirmation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_decision_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    backup_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    backup_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    backup_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    target_table: Mapped[str] = mapped_column(String(128), nullable=False)
    target_patch_type: Mapped[str] = mapped_column(String(128), nullable=False)
    target_rows_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_row_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    safety_flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    blocking_gaps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    before_snapshot_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    after_snapshot_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    original_patch_plan_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run_report_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    readiness_report_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


def validate_production_audit_record(record: ProductionAuditRecord) -> list[str]:
    errors: list[str] = []
    if not record.audit_record_id:
        errors.append("audit_record_id is required")
    if record.audit_type not in ALLOWED_AUDIT_TYPES:
        errors.append("audit_type is not allowlisted")
    if record.execution_mode not in ALLOWED_EXECUTION_MODES:
        errors.append("execution_mode is not allowlisted")
    if record.execution_status not in ALLOWED_EXECUTION_STATUSES:
        errors.append("execution_status is not allowlisted")
    if record.target_table not in ALLOWED_TARGET_TABLES:
        errors.append("target_table is not allowlisted")
    if record.target_patch_type not in ALLOWED_TARGET_PATCH_TYPES:
        errors.append("target_patch_type is not allowlisted")
    if record.audit_type in EXECUTION_RELATED_AUDIT_TYPES and not record.patch_plan_id:
        errors.append("execution-related audits require patch_plan_id")
    if record.audit_type in EXECUTION_RELATED_AUDIT_TYPES and not record.patch_plan_hash:
        errors.append("execution-related audits require patch_plan_hash")
    if record.audit_type in EXECUTION_RELATED_AUDIT_TYPES and not record.preflight_audit_record_id:
        errors.append("execution-related audits require preflight_audit_record_id")
    if record.execution_mode == "production" and record.audit_type in EXECUTION_RELATED_AUDIT_TYPES and not record.confirmation_id:
        errors.append("production execution audits require confirmation_id")
    if record.execution_mode == "production" and record.audit_type in EXECUTION_RELATED_AUDIT_TYPES and not record.backup_ref:
        errors.append("production execution audits require backup_ref")
    if record.persistence_executed and not (
        record.audit_type == "post_execution_audit" and record.execution_status == "executed"
    ):
        errors.append("persistence_executed=true is only allowed for executed post_execution_audit")
    if record.production_db_touched and record.execution_mode != "production":
        errors.append("production_db_touched=true is only allowed for production audits")
    if record.audit_type == "rollback_audit" and not record.rollback_ref:
        errors.append("rollback_audit requires rollback_ref")
    if record.audit_type == "error_audit" and not _json_has_content(record.errors_json):
        errors.append("error_audit requires errors_json")
    if not _json_has_content(record.safety_flags_json):
        errors.append("safety_flags_json is required")
    else:
        safety_flags = _json_loads(record.safety_flags_json)
        if isinstance(safety_flags, dict) and safety_flags.get(CANONICAL_TAG_MUTATION_FLAG):
            errors.append("canonical knowledge tag mutation flag must be false")

    for field_name in _json_payload_fields():
        payload = getattr(record, field_name, None)
        if payload:
            try:
                loaded = _json_loads(payload)
            except ValueError:
                errors.append(f"{field_name} is invalid JSON")
                continue
            if _contains_forbidden_payload_marker(loaded):
                errors.append(f"{field_name} contains forbidden payload marker")
    return errors


def _json_payload_fields() -> tuple[str, ...]:
    return (
        "patch_entry_ids_json",
        "created_by_user_decision_refs_json",
        "target_rows_json",
        "affected_row_ids_json",
        "safety_flags_json",
        "blocking_gaps_json",
        "warnings_json",
        "errors_json",
        "original_patch_plan_summary_json",
    )


def _json_has_content(value: str | None) -> bool:
    if not value:
        return False
    loaded = _json_loads(value)
    if loaded in ({}, [], None, ""):
        return False
    return True


def _json_loads(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON payload") from exc


def _contains_forbidden_payload_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in FORBIDDEN_PAYLOAD_KEYS for key in value):
            return True
        return any(_contains_forbidden_payload_marker(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_payload_marker(child) for child in value)
    if isinstance(value, str):
        return value in FORBIDDEN_PAYLOAD_KEYS
    return False
