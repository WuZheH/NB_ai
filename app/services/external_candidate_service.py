from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.services.evidence_hygiene_service import is_mock_or_test_text


DEFAULT_MAX_CANDIDATE_QUERIES = 5
PHASE10A_DEGRADED_REASON = "Phase 10A.0 only designs external paper candidate queries; real external search is disabled."
PHASE10A_SAFETY_NOTE = (
    "External candidates are reading suggestions only. They are not evidence, not stored in the core library, "
    "and cannot be used for hypothesis generation until the user reads and imports them through the formal pipeline."
)


class TagLike(Protocol):
    name: str
    tag_type: str
    description: str | None


class RelationLike(Protocol):
    relation_type: str
    description: str | None


@dataclass(frozen=True)
class ExternalCandidateReport:
    external_candidate_enabled: bool
    external_search_called: bool
    candidate_queries: list[str]
    candidate_reasons: list[str]
    degraded_reason: str
    safety_note: str


def build_external_candidate_report(
    research_question: str,
    evidence_gaps: list[str],
    related_tags: list[TagLike],
    related_relations: list[RelationLike],
    suggested_next_actions: list[str],
    max_queries: int = DEFAULT_MAX_CANDIDATE_QUERIES,
) -> ExternalCandidateReport:
    normalized_question = research_question.strip()
    if not normalized_question:
        raise ValueError("research question must not be empty.")

    context_text = _join_context(
        normalized_question,
        _drop_mock_context(evidence_gaps),
        _drop_mock_context([tag.name for tag in related_tags]),
        _drop_mock_context([tag.description or "" for tag in related_tags]),
        _drop_mock_context([relation.relation_type for relation in related_relations]),
        _drop_mock_context([relation.description or "" for relation in related_relations]),
        _drop_mock_context(suggested_next_actions),
    )
    candidate_pairs = _build_candidate_pairs(normalized_question, context_text)
    safe_limit = max(1, max_queries)
    selected_pairs = candidate_pairs[:safe_limit]
    return ExternalCandidateReport(
        external_candidate_enabled=False,
        external_search_called=False,
        candidate_queries=[query for query, _ in selected_pairs],
        candidate_reasons=[reason for _, reason in selected_pairs],
        degraded_reason=PHASE10A_DEGRADED_REASON,
        safety_note=PHASE10A_SAFETY_NOTE,
    )


def _build_candidate_pairs(research_question: str, context_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    lowered = context_text.lower()

    if _has_any(lowered, ["edsr", "super-resolution", "超分", "图像超分辨率", "参数效率"]):
        pairs.append(
            (
                "super-resolution real degradation texture restoration limitation",
                "内部证据涉及超分、纹理恢复或局限，但仍需要外部待读材料确认真实退化和纹理恢复缺口。",
            )
        )
        pairs.append(
            (
                "EDSR lightweight super-resolution parameter efficiency ablation",
                "问题涉及 EDSR 或参数效率，适合补充轻量化和 ablation 方向的待读候选。",
            )
        )

    if _has_any(lowered, ["text-to-motion", "humanml3d", "motion", "foot sliding", "temporal", "动作"]):
        pairs.append(
            (
                "text-to-motion foot sliding temporal consistency ablation",
                "内部证据涉及 text-to-motion 或动作质量，需要补充 foot sliding 与 temporal consistency 的待读候选。",
            )
        )
        pairs.append(
            (
                "HumanML3D FID motion quality failure case",
                "问题涉及 HumanML3D、FID 或 motion quality，适合补充数据集和失败案例相关待读候选。",
            )
        )

    if _has_any(lowered, ["limitation", "failure", "ablation", "局限", "失败", "消融", "证据缺口"]):
        pairs.append(
            (
                "method limitation failure case ablation evidence gap",
                "当前 evidence_gaps 指向局限、失败案例或消融证据不足，需要外部待读候选补齐。",
            )
        )

    if _has_any(lowered, ["dataset", "metric", "psnr", "ssim", "fid", "数据集", "指标"]):
        pairs.append(
            (
                "dataset metric benchmark ablation evaluation protocol",
                "当前问题涉及数据集或指标，需要补充 benchmark 与 evaluation protocol 相关待读候选。",
            )
        )

    if _has_any(lowered, ["tag", "relation", "标签", "关系", "knowledge"]):
        pairs.append(
            (
                "method problem relation evidence graph literature survey",
                "当前证据链需要补充方法-问题-证据关系，适合查找综述或关系清晰的待读候选。",
            )
        )

    extracted_terms = _extract_terms(research_question)
    if extracted_terms:
        query = " ".join(extracted_terms[:6] + ["limitation", "ablation", "evidence"])
        pairs.append((query, "根据 research_question 提取关键词，生成一个不联网的外部检索查询建议。"))

    if not pairs:
        pairs.append(
            (
                "research question limitation ablation evidence gap",
                "内部证据不足但方向不够具体，先生成通用的待读检索查询建议。",
            )
        )

    return _dedupe_pairs(pairs)


def _drop_mock_context(items: list[str]) -> list[str]:
    return [item for item in items if item and not is_mock_or_test_text(item)]


def _join_context(*groups: object) -> str:
    parts: list[str] = []
    for group in groups:
        if isinstance(group, list):
            parts.extend(str(item or "") for item in group)
        else:
            parts.append(str(group or ""))
    return "\n".join(parts)


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _extract_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,}", text)
    terms: list[str] = []
    for term in raw_terms:
        cleaned = term.strip()
        if len(cleaned) < 2 or cleaned.lower() in {"phase", "query", "evidence"}:
            continue
        if cleaned not in terms:
            terms.append(cleaned)
    return terms


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    deduped: list[tuple[str, str]] = []
    seen_queries: set[str] = set()
    for query, reason in pairs:
        normalized_query = " ".join(query.split())
        if normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        deduped.append((normalized_query, reason))
    return deduped
