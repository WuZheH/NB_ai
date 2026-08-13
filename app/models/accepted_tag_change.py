from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


ALLOWED_TARGET_BUCKETS = {"topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"}
ALLOWED_RECORD_STATUSES = {
    "accepted_by_user",
    "rejected_by_user",
    "edited_by_user",
    "deferred",
    "superseded",
    "rolled_back",
}
ALLOWED_EXECUTION_STATUSES = {"not_executed", "simulated", "executed", "failed", "rolled_back"}
ALLOWED_DECISIONS = {"accept", "edit", "reject", "defer", "request_more_evidence"}
FORBIDDEN_PAYLOAD_KEYS = {"final_hypothesis", "active_candidate", "confirmed_relation"}


class AcceptedTagChange(Base):
    __tablename__ = "accepted_tag_changes"
    __table_args__ = (
        CheckConstraint(
            "target_bucket IN ('topic_tags', 'problem_tags', 'mechanism_tags', 'inspiration_tags')",
            name="ck_accepted_tag_changes_target_bucket",
        ),
        CheckConstraint(
            "record_status IN ('accepted_by_user', 'rejected_by_user', 'edited_by_user', "
            "'deferred', 'superseded', 'rolled_back')",
            name="ck_accepted_tag_changes_record_status",
        ),
        CheckConstraint(
            "execution_status IN ('not_executed', 'simulated', 'executed', 'failed', 'rolled_back')",
            name="ck_accepted_tag_changes_execution_status",
        ),
        CheckConstraint(
            "decision IN ('accept', 'edit', 'reject', 'defer', 'request_more_evidence')",
            name="ck_accepted_tag_changes_decision",
        ),
        CheckConstraint(
            "record_status NOT IN ('accepted_by_user', 'edited_by_user') OR created_by = 'user'",
            name="ck_accepted_tag_changes_user_for_committed",
        ),
        CheckConstraint(
            "record_status != 'accepted_by_user' OR length(mapped_tag_name) > 0",
            name="ck_accepted_tag_changes_mapped_name_for_accept",
        ),
        UniqueConstraint("review_item_id", "patch_entry_id", name="uq_accepted_tag_changes_review_patch"),
        Index("ix_accepted_tag_changes_review_item_id", "review_item_id"),
        Index("ix_accepted_tag_changes_patch_entry_id", "patch_entry_id"),
        Index("ix_accepted_tag_changes_research_session_id", "research_session_id"),
        Index("ix_accepted_tag_changes_target_bucket", "target_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    accepted_tag_change_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    review_queue_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    review_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    research_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_research_session_output_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    patch_plan_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    patch_entry_id: Mapped[str] = mapped_column(String(128), nullable=False)
    preflight_audit_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_audit_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    source_tag_raw: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_tag_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_tag_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    mapped_tag_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mapping_status_at_review: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_confidence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_human_review_at_generation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    evidence_refs_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_trace_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    record_status: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(64), nullable=False, default="not_executed")
    simulation_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    persistence_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    safety_flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    original_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)


def validate_accepted_tag_change_record(record: AcceptedTagChange) -> None:
    """Validate review/audit invariants that are awkward to express in SQLite."""
    if record.target_bucket not in ALLOWED_TARGET_BUCKETS:
        raise ValueError("target_bucket is not allowlisted")
    if record.record_status not in ALLOWED_RECORD_STATUSES:
        raise ValueError("record_status is not allowlisted")
    if record.execution_status not in ALLOWED_EXECUTION_STATUSES:
        raise ValueError("execution_status is not allowlisted")
    if record.decision not in ALLOWED_DECISIONS:
        raise ValueError("decision is not allowlisted")
    if record.record_status in {"accepted_by_user", "edited_by_user"} and record.created_by != "user":
        raise ValueError("committed review records require created_by=user")
    if record.record_status == "accepted_by_user" and not record.mapped_tag_name:
        raise ValueError("accepted_by_user requires mapped_tag_name")
    if not _json_has_content(record.source_trace_json):
        raise ValueError("source_trace_json must be present")
    if record.record_status == "accepted_by_user" and not _json_has_content(record.evidence_refs_json):
        raise ValueError("accepted_by_user requires evidence_refs_json")
    if not _json_has_content(record.original_payload_json):
        raise ValueError("original_payload_json must be present")
    for field_name in ("original_payload_json", "normalized_payload_json", "edited_payload_json", "safety_flags_json"):
        payload = getattr(record, field_name, None)
        if payload and _contains_forbidden_payload_marker(_json_loads(payload)):
            raise ValueError(f"{field_name} contains forbidden payload marker")


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
