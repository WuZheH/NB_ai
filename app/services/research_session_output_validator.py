from __future__ import annotations

from collections import Counter
from typing import Any


REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "research_goal",
    "constraints",
    "dry_run",
    "evidence_view",
    "tag_aggregation_view",
    "problem_view",
    "mechanism_view",
    "inspiration_path_view",
    "candidate_method_view",
    "draft_hypothesis_view",
    "critic_view",
    "experiment_plan_view",
    "safety_flags",
    "gaps",
}

EXPECTED_SAFETY_FLAGS = {
    "dry_run": True,
    "llm_called": False,
    "api_called": False,
    "external_search_called": False,
    "final_hypothesis_created": False,
    "active_hypothesis_created": False,
    "tested_hypothesis_created": False,
    "long_term_knowledge_written": False,
    "production_db_written": False,
    "note_created": False,
    "relation_created": False,
    "note_evidence_links_written": False,
    "inspiration_card_promoted": False,
    "vector_index_modified": False,
    "chunks_modified": False,
}

FORBIDDEN_OUTPUT_KEYS = {"final_hypothesis"}
FORBIDDEN_CANDIDATE_STATUSES = {
    "accepted",
    "confirmed",
    "user-confirmed",
    "active",
    "tested",
    "final",
    "final_hypothesis",
}


def validate_research_session_output(output: dict[str, Any]) -> None:
    """Validate the Phase 14B machine-checkable Research Session output shape."""
    if not isinstance(output, dict):
        raise ValueError("research session output must be a dict.")

    _validate_required_top_level_fields(output)
    _validate_forbidden_keys(output)
    _validate_safety_flags(output)

    evidence_ids = _evidence_ids(output)
    _validate_all_evidence_refs(output, evidence_ids)

    candidate_ids = _validate_candidates(output)
    _validate_critic_coverage(output, candidate_ids)
    _validate_experiment_plan_coverage(output, candidate_ids)
    _validate_draft_hypotheses(output, candidate_ids)
    _validate_unsupported_claim_boundary(output)


def _validate_required_top_level_fields(output: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_FIELDS - set(output))
    if missing:
        raise ValueError(f"missing top-level fields: {', '.join(missing)}.")


def _validate_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_OUTPUT_KEYS:
                raise ValueError(f"forbidden output key at {path}.{key}: {key}.")
            _validate_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_forbidden_keys(child, f"{path}[{index}]")


def _validate_safety_flags(output: dict[str, Any]) -> None:
    flags = output.get("safety_flags")
    if not isinstance(flags, dict):
        raise ValueError("safety_flags must be a dict.")
    missing = sorted(set(EXPECTED_SAFETY_FLAGS) - set(flags))
    if missing:
        raise ValueError(f"missing safety flags: {', '.join(missing)}.")
    for key, expected in EXPECTED_SAFETY_FLAGS.items():
        actual = flags.get(key)
        if actual is not expected:
            raise ValueError(f"safety_flags.{key} must be {expected!r}.")
    if output.get("dry_run") is not True:
        raise ValueError("top-level dry_run must be true.")


def _evidence_ids(output: dict[str, Any]) -> set[str]:
    items = output.get("evidence_view", {}).get("items", [])
    if not isinstance(items, list):
        raise ValueError("evidence_view.items must be a list.")
    evidence_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("evidence_view.items entries must be dicts.")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("each evidence item must have a non-empty evidence_id.")
        evidence_ids.add(evidence_id)
    return evidence_ids


def _validate_all_evidence_refs(output: dict[str, Any], evidence_ids: set[str]) -> None:
    for path, refs in _iter_evidence_refs(output):
        if not isinstance(refs, list):
            raise ValueError(f"{path} must be a list.")
        for ref in refs:
            if ref not in evidence_ids:
                raise ValueError(f"{path} contains unresolved evidence ref: {ref!r}.")


def _iter_evidence_refs(value: Any, path: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "evidence_refs":
                yield child_path, child
            else:
                yield from _iter_evidence_refs(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_evidence_refs(child, f"{path}[{index}]")


def _validate_candidates(output: dict[str, Any]) -> set[str]:
    candidates = output.get("candidate_method_view", {}).get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidate_method_view.candidates must be a list.")
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("candidate entries must be dicts.")
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("each candidate must have a non-empty candidate_id.")
        if candidate_id in candidate_ids:
            raise ValueError(f"duplicate candidate_id: {candidate_id}.")
        status = candidate.get("status")
        if status in FORBIDDEN_CANDIDATE_STATUSES or status != "draft":
            raise ValueError(f"candidate {candidate_id} has invalid status: {status!r}.")
        candidate_ids.add(candidate_id)
    return candidate_ids


def _validate_critic_coverage(output: dict[str, Any], candidate_ids: set[str]) -> None:
    items = output.get("critic_view", {}).get("items", [])
    if not isinstance(items, list):
        raise ValueError("critic_view.items must be a list.")
    counts = Counter(item.get("candidate_id") for item in items if isinstance(item, dict))
    for candidate_id in candidate_ids:
        if counts[candidate_id] != 1:
            raise ValueError(f"candidate {candidate_id} must have exactly one critic entry.")


def _validate_experiment_plan_coverage(output: dict[str, Any], candidate_ids: set[str]) -> None:
    plans = output.get("experiment_plan_view", {}).get("plans", [])
    if not isinstance(plans, list):
        raise ValueError("experiment_plan_view.plans must be a list.")
    counts = Counter(plan.get("candidate_id") for plan in plans if isinstance(plan, dict))
    for candidate_id in candidate_ids:
        if counts[candidate_id] != 1:
            raise ValueError(f"candidate {candidate_id} must have exactly one experiment plan.")


def _validate_draft_hypotheses(output: dict[str, Any], candidate_ids: set[str]) -> None:
    hypotheses = output.get("draft_hypothesis_view", {}).get("hypotheses", [])
    if not isinstance(hypotheses, list):
        raise ValueError("draft_hypothesis_view.hypotheses must be a list.")
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            raise ValueError("hypothesis entries must be dicts.")
        if hypothesis.get("status") != "draft":
            raise ValueError("draft hypotheses must have status=draft.")
        source_candidate_id = hypothesis.get("source_candidate_id")
        if source_candidate_id not in candidate_ids:
            raise ValueError(f"hypothesis references missing candidate: {source_candidate_id!r}.")


def _validate_unsupported_claim_boundary(output: dict[str, Any]) -> None:
    critic_by_candidate = {
        item.get("candidate_id"): item
        for item in output.get("critic_view", {}).get("items", [])
        if isinstance(item, dict)
    }
    for candidate in output.get("candidate_method_view", {}).get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        unsupported = candidate.get("unsupported_assumptions") or []
        if unsupported and candidate.get("speculation") is not True:
            raise ValueError(f"candidate {candidate.get('candidate_id')} has unsupported assumptions without speculation.")
        critic = critic_by_candidate.get(candidate.get("candidate_id"), {})
        if unsupported and not (critic.get("unsupported_claims") or critic.get("downgrade_reason")):
            raise ValueError(
                f"candidate {candidate.get('candidate_id')} unsupported assumptions must appear in critic output."
            )
    for hypothesis in output.get("draft_hypothesis_view", {}).get("hypotheses", []):
        unsupported = hypothesis.get("unsupported_assumptions") or []
        if unsupported and hypothesis.get("speculation") is not True:
            raise ValueError(
                f"hypothesis {hypothesis.get('hypothesis_id')} has unsupported assumptions without speculation."
            )
