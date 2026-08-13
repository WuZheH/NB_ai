from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PASS_STATUS = "pass"
WEAK_STATUS = "weak"
FAIL_STATUS = "fail"


DOMAIN_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "super_resolution": {
        "query_terms": (
            "edsr",
            "mdsr",
            "super-resolution",
            "super resolution",
            "single image super-resolution",
            "single image super resolution",
            "srresnet",
            "div2k",
            "psnr",
            "ssim",
            "超分",
            "图像超分",
            "单图像超分",
            "纹理恢复",
            "参数效率",
        ),
        "evidence_terms": (
            "edsr",
            "mdsr",
            "super-resolution",
            "super resolution",
            "single image super-resolution",
            "single image super resolution",
            "srresnet",
            "div2k",
            "ntire",
            "psnr",
            "ssim",
            "residual scaling",
            "batch normalization",
            "upscaling",
            "texture",
            "超分",
            "图像超分",
            "纹理",
        ),
    },
    "text_to_motion": {
        "query_terms": (
            "text-to-motion",
            "text to motion",
            "text2motion",
            "motion generation",
            "human motion",
            "temporal consistency",
            "foot sliding",
            "motion diffusion",
            "humanml3d",
            "kit-ml",
            "文本到动作",
            "文本生成动作",
            "动作生成",
            "足滑",
            "时序一致",
        ),
        "evidence_terms": (
            "text-to-motion",
            "text to motion",
            "text2motion",
            "motion generation",
            "human motion",
            "temporal consistency",
            "foot sliding",
            "motion diffusion",
            "humanml3d",
            "kit-ml",
            "动作生成",
            "文本生成动作",
            "足滑",
            "时序一致",
        ),
    },
    "multimodal_vision_foundation": {
        "query_terms": (
            "multimodal",
            "multi-modal",
            "vision foundation",
            "foundation model",
            "medical image",
            "medical segmentation",
            "sam",
            "medsam",
            "clip",
            "多模态",
            "视觉基础模型",
            "基础模型",
            "医学图像",
            "医学分割",
            "小样本",
        ),
        "evidence_terms": (
            "multimodal",
            "multi-modal",
            "vision foundation",
            "foundation model",
            "medical image",
            "medical segmentation",
            "sam",
            "medsam",
            "clip",
            "segment anything",
            "few-shot",
            "多模态",
            "视觉基础模型",
            "医学图像",
            "医学分割",
            "小样本",
        ),
    },
}


@dataclass(frozen=True)
class CandidateVerification:
    hypothesis_id: str
    verification_status: str
    evidence_support_score: float
    evidence_coverage: dict[str, object]
    unsupported_claims: list[str]
    missing_evidence: list[str]
    risk_flags: list[str]
    minimum_validation_experiment_check: str
    downgrade_to_next_action: bool
    verifier_notes: list[str]


@dataclass(frozen=True)
class VerificationReport:
    candidate_verifications: list[CandidateVerification]
    verified_candidate_hypothesis_drafts: list[dict[str, Any]]
    downgraded_candidates: list[dict[str, Any]]
    critic_summary: dict[str, object]


def verify_candidate_hypothesis_drafts(
    candidate_hypothesis_drafts: list[dict[str, Any]],
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
    evidence_readiness: dict[str, Any],
    excluded_evidence: list[dict[str, Any]],
    hygiene_warnings: list[str],
    external_candidate_queries: list[str] | None = None,
    research_question: str | None = None,
) -> VerificationReport:
    external_queries = list(external_candidate_queries or [])
    verifications: list[CandidateVerification] = []
    verified: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []

    for candidate in candidate_hypothesis_drafts:
        verification = _verify_one_candidate(
            candidate=candidate,
            evidence_summary=evidence_summary,
            related_notes=related_notes,
            related_tags=related_tags,
            related_relations=related_relations,
            evidence_readiness=evidence_readiness,
            excluded_evidence=excluded_evidence,
            hygiene_warnings=hygiene_warnings,
            external_candidate_queries=external_queries,
            research_question=research_question,
        )
        verifications.append(verification)
        if verification.verification_status == PASS_STATUS:
            verified.append(candidate)
        else:
            downgraded.append(
                {
                    "hypothesis_id": candidate.get("hypothesis_id", ""),
                    "verification_status": verification.verification_status,
                    "suggested_next_action": _downgrade_action(candidate, verification),
                    "risk_flags": list(verification.risk_flags),
                    "missing_evidence": list(verification.missing_evidence),
                }
            )

    return VerificationReport(
        candidate_verifications=verifications,
        verified_candidate_hypothesis_drafts=verified,
        downgraded_candidates=downgraded,
        critic_summary=_build_critic_summary(verifications, hygiene_warnings),
    )


def serialize_verification_report(report: VerificationReport) -> dict[str, object]:
    return {
        "candidate_verifications": [_serialize_verification(item) for item in report.candidate_verifications],
        "verified_candidate_hypothesis_drafts": list(report.verified_candidate_hypothesis_drafts),
        "downgraded_candidates": list(report.downgraded_candidates),
        "critic_summary": dict(report.critic_summary),
    }


def _verify_one_candidate(
    candidate: dict[str, Any],
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_tags: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
    evidence_readiness: dict[str, Any],
    excluded_evidence: list[dict[str, Any]],
    hygiene_warnings: list[str],
    external_candidate_queries: list[str],
    research_question: str | None,
) -> CandidateVerification:
    hypothesis_id = str(candidate.get("hypothesis_id") or "")
    evidence_ids = _int_set(candidate.get("supporting_evidence_ids") or [])
    note_ids = _int_set(candidate.get("supporting_note_ids") or [])
    relation_ids = _int_set(candidate.get("supporting_relation_ids") or [])
    valid_evidence_ids = _int_set(item.get("chunk_id") for item in evidence_summary)
    valid_note_ids = _int_set(item.get("note_id") for item in related_notes)
    valid_relation_ids = _int_set(item.get("relation_id") for item in related_relations)
    excluded_chunk_ids = _int_set(item.get("chunk_id") for item in excluded_evidence)
    excluded_note_ids = _int_set(item.get("note_id") for item in excluded_evidence)
    excluded_relation_ids = _int_set(item.get("relation_id") for item in excluded_evidence)

    unsupported_claims: list[str] = []
    missing_evidence: list[str] = []
    risk_flags: list[str] = []
    verifier_notes: list[str] = []
    hard_fail = False

    if not bool(evidence_readiness.get("ready_for_hypothesis_dry_run")):
        hard_fail = True
        risk_flags.append("evidence_readiness_blocked")
        verifier_notes.append("Evidence readiness is blocked; candidates cannot be accepted.")

    if not evidence_ids and not note_ids:
        hard_fail = True
        missing_evidence.append("candidate has no supporting_evidence_ids or supporting_note_ids.")

    if _uses_external_queries(candidate, external_candidate_queries):
        hard_fail = True
        unsupported_claims.append("external_candidate_queries are used as evidence.")

    if evidence_ids & excluded_chunk_ids or note_ids & excluded_note_ids or relation_ids & excluded_relation_ids:
        hard_fail = True
        unsupported_claims.append("candidate references excluded mock/test/acceptance evidence.")

    missing_valid_evidence = sorted(evidence_ids - valid_evidence_ids)
    missing_valid_notes = sorted(note_ids - valid_note_ids)
    missing_valid_relations = sorted(relation_ids - valid_relation_ids)
    if missing_valid_evidence:
        hard_fail = True
        missing_evidence.append(f"supporting_evidence_ids not found in evidence_summary: {missing_valid_evidence}")
    if missing_valid_notes:
        hard_fail = True
        missing_evidence.append(f"supporting_note_ids not found in related_notes: {missing_valid_notes}")
    if missing_valid_relations:
        risk_flags.append(f"supporting_relation_ids not found in related_relations: {missing_valid_relations}")

    experiment_check = _check_minimum_validation_experiment(candidate.get("minimum_validation_experiment"))
    if experiment_check == FAIL_STATUS:
        hard_fail = True
        risk_flags.append("minimum_validation_experiment_missing_or_too_generic")
    elif experiment_check == WEAK_STATUS:
        risk_flags.append("minimum_validation_experiment_weak")

    risks = candidate.get("risks") or []
    if not risks:
        risk_flags.append("risks_missing")

    confidence_level = str(candidate.get("confidence_level") or "low").lower()
    evidence_support_score = _score_support(
        evidence_ids=evidence_ids,
        note_ids=note_ids,
        relation_ids=relation_ids,
        valid_evidence_ids=valid_evidence_ids,
        valid_note_ids=valid_note_ids,
        valid_relation_ids=valid_relation_ids,
        related_tags=related_tags,
        experiment_check=experiment_check,
        has_risks=bool(risks),
        hard_fail=hard_fail,
    )
    if confidence_level == "high" and evidence_support_score < 0.85:
        risk_flags.append("confidence_level_too_high_for_evidence_support")
    if confidence_level == "medium" and evidence_support_score < 0.5:
        risk_flags.append("confidence_level_too_high_for_evidence_support")

    domain_alignment = _check_domain_alignment(
        research_question=research_question,
        candidate=candidate,
        evidence_summary=evidence_summary,
        related_notes=related_notes,
        related_relations=related_relations,
    )
    if domain_alignment["query_domain"] and domain_alignment["checked_support_count"] > 0:
        if domain_alignment["aligned_support_count"] == 0:
            risk_flags.append("domain_alignment_mismatch")
            verifier_notes.append(
                "Supporting evidence is traceable but does not match the query domain; it cannot support a verified candidate."
            )

    status = _status_from_findings(
        hard_fail=hard_fail,
        evidence_support_score=evidence_support_score,
        risk_flags=risk_flags,
        unsupported_claims=unsupported_claims,
    )
    if status == PASS_STATUS:
        verifier_notes.append("Candidate has traceable local evidence and basic validation/risk fields.")
    elif status == WEAK_STATUS:
        verifier_notes.append("Candidate remains usable only as a weak draft and needs more evidence or clearer validation.")
    else:
        verifier_notes.append("Candidate should be downgraded to a suggested next action.")

    return CandidateVerification(
        hypothesis_id=hypothesis_id,
        verification_status=status,
        evidence_support_score=evidence_support_score,
        evidence_coverage={
            "supporting_evidence_count": len(evidence_ids),
            "supporting_note_count": len(note_ids),
            "supporting_relation_count": len(relation_ids),
            "related_tag_count": len(related_tags),
            "hygiene_warning_count": len(hygiene_warnings),
            "query_domain": domain_alignment["query_domain"],
            "domain_checked_support_count": domain_alignment["checked_support_count"],
            "domain_aligned_support_count": domain_alignment["aligned_support_count"],
        },
        unsupported_claims=unsupported_claims,
        missing_evidence=missing_evidence,
        risk_flags=risk_flags,
        minimum_validation_experiment_check=experiment_check,
        downgrade_to_next_action=status != PASS_STATUS,
        verifier_notes=verifier_notes,
    )


def _check_minimum_validation_experiment(value: object) -> str:
    text = " ".join(str(value or "").split()).lower()
    if not text:
        return FAIL_STATUS
    if len(text) < 24:
        return FAIL_STATUS
    generic_terms = {"validate", "validation", "experiment", "test", "验证", "实验", "检查"}
    tokens = set(text.replace("/", " ").replace("-", " ").split())
    if len(tokens - generic_terms) <= 2:
        return WEAK_STATUS
    if not any(term in text for term in ["ablation", "sanity", "dataset", "metric", "failure", "消融", "指标", "数据集"]):
        return WEAK_STATUS
    return PASS_STATUS


def _uses_external_queries(candidate: dict[str, Any], external_candidate_queries: list[str]) -> bool:
    if candidate.get("supporting_external_candidate_queries"):
        return True
    text_parts = [
        str(candidate.get("core_idea") or ""),
        str(candidate.get("target_problem") or ""),
        str(candidate.get("expected_difference_from_existing_methods") or ""),
        str(candidate.get("minimum_validation_experiment") or ""),
    ]
    joined = "\n".join(text_parts).lower()
    for query in external_candidate_queries:
        normalized = str(query or "").strip().lower()
        if normalized and normalized in joined:
            return True
    return False


def _check_domain_alignment(
    research_question: str | None,
    candidate: dict[str, Any],
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
) -> dict[str, object]:
    query_text = research_question or _candidate_text(candidate)
    query_domain = _infer_domain(query_text, term_type="query_terms")
    if not query_domain:
        return {
            "query_domain": None,
            "checked_support_count": 0,
            "aligned_support_count": 0,
        }

    support_texts = _collect_support_texts(
        candidate=candidate,
        evidence_summary=evidence_summary,
        related_notes=related_notes,
        related_relations=related_relations,
    )
    aligned_count = sum(1 for text in support_texts if _text_matches_domain(text, query_domain, "evidence_terms"))
    return {
        "query_domain": query_domain,
        "checked_support_count": len(support_texts),
        "aligned_support_count": aligned_count,
    }


def _collect_support_texts(
    candidate: dict[str, Any],
    evidence_summary: list[dict[str, Any]],
    related_notes: list[dict[str, Any]],
    related_relations: list[dict[str, Any]],
) -> list[str]:
    evidence_ids = _int_set(candidate.get("supporting_evidence_ids") or [])
    note_ids = _int_set(candidate.get("supporting_note_ids") or [])
    relation_ids = _int_set(candidate.get("supporting_relation_ids") or [])
    texts: list[str] = []

    for evidence in evidence_summary:
        chunk_id = _safe_int(evidence.get("chunk_id"))
        if chunk_id is not None and chunk_id in evidence_ids:
            texts.append(
                " ".join(
                    str(part or "")
                    for part in [
                        evidence.get("document_title"),
                        evidence.get("title"),
                        evidence.get("document_type"),
                        evidence.get("heading_path"),
                        evidence.get("snippet"),
                        " ".join(str(tag) for tag in evidence.get("tags") or []),
                        " ".join(str(term) for term in evidence.get("matched_terms") or []),
                    ]
                )
            )

    for note in related_notes:
        note_id = _safe_int(note.get("note_id"))
        if note_id is not None and note_id in note_ids:
            texts.append(
                " ".join(
                    str(part or "")
                    for part in [
                        note.get("title"),
                        note.get("note_type"),
                        note.get("source_path"),
                        note.get("snippet"),
                        " ".join(str(tag) for tag in note.get("note_tags") or []),
                    ]
                )
            )

    for relation in related_relations:
        relation_id = _safe_int(relation.get("relation_id"))
        if relation_id is not None and relation_id in relation_ids:
            texts.append(
                " ".join(
                    str(part or "")
                    for part in [
                        relation.get("relation_type"),
                        relation.get("description"),
                        relation.get("evidence_document_title"),
                        relation.get("evidence_heading_path"),
                    ]
                )
            )

    return [" ".join(text.split()) for text in texts if text and text.split()]


def _infer_domain(text: str, term_type: str) -> str | None:
    for domain, profile in DOMAIN_PROFILES.items():
        if _contains_any(text, profile[term_type]):
            return domain
    return None


def _text_matches_domain(text: str, domain: str, term_type: str) -> bool:
    profile = DOMAIN_PROFILES.get(domain)
    if not profile:
        return False
    return _contains_any(text, profile[term_type])


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "")
        for key in [
            "core_idea",
            "target_problem",
            "expected_difference_from_existing_methods",
            "minimum_validation_experiment",
        ]
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _score_support(
    evidence_ids: set[int],
    note_ids: set[int],
    relation_ids: set[int],
    valid_evidence_ids: set[int],
    valid_note_ids: set[int],
    valid_relation_ids: set[int],
    related_tags: list[dict[str, Any]],
    experiment_check: str,
    has_risks: bool,
    hard_fail: bool,
) -> float:
    if hard_fail:
        return 0.0
    score = 0.0
    if evidence_ids and evidence_ids <= valid_evidence_ids:
        score += 0.35
    if note_ids and note_ids <= valid_note_ids:
        score += 0.2
    if relation_ids and relation_ids <= valid_relation_ids:
        score += 0.15
    if related_tags:
        score += 0.1
    if experiment_check == PASS_STATUS:
        score += 0.1
    elif experiment_check == WEAK_STATUS:
        score += 0.04
    if has_risks:
        score += 0.1
    return round(min(score, 1.0), 4)


def _status_from_findings(
    hard_fail: bool,
    evidence_support_score: float,
    risk_flags: list[str],
    unsupported_claims: list[str],
) -> str:
    if hard_fail or unsupported_claims:
        return FAIL_STATUS
    if risk_flags or evidence_support_score < 0.75:
        return WEAK_STATUS
    return PASS_STATUS


def _downgrade_action(candidate: dict[str, Any], verification: CandidateVerification) -> str:
    hypothesis_id = candidate.get("hypothesis_id") or "candidate"
    reasons = verification.missing_evidence or verification.risk_flags or verification.unsupported_claims
    reason_text = "; ".join(str(reason) for reason in reasons[:3]) if reasons else "需要补充证据后再审查。"
    return f"将 {hypothesis_id} 降级为待办：补充证据或验证设计后再进入候选草稿。原因：{reason_text}"


def _build_critic_summary(
    verifications: list[CandidateVerification],
    hygiene_warnings: list[str],
) -> dict[str, object]:
    pass_count = sum(1 for item in verifications if item.verification_status == PASS_STATUS)
    weak_count = sum(1 for item in verifications if item.verification_status == WEAK_STATUS)
    fail_count = sum(1 for item in verifications if item.verification_status == FAIL_STATUS)
    return {
        "total_candidates": len(verifications),
        "pass_count": pass_count,
        "weak_count": weak_count,
        "fail_count": fail_count,
        "downgraded_count": weak_count + fail_count,
        "hygiene_warning_count": len(hygiene_warnings),
        "final_hypothesis_allowed": False,
    }


def _serialize_verification(verification: CandidateVerification) -> dict[str, object]:
    return {
        "hypothesis_id": verification.hypothesis_id,
        "verification_status": verification.verification_status,
        "evidence_support_score": verification.evidence_support_score,
        "evidence_coverage": dict(verification.evidence_coverage),
        "unsupported_claims": list(verification.unsupported_claims),
        "missing_evidence": list(verification.missing_evidence),
        "risk_flags": list(verification.risk_flags),
        "minimum_validation_experiment_check": verification.minimum_validation_experiment_check,
        "downgrade_to_next_action": verification.downgrade_to_next_action,
        "verifier_notes": list(verification.verifier_notes),
    }


def _int_set(values: object) -> set[int]:
    result: set[int] = set()
    if values is None:
        return result
    if isinstance(values, (str, bytes, dict)):
        iterable = [values]
    else:
        try:
            iterable = iter(values)
        except TypeError:
            iterable = iter([values])
    for value in iterable:
        try:
            if value is not None:
                result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
