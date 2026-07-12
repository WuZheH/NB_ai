from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.rerank_service import RERANK_HEURISTIC
from app.services.research_session_service import (
    ResearchSessionDryRunReport,
    build_research_session_sections,
    run_research_session_dry_run,
)
from app.services.research_copilot_verifier_service import (
    serialize_verification_report,
    verify_candidate_hypothesis_drafts,
)


DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class CandidateHypothesisDraft:
    hypothesis_id: str
    core_idea: str
    target_problem: str
    supporting_evidence_ids: list[int]
    supporting_note_ids: list[int]
    supporting_relation_ids: list[int]
    expected_difference_from_existing_methods: str
    minimum_validation_experiment: str
    risks: list[str]
    missing_evidence: list[str]
    confidence_level: str


@dataclass(frozen=True)
class ResearchCopilotDryRunReport:
    research_question: str
    top_k: int
    dry_run: bool
    status: str
    verify: bool
    multi_candidate: bool
    llm_called: bool
    api_called: bool
    external_search_called: bool
    external_llm_called: bool
    final_hypothesis_generated: bool
    final_hypothesis: None
    research_session_report: ResearchSessionDryRunReport
    evidence_summary: list[dict[str, Any]]
    related_notes: list[dict[str, Any]]
    related_tags: list[dict[str, Any]]
    related_relations: list[dict[str, Any]]
    evidence_readiness: dict[str, Any]
    external_candidate_section: dict[str, Any]
    external_candidate_queries: list[str]
    candidate_hypothesis_drafts: list[CandidateHypothesisDraft]
    candidate_verifications: list[dict[str, Any]]
    verified_candidate_hypothesis_drafts: list[dict[str, Any]]
    downgraded_candidates: list[dict[str, Any]]
    critic_summary: dict[str, object]
    human_review_queue: list[dict[str, Any]]
    blocking_gaps: list[str]
    warning_gaps: list[str]
    suggested_next_actions: list[str]
    excluded_evidence: list[dict[str, Any]]
    hygiene_warnings: list[str]


def run_research_copilot_dry_run(
    research_question: str,
    top_k: int = DEFAULT_TOP_K,
    dry_run: bool = True,
    rerank: str = RERANK_HEURISTIC,
    verify: bool = False,
    multi_candidate: bool = False,
) -> ResearchCopilotDryRunReport:
    normalized_question = research_question.strip()
    if not normalized_question:
        raise ValueError("research question must not be empty.")
    if not dry_run:
        raise ValueError("Phase 10B.0 only supports --dry-run. Controlled Research Copilot generation is not enabled.")

    safe_top_k = max(1, top_k)
    effective_verify = bool(verify or multi_candidate)
    session_report = run_research_session_dry_run(
        normalized_question,
        top_k=safe_top_k,
        dry_run=True,
        rerank=rerank,
    )
    sections = build_research_session_sections(session_report, evidence_limit=safe_top_k)
    readiness = sections["readiness_judgement"]
    ready = bool(readiness["ready_for_hypothesis_dry_run"])
    drafts: list[CandidateHypothesisDraft] = []
    if ready:
        drafts = _build_candidate_drafts(
            research_question=normalized_question,
            evidence_summary=sections["evidence_summary"],
            related_notes=sections["related_notes"],
            related_tags=sections["related_tags"],
            related_relations=sections["related_relations"],
            warning_gaps=readiness["warning_gaps"],
            multi_candidate=multi_candidate,
        )
    status = "ready" if drafts else "blocked"
    if ready and not drafts:
        warning_gaps = list(readiness["warning_gaps"])
        warning_gaps.append("证据未绑定真实 evidence chunk 或 note，不能生成 candidate_hypothesis_drafts。")
    else:
        warning_gaps = list(readiness["warning_gaps"])
    verifier_sections = _build_verifier_sections(
        research_question=normalized_question,
        verify=effective_verify,
        drafts=drafts,
        evidence_summary=sections["evidence_summary"],
        related_notes=sections["related_notes"],
        related_tags=sections["related_tags"],
        related_relations=sections["related_relations"],
        evidence_readiness=readiness,
        excluded_evidence=sections["excluded_evidence"],
        hygiene_warnings=sections["hygiene_warnings"],
        external_candidate_queries=list(sections["external_candidate_section"]["candidate_queries"]),
    )
    human_review_queue = (
        _build_human_review_queue(
            drafts=drafts,
            candidate_verifications=verifier_sections["candidate_verifications"],
            verify=effective_verify,
        )
        if multi_candidate
        else []
    )

    return ResearchCopilotDryRunReport(
        research_question=normalized_question,
        top_k=safe_top_k,
        dry_run=True,
        status=status,
        verify=effective_verify,
        multi_candidate=multi_candidate,
        llm_called=False,
        api_called=False,
        external_search_called=False,
        external_llm_called=False,
        final_hypothesis_generated=False,
        final_hypothesis=None,
        research_session_report=session_report,
        evidence_summary=sections["evidence_summary"],
        related_notes=sections["related_notes"],
        related_tags=sections["related_tags"],
        related_relations=sections["related_relations"],
        evidence_readiness=readiness,
        external_candidate_section=sections["external_candidate_section"],
        external_candidate_queries=list(sections["external_candidate_section"]["candidate_queries"]),
        candidate_hypothesis_drafts=drafts,
        candidate_verifications=verifier_sections["candidate_verifications"],
        verified_candidate_hypothesis_drafts=verifier_sections["verified_candidate_hypothesis_drafts"],
        downgraded_candidates=verifier_sections["downgraded_candidates"],
        critic_summary=verifier_sections["critic_summary"],
        human_review_queue=human_review_queue,
        blocking_gaps=list(readiness["blocking_gaps"]),
        warning_gaps=warning_gaps,
        suggested_next_actions=list(sections["suggested_next_actions"]),
        excluded_evidence=list(sections["excluded_evidence"]),
        hygiene_warnings=list(sections["hygiene_warnings"]),
    )


def build_research_copilot_sections(report: ResearchCopilotDryRunReport) -> dict[str, Any]:
    return {
        "question": {
            "research_question": report.research_question,
            "top_k": report.top_k,
            "dry_run": report.dry_run,
        },
        "status": report.status,
        "verify": report.verify,
        "multi_candidate": report.multi_candidate,
        "evidence_readiness": dict(report.evidence_readiness),
        "evidence_summary": list(report.evidence_summary),
        "related_notes": list(report.related_notes),
        "related_tags": list(report.related_tags),
        "related_relations": list(report.related_relations),
        "excluded_evidence": list(report.excluded_evidence),
        "hygiene_warnings": list(report.hygiene_warnings),
        "candidate_hypothesis_drafts": [_serialize_candidate(candidate) for candidate in report.candidate_hypothesis_drafts],
        "candidate_verifications": list(report.candidate_verifications),
        "verified_candidate_hypothesis_drafts": list(report.verified_candidate_hypothesis_drafts),
        "downgraded_candidates": list(report.downgraded_candidates),
        "critic_summary": dict(report.critic_summary),
        "human_review_queue": list(report.human_review_queue),
        "final_hypothesis": report.final_hypothesis,
        "blocking_gaps": list(report.blocking_gaps),
        "warning_gaps": list(report.warning_gaps),
        "suggested_next_actions": list(report.suggested_next_actions),
        "external_candidate_section": dict(report.external_candidate_section),
        "external_candidate_queries": list(report.external_candidate_queries),
        "safety_flags": {
            "dry_run": report.dry_run,
            "llm_called": report.llm_called,
            "api_called": report.api_called,
            "external_search_called": report.external_search_called,
            "external_llm_called": report.external_llm_called,
            "final_hypothesis_generated": report.final_hypothesis_generated,
        },
    }


def _build_verifier_sections(
    research_question: str,
    verify: bool,
    drafts: list[CandidateHypothesisDraft],
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
    evidence_readiness: dict[str, Any],
    excluded_evidence: list[dict[str, Any]],
    hygiene_warnings: list[str],
    external_candidate_queries: list[str],
) -> dict[str, Any]:
    if not verify:
        return {
            "candidate_verifications": [],
            "verified_candidate_hypothesis_drafts": [],
            "downgraded_candidates": [],
            "critic_summary": {
                "enabled": False,
                "total_candidates": 0,
                "pass_count": 0,
                "weak_count": 0,
                "fail_count": 0,
                "downgraded_count": 0,
                "final_hypothesis_allowed": False,
            },
        }
    serialized_drafts = [_serialize_candidate(candidate) for candidate in drafts]
    report = verify_candidate_hypothesis_drafts(
        candidate_hypothesis_drafts=serialized_drafts,
        evidence_summary=evidence_summary,
        related_notes=related_notes,
        related_tags=related_tags,
        related_relations=related_relations,
        evidence_readiness=evidence_readiness,
        excluded_evidence=excluded_evidence,
        hygiene_warnings=hygiene_warnings,
        external_candidate_queries=external_candidate_queries,
        research_question=research_question,
    )
    sections = serialize_verification_report(report)
    sections["critic_summary"]["enabled"] = True
    return sections


def _build_candidate_drafts(
    research_question: str,
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
    warning_gaps: list[str],
    multi_candidate: bool = False,
) -> list[CandidateHypothesisDraft]:
    if multi_candidate:
        return _build_multi_candidate_drafts(
            research_question=research_question,
            evidence_summary=evidence_summary,
            related_notes=related_notes,
            related_tags=related_tags,
            related_relations=related_relations,
            warning_gaps=warning_gaps,
        )

    evidence_ids = [int(item["chunk_id"]) for item in evidence_summary[:1] if item.get("chunk_id") is not None]
    note_ids = [int(item["note_id"]) for item in related_notes if item.get("note_id") is not None]
    relation_ids = [
        int(item["relation_id"])
        for item in related_relations
        if item.get("relation_id") is not None
    ]
    if not evidence_ids and not note_ids:
        return []

    primary_evidence = evidence_summary[0] if evidence_summary else {}
    target_problem = _derive_target_problem(research_question, primary_evidence)
    confidence_level = _confidence_level(evidence_summary, related_notes, related_relations)
    missing_evidence = _missing_evidence(warning_gaps)
    return [
        CandidateHypothesisDraft(
            hypothesis_id="draft-1",
            core_idea=(
                f"围绕“{research_question}”，基于已读库中可回溯证据，形成一个需要继续人工验证的候选研究假设草稿。"
            ),
            target_problem=target_problem,
            supporting_evidence_ids=evidence_ids[:5],
            supporting_note_ids=note_ids[:5],
            supporting_relation_ids=relation_ids[:5],
            expected_difference_from_existing_methods=(
                "该草稿只指出可能的差异化方向，差异是否成立必须继续回到 evidence chunk、note 和 relation 人工核查。"
            ),
            minimum_validation_experiment=(
                "选取已读证据覆盖的数据集、指标或失败案例，设计一个最小 ablation / sanity-check 实验，比较问题维度是否被改善。"
            ),
            risks=[
                "当前输出是候选草稿，不是最终创新点。",
                "证据可能仍缺少跨论文对照、消融或失败案例。",
                "外部候选 query 仅是待读建议，不能作为支持证据。",
            ],
            missing_evidence=missing_evidence,
            confidence_level=confidence_level,
        )
    ]


def _build_multi_candidate_drafts(
    research_question: str,
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
    warning_gaps: list[str],
) -> list[CandidateHypothesisDraft]:
    drafts: list[CandidateHypothesisDraft] = []
    seen_support_keys: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    note_clusters = _notes_by_chunk_id(related_notes)
    relation_clusters = _relations_by_evidence_or_note(related_relations)

    for index, evidence in enumerate(evidence_summary, start=1):
        chunk_id = _safe_int(evidence.get("chunk_id"))
        if chunk_id is None:
            continue
        cluster_notes = note_clusters.get(chunk_id, [])
        note_ids = _note_ids(cluster_notes)
        cluster_relations = relation_clusters["by_chunk"].get(chunk_id, [])
        if note_ids:
            for note_id in note_ids:
                cluster_relations.extend(relation_clusters["by_note"].get(note_id, []))
        relation_ids = _relation_ids(cluster_relations)
        support_key = ((chunk_id,), tuple(note_ids))
        if support_key in seen_support_keys:
            continue
        seen_support_keys.add(support_key)
        drafts.append(
            _build_candidate_from_cluster(
                hypothesis_id=f"draft-evidence-{len(drafts) + 1}",
                research_question=research_question,
                evidence_items=[evidence],
                note_items=cluster_notes,
                relation_items=cluster_relations,
                related_tags=related_tags,
                warning_gaps=warning_gaps,
                split_basis="evidence_cluster",
                evidence_ids=[chunk_id],
                note_ids=note_ids,
                relation_ids=relation_ids,
            )
        )

    for note in related_notes:
        note_id = _safe_int(note.get("note_id"))
        if note_id is None:
            continue
        linked_chunk_ids = _int_list(note.get("linked_chunk_ids") or [])
        evidence_items = [
            item
            for item in evidence_summary
            if _safe_int(item.get("chunk_id")) in linked_chunk_ids
        ]
        evidence_ids = [_safe_int(item.get("chunk_id")) for item in evidence_items]
        evidence_ids = [item for item in evidence_ids if item is not None]
        support_key = (tuple(evidence_ids), (note_id,))
        if support_key in seen_support_keys:
            continue
        if not evidence_ids and not note_id:
            continue
        cluster_relations = list(relation_clusters["by_note"].get(note_id, []))
        for chunk_id in evidence_ids:
            cluster_relations.extend(relation_clusters["by_chunk"].get(chunk_id, []))
        drafts.append(
            _build_candidate_from_cluster(
                hypothesis_id=f"draft-note-{len(drafts) + 1}",
                research_question=research_question,
                evidence_items=evidence_items,
                note_items=[note],
                relation_items=cluster_relations,
                related_tags=related_tags,
                warning_gaps=warning_gaps,
                split_basis="related_note_cluster",
                evidence_ids=evidence_ids,
                note_ids=[note_id],
                relation_ids=_relation_ids(cluster_relations),
            )
        )

    return drafts[:5]


def _build_candidate_from_cluster(
    hypothesis_id: str,
    research_question: str,
    evidence_items: list[dict[str, Any]],
    note_items: list[dict[str, Any]],
    relation_items: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    warning_gaps: list[str],
    split_basis: str,
    evidence_ids: list[int],
    note_ids: list[int],
    relation_ids: list[int],
) -> CandidateHypothesisDraft:
    primary_evidence = evidence_items[0] if evidence_items else {}
    target_problem = _derive_target_problem(research_question, primary_evidence)
    cluster_label = _cluster_label(primary_evidence, note_items, relation_items, related_tags)
    confidence_level = _confidence_level(evidence_items, note_items, relation_items)
    missing_evidence = _missing_evidence(warning_gaps)
    return CandidateHypothesisDraft(
        hypothesis_id=hypothesis_id,
        core_idea=(
            f"围绕“{research_question}”的 {split_basis}，形成一个待总指挥人工审阅的候选研究假设草稿：{cluster_label}。"
        ),
        target_problem=target_problem,
        supporting_evidence_ids=evidence_ids[:5],
        supporting_note_ids=note_ids[:5],
        supporting_relation_ids=relation_ids[:5],
        expected_difference_from_existing_methods=(
            "该候选只基于当前 cluster 的本地证据提出差异化方向，必须由人工回看 evidence chunk、note 和 relation 后决定保留或降级。"
        ),
        minimum_validation_experiment=(
            f"围绕 {cluster_label} 选择一个已读证据覆盖的数据集、指标或 failure case，做最小 ablation / sanity-check，"
            "验证该问题维度是否相对现有方法有可观测改善。"
        ),
        risks=[
            "当前输出是候选草稿，不是最终创新点。",
            "该 cluster 可能只覆盖局部证据，仍需人工比较其他论文和实验日志。",
            "外部候选 query 仅是待读建议，不能作为支持证据。",
        ],
        missing_evidence=missing_evidence,
        confidence_level=confidence_level,
    )


def _notes_by_chunk_id(related_notes: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    mapping: dict[int, list[dict[str, Any]]] = {}
    for note in related_notes:
        for chunk_id in _int_list(note.get("linked_chunk_ids") or []):
            mapping.setdefault(chunk_id, []).append(note)
    return mapping


def _relations_by_evidence_or_note(
    related_relations: list[dict[str, Any]],
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    by_chunk: dict[int, list[dict[str, Any]]] = {}
    by_note: dict[int, list[dict[str, Any]]] = {}
    for relation in related_relations:
        chunk_id = _safe_int(relation.get("evidence_chunk_id"))
        note_id = _safe_int(relation.get("note_id"))
        if chunk_id is not None:
            by_chunk.setdefault(chunk_id, []).append(relation)
        if note_id is not None:
            by_note.setdefault(note_id, []).append(relation)
    return {"by_chunk": by_chunk, "by_note": by_note}


def _cluster_label(
    primary_evidence: dict[str, Any],
    note_items: list[dict[str, Any]],
    relation_items: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
) -> str:
    candidates: list[str] = []
    candidates.extend(str(term) for term in (primary_evidence.get("matched_terms") or [])[:3])
    candidates.extend(str(tag.get("name")) for tag in related_tags[:2] if tag.get("name"))
    candidates.extend(str(note.get("title")) for note in note_items[:1] if note.get("title"))
    candidates.extend(str(relation.get("relation_type")) for relation in relation_items[:1] if relation.get("relation_type"))
    deduped: list[str] = []
    for item in candidates:
        normalized = " ".join(item.split())
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return " / ".join(deduped[:4]) if deduped else "当前 evidence cluster"


def _note_ids(notes: list[dict[str, Any]]) -> list[int]:
    return _dedupe_ints(_safe_int(note.get("note_id")) for note in notes)


def _relation_ids(relations: list[dict[str, Any]]) -> list[int]:
    return _dedupe_ints(_safe_int(relation.get("relation_id")) for relation in relations)


def _int_list(values: object) -> list[int]:
    return _dedupe_ints(_safe_int(value) for value in values)


def _dedupe_ints(values: object) -> list[int]:
    deduped: list[int] = []
    for value in values:
        if value is None:
            continue
        if value not in deduped:
            deduped.append(value)
    return deduped


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_human_review_queue(
    drafts: list[CandidateHypothesisDraft],
    candidate_verifications: list[dict[str, Any]],
    verify: bool,
) -> list[dict[str, Any]]:
    verification_by_id = {
        str(item.get("hypothesis_id")): item
        for item in candidate_verifications
        if item.get("hypothesis_id") is not None
    }
    queue: list[dict[str, Any]] = []
    for index, draft in enumerate(drafts, start=1):
        verification = verification_by_id.get(draft.hypothesis_id, {})
        status = str(verification.get("verification_status") or ("pending" if not verify else "fail"))
        queue.append(
            {
                "review_id": f"review-{index}",
                "hypothesis_id": draft.hypothesis_id,
                "review_status": "pending",
                "recommended_action": _recommended_review_action(status),
                "review_reason": _review_reason(status, verification),
                "required_human_checks": _required_human_checks(status),
                "evidence_to_inspect": {
                    "supporting_evidence_ids": list(draft.supporting_evidence_ids),
                    "supporting_note_ids": list(draft.supporting_note_ids),
                    "supporting_relation_ids": list(draft.supporting_relation_ids),
                },
            }
        )
    return queue


def _recommended_review_action(verification_status: str) -> str:
    if verification_status == "pass":
        return "keep"
    if verification_status == "weak":
        return "revise"
    if verification_status == "fail":
        return "discard"
    return "downgrade"


def _review_reason(verification_status: str, verification: dict[str, Any]) -> str:
    notes = verification.get("verifier_notes") or []
    if notes:
        return str(notes[0])
    if verification_status == "pass":
        return "Verifier 通过，但仍需总指挥人工复核证据和实验边界。"
    if verification_status == "weak":
        return "Verifier 判断为弱候选，需要补证据、补风险或补最小验证实验。"
    if verification_status == "fail":
        return "Verifier 拒绝该候选，应丢弃或退回为待读/补证据待办。"
    return "尚未完成 verifier 审查，不能直接保留为候选。"


def _required_human_checks(verification_status: str) -> list[str]:
    checks = [
        "回看 supporting_evidence_ids 对应 chunk，确认问题、方法和局限表述真实存在。",
        "检查 supporting_note_ids 是否包含个人理解，而不是测试或占位内容。",
        "确认 minimum_validation_experiment 是否可执行且不会被误读为最终创新点。",
    ]
    if verification_status != "pass":
        checks.append("根据 verifier 的 missing_evidence / risk_flags 决定 revise、downgrade 或 discard。")
    return checks


def _derive_target_problem(research_question: str, primary_evidence: dict[str, Any]) -> str:
    matched_terms = primary_evidence.get("matched_terms") or []
    if matched_terms:
        return f"与查询命中词相关的问题维度：{', '.join(str(term) for term in matched_terms[:5])}"
    return f"研究问题中的待验证问题：{research_question}"


def _confidence_level(
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
) -> str:
    if evidence_summary and related_notes and related_relations:
        return "medium"
    return "low"


def _missing_evidence(warning_gaps: list[str]) -> list[str]:
    if warning_gaps:
        return list(warning_gaps)
    return ["仍需人工补充跨论文对照、局限章节和最小验证实验记录。"]


def _serialize_candidate(candidate: CandidateHypothesisDraft) -> dict[str, Any]:
    return {
        "hypothesis_id": candidate.hypothesis_id,
        "core_idea": candidate.core_idea,
        "target_problem": candidate.target_problem,
        "supporting_evidence_ids": list(candidate.supporting_evidence_ids),
        "supporting_note_ids": list(candidate.supporting_note_ids),
        "supporting_relation_ids": list(candidate.supporting_relation_ids),
        "expected_difference_from_existing_methods": candidate.expected_difference_from_existing_methods,
        "minimum_validation_experiment": candidate.minimum_validation_experiment,
        "risks": list(candidate.risks),
        "missing_evidence": list(candidate.missing_evidence),
        "confidence_level": candidate.confidence_level,
    }
