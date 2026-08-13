from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.services.human_review_queue_builder import validate_human_review_queue


def render_human_review_queue_markdown(queue: dict[str, Any]) -> str:
    """Render a validated human_review_queue as a dry-run Markdown report."""
    validate_human_review_queue(queue)
    data = deepcopy(queue)
    lines: list[str] = ["# Human Review Queue Dry-run Report", ""]
    lines.extend(_metadata_section(data))
    lines.extend(_review_summary_section(data))
    lines.extend(_safety_flags_section(data))
    lines.extend(_item_type_overview_section(data))
    lines.extend(_suggested_tag_mapping_section(data))
    lines.extend(_draft_candidate_section(data))
    lines.extend(_draft_hypothesis_section(data))
    lines.extend(_critic_section(data))
    lines.extend(_experiment_plan_section(data))
    lines.extend(_gap_and_unsupported_section(data))
    lines.extend(_appendix_section(data))
    return "\n".join(lines).rstrip() + "\n"


def render_human_review_queue_json(queue: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable wrapper preserving the validated queue unchanged."""
    validate_human_review_queue(queue)
    return {
        "report_type": "human_review_queue",
        "renderer_phase": "phase14n",
        "rendered_at": _utc_now(),
        "human_review_queue": deepcopy(queue),
    }


def summarize_human_review_queue(queue: dict[str, Any]) -> dict[str, Any]:
    validate_human_review_queue(queue)
    items = list(queue.get("items") or [])
    type_counts = _type_counts(items)
    safety_flags = dict(queue.get("safety_flags") or {})
    return {
        "review_queue_id": queue.get("review_queue_id"),
        "research_goal": queue.get("research_goal"),
        "total_items": len(items),
        "pending_count": sum(1 for item in items if item.get("status") == "pending_review"),
        "accepted_by_user_count": sum(1 for item in items if item.get("status") == "accepted_by_user"),
        "active_candidate_count": sum(1 for item in items if item.get("status") == "active_candidate"),
        "suggested_tag_mapping_count": type_counts.get("suggested_tag_mapping", 0),
        "draft_candidate_method_count": type_counts.get("draft_candidate_method", 0),
        "draft_hypothesis_count": type_counts.get("draft_hypothesis", 0),
        "critic_entry_count": type_counts.get("critic_entry", 0),
        "experiment_plan_count": type_counts.get("experiment_plan", 0),
        "unsupported_claim_count": type_counts.get("unsupported_claim", 0),
        "gap_item_count": (
            type_counts.get("evidence_gap", 0)
            + type_counts.get("source_trace_gap", 0)
            + type_counts.get("unmapped_tag", 0)
            + type_counts.get("missing_mechanism", 0)
            + type_counts.get("missing_inspiration", 0)
        ),
        "safety_flags_all_false": all(value is False for value in safety_flags.values()),
        "safety_flags": safety_flags,
    }


def _metadata_section(data: dict[str, Any]) -> list[str]:
    return [
        "## Metadata",
        "",
        f"- review_queue_id: {_value(data.get('review_queue_id'))}",
        f"- research_session_id: {_value(data.get('research_session_id'))}",
        f"- research_goal: {_value(data.get('research_goal'))}",
        f"- source_research_session_output_id: {_value(data.get('source_research_session_output_id'))}",
        f"- status: {_value(data.get('status'))}",
        f"- created_at: {_value(data.get('created_at'))}",
        f"- created_by: {_value(data.get('created_by'))}",
        "- renderer: Phase 14N review queue dry-run renderer",
        "",
    ]


def _review_summary_section(data: dict[str, Any]) -> list[str]:
    summary = data.get("review_summary") or {}
    keys = [
        "total_items",
        "pending_count",
        "accepted_count",
        "rejected_count",
        "edited_count",
        "deferred_count",
        "needs_more_evidence_count",
        "draft_candidate_count",
        "draft_hypothesis_count",
        "critic_count",
        "experiment_plan_count",
        "unsupported_claim_count",
        "unresolved_gap_count",
    ]
    lines = ["## Review Summary", "", "| Field | Value |", "|---|---|"]
    for key in keys:
        lines.append(f"| `{key}` | `{summary.get(key, 0)}` |")
    lines.append("")
    return lines


def _safety_flags_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Safety Flags", "", "| Flag | Value |", "|---|---|"]
    for key, value in sorted((data.get("safety_flags") or {}).items()):
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "All generated review items are dry-run review prompts. They do not accept tags, activate hypotheses, or create final_hypothesis.",
            "",
        ]
    )
    return lines


def _item_type_overview_section(data: dict[str, Any]) -> list[str]:
    counts = _type_counts(data.get("items") or [])
    lines = ["## Item Type Overview", "", "| Item type | Count |", "|---|---:|"]
    for item_type in sorted(counts):
        lines.append(f"| `{item_type}` | {counts[item_type]} |")
    if not counts:
        lines.append("| none | 0 |")
    lines.append("")
    return lines


def _suggested_tag_mapping_section(data: dict[str, Any]) -> list[str]:
    items = _items_of_type(data, "suggested_tag_mapping")
    lines = ["## Suggested Tag Mapping Review Items", ""]
    if not items:
        return [*lines, "- No suggested tag mapping items.", ""]
    for item in items:
        payload = item.get("payload") or {}
        source_tag = payload.get("source_tag") or {}
        lines.extend(
            [
                f"### `{_value(item.get('review_item_id'))}`",
                "",
                f"- source_tag: {_value(source_tag.get('raw'))}",
                f"- target_bucket: {_value(payload.get('target_bucket'))}",
                f"- mapped_name: {_value(payload.get('mapped_name'))}",
                f"- status: {_value(payload.get('status'))}",
                f"- confidence: {_value(payload.get('confidence'))}",
                f"- needs_human_review: {_value(payload.get('needs_human_review'))}",
                f"- mapping_reason: {_short(payload.get('mapping_reason'))}",
                f"- evidence_refs: {_list(item.get('evidence_refs') or [])}",
                f"- risk_flags: {_list(item.get('risk_flags') or [])}",
                f"- allowed_decisions: {_list(item.get('allowed_decisions') or [])}",
                "",
            ]
        )
    return lines


def _draft_candidate_section(data: dict[str, Any]) -> list[str]:
    items = _items_of_type(data, "draft_candidate_method")
    lines = ["## Draft Candidate Review Items", ""]
    if not items:
        return [*lines, "- No draft candidate method items.", ""]
    for item in items:
        payload = item.get("payload") or {}
        lines.extend(
            [
                f"### Candidate `{_value(payload.get('candidate_id'))}`",
                "",
                f"- review_item_id: {_value(item.get('review_item_id'))}",
                f"- status: {_value(item.get('status'))}",
                f"- title: {_short(payload.get('title'))}",
                f"- target_problem: {_short(payload.get('target_problem'))}",
                f"- proposed_mechanism: {_short(payload.get('proposed_mechanism'))}",
                f"- speculation: {_value(payload.get('speculation'))}",
                f"- evidence_refs: {_list(item.get('evidence_refs') or [])}",
                f"- risk_flags: {_list(item.get('risk_flags') or [])}",
                f"- allowed_decisions: {_list(item.get('allowed_decisions') or [])}",
                "",
            ]
        )
    return lines


def _draft_hypothesis_section(data: dict[str, Any]) -> list[str]:
    items = _items_of_type(data, "draft_hypothesis")
    lines = [
        "## Draft Hypothesis Review Items",
        "",
        "Draft hypotheses remain review items only. They are not active hypotheses and not final_hypothesis objects.",
        "",
    ]
    if not items:
        return [*lines, "- No draft hypothesis items.", ""]
    for item in items:
        payload = item.get("payload") or {}
        lines.extend(
            [
                f"### Hypothesis `{_value(payload.get('hypothesis_id'))}`",
                "",
                f"- candidate_id: {_value(payload.get('candidate_id'))}",
                f"- status: {_value(payload.get('status'))}",
                f"- claim: {_short(payload.get('claim'))}",
                f"- unsupported_assumptions: {_list(payload.get('unsupported_assumptions') or [])}",
                f"- evidence_refs: {_list(item.get('evidence_refs') or [])}",
                f"- allowed_decisions: {_list(item.get('allowed_decisions') or [])}",
                "",
            ]
        )
    return lines


def _critic_section(data: dict[str, Any]) -> list[str]:
    items = _items_of_type(data, "critic_entry")
    lines = ["## Critic Review Items", ""]
    if not items:
        return [*lines, "- No critic review items.", ""]
    for item in items:
        payload = item.get("payload") or {}
        lines.extend(
            [
                f"### Critic `{_value(payload.get('critic_id'))}`",
                "",
                f"- novelty_risk: {_value(payload.get('novelty_risk'))}",
                f"- incrementality_risk: {_value(payload.get('incrementality_risk'))}",
                f"- evidence_strength: {_value(payload.get('evidence_strength'))}",
                f"- implementation_cost: {_value(payload.get('implementation_cost'))}",
                f"- reviewer_attack_points: {_list(payload.get('reviewer_attack_points') or [])}",
                f"- allowed_decisions: {_list(item.get('allowed_decisions') or [])}",
                "",
            ]
        )
    return lines


def _experiment_plan_section(data: dict[str, Any]) -> list[str]:
    items = _items_of_type(data, "experiment_plan")
    lines = [
        "## Experiment Plan Review Items",
        "",
        "Experiment plans are draft sketches, not executed experiment results.",
        "",
    ]
    if not items:
        return [*lines, "- No experiment plan items.", ""]
    for item in items:
        payload = item.get("payload") or {}
        lines.extend(
            [
                f"### Experiment Plan `{_value(payload.get('experiment_plan_id'))}`",
                "",
                f"- baseline: {_short(payload.get('baseline'))}",
                f"- variant: {_short(payload.get('variant'))}",
                f"- dataset: {_value(payload.get('dataset'))}",
                f"- metric: {_value(payload.get('metric'))}",
                f"- ablation: {_short(payload.get('ablation'))}",
                f"- parameter_flops_control: {_short(payload.get('parameter_flops_control'))}",
                f"- failure_case_analysis: {_short(payload.get('failure_case_analysis'))}",
                f"- allowed_decisions: {_list(item.get('allowed_decisions') or [])}",
                "",
            ]
        )
    return lines


def _gap_and_unsupported_section(data: dict[str, Any]) -> list[str]:
    gap_types = {"evidence_gap", "source_trace_gap", "unmapped_tag", "missing_mechanism", "missing_inspiration"}
    gap_items = [item for item in data.get("items") or [] if item.get("item_type") in gap_types]
    unsupported = _items_of_type(data, "unsupported_claim")
    lines = ["## Gaps and Unsupported Claims", ""]
    if not gap_items and not unsupported:
        return [*lines, "- No gap or unsupported claim items.", ""]
    for item in gap_items:
        lines.append(
            f"- `{item.get('item_type')}` `{item.get('review_item_id')}`: "
            f"{_short(item.get('summary'))} (evidence_refs={_list(item.get('evidence_refs') or [])})"
        )
    for item in unsupported:
        payload = item.get("payload") or {}
        lines.append(
            f"- `unsupported_claim` `{item.get('review_item_id')}`: "
            f"{_short(payload.get('claim_text'))} (related_candidate_id={_value(payload.get('related_candidate_id'))})"
        )
    lines.append("")
    return lines


def _appendix_section(data: dict[str, Any]) -> list[str]:
    items = data.get("items") or []
    return [
        "## Appendix: Machine-readable IDs",
        "",
        f"- review_item_id: {_list([item.get('review_item_id') for item in items])}",
        f"- source_id: {_list([item.get('source_id') for item in items])}",
        f"- candidate item ids: {_list([item.get('review_item_id') for item in _items_of_type(data, 'draft_candidate_method')])}",
        f"- hypothesis item ids: {_list([item.get('review_item_id') for item in _items_of_type(data, 'draft_hypothesis')])}",
        f"- queue status: {_value(data.get('status'))}",
        "",
    ]


def _items_of_type(data: dict[str, Any], item_type: str) -> list[dict[str, Any]]:
    return [item for item in data.get("items") or [] if item.get("item_type") == item_type]


def _type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("item_type") or "unknown")
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _short(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "none").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _list(values: list[Any]) -> str:
    clean = [str(value) for value in values if value not in (None, "")]
    return ", ".join(clean) if clean else "none"


def _value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
