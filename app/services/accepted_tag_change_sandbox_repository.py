from __future__ import annotations

import json
from typing import Any

from app.models.accepted_tag_change import AcceptedTagChange, validate_accepted_tag_change_record


class AcceptedTagChangeSandboxRepository:
    """Sandbox-only repository for accepted_tag_changes rows.

    The caller must pass an explicit SQLAlchemy session bound to an isolated
    sandbox/test database. This class deliberately does not create engines or
    sessions.
    """

    def __init__(self, session: Any) -> None:
        if session is None:
            raise ValueError("sandbox SQLAlchemy session is required")
        self.session = session

    def create_accepted_tag_change_from_patch(
        self,
        patch_entry: dict[str, Any],
        *,
        patch_plan: dict[str, Any],
        preflight_package: dict[str, Any],
        execution_audit_record_id: str | None = None,
    ) -> AcceptedTagChange:
        record = _record_from_patch_entry(
            patch_entry,
            patch_plan=patch_plan,
            preflight_package=preflight_package,
            execution_audit_record_id=execution_audit_record_id,
        )
        validate_accepted_tag_change_record(record)
        self.session.add(record)
        self.session.flush()
        validate_accepted_tag_change_record(record)
        return record

    def list_accepted_tag_changes(self) -> list[AcceptedTagChange]:
        return list(self.session.query(AcceptedTagChange).order_by(AcceptedTagChange.id).all())

    def count_accepted_tag_changes(self) -> int:
        return int(self.session.query(AcceptedTagChange).count())


def _record_from_patch_entry(
    patch_entry: dict[str, Any],
    *,
    patch_plan: dict[str, Any],
    preflight_package: dict[str, Any],
    execution_audit_record_id: str | None,
) -> AcceptedTagChange:
    payload = patch_entry.get("original_payload") or {}
    decision_metadata = patch_entry.get("decision_metadata") or {}
    source_trace = patch_entry.get("source_trace") or {}
    evidence_refs = patch_entry.get("evidence_refs") or []
    source_tag = _source_tag(payload)
    target_bucket = payload.get("target_bucket") or payload.get("suggested_bucket")
    mapped_tag_name = payload.get("mapped_tag_name") or payload.get("name") or payload.get("tag_name")
    review_queue_id = patch_plan.get("review_queue_id")
    patch_id = patch_entry.get("patch_id")
    review_item_id = patch_entry.get("review_item_id")
    document_ids, chunk_ids = _source_trace_ids(source_trace)

    return AcceptedTagChange(
        accepted_tag_change_id=_accepted_tag_change_id(review_item_id, patch_id),
        review_queue_id=review_queue_id,
        review_item_id=review_item_id,
        review_decision_id=decision_metadata.get("decision_id") or decision_metadata.get("review_decision_id"),
        research_session_id=patch_plan.get("research_session_id") or source_trace.get("research_session_id"),
        source_research_session_output_id=patch_plan.get("source_research_session_output_id"),
        patch_plan_id=patch_plan.get("patch_plan_id"),
        patch_entry_id=patch_id,
        preflight_audit_record_id=(preflight_package.get("audit_record") or {}).get("audit_record_id"),
        execution_audit_record_id=execution_audit_record_id,
        source_tag_raw=source_tag.get("raw"),
        source_tag_type=source_tag.get("tag_type"),
        source_tag_name=source_tag.get("name"),
        target_bucket=target_bucket,
        mapped_tag_name=mapped_tag_name,
        mapping_status_at_review=payload.get("status") or payload.get("mapping_status") or "suggested",
        mapping_confidence=payload.get("confidence") or payload.get("mapping_confidence"),
        mapping_reason=payload.get("mapping_reason"),
        needs_human_review_at_generation=bool(payload.get("needs_human_review", False)),
        decision=patch_entry.get("decision") or decision_metadata.get("decision"),
        created_by=patch_entry.get("created_by"),
        reviewer_note=decision_metadata.get("reviewer_note") or decision_metadata.get("note"),
        edited_payload_json=_json_or_none(decision_metadata.get("edited_payload")),
        evidence_refs_json=json.dumps(evidence_refs, ensure_ascii=True),
        source_trace_json=json.dumps(source_trace, ensure_ascii=True),
        document_ids_json=json.dumps(document_ids, ensure_ascii=True),
        chunk_ids_json=json.dumps(chunk_ids, ensure_ascii=True),
        record_status="accepted_by_user",
        execution_status="simulated",
        simulation_source="phase15h_sandbox_executor",
        persistence_executed=False,
        safety_flags_json=json.dumps({"production_db_touched": False}, ensure_ascii=True),
        original_payload_json=json.dumps(payload, ensure_ascii=True),
        normalized_payload_json=json.dumps(
            {
                "target_bucket": target_bucket,
                "mapped_tag_name": mapped_tag_name,
                "source_tag": source_tag,
            },
            ensure_ascii=True,
        ),
        rollback_ref=None,
        error_json=None,
    )


def _source_tag(payload: dict[str, Any]) -> dict[str, Any]:
    source_tag = payload.get("source_tag")
    if isinstance(source_tag, dict):
        return {
            "raw": source_tag.get("raw"),
            "tag_type": source_tag.get("tag_type") or source_tag.get("type"),
            "name": source_tag.get("name"),
        }
    raw = payload.get("source_tag_raw")
    return {
        "raw": raw,
        "tag_type": payload.get("source_tag_type"),
        "name": payload.get("source_tag_name"),
    }


def _source_trace_ids(source_trace: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    document_ids = _as_list(source_trace.get("document_ids"))
    chunk_ids = _as_list(source_trace.get("chunk_ids"))
    if source_trace.get("document_id") is not None:
        document_ids.append(source_trace.get("document_id"))
    if source_trace.get("chunk_id") is not None:
        chunk_ids.append(source_trace.get("chunk_id"))
    return _dedupe(document_ids), _dedupe(chunk_ids)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _dedupe(values: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _json_or_none(value: Any) -> str | None:
    if value in (None, "", {}, []):
        return None
    return json.dumps(value, ensure_ascii=True)


def _accepted_tag_change_id(review_item_id: Any, patch_id: Any) -> str:
    raw = f"atc_{review_item_id or 'unknown'}_{patch_id or 'patch'}"
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw)[:128]
