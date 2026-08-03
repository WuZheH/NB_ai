from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_human_review_queue(research_session_output: dict[str, Any]) -> dict[str, Any]:
    """Convert a validated ResearchSessionOutput into a human_review_queue dict.

    Pure in-memory transformation. No DB writes, no network calls, no side effects.
    Implements the MVP subset of Phase 14J item types:
      1. suggested_tag_mapping
      2. draft_candidate_method
      3. draft_hypothesis
      4. critic_entry
      5. experiment_plan
      6. unsupported_claim
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    source_id = research_session_output.get("schema_version", "phase14c.in_memory.v1")

    items: list[dict[str, Any]] = []
    _extract_suggested_tag_mappings(items, research_session_output)
    _extract_draft_candidate_methods(items, research_session_output)
    _extract_draft_hypotheses(items, research_session_output)
    _extract_critic_entries(items, research_session_output)
    _extract_experiment_plans(items, research_session_output)
    _extract_unsupported_claims(items, research_session_output)
    _extract_gap_items(items, research_session_output)

    research_goal = str(research_session_output.get("research_goal") or "Untitled research session")

    queue: dict[str, Any] = {
        "review_queue_id": f"rq_{ts}",
        "research_session_id": f"session_{ts}",
        "research_goal": research_goal,
        "source_research_session_output_id": source_id,
        "status": "pending_review",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": "system",
        "items": items,
        "review_summary": _build_summary(items),
        "safety_flags": _build_safety_flags(items, research_session_output),
    }
    return queue


# ---------------------------------------------------------------------------
# Item extractors
# ---------------------------------------------------------------------------

def _extract_suggested_tag_mappings(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    """Create suggested_tag_mapping items from tag aggregation view and retrieval metadata."""
    seen: set[str] = set()
    for item in _iter_evidence_items(output):
        for mapping in item.get("retrieval_metadata", {}).get("tag_mapping_results", []):
            if not isinstance(mapping, dict):
                continue
            source_tag = mapping.get("source_tag") or {}
            raw = source_tag.get("raw") or mapping.get("name") or ""
            uid = f"tagmap:{raw}:{mapping.get('target_bucket')}"
            if uid in seen:
                continue
            seen.add(uid)
            items.append(_make_tag_mapping_item(mapping, item, len(items)))
    if not items and not _tag_aggregation_empty(output):
        _extract_tag_mappings_from_aggregation(items, output)


def _tag_aggregation_empty(output: dict[str, Any]) -> bool:
    tag_view = output.get("tag_aggregation_view") or {}
    if not isinstance(tag_view, dict):
        return True
    for bucket in ("topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"):
        if tag_view.get(bucket):
            return False
    return True


def _extract_tag_mappings_from_aggregation(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    tag_view = output.get("tag_aggregation_view") or {}
    if not isinstance(tag_view, dict):
        return
    evidence_items = list(_iter_evidence_items(output))
    for bucket in ("topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"):
        for tag_entry in tag_view.get(bucket, []):
            if not isinstance(tag_entry, dict):
                continue
            tag_name = tag_entry.get("tag") or tag_entry.get("name") or ""
            if not tag_name:
                continue
            source_trace = {}
            evidence_refs = list(tag_entry.get("evidence_refs") or [])
            if evidence_refs and evidence_items:
                source_trace = _find_source_trace(evidence_refs[0], evidence_items)
            items.append(
                _make_review_item(
                    index=len(items),
                    item_type="suggested_tag_mapping",
                    source_view="tag_aggregation_view",
                    source_id=f"{bucket}:{tag_name}",
                    summary=f"{tag_name} → {bucket} as suggested",
                    priority="medium",
                    payload={
                        "source_tag": {"tag_type": bucket.rstrip("_tags"), "name": tag_name, "raw": f"{bucket}:{tag_name}"},
                        "target_bucket": bucket,
                        "mapped_name": tag_name,
                        "status": "suggested",
                        "confidence": "medium",
                        "mapping_reason": f"Detected as {bucket} from evidence tags.",
                        "needs_human_review": False,
                    },
                    evidence_refs=evidence_refs,
                    source_trace=source_trace,
                    risk_flags=[],
                    suggested_action="accept_or_edit",
                    allowed_decisions=["accept", "reject", "edit", "defer", "request_more_evidence"],
                )
            )


def _make_tag_mapping_item(mapping: dict[str, Any], evidence_item: dict[str, Any], index: int) -> dict[str, Any]:
    source_tag = mapping.get("source_tag") or {}
    raw = source_tag.get("raw") or mapping.get("name") or ""
    target_bucket = mapping.get("target_bucket")
    status = mapping.get("status", "suggested")
    confidence = mapping.get("confidence", "medium")
    needs_review = bool(mapping.get("needs_human_review", False))
    unmapped = target_bucket is None or status == "unmapped"
    risk_flags: list[str] = []
    if unmapped:
        risk_flags.append("needs_human_review")
        confidence = "low"
    elif needs_review:
        risk_flags.append("needs_human_review")
    allowed_decisions = ["accept", "reject", "edit", "defer", "request_more_evidence"]
    suggested_action = "manual_classification_or_ignore" if unmapped else "accept_or_edit"

    return _make_review_item(
        index=index,
        item_type="suggested_tag_mapping",
        source_view="tag_aggregation_view",
        source_id=raw,
        summary=_summarize_mapping(mapping),
        priority="high" if unmapped else "medium",
        payload={
            "source_tag": dict(source_tag),
            "target_bucket": target_bucket,
            "mapped_name": mapping.get("name") or source_tag.get("name") or "",
            "status": status,
            "confidence": confidence,
            "mapping_reason": mapping.get("mapping_reason") or f"Legacy tag mapped to {target_bucket or 'unmapped'}.",
            "needs_human_review": needs_review or unmapped,
        },
        evidence_refs=[evidence_item.get("evidence_id")] if evidence_item.get("evidence_id") else [],
        source_trace=dict(evidence_item.get("source_trace") or {}),
        risk_flags=risk_flags,
        suggested_action=suggested_action,
        allowed_decisions=allowed_decisions,
    )


def _summarize_mapping(mapping: dict[str, Any]) -> str:
    source_tag = mapping.get("source_tag") or {}
    raw = source_tag.get("raw") or mapping.get("name") or "unknown"
    bucket = mapping.get("target_bucket") or "unmapped"
    return f"{raw} → {bucket} as {mapping.get('status', 'suggested')}"


def _extract_draft_candidate_methods(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    candidates = output.get("candidate_method_view", {}).get("candidates") or []
    evidence_items = list(_iter_evidence_items(output))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id") or "unknown"
        evidence_refs = list(candidate.get("evidence_refs") or [])
        source_trace = _find_source_trace_from_refs(evidence_refs, evidence_items)
        risk_flags: list[str] = []
        if candidate.get("speculation"):
            risk_flags.append("speculation")
        if candidate.get("evidence_strength") == "weak":
            risk_flags.append("weak_evidence")
        unsupported = candidate.get("unsupported_assumptions") or []
        if unsupported:
            risk_flags.append("unsupported_claims")

        items.append(
            _make_review_item(
                index=len(items),
                item_type="draft_candidate_method",
                source_view="candidate_method_view",
                source_id=candidate_id,
                summary=candidate.get("title") or candidate.get("problem_addressed") or str(candidate_id),
                priority="high",
                payload={
                    "candidate_id": candidate_id,
                    "title": candidate.get("title"),
                    "target_problem": candidate.get("problem_addressed"),
                    "proposed_mechanism": candidate.get("proposed_mechanism"),
                    "inspiration_path_ids": list(candidate.get("inspiration_path_ids") or []),
                    "baseline": candidate.get("baseline"),
                    "minimum_implementation": candidate.get("minimum_implementation"),
                    "expected_gain": candidate.get("expected_gain"),
                    "speculation": candidate.get("speculation", False),
                    "failure_risks": list(unsupported),
                    "linked_critic_id": candidate_id,
                    "linked_experiment_plan_id": candidate_id,
                },
                evidence_refs=evidence_refs,
                source_trace=source_trace,
                risk_flags=risk_flags,
                suggested_action="review_evidence_and_critic",
                allowed_decisions=[
                    "keep_draft", "reject", "revise", "request_more_evidence",
                    "request_novelty_check", "convert_to_hypothesis_draft", "defer",
                ],
            )
        )


def _extract_draft_hypotheses(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    hypotheses = output.get("draft_hypothesis_view", {}).get("hypotheses") or []
    evidence_items = list(_iter_evidence_items(output))
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        hypothesis_id = hypothesis.get("hypothesis_id") or "unknown"
        candidate_id = hypothesis.get("source_candidate_id") or "unknown"
        evidence_refs = list(hypothesis.get("evidence_refs") or [])
        source_trace = _find_source_trace_from_refs(evidence_refs, evidence_items)
        unsupported = hypothesis.get("unsupported_assumptions") or []
        risk_flags: list[str] = []
        if hypothesis.get("speculation"):
            risk_flags.append("speculation")
        if unsupported:
            risk_flags.append("unsupported_claims")

        items.append(
            _make_review_item(
                index=len(items),
                item_type="draft_hypothesis",
                source_view="draft_hypothesis_view",
                source_id=hypothesis_id,
                summary=hypothesis.get("claim") or str(hypothesis_id),
                priority="high",
                payload={
                    "hypothesis_id": hypothesis_id,
                    "candidate_id": candidate_id,
                    "status": "draft",
                    "claim": hypothesis.get("claim"),
                    "problem": hypothesis.get("problem"),
                    "proposed_mechanism": hypothesis.get("proposed_mechanism"),
                    "unsupported_assumptions": list(unsupported),
                    "unsupported_claims": list(unsupported),
                    "speculation": hypothesis.get("speculation", False),
                },
                evidence_refs=evidence_refs,
                source_trace=source_trace,
                risk_flags=risk_flags,
                suggested_action="review_unsupported_assumptions",
                allowed_decisions=[
                    "keep_draft", "reject", "revise",
                    "request_experiment_plan_revision", "request_more_evidence", "defer",
                ],
            )
        )


def _extract_critic_entries(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    critic_items = output.get("critic_view", {}).get("items") or []
    for critic in critic_items:
        if not isinstance(critic, dict):
            continue
        candidate_id = critic.get("candidate_id") or "unknown"
        items.append(
            _make_review_item(
                index=len(items),
                item_type="critic_entry",
                source_view="critic_view",
                source_id=candidate_id,
                summary=f"Novelty risk: {critic.get('novelty_risk')}, evidence: {critic.get('evidence_strength')}, {critic.get('downgrade_reason', '')}",
                priority="medium",
                payload={
                    "critic_id": candidate_id,
                    "candidate_id": candidate_id,
                    "novelty_risk": critic.get("novelty_risk"),
                    "incrementality_risk": critic.get("incrementality_risk"),
                    "evidence_strength": critic.get("evidence_strength"),
                    "evidence_limitations": critic.get("downgrade_reason"),
                    "implementation_cost": critic.get("implementation_cost"),
                    "module_stacking_risk": "review required",
                    "reviewer_attack_points": list(critic.get("critical_questions") or []),
                    "recommendation": "manual review before further design",
                },
                evidence_refs=[],
                source_trace={},
                risk_flags=(
                    ["weak_evidence", "downgrade_reason"]
                    if critic.get("evidence_strength") == "weak"
                    else []
                ),
                suggested_action="accept_or_edit",
                allowed_decisions=[
                    "accept_critic", "edit_critic", "reject_critic",
                    "mark_too_weak", "request_stronger_critic", "override_with_reason", "defer",
                ],
            )
        )


def _extract_experiment_plans(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    plans = output.get("experiment_plan_view", {}).get("plans") or []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        candidate_id = plan.get("candidate_id") or "unknown"
        missing_parts: list[str] = []
        if not plan.get("dataset") or "must be selected" in str(plan.get("dataset", "")):
            missing_parts.append("dataset_focus_missing")
        if not plan.get("metric") or "must be selected" in str(plan.get("metric", "")):
            missing_parts.append("metric_focus_missing")
        items.append(
            _make_review_item(
                index=len(items),
                item_type="experiment_plan",
                source_view="experiment_plan_view",
                source_id=candidate_id,
                summary=f"Baseline: {plan.get('baseline')}, Dataset: {plan.get('dataset')}",
                priority="medium",
                payload={
                    "experiment_plan_id": candidate_id,
                    "candidate_id": candidate_id,
                    "baseline": plan.get("baseline"),
                    "variant": plan.get("proposed_variant"),
                    "dataset": plan.get("dataset"),
                    "metric": plan.get("metric"),
                    "ablation": plan.get("ablation"),
                    "parameter_flops_control": plan.get("parameter_or_flops_control"),
                    "failure_case_analysis": plan.get("visualization_or_failure_case_analysis"),
                    "expected_positive_signal": "improvement on target metric without worsening key failure cases",
                    "negative_result_interpretation": "candidate mechanism may be insufficient or evidence too weak",
                },
                evidence_refs=[],
                source_trace={},
                risk_flags=missing_parts,
                suggested_action="review_and_revise_if_needed",
                allowed_decisions=[
                    "accept_as_draft_plan", "revise", "reject",
                    "split_into_minimum_experiment", "request_dataset_or_metric_fix",
                    "link_to_experiment_log_later", "defer",
                ],
            )
        )


def _extract_unsupported_claims(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    seen: set[str] = set()
    for candidate in output.get("candidate_method_view", {}).get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id") or "unknown"
        for claim in candidate.get("unsupported_assumptions") or []:
            claim_text = str(claim).strip()
            if not claim_text or claim_text in seen:
                continue
            seen.add(claim_text)
            items.append(
                _make_review_item(
                    index=len(items),
                    item_type="unsupported_claim",
                    source_view="candidate_method_view",
                    source_id=f"{candidate_id}:{claim_text[:60]}",
                    summary=claim_text,
                    priority="medium",
                    payload={
                        "claim_id": f"claim_{len(items):03d}",
                        "claim_text": claim_text,
                        "source_view": "candidate_method_view",
                        "related_candidate_id": candidate_id,
                        "related_hypothesis_id": None,
                        "reason_unsupported": "no evidence in retrieved chunks confirms this claim",
                        "required_evidence": "direct evidence supporting the claim",
                    },
                    evidence_refs=[],
                    source_trace={},
                    risk_flags=["unsupported", "speculation"],
                    suggested_action="find_supporting_evidence_or_mark_speculation",
                    allowed_decisions=[
                        "remove_claim", "keep_as_speculation",
                        "request_more_evidence", "rewrite_claim", "defer",
                    ],
                )
            )

    for hypothesis in output.get("draft_hypothesis_view", {}).get("hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        hypothesis_id = hypothesis.get("hypothesis_id") or "unknown"
        candidate_id = hypothesis.get("source_candidate_id") or "unknown"
        for claim in hypothesis.get("unsupported_assumptions") or []:
            claim_text = str(claim).strip()
            if not claim_text or claim_text in seen:
                continue
            seen.add(claim_text)
            items.append(
                _make_review_item(
                    index=len(items),
                    item_type="unsupported_claim",
                    source_view="draft_hypothesis_view",
                    source_id=f"{hypothesis_id}:{claim_text[:60]}",
                    summary=claim_text,
                    priority="medium",
                    payload={
                        "claim_id": f"claim_{len(items):03d}",
                        "claim_text": claim_text,
                        "source_view": "draft_hypothesis_view",
                        "related_candidate_id": candidate_id,
                        "related_hypothesis_id": hypothesis_id,
                        "reason_unsupported": "no evidence in retrieved chunks confirms this claim",
                        "required_evidence": "direct evidence supporting the claim",
                    },
                    evidence_refs=[],
                    source_trace={},
                    risk_flags=["unsupported", "speculation"],
                    suggested_action="find_supporting_evidence_or_mark_speculation",
                    allowed_decisions=[
                        "remove_claim", "keep_as_speculation",
                        "request_more_evidence", "rewrite_claim", "defer",
                    ],
                )
            )


def _extract_gap_items(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    """Optional gap item types: evidence_gap, source_trace_gap, unmapped_tag, missing_mechanism, missing_inspiration."""
    _extract_evidence_gaps(items, output)
    _extract_source_trace_gaps(items, output)
    _extract_unmapped_tags(items, output)
    _extract_missing_mechanisms(items, output)
    _extract_missing_inspirations(items, output)


def _extract_evidence_gaps(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    gaps = list(output.get("gaps") or [])
    gaps.extend(output.get("evidence_view", {}).get("gaps") or [])
    for gap in _unique_strings(gaps):
        items.append(
            _make_review_item(
                index=len(items),
                item_type="evidence_gap",
                source_view="evidence_view",
                source_id=gap,
                summary=gap,
                priority="low",
                payload={"gap_id": f"gap_{len(items):03d}", "gap_type": "top_level_gap", "affected_view": "evidence_view"},
                evidence_refs=[],
                source_trace={},
                risk_flags=["needs_human_review"],
                suggested_action="manual_classification_or_ignore",
                allowed_decisions=["defer", "request_more_evidence"],
            )
        )


def _extract_source_trace_gaps(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    for item in _iter_evidence_items(output):
        trace = item.get("source_trace") or {}
        for field in ("document_id", "document_title", "chunk_id", "heading_path", "pdf_path"):
            if trace.get(field) in (None, ""):
                items.append(
                    _make_review_item(
                        index=len(items),
                        item_type="source_trace_gap",
                        source_view="evidence_view",
                        source_id=f"missing_{field}:{item.get('evidence_id', 'unknown')}",
                        summary=f"missing source_trace.{field} for {item.get('evidence_id')}",
                        priority="low",
                        payload={"gap_id": f"stg_{len(items):03d}", "missing_field": field, "evidence_id": item.get("evidence_id")},
                        evidence_refs=[item.get("evidence_id")] if item.get("evidence_id") else [],
                        source_trace=dict(trace),
                        risk_flags=["source_trace_incomplete"],
                        suggested_action="manual_classification_or_ignore",
                        allowed_decisions=["defer", "request_more_evidence"],
                    )
                )


def _extract_unmapped_tags(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    seen: set[str] = set()
    for item in _iter_evidence_items(output):
        for mapping in item.get("retrieval_metadata", {}).get("tag_mapping_results", []):
            if not isinstance(mapping, dict):
                continue
            if mapping.get("status") != "unmapped" and mapping.get("target_bucket") is not None:
                continue
            source_tag = mapping.get("source_tag") or {}
            raw = source_tag.get("raw") or mapping.get("name") or ""
            if raw in seen:
                continue
            seen.add(raw)
            items.append(
                _make_review_item(
                    index=len(items),
                    item_type="unmapped_tag",
                    source_view="tag_aggregation_view",
                    source_id=f"unmapped:{raw}",
                    summary=f"Unmapped tag: {raw} — {mapping.get('mapping_reason', 'no mapping available')}",
                    priority="low",
                    payload={
                        "source_tag": dict(source_tag),
                        "target_bucket": None,
                        "mapped_name": mapping.get("name") or source_tag.get("name") or raw,
                        "status": "unmapped",
                        "confidence": "low",
                        "mapping_reason": mapping.get("mapping_reason", ""),
                        "needs_human_review": True,
                    },
                    evidence_refs=[item.get("evidence_id")] if item.get("evidence_id") else [],
                    source_trace=dict(item.get("source_trace") or {}),
                    risk_flags=["needs_human_review"],
                    suggested_action="manual_classification_or_ignore",
                    allowed_decisions=["defer", "request_more_evidence"],
                )
            )


def _extract_missing_mechanisms(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    mechanism_view = output.get("mechanism_view") or {}
    for gap in _unique_strings(mechanism_view.get("gaps") or []):
        if "mechanism" not in gap.lower():
            continue
        items.append(
            _make_review_item(
                index=len(items),
                item_type="missing_mechanism",
                source_view="mechanism_view",
                source_id=gap,
                summary=gap,
                priority="low",
                payload={"gap_id": f"mm_{len(items):03d}", "gap": gap},
                evidence_refs=[],
                source_trace={},
                risk_flags=["mechanism_view_empty"],
                suggested_action="manual_classification_or_ignore",
                allowed_decisions=["defer", "request_more_evidence"],
            )
        )


def _extract_missing_inspirations(items: list[dict[str, Any]], output: dict[str, Any]) -> None:
    inspiration_view = output.get("inspiration_path_view") or {}
    for gap in _unique_strings(inspiration_view.get("gaps") or []):
        if "inspiration" not in gap.lower():
            continue
        items.append(
            _make_review_item(
                index=len(items),
                item_type="missing_inspiration",
                source_view="inspiration_path_view",
                source_id=gap,
                summary=gap,
                priority="low",
                payload={"gap_id": f"mi_{len(items):03d}", "gap": gap},
                evidence_refs=[],
                source_trace={},
                risk_flags=["inspiration_path_empty"],
                suggested_action="manual_classification_or_ignore",
                allowed_decisions=["defer", "request_more_evidence"],
            )
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_review_item(
    *,
    index: int,
    item_type: str,
    source_view: str,
    source_id: str,
    summary: str,
    priority: str,
    payload: dict[str, Any],
    evidence_refs: list[str],
    source_trace: dict[str, Any],
    risk_flags: list[str],
    suggested_action: str,
    allowed_decisions: list[str],
) -> dict[str, Any]:
    return {
        "review_item_id": f"ri_{index + 1:03d}",
        "item_type": item_type,
        "source_view": source_view,
        "source_id": source_id,
        "status": "pending_review",
        "priority": priority,
        "summary": summary[:280],
        "payload": payload,
        "evidence_refs": evidence_refs,
        "source_trace": source_trace,
        "risk_flags": risk_flags,
        "suggested_action": suggested_action,
        "allowed_decisions": allowed_decisions,
        "review_decision": None,
    }


def _iter_evidence_items(output: dict[str, Any]):
    for item in output.get("evidence_view", {}).get("items") or []:
        if isinstance(item, dict):
            yield item


def _find_source_trace(evidence_id: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in evidence_items:
        if item.get("evidence_id") == evidence_id:
            return dict(item.get("source_trace") or {})
    return {}


def _find_source_trace_from_refs(refs: list[str], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    for ref in refs:
        trace = _find_source_trace(ref, evidence_items)
        if trace:
            return trace
    return {}


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


# ---------------------------------------------------------------------------
# Review summary
# ---------------------------------------------------------------------------

def _build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for item in items:
        item_type = item.get("item_type") or "unknown"
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        status = item.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "total_items": len(items),
        "pending_count": status_counts.get("pending_review", 0),
        "accepted_count": status_counts.get("accepted_by_user", 0),
        "rejected_count": status_counts.get("rejected_by_user", 0),
        "edited_count": status_counts.get("edited_by_user", 0),
        "deferred_count": status_counts.get("deferred", 0),
        "needs_more_evidence_count": status_counts.get("needs_more_evidence", 0),
        "draft_candidate_count": type_counts.get("draft_candidate_method", 0),
        "draft_hypothesis_count": type_counts.get("draft_hypothesis", 0),
        "critic_count": type_counts.get("critic_entry", 0),
        "experiment_plan_count": type_counts.get("experiment_plan", 0),
        "unsupported_claim_count": type_counts.get("unsupported_claim", 0),
        "unresolved_gap_count": (
            type_counts.get("evidence_gap", 0)
            + type_counts.get("source_trace_gap", 0)
            + type_counts.get("unmapped_tag", 0)
            + type_counts.get("missing_mechanism", 0)
            + type_counts.get("missing_inspiration", 0)
        ),
    }


# ---------------------------------------------------------------------------
# Safety flags
# ---------------------------------------------------------------------------

def _build_safety_flags(items: list[dict[str, Any]], output: dict[str, Any]) -> dict[str, bool]:
    flags: dict[str, bool] = {
        "contains_auto_accepted_tag": False,
        "contains_auto_active_hypothesis": False,
        "contains_final_hypothesis": False,
        "contains_unreviewed_candidate_as_confirmed": False,
        "contains_experiment_plan_as_result": False,
        "contains_external_context_as_core_evidence": False,
        "contains_retrieval_score_as_evidence_strength": False,
        "contains_mapping_confidence_as_evidence_strength": False,
        "missing_evidence_refs_for_promoted_item": False,
        "missing_user_decision_for_accepted_state": False,
    }

    # Scan for forbidden states in the input
    if _contains_key_recursive(output, "final_hypothesis"):
        flags["contains_final_hypothesis"] = True

    for item in items:
        if item.get("status") == "accepted_by_user" and not item.get("review_decision"):
            flags["missing_user_decision_for_accepted_state"] = True
        if item.get("status") == "active_candidate":
            flags["contains_auto_active_hypothesis"] = True

    # Check candidate/hypothesis items have evidence_refs
    for item in items:
        if item.get("item_type") in ("draft_candidate_method", "draft_hypothesis"):
            if not item.get("evidence_refs"):
                flags["missing_evidence_refs_for_promoted_item"] = True

    return flags


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_human_review_queue(queue: dict[str, Any]) -> None:
    """Validate a human_review_queue dict against Phase 14J invariants."""
    if not isinstance(queue, dict):
        raise ValueError("human_review_queue must be a dict.")

    required_top = [
        "review_queue_id", "research_session_id", "research_goal",
        "source_research_session_output_id", "status", "created_by",
        "items", "review_summary", "safety_flags",
    ]
    for field in required_top:
        if field not in queue:
            raise ValueError(f"missing top-level field: {field}")

    items = queue.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list.")

    valid_item_types = {
        "suggested_tag_mapping", "draft_candidate_method", "draft_hypothesis",
        "critic_entry", "experiment_plan", "unsupported_claim",
        "evidence_gap", "source_trace_gap", "unmapped_tag",
        "missing_mechanism", "missing_inspiration",
    }

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each item must be a dict.")
        for field in ("review_item_id", "item_type", "status", "source_id", "payload"):
            if field not in item:
                raise ValueError(f"item {item.get('review_item_id', '?')} missing field: {field}")
        if item["item_type"] not in valid_item_types:
            raise ValueError(f"unknown item_type: {item['item_type']}")

    # Invariant: no final_hypothesis key anywhere
    if _contains_key_recursive(queue, "final_hypothesis"):
        raise ValueError("forbidden key 'final_hypothesis' found in review queue.")

    # Invariant: no accepted_by_user without review_decision.created_by=user
    for item in items:
        if item.get("status") == "accepted_by_user":
            decision = item.get("review_decision")
            if not isinstance(decision, dict) or decision.get("created_by") != "user":
                raise ValueError(
                    f"item {item.get('review_item_id')} has status=accepted_by_user "
                    "without review_decision.created_by=user."
                )

    # Invariant: no active_candidate
    for item in items:
        if item.get("status") == "active_candidate":
            raise ValueError(f"item {item.get('review_item_id')} has forbidden status active_candidate.")

    # Invariant: safety flags must all be false
    flags = queue.get("safety_flags") or {}
    for key, value in flags.items():
        if value is not False:
            raise ValueError(f"safety_flags.{key} must be false, got {value}.")

    # Invariant: review_summary counts must match items
    summary = queue.get("review_summary") or {}
    if summary.get("total_items") != len(items):
        raise ValueError(
            f"review_summary.total_items={summary.get('total_items')} but items count={len(items)}."
        )


def _contains_key_recursive(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_contains_key_recursive(v, target_key) for v in value.values())
    if isinstance(value, list):
        return any(_contains_key_recursive(v, target_key) for v in value)
    return False
