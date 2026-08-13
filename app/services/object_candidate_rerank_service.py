from __future__ import annotations

import re
from collections import Counter
from typing import Any


SYNTHETIC_TITLE_MARKERS = ("mock", "test", "phase")
METHOD_SECTION_TERMS = ("method", "proposed", "approach")
FRONT_MATTER_TERMS = ("front matter", "title page")


def rerank_object_candidates(query: str, object_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [_with_rank_fields(query, candidate) for candidate in object_candidates]
    ranked.sort(
        key=lambda item: (
            item["object_score"],
            item.get("evidence_count", 0),
            _norm(item.get("object_name")),
        ),
        reverse=True,
    )
    return ranked


def _with_rank_fields(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    breakdown, summaries, warnings = _score_candidate(query, candidate)
    score = _clamp(sum(breakdown.values()))
    enriched = dict(candidate)
    enriched["warnings"] = _merge_warnings(candidate.get("warnings", []), warnings)
    enriched["object_score"] = round(score, 3)
    enriched["score_label"] = _score_label(score)
    enriched["score_breakdown"] = {key: round(value, 3) for key, value in breakdown.items()}
    enriched["match_summary"] = summaries[:6]
    enriched["evidence_count"] = len(candidate.get("evidence_refs") or [])
    enriched["top_documents"] = _top_documents(candidate.get("evidence_refs") or [])
    return enriched


def _score_candidate(query: str, candidate: dict[str, Any]) -> tuple[dict[str, float], list[str], list[Any]]:
    q = _norm(query)
    aliases = [str(alias or "") for alias in candidate.get("aliases") or []]
    names = [str(candidate.get("object_name") or ""), str(candidate.get("object_key") or ""), *aliases]
    evidence_refs = list(candidate.get("evidence_refs") or [])
    notes = list(candidate.get("linked_personal_notes") or [])
    warnings = list(candidate.get("warnings") or [])
    summaries: list[str] = []

    alias_score, alias_label = _alias_score(q, names)
    if alias_label:
        summaries.append(alias_label)

    object_type_score, intent_label = _object_type_intent_score(q, str(candidate.get("object_type") or ""))
    if intent_label:
        summaries.append(intent_label)

    evidence_score, evidence_labels, synthetic_warnings = _evidence_score(q, aliases, candidate, evidence_refs)
    summaries.extend(evidence_labels)
    warnings.extend(synthetic_warnings)

    section_score, section_label = _section_score(evidence_refs)
    if section_label:
        summaries.append(section_label)

    note_score, note_label = _note_score(q, aliases, notes)
    if note_label:
        summaries.append(note_label)

    # Source-based bonus: DB object_candidates > rule-derived
    source_score, source_label = _source_bonus(candidate)
    if source_label:
        summaries.append(source_label)

    warning_penalty, warning_label = _warning_penalty(candidate, warnings)
    if warning_label:
        summaries.append(warning_label)

    if not summaries:
        summaries.append("本地只读规则未找到强匹配信号")

    return (
        {
            "alias_direct_match": alias_score,
            "object_type_intent": object_type_score,
            "evidence_support": evidence_score,
            "section_priority": section_score,
            "note_support": note_score,
            "source_priority": source_score,
            "warning_penalty": warning_penalty,
        },
        summaries,
        warnings,
    )


def _alias_score(q: str, names: list[str]) -> tuple[float, str | None]:
    if not q:
        return 0.0, None
    normalized_names = [(raw, _norm(raw)) for raw in names if _norm(raw)]
    for raw, normalized in normalized_names:
        if q == normalized:
            return 0.42, f"别名直接命中：{raw}"
    for raw, normalized in normalized_names:
        if q in normalized or normalized in q:
            return 0.34, f"名称/别名强匹配：{raw}"
    tokens = _tokens(q)
    if tokens and any(token in normalized for token in tokens for _, normalized in normalized_names):
        return 0.14, "查询词与对象名称部分匹配"
    return 0.0, None


def _object_type_intent_score(q: str, object_type: str) -> tuple[float, str | None]:
    object_type = _norm(object_type)
    intent_map = [
        (("指标", "metric", "psnr", "fid"), "metric"),
        (("数据集", "benchmark", "div2k"), "dataset"),
        (("方法", "model", "algorithm", "估计"), "method"),
        (("机制", "原理", "怎么", "为什么", "block", "scaling", "约束"), "mechanism"),
        (("问题", "failure", "limitation", "不合理", "脚滑", "穿地"), "problem"),
    ]
    for terms, intended_type in intent_map:
        if any(term in q for term in terms) and intended_type in object_type:
            return 0.1, f"对象类型匹配：{object_type}"
    return 0.0, None


def _evidence_score(
    q: str,
    aliases: list[str],
    candidate: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
) -> tuple[float, list[str], list[Any]]:
    if not evidence_refs:
        return 0.0, [], ["no_evidence_refs"]

    labels: list[str] = []
    warnings: list[Any] = []
    direct_hits = 0
    role_hits = 0
    synthetic_hits = 0
    normalized_aliases = [_norm(alias) for alias in aliases if _norm(alias)]
    explicit_mock_query = any(term in q for term in ("mock", "test", "phase"))

    for evidence in evidence_refs:
        snippet_text = _norm(evidence.get("snippet"))
        title_text = _norm(evidence.get("document_title"))
        role_text = _norm(evidence.get("evidence_role"))
        haystack = " ".join([snippet_text, title_text])
        if (q and q in haystack) or any(alias and alias in haystack for alias in normalized_aliases):
            direct_hits += 1
        if role_text and role_text in _norm(candidate.get("object_type")):
            role_hits += 1
        if not explicit_mock_query and any(marker in title_text for marker in SYNTHETIC_TITLE_MARKERS):
            synthetic_hits += 1

    count_score = min(0.16, len(evidence_refs) * 0.035)
    direct_score = min(0.1, direct_hits * 0.03)
    role_score = min(0.04, role_hits * 0.015)
    synthetic_penalty = min(0.12, synthetic_hits * 0.04)
    score = max(0.0, count_score + direct_score + role_score - synthetic_penalty)

    labels.append(f"证据支持：{len(evidence_refs)} 条")
    if direct_hits:
        labels.append(f"证据片段直接命中：{direct_hits} 条")
    if role_hits:
        labels.append(f"证据角色匹配：{role_hits} 条")
    if synthetic_hits:
        warnings.append("synthetic_test_document_downranked")
        labels.append("mock/test/phase 文档已降权")
    return score, labels, warnings


def _section_score(evidence_refs: list[dict[str, Any]]) -> tuple[float, str | None]:
    if not evidence_refs:
        return 0.0, None
    method_hits = 0
    front_hits = 0
    for evidence in evidence_refs:
        section = _norm(evidence.get("section_label"))
        if any(term in section for term in METHOD_SECTION_TERMS):
            method_hits += 1
        if any(term in section for term in FRONT_MATTER_TERMS):
            front_hits += 1
    score = max(0.0, min(0.1, method_hits * 0.03) - min(0.06, front_hits * 0.02))
    if method_hits:
        return score, f"方法章节证据：{method_hits} 条"
    if front_hits:
        return score, "Front matter 证据已降权"
    return score, None


def _note_score(q: str, aliases: list[str], notes: list[dict[str, Any]]) -> tuple[float, str | None]:
    if not notes:
        return 0.0, None
    aliases_norm = [_norm(alias) for alias in aliases if _norm(alias)]
    hits = 0
    for note in notes:
        text = _norm(" ".join([str(note.get("title") or ""), str(note.get("short_preview") or "")]))
        if (q and q in text) or any(alias and alias in text for alias in aliases_norm):
            hits += 1
    if hits:
        return min(0.08, hits * 0.035), f"个人笔记匹配：{hits} 条"
    return min(0.03, len(notes) * 0.01), f"关联个人笔记：{len(notes)} 条"


def _warning_penalty(candidate: dict[str, Any], warnings: list[Any]) -> tuple[float, str | None]:
    penalty = 0.0
    if str(candidate.get("confidence") or "").lower() == "low":
        penalty -= 0.05
    for warning in warnings:
        warning_text = _norm(warning)
        if "source_gap_reason" in warning_text or "no_evidence_refs" in warning_text:
            penalty -= 0.14
        elif "synthetic_test_document_downranked" in warning_text:
            penalty -= 0.05
    return max(-0.25, penalty), "低置信度或弱证据已降权" if penalty else None


def _top_documents(evidence_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter()
    titles: dict[Any, str] = {}
    for evidence in evidence_refs:
        document_id = evidence.get("document_id")
        if document_id is None:
            continue
        counts[document_id] += 1
        titles[document_id] = str(evidence.get("document_title") or "")
    ordered = sorted(
        counts.items(),
        key=lambda item: (
            -item[1],
            any(marker in _norm(titles.get(item[0], "")) for marker in SYNTHETIC_TITLE_MARKERS),
            item[0],
        ),
    )
    return [
        {"document_id": document_id, "title": titles.get(document_id, ""), "evidence_count": count}
        for document_id, count in ordered[:3]
    ]


def _merge_warnings(existing: list[Any], extra: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for warning in [*existing, *extra]:
        key = repr(warning)
        if key in seen:
            continue
        merged.append(warning)
        seen.add(key)
    return merged


def _score_label(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.38:
        return "medium"
    return "low"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_\-]+|[\u4e00-\u9fff]{2,}", text)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _source_bonus(candidate: dict[str, Any]) -> tuple[float, str | None]:
    """Score bonus based on source (DB > rule-derived) and mapping quality."""
    source = str(candidate.get("source") or "")
    mapping = str(candidate.get("mapping_status") or "")
    review = str(candidate.get("review_status") or "")

    score = 0.0
    labels = []

    if source == "object_candidates":
        score += 0.12
        labels.append("DB审核对象")
        if review == "accepted":
            score += 0.04
            labels.append("用户已审核通过")
        if mapping == "mapped":
            score += 0.04
        elif mapping == "partial":
            score += 0.01
        elif mapping == "failed":
            score -= 0.05
            labels.append("证据映射失败请人工复核")
    elif source == "derived_from_existing_chunks_notes":
        labels.append("规则派生对象")

    return score, ", ".join(labels) if labels else None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
