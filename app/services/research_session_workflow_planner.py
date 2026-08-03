from __future__ import annotations

from typing import Any

from app.services.research_session_output_validator import validate_research_session_output


ResearchSessionOutput = dict[str, Any]

TAG_BUCKETS = ("topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags")
VALID_EVIDENCE_STRENGTHS = {"strong", "medium", "weak"}

PROBLEM_TYPE_BY_TAG_HINT = {
    "failure": "failure_case",
    "implausibility": "failure_case",
    "mismatch": "metric_mismatch",
    "generalization": "weak_generalization",
    "cost": "implementation_cost",
    "inefficiency": "inefficiency",
    "limitation": "explicit_limitation",
}

MECHANISM_TYPE_BY_TAG_HINT = {
    "constraint": "constraint",
    "architecture": "architecture_pattern",
    "loss": "loss_or_objective",
    "objective": "loss_or_objective",
    "data": "data_organization",
    "metric": "evaluation_driven_design",
    "evaluation": "evaluation_driven_design",
    "failure": "failure_driven_supervision",
}

INSPIRATION_TYPES = {
    "failure-driven",
    "metric-driven",
    "prior-driven",
    "analogy-driven",
    "architecture-driven",
    "data-driven",
    "constraint-driven",
    "efficiency-driven",
}


def plan_research_session_workflow(
    research_goal: str,
    evidence_items: list[dict[str, Any]] | None,
    constraints: dict[str, Any] | None = None,
) -> ResearchSessionOutput:
    """Build a deterministic dry-run Evidence-to-Inspiration workflow output."""
    clean_constraints = dict(constraints or {})
    clean_constraints["dry_run"] = True
    normalized_evidence, evidence_gaps = _normalize_evidence_items(evidence_items or [])
    usable_evidence_ids = [
        item["evidence_id"]
        for item in normalized_evidence
        if not item["is_mock_or_acceptance"] and not item["is_external_context"]
    ]

    tag_view = _aggregate_tags(normalized_evidence, usable_evidence_ids)
    problem_view = _build_problem_view(tag_view, normalized_evidence, usable_evidence_ids)
    mechanism_view = _build_mechanism_view(tag_view, normalized_evidence, problem_view, usable_evidence_ids)
    inspiration_path_view = _build_inspiration_path_view(
        tag_view,
        problem_view,
        mechanism_view,
        usable_evidence_ids,
    )
    candidate_method_view = _build_candidate_method_view(
        problem_view,
        mechanism_view,
        inspiration_path_view,
        normalized_evidence,
        usable_evidence_ids,
    )
    draft_hypothesis_view = _build_draft_hypothesis_view(candidate_method_view)
    critic_view = _build_critic_view(candidate_method_view, clean_constraints)
    experiment_plan_view = _build_experiment_plan_view(candidate_method_view, clean_constraints)
    gaps = _top_level_gaps(
        usable_evidence_ids,
        problem_view,
        mechanism_view,
        candidate_method_view,
        evidence_gaps,
    )

    output: ResearchSessionOutput = {
        "schema_version": "phase14c.in_memory.v1",
        "research_goal": research_goal,
        "constraints": clean_constraints,
        "dry_run": True,
        "evidence_view": {"items": _strip_internal_evidence(normalized_evidence), "gaps": evidence_gaps},
        "tag_aggregation_view": tag_view,
        "problem_view": problem_view,
        "mechanism_view": mechanism_view,
        "inspiration_path_view": inspiration_path_view,
        "candidate_method_view": candidate_method_view,
        "draft_hypothesis_view": draft_hypothesis_view,
        "critic_view": critic_view,
        "experiment_plan_view": experiment_plan_view,
        "readiness": {
            "ready_for_candidate_methods": bool(candidate_method_view["candidates"]),
            "blocking_gaps": gaps,
            "warning_gaps": [],
        },
        "safety_flags": _safety_flags(),
        "gaps": gaps,
    }
    validate_research_session_output(output)
    return output


def _normalize_evidence_items(evidence_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    gaps: list[str] = []
    for index, item in enumerate(evidence_items, start=1):
        evidence_id = str(item.get("evidence_id") or f"ev_{index:03d}")
        item_gaps = list(item.get("gaps") or [])
        evidence_strength = item.get("evidence_strength")
        if evidence_strength not in VALID_EVIDENCE_STRENGTHS:
            evidence_strength = "weak"
            item_gaps.append("evidence_strength_missing_or_invalid")
        if "retrieval_score" in item:
            item_gaps.append("retrieval_score_not_used_as_evidence_strength")
        if not item.get("pdf_page"):
            item_gaps.append("pdf_page_missing")
        is_mock_or_acceptance = bool(item.get("is_mock_or_acceptance", False))
        is_external_context = bool(item.get("is_external_context", False))
        if is_mock_or_acceptance:
            item_gaps.append("mock_or_acceptance_evidence_not_counted_as_real")
        if is_external_context:
            item_gaps.append("external_context_not_counted_as_core_evidence")
        normalized.append(
            {
                "evidence_id": evidence_id,
                "source_type": item.get("source_type") or "chunk",
                "source_trace": dict(item.get("source_trace") or {}),
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "document_title": item.get("document_title"),
                "heading_path": item.get("heading_path"),
                "pdf_path": item.get("pdf_path"),
                "pdf_page": item.get("pdf_page"),
                "pdf_page_end": item.get("pdf_page_end"),
                "zotero_open_url": item.get("zotero_open_url"),
                "source_channels": list(item.get("source_channels") or ["manual"]),
                "evidence_strength": evidence_strength,
                "snippet": _short_snippet(item.get("snippet") or item.get("text") or ""),
                "retrieval_metadata": dict(item.get("retrieval_metadata") or {}),
                "related_notes": list(item.get("related_notes") or []),
                "related_relations": list(item.get("related_relations") or []),
                "is_mock_or_acceptance": is_mock_or_acceptance,
                "is_external_context": is_external_context,
                "gaps": _dedupe(item_gaps),
                "_raw": item,
            }
        )
    if not normalized:
        gaps.append("no_prepared_evidence_items")
    return normalized, gaps


def _strip_internal_evidence(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in evidence_items
    ]


def _aggregate_tags(evidence_items: list[dict[str, Any]], usable_evidence_ids: list[str]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in TAG_BUCKETS}
    seen: set[tuple[str, str]] = set()
    gaps: list[str] = []

    for item in evidence_items:
        if item["evidence_id"] not in usable_evidence_ids:
            continue
        raw = item["_raw"]
        tags = raw.get("tags") or {}
        for bucket in TAG_BUCKETS:
            for tag_value in _tag_values(tags.get(bucket) or raw.get(bucket) or []):
                tag = tag_value["tag"]
                key = (bucket, tag.lower())
                if key in seen:
                    _append_ref(buckets[bucket], tag, item["evidence_id"])
                    continue
                seen.add(key)
                status = tag_value.get("status") or "suggested"
                source = tag_value.get("source") or "workflow_inference"
                if status == "accepted":
                    status = "suggested"
                    gaps.append(f"{bucket}:{tag}:accepted_downgraded_to_suggested")
                buckets[bucket].append(
                    {
                        "tag": tag,
                        "status": status,
                        "evidence_refs": [item["evidence_id"]],
                        "source": source,
                    }
                )
    gaps.extend(f"missing_{bucket}" for bucket in TAG_BUCKETS if not buckets[bucket])
    return {**buckets, "gaps": _dedupe(gaps)}


def _build_problem_view(
    tag_view: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    usable_evidence_ids: list[str],
) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    for tag in tag_view["problem_tags"]:
        problem_id = f"p_{len(problems) + 1:03d}"
        evidence_refs = _usable_refs(tag["evidence_refs"], usable_evidence_ids)
        problems.append(
            {
                "problem_id": problem_id,
                "statement": f"Research goal may involve {tag['tag']}.",
                "problem_type": _problem_type_for(tag["tag"]),
                "evidence_refs": evidence_refs,
                "inferred": True,
                "speculation": not bool(evidence_refs),
                "gaps": [] if evidence_refs else ["problem_tag_without_usable_evidence"],
            }
        )
    for item in evidence_items:
        if item["evidence_id"] not in usable_evidence_ids:
            continue
        for raw_problem in item["_raw"].get("problems") or []:
            problem = _coerce_problem(raw_problem)
            problem["problem_id"] = f"p_{len(problems) + 1:03d}"
            problem["evidence_refs"] = [item["evidence_id"]]
            problems.append(problem)
    gaps = [] if problems else ["no_problem_evidence_or_problem_tags"]
    return {"problems": problems, "gaps": gaps}


def _build_mechanism_view(
    tag_view: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    problem_view: dict[str, Any],
    usable_evidence_ids: list[str],
) -> dict[str, Any]:
    problem_ids = [problem["problem_id"] for problem in problem_view["problems"]]
    mechanisms: list[dict[str, Any]] = []
    for tag in tag_view["mechanism_tags"]:
        evidence_refs = _usable_refs(tag["evidence_refs"], usable_evidence_ids)
        mechanisms.append(
            {
                "mechanism_id": f"m_{len(mechanisms) + 1:03d}",
                "name": tag["tag"],
                "mechanism_type": _mechanism_type_for(tag["tag"]),
                "description": f"Use {tag['tag']} as a reusable mechanism candidate.",
                "evidence_refs": evidence_refs,
                "speculative": not bool(evidence_refs),
                "related_problem_ids": problem_ids[:1],
                "gaps": [] if evidence_refs else ["mechanism_tag_without_usable_evidence"],
            }
        )
    for item in evidence_items:
        if item["evidence_id"] not in usable_evidence_ids:
            continue
        for raw_mechanism in item["_raw"].get("mechanisms") or []:
            mechanism = _coerce_mechanism(raw_mechanism, problem_ids[:1])
            mechanism["mechanism_id"] = f"m_{len(mechanisms) + 1:03d}"
            mechanism["evidence_refs"] = [item["evidence_id"]]
            mechanisms.append(mechanism)
    gaps = [] if mechanisms else ["no_mechanism_evidence_or_mechanism_tags"]
    return {"mechanisms": mechanisms, "gaps": gaps}


def _build_inspiration_path_view(
    tag_view: dict[str, Any],
    problem_view: dict[str, Any],
    mechanism_view: dict[str, Any],
    usable_evidence_ids: list[str],
) -> dict[str, Any]:
    problem_ids = [problem["problem_id"] for problem in problem_view["problems"]]
    mechanism_ids = [mechanism["mechanism_id"] for mechanism in mechanism_view["mechanisms"]]
    paths: list[dict[str, Any]] = []
    for tag in tag_view["inspiration_tags"]:
        evidence_refs = _usable_refs(tag["evidence_refs"], usable_evidence_ids)
        inspiration_type = _normalize_inspiration_type(tag["tag"])
        paths.append(
            {
                "inspiration_path_id": f"ip_{len(paths) + 1:03d}",
                "inspiration_type": inspiration_type,
                "explanation": f"Use a {inspiration_type} path to connect evidence to a draft method.",
                "evidence_refs": evidence_refs,
                "problem_ids": problem_ids[:1],
                "mechanism_ids": mechanism_ids[:1],
                "speculation": not bool(evidence_refs),
                "gaps": [] if evidence_refs else ["inspiration_tag_without_usable_evidence"],
            }
        )
    gaps = [] if paths else ["no_inspiration_path_tags"]
    return {"paths": paths, "gaps": gaps}


def _build_candidate_method_view(
    problem_view: dict[str, Any],
    mechanism_view: dict[str, Any],
    inspiration_path_view: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    usable_evidence_ids: list[str],
) -> dict[str, Any]:
    if not (problem_view["problems"] and mechanism_view["mechanisms"] and usable_evidence_ids):
        return {"candidates": [], "gaps": ["insufficient_problem_mechanism_or_evidence_for_candidate"]}

    problem = problem_view["problems"][0]
    mechanism = mechanism_view["mechanisms"][0]
    evidence_strength = _combined_strength(evidence_items, usable_evidence_ids)
    unsupported_assumptions = _unsupported_claims(evidence_items, usable_evidence_ids)
    if not unsupported_assumptions:
        unsupported_assumptions = ["expected gain is unverified until minimum experiment is run"]
    candidate = {
        "candidate_id": "c_001",
        "status": "draft",
        "title": f"{mechanism['name']} for {problem['statement']}",
        "problem_ids": [problem["problem_id"]],
        "mechanism_ids": [mechanism["mechanism_id"]],
        "inspiration_path_ids": [path["inspiration_path_id"] for path in inspiration_path_view["paths"][:1]],
        "evidence_refs": usable_evidence_ids[:],
        "problem_addressed": problem["statement"],
        "proposed_mechanism": mechanism["description"],
        "expected_gain": "Potential improvement requires validation against the specified metrics and baselines.",
        "unsupported_assumptions": unsupported_assumptions,
        "evidence_strength": evidence_strength,
        "speculation": True,
    }
    return {"candidates": [candidate], "gaps": []}


def _build_draft_hypothesis_view(candidate_method_view: dict[str, Any]) -> dict[str, Any]:
    hypotheses = []
    for index, candidate in enumerate(candidate_method_view["candidates"], start=1):
        hypotheses.append(
            {
                "hypothesis_id": f"h_draft_{index:03d}",
                "status": "draft",
                "source_candidate_id": candidate["candidate_id"],
                "claim": f"{candidate['proposed_mechanism']} may address {candidate['problem_addressed']}",
                "problem": candidate["problem_addressed"],
                "proposed_mechanism": candidate["proposed_mechanism"],
                "evidence_refs": candidate["evidence_refs"],
                "expected_gain": candidate["expected_gain"],
                "unsupported_assumptions": candidate["unsupported_assumptions"],
                "speculation": True,
            }
        )
    gaps = [] if hypotheses else ["no_draft_hypotheses_without_candidate_methods"]
    return {"hypotheses": hypotheses, "gaps": gaps}


def _build_critic_view(candidate_method_view: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    items = []
    for candidate in candidate_method_view["candidates"]:
        evidence_strength = candidate["evidence_strength"]
        weak = evidence_strength == "weak"
        items.append(
            {
                "candidate_id": candidate["candidate_id"],
                "novelty_risk": "high" if candidate["speculation"] else "medium",
                "incrementality_risk": "medium",
                "evidence_strength": evidence_strength,
                "implementation_cost": "medium",
                "critical_questions": [
                    "Is the mechanism materially different from existing baselines?",
                    "Does the candidate improve the target metric without hiding regressions?",
                    "Is the evidence direct enough for the proposed mechanism?",
                ],
                "downgrade_reason": "weak or speculative evidence requires manual review" if weak else "draft candidate still needs validation",
                "unsupported_claims": candidate["unsupported_assumptions"],
                "blocking_gaps": [],
                "warning_gaps": _dedupe(
                    [
                        "dataset_focus_missing" if not constraints.get("dataset_focus") else "",
                        "metric_focus_missing" if not constraints.get("metric_focus") else "",
                    ]
                ),
            }
        )
    gaps = [] if items else ["no_critic_entries_without_candidate_methods"]
    return {"items": items, "gaps": gaps}


def _build_experiment_plan_view(candidate_method_view: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    plans = []
    for candidate in candidate_method_view["candidates"]:
        dataset = constraints.get("dataset_focus") or "dataset must be selected before implementation"
        metric = constraints.get("metric_focus") or "metric must be selected before implementation"
        plans.append(
            {
                "candidate_id": candidate["candidate_id"],
                "baseline": constraints.get("method_family") or "existing baseline for the target domain",
                "proposed_variant": candidate["title"],
                "ablation": "remove the proposed mechanism while keeping other settings fixed",
                "metric": metric,
                "dataset": dataset,
                "parameter_or_flops_control": "report parameter count and FLOPs delta against baseline",
                "visualization_or_failure_case_analysis": "inspect representative failure cases before and after the variant",
                "blocking_gaps": _dedupe(
                    [
                        "dataset_focus_missing" if not constraints.get("dataset_focus") else "",
                        "metric_focus_missing" if not constraints.get("metric_focus") else "",
                    ]
                ),
                "warning_gaps": [],
            }
        )
    gaps = [] if plans else ["no_experiment_plans_without_candidate_methods"]
    return {"plans": plans, "gaps": gaps}


def _safety_flags() -> dict[str, bool]:
    return {
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


def _top_level_gaps(
    usable_evidence_ids: list[str],
    problem_view: dict[str, Any],
    mechanism_view: dict[str, Any],
    candidate_method_view: dict[str, Any],
    evidence_gaps: list[str],
) -> list[str]:
    gaps = list(evidence_gaps)
    if not usable_evidence_ids:
        gaps.append("no_usable_core_evidence")
    if not problem_view["problems"]:
        gaps.append("no_problem_view_items")
    if not mechanism_view["mechanisms"]:
        gaps.append("no_mechanism_view_items")
    if not candidate_method_view["candidates"]:
        gaps.append("candidate_generation_blocked")
    return _dedupe(gaps)


def _tag_values(raw_tags: list[Any]) -> list[dict[str, Any]]:
    values = []
    for raw in raw_tags:
        if isinstance(raw, dict):
            tag = raw.get("tag") or raw.get("name")
            if tag:
                values.append({"tag": str(tag), "status": raw.get("status"), "source": raw.get("source")})
        elif raw:
            values.append({"tag": str(raw), "status": "suggested", "source": "workflow_inference"})
    return values


def _append_ref(bucket_items: list[dict[str, Any]], tag: str, evidence_id: str) -> None:
    for item in bucket_items:
        if item["tag"].lower() == tag.lower() and evidence_id not in item["evidence_refs"]:
            item["evidence_refs"].append(evidence_id)


def _usable_refs(refs: list[str], usable_evidence_ids: list[str]) -> list[str]:
    usable = set(usable_evidence_ids)
    return [ref for ref in refs if ref in usable]


def _problem_type_for(tag: str) -> str:
    lowered = tag.lower()
    for hint, problem_type in PROBLEM_TYPE_BY_TAG_HINT.items():
        if hint in lowered:
            return problem_type
    return "unsolved_problem"


def _mechanism_type_for(tag: str) -> str:
    lowered = tag.lower()
    for hint, mechanism_type in MECHANISM_TYPE_BY_TAG_HINT.items():
        if hint in lowered:
            return mechanism_type
    return "architecture_pattern"


def _normalize_inspiration_type(tag: str) -> str:
    lowered = tag.lower().replace(" idea", "").replace("_", "-")
    for inspiration_type in INSPIRATION_TYPES:
        if inspiration_type in lowered:
            return inspiration_type
    return "analogy-driven"


def _coerce_problem(raw_problem: Any) -> dict[str, Any]:
    if isinstance(raw_problem, dict):
        statement = str(raw_problem.get("statement") or raw_problem.get("problem") or "Unspecified problem")
        return {
            "statement": statement,
            "problem_type": raw_problem.get("problem_type") or "explicit_limitation",
            "evidence_refs": [],
            "inferred": bool(raw_problem.get("inferred", False)),
            "speculation": bool(raw_problem.get("speculation", False)),
            "gaps": list(raw_problem.get("gaps") or []),
        }
    return {
        "statement": str(raw_problem),
        "problem_type": "explicit_limitation",
        "evidence_refs": [],
        "inferred": False,
        "speculation": False,
        "gaps": [],
    }


def _coerce_mechanism(raw_mechanism: Any, related_problem_ids: list[str]) -> dict[str, Any]:
    if isinstance(raw_mechanism, dict):
        name = str(raw_mechanism.get("name") or raw_mechanism.get("mechanism") or "Unspecified mechanism")
        return {
            "name": name,
            "mechanism_type": raw_mechanism.get("mechanism_type") or _mechanism_type_for(name),
            "description": raw_mechanism.get("description") or f"Use {name} as a reusable mechanism candidate.",
            "evidence_refs": [],
            "speculative": bool(raw_mechanism.get("speculative", False)),
            "related_problem_ids": list(raw_mechanism.get("related_problem_ids") or related_problem_ids),
            "gaps": list(raw_mechanism.get("gaps") or []),
        }
    name = str(raw_mechanism)
    return {
        "name": name,
        "mechanism_type": _mechanism_type_for(name),
        "description": f"Use {name} as a reusable mechanism candidate.",
        "evidence_refs": [],
        "speculative": False,
        "related_problem_ids": related_problem_ids,
        "gaps": [],
    }


def _combined_strength(evidence_items: list[dict[str, Any]], evidence_refs: list[str]) -> str:
    rank = {"weak": 0, "medium": 1, "strong": 2}
    strengths = [
        item["evidence_strength"]
        for item in evidence_items
        if item["evidence_id"] in evidence_refs and item["evidence_strength"] in rank
    ]
    if not strengths:
        return "weak"
    return min(strengths, key=lambda value: rank[value])


def _unsupported_claims(evidence_items: list[dict[str, Any]], evidence_refs: list[str]) -> list[str]:
    claims: list[str] = []
    for item in evidence_items:
        if item["evidence_id"] in evidence_refs:
            claims.extend(str(claim) for claim in item["_raw"].get("unsupported_claims") or [])
    return _dedupe(claims)


def _short_snippet(text: str, limit: int = 280) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
