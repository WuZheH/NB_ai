from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.services.research_session_output_validator import validate_research_session_output


SNIPPET_LIMIT = 220


def render_research_session_markdown(output: dict[str, Any]) -> str:
    """Render a validated ResearchSessionOutput as a human-readable Markdown report."""
    validate_research_session_output(output)
    data = deepcopy(output)
    lines: list[str] = ["# Research Session Report", ""]
    lines.extend(_metadata_section(data))
    lines.extend(_readiness_section(data))
    lines.extend(_safety_flags_section(data))
    lines.extend(_evidence_view_section(data))
    lines.extend(_tag_aggregation_section(data))
    lines.extend(_problem_view_section(data))
    lines.extend(_mechanism_view_section(data))
    lines.extend(_inspiration_path_view_section(data))
    lines.extend(_candidate_method_view_section(data))
    lines.extend(_draft_hypothesis_view_section(data))
    lines.extend(_critic_view_section(data))
    lines.extend(_experiment_plan_view_section(data))
    lines.extend(_gaps_and_next_actions_section(data))
    lines.extend(_machine_readable_ids_section(data))
    return "\n".join(lines).rstrip() + "\n"


def render_research_session_json(output: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable wrapper that preserves the validated output unchanged."""
    validate_research_session_output(output)
    return {
        "report_type": "research_session",
        "renderer_phase": "phase14e",
        "rendered_at": _utc_now(),
        "research_session_output": deepcopy(output),
    }


def _metadata_section(data: dict[str, Any]) -> list[str]:
    metadata = data.get("metadata") or {}
    return [
        "## Metadata",
        "",
        f"- research_session_id: {_value(metadata.get('research_session_id'))}",
        f"- research_goal: {_value(data.get('research_goal'))}",
        f"- status: {_value(metadata.get('status') or 'dry-run')}",
        f"- generated_at: {_value(metadata.get('generated_at'))}",
        f"- schema_version: {_value(data.get('schema_version'))}",
        "- planner: Phase 14C in-memory workflow planner",
        "- renderer: Phase 14E report renderer",
        "",
    ]


def _readiness_section(data: dict[str, Any]) -> list[str]:
    readiness = data.get("readiness") or {}
    candidates = data.get("candidate_method_view", {}).get("candidates", [])
    hypotheses = data.get("draft_hypothesis_view", {}).get("hypotheses", [])
    evidence = data.get("evidence_view", {}).get("items", [])
    return [
        "## Readiness Summary",
        "",
        f"- ready_for_candidate_methods: {_value(readiness.get('ready_for_candidate_methods', False))}",
        f"- blocking_gaps: {_list(readiness.get('blocking_gaps') or data.get('gaps') or [])}",
        f"- warning_gaps: {_list(readiness.get('warning_gaps') or [])}",
        f"- evidence count: {len(evidence)}",
        f"- candidate count: {len(candidates)}",
        f"- draft hypothesis count: {len(hypotheses)}",
        "",
    ]


def _safety_flags_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Safety Flags", "", "| Flag | Value |", "|---|---|"]
    for key, value in sorted((data.get("safety_flags") or {}).items()):
        lines.append(f"| `{key}` | `{value}` |")
    lines.append("")
    return lines


def _evidence_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Evidence View", ""]
    items = data.get("evidence_view", {}).get("items", [])
    if not items:
        return [*lines, "- No evidence items.", ""]
    for item in items:
        lines.extend(
            [
                f"### Evidence `{_value(item.get('evidence_id'))}`",
                "",
                f"- source_type: {_value(item.get('source_type'))}",
                f"- document: {_value(item.get('document_title'))} (`document_id={_value(item.get('document_id'))}`)",
                f"- chunk_id: {_value(item.get('chunk_id'))}",
                f"- heading_path: {_value(item.get('heading_path'))}",
                f"- PDF page: {_value(item.get('pdf_page'))}",
                f"- Zotero link: {_value(item.get('zotero_open_url'))}",
                f"- source_channels: {_list(item.get('source_channels') or [])}",
                f"- retrieval_metadata: {_metadata_summary(item.get('retrieval_metadata') or {})}",
                f"- gaps / hygiene warnings: {_list(item.get('gaps') or [])}",
                f"- snippet: {_quote(_short_snippet(item.get('snippet') or ''))}",
                "",
            ]
        )
    return lines


def _tag_aggregation_section(data: dict[str, Any]) -> list[str]:
    tag_view = data.get("tag_aggregation_view") or {}
    lines = ["## Tag Aggregation View", ""]
    for bucket in ("topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"):
        lines.extend([f"### {bucket}", ""])
        tags = tag_view.get(bucket) or []
        if not tags:
            lines.extend(["- None.", ""])
            continue
        for tag in tags:
            lines.append(
                f"- {_value(tag.get('tag'))} "
                f"(status={_value(tag.get('status'))}, evidence_refs={_list(tag.get('evidence_refs') or [])})"
            )
        lines.append("")
    lines.extend([f"- tag gaps: {_list(tag_view.get('gaps') or [])}", ""])
    return lines


def _problem_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Problem View", ""]
    problems = data.get("problem_view", {}).get("problems", [])
    if not problems:
        return [*lines, "- No problem view items.", ""]
    for problem in problems:
        lines.extend(
            [
                f"### Problem `{_value(problem.get('problem_id'))}`",
                "",
                f"- statement: {_value(problem.get('statement'))}",
                f"- evidence_refs: {_list(problem.get('evidence_refs') or [])}",
                f"- speculation: {_value(problem.get('speculation'))}",
                f"- gaps: {_list(problem.get('gaps') or [])}",
                "",
            ]
        )
    return lines


def _mechanism_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Mechanism View", ""]
    mechanisms = data.get("mechanism_view", {}).get("mechanisms", [])
    if not mechanisms:
        return [*lines, "- No mechanism view items.", ""]
    for mechanism in mechanisms:
        lines.extend(
            [
                f"### Mechanism `{_value(mechanism.get('mechanism_id'))}`",
                "",
                f"- name: {_value(mechanism.get('name'))}",
                f"- description: {_value(mechanism.get('description'))}",
                f"- evidence_refs: {_list(mechanism.get('evidence_refs') or [])}",
                f"- speculation: {_value(mechanism.get('speculative'))}",
                f"- gaps: {_list(mechanism.get('gaps') or [])}",
                "",
            ]
        )
    return lines


def _inspiration_path_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Inspiration Path View", ""]
    paths = data.get("inspiration_path_view", {}).get("paths", [])
    if not paths:
        return [*lines, "- No inspiration path items.", ""]
    for path in paths:
        lines.extend(
            [
                f"### Inspiration Path `{_value(path.get('inspiration_path_id'))}`",
                "",
                f"- path type: {_value(path.get('inspiration_type'))}",
                f"- description: {_value(path.get('explanation'))}",
                f"- evidence_refs: {_list(path.get('evidence_refs') or [])}",
                f"- speculation: {_value(path.get('speculation'))}",
                f"- gaps: {_list(path.get('gaps') or [])}",
                "",
            ]
        )
    return lines


def _candidate_method_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Candidate Method View", ""]
    candidates = data.get("candidate_method_view", {}).get("candidates", [])
    plans = {plan.get("candidate_id"): plan for plan in data.get("experiment_plan_view", {}).get("plans", [])}
    critics = {item.get("candidate_id"): item for item in data.get("critic_view", {}).get("items", [])}
    if not candidates:
        return [*lines, "- No draft candidate methods.", ""]
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        plan = plans.get(candidate_id, {})
        critic = critics.get(candidate_id, {})
        lines.extend(
            [
                f"### Candidate `{_value(candidate_id)}`",
                "",
                f"- status: {_value(candidate.get('status'))}",
                f"- target problem: {_value(candidate.get('problem_addressed'))}",
                f"- proposed mechanism: {_value(candidate.get('proposed_mechanism'))}",
                f"- inspiration path: {_list(candidate.get('inspiration_path_ids') or [])}",
                f"- baseline: {_value(plan.get('baseline'))}",
                f"- minimum implementation idea: {_value(plan.get('proposed_variant'))}",
                f"- expected gain: {_value(candidate.get('expected_gain'))}",
                f"- evidence_refs: {_list(candidate.get('evidence_refs') or [])}",
                f"- speculation: {_value(candidate.get('speculation'))}",
                f"- failure risks: {_list((critic.get('unsupported_claims') or []) + (critic.get('warning_gaps') or []))}",
                "",
            ]
        )
    return lines


def _draft_hypothesis_view_section(data: dict[str, Any]) -> list[str]:
    lines = [
        "## Draft Hypothesis View",
        "",
        "These are draft hypotheses for review only; they are not final_hypothesis objects.",
        "",
    ]
    hypotheses = data.get("draft_hypothesis_view", {}).get("hypotheses", [])
    if not hypotheses:
        return [*lines, "- No draft hypotheses.", ""]
    critic_by_candidate = {
        item.get("candidate_id"): item
        for item in data.get("critic_view", {}).get("items", [])
    }
    for hypothesis in hypotheses:
        critic = critic_by_candidate.get(hypothesis.get("source_candidate_id"), {})
        lines.extend(
            [
                f"### Draft Hypothesis `{_value(hypothesis.get('hypothesis_id'))}`",
                "",
                f"- candidate_id: {_value(hypothesis.get('source_candidate_id'))}",
                f"- status: {_value(hypothesis.get('status'))}",
                f"- claim: {_value(hypothesis.get('claim'))}",
                f"- evidence_refs: {_list(hypothesis.get('evidence_refs') or [])}",
                f"- unsupported assumptions: {_list(hypothesis.get('unsupported_assumptions') or [])}",
                f"- unsupported claims: {_list(critic.get('unsupported_claims') or [])}",
                f"- speculation: {_value(hypothesis.get('speculation'))}",
                "",
            ]
        )
    return lines


def _critic_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Critic View", ""]
    items = data.get("critic_view", {}).get("items", [])
    if not items:
        return [*lines, "- No critic entries.", ""]
    for item in items:
        lines.extend(
            [
                f"### Critic `{_value(item.get('candidate_id'))}`",
                "",
                f"- novelty risk: {_value(item.get('novelty_risk'))}",
                f"- incrementality risk: {_value(item.get('incrementality_risk'))}",
                f"- evidence strength / limitation: {_value(item.get('evidence_strength'))}; {_value(item.get('downgrade_reason'))}",
                f"- implementation cost: {_value(item.get('implementation_cost'))}",
                f"- module stacking risk: {_value('review required')}",
                f"- reviewer attack points: {_list(item.get('critical_questions') or [])}",
                f"- recommendation: {_recommendation(item)}",
                "",
            ]
        )
    return lines


def _experiment_plan_view_section(data: dict[str, Any]) -> list[str]:
    lines = ["## Experiment Plan View", ""]
    plans = data.get("experiment_plan_view", {}).get("plans", [])
    if not plans:
        return [*lines, "- No experiment plans.", ""]
    for plan in plans:
        lines.extend(
            [
                f"### Experiment Plan `{_value(plan.get('candidate_id'))}`",
                "",
                f"- baseline: {_value(plan.get('baseline'))}",
                f"- variant: {_value(plan.get('proposed_variant'))}",
                f"- dataset: {_value(plan.get('dataset'))}",
                f"- metric: {_value(plan.get('metric'))}",
                f"- ablation: {_value(plan.get('ablation'))}",
                f"- parameter / FLOPs control: {_value(plan.get('parameter_or_flops_control'))}",
                f"- failure case analysis: {_value(plan.get('visualization_or_failure_case_analysis'))}",
                f"- expected positive signal: improvement on target metric without worsening key failure cases",
                f"- negative result interpretation: candidate mechanism may be insufficient or evidence too weak",
                f"- gaps: {_list((plan.get('blocking_gaps') or []) + (plan.get('warning_gaps') or []))}",
                "",
            ]
        )
    return lines


def _gaps_and_next_actions_section(data: dict[str, Any]) -> list[str]:
    gaps = _collect_gaps(data)
    unsupported = _collect_unsupported(data)
    actions = [
        "review evidence gaps",
        "add missing four-layer tags where appropriate",
        "verify unsupported assumptions manually",
        "define dataset and metric before implementation",
    ]
    return [
        "## Gaps and Next Actions",
        "",
        f"- evidence / tag / source gaps: {_list(gaps)}",
        f"- unsupported assumptions / claims: {_list(unsupported)}",
        f"- recommended next actions: {_list(actions)}",
        "",
    ]


def _machine_readable_ids_section(data: dict[str, Any]) -> list[str]:
    evidence_ids = [item.get("evidence_id") for item in data.get("evidence_view", {}).get("items", [])]
    problem_ids = [item.get("problem_id") for item in data.get("problem_view", {}).get("problems", [])]
    mechanism_ids = [item.get("mechanism_id") for item in data.get("mechanism_view", {}).get("mechanisms", [])]
    path_ids = [item.get("inspiration_path_id") for item in data.get("inspiration_path_view", {}).get("paths", [])]
    candidate_ids = [item.get("candidate_id") for item in data.get("candidate_method_view", {}).get("candidates", [])]
    hypothesis_ids = [item.get("hypothesis_id") for item in data.get("draft_hypothesis_view", {}).get("hypotheses", [])]
    return [
        "## Appendix: Machine-readable IDs",
        "",
        f"- evidence_id: {_list(evidence_ids)}",
        f"- problem_id: {_list(problem_ids)}",
        f"- mechanism_id: {_list(mechanism_ids)}",
        f"- inspiration_path_id: {_list(path_ids)}",
        f"- candidate_id: {_list(candidate_ids)}",
        f"- hypothesis_id: {_list(hypothesis_ids)}",
        f"- critic_id: {_list(candidate_ids)}",
        f"- experiment_plan_id: {_list(candidate_ids)}",
        "",
    ]


def _collect_gaps(data: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for key in (
        "evidence_view",
        "tag_aggregation_view",
        "problem_view",
        "mechanism_view",
        "inspiration_path_view",
        "candidate_method_view",
        "draft_hypothesis_view",
        "critic_view",
        "experiment_plan_view",
    ):
        gaps.extend(data.get(key, {}).get("gaps") or [])
    gaps.extend(data.get("gaps") or [])
    for item in data.get("evidence_view", {}).get("items", []):
        gaps.extend(item.get("gaps") or [])
    return _dedupe(gaps)


def _collect_unsupported(data: dict[str, Any]) -> list[str]:
    unsupported: list[str] = []
    for candidate in data.get("candidate_method_view", {}).get("candidates", []):
        unsupported.extend(candidate.get("unsupported_assumptions") or [])
    for item in data.get("critic_view", {}).get("items", []):
        unsupported.extend(item.get("unsupported_claims") or [])
    for hypothesis in data.get("draft_hypothesis_view", {}).get("hypotheses", []):
        unsupported.extend(hypothesis.get("unsupported_assumptions") or [])
    return _dedupe(unsupported)


def _metadata_summary(metadata: dict[str, Any]) -> str:
    if not metadata:
        return "none"
    parts = []
    for key in ("fusion_score", "rerank_score", "retrieval_score", "matched_terms"):
        if key in metadata:
            parts.append(f"{key}={metadata[key]}")
    return ", ".join(parts) if parts else "available"


def _recommendation(item: dict[str, Any]) -> str:
    if item.get("blocking_gaps"):
        return "block until gaps are resolved"
    if item.get("evidence_strength") == "weak":
        return "manual review before further design"
    return "safe for dry-run discussion only"


def _short_snippet(text: str) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= SNIPPET_LIMIT:
        return compact
    return compact[: SNIPPET_LIMIT - 3].rstrip() + "..."


def _quote(text: str) -> str:
    return f"> {text}" if text else "none"


def _list(values: list[Any]) -> str:
    clean = [str(value) for value in values if value not in (None, "")]
    return ", ".join(clean) if clean else "none"


def _value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
