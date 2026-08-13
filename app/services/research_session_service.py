from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.core.external_api_config import DEFAULT_EXTERNAL_API_CONFIG, ExternalApiConfig
from app.services.evidence_hygiene_service import (
    EvidenceHygieneIssue,
    build_hygiene_issue,
    match_mock_or_test_markers,
    serialize_hygiene_issue,
)
from app.services.external_candidate_service import ExternalCandidateReport, build_external_candidate_report
from app.services.hypothesis_service import (
    DIMENSION_KEYWORDS,
    DryRunNoteCandidate,
    DryRunTagCandidate,
    HypothesisDryRunReport,
    LIMITATION_EVIDENCE_KEYWORDS,
    generate_hypothesis_dry_run,
)
from app.services.keyword_search_service import KeywordSearchResult
from app.services.rerank_service import RERANK_HEURISTIC
from app.services.relation_service import RelationResult
from app.services.retrieval_fusion_service import FusedRetrievalResult, RetrievalFusionReport, search_retrieval


DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class RetrievalQualitySummary:
    total_results: int
    high_confidence_count: int
    evidence_backed_count: int
    tag_or_relation_supported_count: int
    vector_index_available: bool
    degraded_reason: str | None


@dataclass(frozen=True)
class EvidenceReadiness:
    ready_for_hypothesis_dry_run: bool
    blocking_gaps: list[str]
    warning_gaps: list[str]


@dataclass(frozen=True)
class ResearchSessionDryRunReport:
    research_question: str
    top_k: int
    dry_run: bool
    llm_called: bool
    api_called: bool
    external_api_enabled: bool
    external_search_called: bool
    external_rerank_called: bool
    external_llm_called: bool
    final_hypothesis_generated: bool
    external_call_audit: list[dict[str, Any]]
    privacy_mode: str
    degraded_reason: str | None
    retrieval_quality_summary: RetrievalQualitySummary
    evidence_readiness: EvidenceReadiness
    readiness_judgement: EvidenceReadiness
    external_candidate_section: ExternalCandidateReport
    library_results: list[FusedRetrievalResult]
    evidence_chunks: list[KeywordSearchResult]
    related_notes: list[DryRunNoteCandidate]
    related_tags: list[DryRunTagCandidate]
    related_relations: list[RelationResult]
    excluded_evidence: list[EvidenceHygieneIssue]
    hygiene_warnings: list[str]
    evidence_gaps: list[str]
    suggested_next_actions: list[str]


def run_research_session_dry_run(
    research_question: str,
    top_k: int = DEFAULT_TOP_K,
    dry_run: bool = True,
    rerank: str = RERANK_HEURISTIC,
    external_api_config: ExternalApiConfig = DEFAULT_EXTERNAL_API_CONFIG,
) -> ResearchSessionDryRunReport:
    normalized_question = research_question.strip()
    if not normalized_question:
        raise ValueError("research question must not be empty.")
    if not dry_run:
        raise ValueError("Phase 9B.1 only supports dry-run. Research generation is not implemented.")

    safe_top_k = max(1, top_k)
    retrieval_report = search_retrieval(
        query=normalized_question,
        top_k=safe_top_k,
        rerank=rerank,
        external_api_config=external_api_config,
    )
    evidence_report = generate_hypothesis_dry_run(question=normalized_question, limit=safe_top_k)
    hygiene_report = _apply_evidence_hygiene(normalized_question, evidence_report, retrieval_report.results)
    filtered_retrieval_report = replace(retrieval_report, results=hygiene_report.library_results)
    filtered_evidence_report = replace(
        evidence_report,
        evidence_chunks=hygiene_report.evidence_chunks,
        related_notes=hygiene_report.related_notes,
        related_tags=hygiene_report.related_tags,
        related_relations=hygiene_report.related_relations,
        evidence_gaps=_build_filtered_evidence_gaps(
            normalized_question,
            hygiene_report.evidence_chunks,
            hygiene_report.related_notes,
            hygiene_report.related_tags,
            hygiene_report.related_relations,
            hygiene_report.library_results,
            hygiene_report.excluded_evidence,
        ),
        suggested_next_actions=_build_base_next_actions(
            hygiene_report.evidence_chunks,
            hygiene_report.related_notes,
            hygiene_report.related_relations,
        ),
    )
    evidence_gaps = _merge_evidence_gaps(
        filtered_evidence_report,
        hygiene_report.library_results,
        hygiene_report.excluded_evidence,
    )
    degraded_reason = _combine_degraded_reason(filtered_retrieval_report)
    retrieval_quality_summary = _build_retrieval_quality_summary(filtered_retrieval_report, degraded_reason)
    evidence_readiness = _build_evidence_readiness(
        retrieval_quality_summary,
        evidence_gaps,
        hygiene_report.library_results,
    )
    session_next_actions = _build_session_next_actions(
        filtered_evidence_report.suggested_next_actions,
        retrieval_quality_summary,
        evidence_readiness,
        hygiene_report.library_results,
    )
    external_candidate_section = build_external_candidate_report(
        research_question=normalized_question,
        evidence_gaps=evidence_gaps,
        related_tags=filtered_evidence_report.related_tags,
        related_relations=filtered_evidence_report.related_relations,
        suggested_next_actions=session_next_actions,
    )
    privacy_mode = _build_privacy_mode(external_api_config, retrieval_report.external_rerank_called)

    return ResearchSessionDryRunReport(
        research_question=normalized_question,
        top_k=safe_top_k,
        dry_run=True,
        llm_called=False,
        api_called=False,
        external_api_enabled=external_api_config.external_api_enabled,
        external_search_called=False,
        external_rerank_called=retrieval_report.external_rerank_called,
        external_llm_called=False,
        final_hypothesis_generated=False,
        external_call_audit=retrieval_report.external_call_audit,
        privacy_mode=privacy_mode,
        degraded_reason=degraded_reason,
        retrieval_quality_summary=retrieval_quality_summary,
        evidence_readiness=evidence_readiness,
        readiness_judgement=evidence_readiness,
        external_candidate_section=external_candidate_section,
        library_results=hygiene_report.library_results,
        evidence_chunks=filtered_evidence_report.evidence_chunks,
        related_notes=filtered_evidence_report.related_notes,
        related_tags=filtered_evidence_report.related_tags,
        related_relations=filtered_evidence_report.related_relations,
        excluded_evidence=hygiene_report.excluded_evidence,
        hygiene_warnings=hygiene_report.hygiene_warnings,
        evidence_gaps=evidence_gaps,
        suggested_next_actions=session_next_actions,
    )


def build_research_session_sections(
    report: ResearchSessionDryRunReport,
    evidence_limit: int | None = None,
) -> dict[str, Any]:
    limit = report.top_k if evidence_limit is None else max(1, evidence_limit)
    return {
        "question": {
            "research_question": report.research_question,
            "top_k": report.top_k,
            "dry_run": report.dry_run,
        },
        "retrieval_summary": {
            "total_results": report.retrieval_quality_summary.total_results,
            "high_confidence_count": report.retrieval_quality_summary.high_confidence_count,
            "evidence_backed_count": report.retrieval_quality_summary.evidence_backed_count,
            "tag_or_relation_supported_count": report.retrieval_quality_summary.tag_or_relation_supported_count,
            "vector_index_available": report.retrieval_quality_summary.vector_index_available,
            "degraded_reason": report.retrieval_quality_summary.degraded_reason,
        },
        "evidence_summary": [_serialize_library_result(result) for result in report.library_results[:limit]],
        "excluded_evidence": [
            serialize_hygiene_issue(issue)
            for issue in report.excluded_evidence[:limit]
        ],
        "hygiene_warnings": list(report.hygiene_warnings),
        "related_notes": [_serialize_note(note) for note in report.related_notes],
        "related_tags": [_serialize_tag(tag) for tag in report.related_tags],
        "related_relations": [_serialize_relation(relation) for relation in report.related_relations],
        "evidence_gaps": list(report.evidence_gaps),
        "readiness_judgement": {
            "ready_for_hypothesis_dry_run": report.readiness_judgement.ready_for_hypothesis_dry_run,
            "blocking_gaps": list(report.readiness_judgement.blocking_gaps),
            "warning_gaps": list(report.readiness_judgement.warning_gaps),
        },
        "external_candidate_section": _serialize_external_candidate_section(report.external_candidate_section),
        "suggested_next_actions": list(report.suggested_next_actions),
        "safety_flags": {
            "dry_run": report.dry_run,
            "llm_called": report.llm_called,
            "api_called": report.api_called,
            "external_api_enabled": report.external_api_enabled,
            "external_search_called": report.external_search_called,
            "external_rerank_called": report.external_rerank_called,
            "external_llm_called": report.external_llm_called,
            "final_hypothesis_generated": report.final_hypothesis_generated,
            "privacy_mode": report.privacy_mode,
            "external_call_audit": list(report.external_call_audit),
        },
    }


def _serialize_external_candidate_section(report: ExternalCandidateReport) -> dict[str, Any]:
    return {
        "enabled": report.external_candidate_enabled,
        "called": report.external_search_called,
        "candidate_queries": list(report.candidate_queries),
        "reasons": list(report.candidate_reasons),
        "degraded_reason": report.degraded_reason,
        "safety_note": report.safety_note,
    }


def classify_evidence_confidence(result: FusedRetrievalResult) -> str:
    if _is_high_confidence_result(result):
        return "high"
    if _has_tag_or_relation_support(result) or result.related_note_count > 0:
        return "medium"
    return "low"


def _serialize_library_result(result: FusedRetrievalResult) -> dict[str, Any]:
    return {
        "result_type": result.result_type,
        "id": result.id,
        "document_id": result.document_id,
        "chunk_id": result.chunk_id,
        "title": result.title,
        "document_title": result.document_title,
        "document_type": result.document_type,
        "heading_path": result.heading_path,
        "pdf_path": result.pdf_path,
        "pdf_page_start": result.pdf_page_start,
        "pdf_open_url": result.pdf_open_url,
        "zotero_open_url": result.zotero_open_url,
        "source_channels": list(result.source_channels),
        "confidence": classify_evidence_confidence(result),
        "fusion_score": result.fusion_score,
        "rerank_score": result.rerank_score,
        "matched_terms": list(result.matched_terms),
        "tag_match_count": result.tag_match_count,
        "related_note_count": result.related_note_count,
        "relation_count": result.relation_count,
        "tags": list(result.tags),
        "related_notes": list(result.related_notes),
        "related_relations": list(result.related_relations),
        "snippet": result.snippet,
    }


def _serialize_note(note: DryRunNoteCandidate) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "title": note.title,
        "note_type": note.note_type,
        "source_path": note.source_path,
        "snippet": note.snippet,
        "linked_chunk_ids": list(note.linked_chunk_ids),
        "note_tags": list(note.note_tags),
    }


def _serialize_tag(tag: DryRunTagCandidate) -> dict[str, Any]:
    return {
        "tag_id": tag.tag_id,
        "name": tag.name,
        "tag_type": tag.tag_type,
        "description": tag.description,
    }


def _serialize_relation(relation: RelationResult) -> dict[str, Any]:
    return {
        "relation_id": relation.relation_id,
        "source_type": relation.source_type,
        "source_id": relation.source_id,
        "relation_type": relation.relation_type,
        "target_type": relation.target_type,
        "target_id": relation.target_id,
        "evidence_chunk_id": relation.evidence_chunk_id,
        "note_id": relation.note_id,
        "confidence": relation.confidence,
        "description": relation.description,
        "evidence_document_title": relation.evidence_document_title,
        "evidence_heading_path": relation.evidence_heading_path,
        "evidence_pdf_path": relation.evidence_pdf_path,
        "evidence_pdf_page_start": relation.evidence_pdf_page_start,
        "evidence_pdf_open_url": relation.evidence_pdf_open_url,
    }


@dataclass(frozen=True)
class _EvidenceHygieneReport:
    library_results: list[FusedRetrievalResult]
    evidence_chunks: list[KeywordSearchResult]
    related_notes: list[DryRunNoteCandidate]
    related_tags: list[DryRunTagCandidate]
    related_relations: list[RelationResult]
    excluded_evidence: list[EvidenceHygieneIssue]
    hygiene_warnings: list[str]


def _apply_evidence_hygiene(
    research_question: str,
    evidence_report: HypothesisDryRunReport,
    library_results: list[FusedRetrievalResult],
) -> _EvidenceHygieneReport:
    acceptance_scope_markers = match_mock_or_test_markers(research_question)
    excluded: list[EvidenceHygieneIssue] = []
    valid_results: list[FusedRetrievalResult] = []
    for result in library_results:
        if acceptance_scope_markers and not _matches_acceptance_scope(
            " ".join(
                [
                    result.document_title,
                    result.heading_path,
                    result.snippet,
                    result.pdf_path or "",
                    " ".join(result.tags),
                    " ".join(result.related_notes),
                ]
            ),
            acceptance_scope_markers,
        ):
            continue
        issue = _classify_library_result(result)
        if issue:
            excluded.append(issue)
        else:
            valid_results.append(result)

    valid_chunks: list[KeywordSearchResult] = []
    for chunk in evidence_report.evidence_chunks:
        if acceptance_scope_markers and not _matches_acceptance_scope(_collect_keyword_result_scope_text(chunk), acceptance_scope_markers):
            continue
        issue = _classify_keyword_result(chunk)
        if issue:
            excluded.append(issue)
        else:
            valid_chunks.append(chunk)

    excluded_chunk_ids = {
        issue.chunk_id
        for issue in excluded
        if issue.chunk_id is not None
    }
    valid_notes: list[DryRunNoteCandidate] = []
    excluded_note_ids: set[int] = set()
    for note in evidence_report.related_notes:
        if acceptance_scope_markers and not _matches_acceptance_scope(
            " ".join([note.title, note.source_path or "", note.snippet, " ".join(note.note_tags)]),
            acceptance_scope_markers,
        ):
            continue
        issue = _classify_note(note)
        if issue is None and any(chunk_id in excluded_chunk_ids for chunk_id in note.linked_chunk_ids):
            issue = EvidenceHygieneIssue(
                source="related_note",
                title=note.title,
                reason="related note is linked only to excluded mock/test/acceptance evidence.",
                matched_markers=["linked_excluded_evidence"],
                note_id=note.note_id,
            )
        if issue:
            excluded.append(issue)
            excluded_note_ids.add(note.note_id)
        else:
            valid_notes.append(note)

    valid_tags: list[DryRunTagCandidate] = []
    excluded_tag_ids: set[int] = set()
    for tag in evidence_report.related_tags:
        if acceptance_scope_markers and not _matches_acceptance_scope(
            " ".join([tag.name, tag.tag_type, tag.description or ""]),
            acceptance_scope_markers,
        ):
            continue
        issue = _classify_tag(tag)
        if issue:
            excluded.append(issue)
            excluded_tag_ids.add(tag.tag_id)
        else:
            valid_tags.append(tag)

    valid_relations: list[RelationResult] = []
    for relation in evidence_report.related_relations:
        if acceptance_scope_markers and not _matches_acceptance_scope(
            " ".join(
                [
                    relation.relation_type,
                    relation.description or "",
                    relation.evidence_document_title or "",
                    relation.evidence_heading_path or "",
                ]
            ),
            acceptance_scope_markers,
        ):
            continue
        issue = _classify_relation(relation, excluded_chunk_ids, excluded_note_ids, excluded_tag_ids)
        if issue:
            excluded.append(issue)
        else:
            valid_relations.append(relation)

    excluded = _dedupe_hygiene_issues(excluded)
    hygiene_warnings = _build_hygiene_warnings(excluded, valid_results, valid_chunks)
    return _EvidenceHygieneReport(
        library_results=valid_results,
        evidence_chunks=valid_chunks,
        related_notes=valid_notes,
        related_tags=valid_tags,
        related_relations=valid_relations,
        excluded_evidence=excluded,
        hygiene_warnings=hygiene_warnings,
    )


def _classify_library_result(result: FusedRetrievalResult) -> EvidenceHygieneIssue | None:
    return build_hygiene_issue(
        "retrieval_result",
        result.document_title,
        " ".join(
            [
                result.document_title,
                result.heading_path,
                result.snippet,
                result.pdf_path or "",
                " ".join(result.tags),
                " ".join(result.related_notes),
            ]
        ),
        document_id=result.document_id,
        chunk_id=result.chunk_id,
    )


def _collect_keyword_result_scope_text(result: KeywordSearchResult) -> str:
    return " ".join(
        [
            result.document_title,
            result.heading_path,
            result.chunk_text_snippet,
            result.pdf_path or "",
            " ".join(result.chunk_tags),
            " ".join(result.related_note_titles),
        ]
    )


def _matches_acceptance_scope(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    for marker in markers:
        normalized_marker = marker.lower()
        variants = {
            normalized_marker,
            normalized_marker.replace("_", " "),
            normalized_marker.replace("_", "-"),
        }
        if any(variant in lowered for variant in variants):
            return True
    return False


def _classify_keyword_result(result: KeywordSearchResult) -> EvidenceHygieneIssue | None:
    return build_hygiene_issue(
        "hypothesis_evidence",
        result.document_title,
        " ".join(
            [
                result.document_title,
                result.heading_path,
                result.chunk_text_snippet,
                result.pdf_path or "",
                " ".join(result.chunk_tags),
                " ".join(result.related_note_titles),
            ]
        ),
        document_id=result.document_id,
        chunk_id=result.chunk_id,
    )


def _classify_note(note: DryRunNoteCandidate) -> EvidenceHygieneIssue | None:
    return build_hygiene_issue(
        "related_note",
        note.title,
        " ".join([note.title, note.source_path or "", note.snippet, " ".join(note.note_tags)]),
        note_id=note.note_id,
    )


def _classify_tag(tag: DryRunTagCandidate) -> EvidenceHygieneIssue | None:
    return build_hygiene_issue(
        "related_tag",
        tag.name,
        " ".join([tag.name, tag.tag_type, tag.description or ""]),
        tag_id=tag.tag_id,
    )


def _classify_relation(
    relation: RelationResult,
    excluded_chunk_ids: set[int | None],
    excluded_note_ids: set[int],
    excluded_tag_ids: set[int],
) -> EvidenceHygieneIssue | None:
    issue = build_hygiene_issue(
        "related_relation",
        relation.relation_type,
        " ".join(
            [
                relation.relation_type,
                relation.description or "",
                relation.evidence_document_title or "",
                relation.evidence_heading_path or "",
            ]
        ),
        relation_id=relation.relation_id,
    )
    if issue:
        return issue
    source_tag_excluded = relation.source_type == "tag" and relation.source_id in excluded_tag_ids
    target_tag_excluded = relation.target_type == "tag" and relation.target_id in excluded_tag_ids
    if (
        relation.evidence_chunk_id in excluded_chunk_ids
        or (relation.note_id is not None and relation.note_id in excluded_note_ids)
        or source_tag_excluded
        or target_tag_excluded
    ):
        return EvidenceHygieneIssue(
            source="related_relation",
            title=relation.relation_type,
            reason="relation is connected to excluded mock/test/acceptance evidence.",
            matched_markers=["linked_excluded_evidence"],
            relation_id=relation.relation_id,
            chunk_id=relation.evidence_chunk_id,
            note_id=relation.note_id,
        )
    return None


def _dedupe_hygiene_issues(issues: list[EvidenceHygieneIssue]) -> list[EvidenceHygieneIssue]:
    deduped: list[EvidenceHygieneIssue] = []
    seen: set[tuple[object, ...]] = set()
    for issue in issues:
        key = (
            issue.source,
            issue.document_id,
            issue.chunk_id,
            issue.note_id,
            issue.tag_id,
            issue.relation_id,
            issue.title,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _build_hygiene_warnings(
    excluded: list[EvidenceHygieneIssue],
    valid_results: list[FusedRetrievalResult],
    valid_chunks: list[KeywordSearchResult],
) -> list[str]:
    if not excluded:
        return []
    warnings = [
        f"已隔离 {len(excluded)} 条 mock/test/acceptance evidence；这些条目不会进入 evidence_summary、readiness_judgement 或 external_candidate_section 上下文。"
    ]
    if not valid_results and not valid_chunks:
        warnings.append("当前只命中测试证据，Research Session 被阻断。")
    return warnings


def _build_filtered_evidence_gaps(
    question: str,
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_tags: list[DryRunTagCandidate],
    related_relations: list[RelationResult],
    library_results: list[FusedRetrievalResult],
    excluded: list[EvidenceHygieneIssue],
) -> list[str]:
    gaps: list[str] = []
    evidence_text = _collect_valid_evidence_text(
        evidence_chunks,
        related_notes,
        related_tags,
        related_relations,
        library_results,
    )
    if not evidence_chunks and not library_results:
        gaps.append("当前知识库未检索到可用于真实研究判断的有效 evidence chunk，不能进入假设生成。")
    if excluded and not evidence_chunks and not library_results:
        gaps.append("当前检索结果只包含 mock/test/acceptance 数据，必须先补充真实已读证据。")
    if not related_notes:
        gaps.append("未找到相关 personal_notes，缺少个人理解层判断。")
    if not related_tags:
        gaps.append("未找到相关标签，任务/方法/问题/局限结构仍不明确。")
    if not related_relations:
        gaps.append("未找到相关 knowledge_relations，方法-问题-证据链仍不完整。")
    for dimension in _find_uncovered_question_dimensions(question, evidence_text):
        gaps.append(f"当前 evidence 未覆盖问题维度：{dimension}。")
    if (
        (len(evidence_chunks) + len(library_results)) <= 1
        and not _contains_any_keyword(evidence_text, LIMITATION_EVIDENCE_KEYWORDS)
    ):
        gaps.append("证据数量偏少，且缺少 limitation / experiment / ablation / failure case 相关证据。")
    return gaps


def _collect_valid_evidence_text(
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_tags: list[DryRunTagCandidate],
    related_relations: list[RelationResult],
    library_results: list[FusedRetrievalResult],
) -> str:
    parts: list[str] = []
    for chunk in evidence_chunks:
        parts.extend(
            [
                chunk.document_title,
                chunk.heading_path,
                chunk.chunk_text_snippet,
                " ".join(chunk.chunk_tags),
                " ".join(chunk.related_note_titles),
            ]
        )
    for result in library_results:
        parts.extend(
            [
                result.document_title,
                result.heading_path,
                result.snippet,
                " ".join(result.tags),
                " ".join(result.related_notes),
            ]
        )
    for note in related_notes:
        parts.extend([note.title, note.snippet, " ".join(note.note_tags)])
    for tag in related_tags:
        parts.extend([tag.name, tag.tag_type, tag.description or ""])
    for relation in related_relations:
        parts.extend([relation.relation_type, relation.description or ""])
    return " ".join(part for part in parts if part)


def _find_uncovered_question_dimensions(question: str, evidence_text: str) -> list[str]:
    uncovered: list[str] = []
    for dimension, keywords in DIMENSION_KEYWORDS.items():
        if _contains_any_keyword(question, keywords) and not _contains_any_keyword(evidence_text, keywords):
            uncovered.append(dimension)
    return uncovered


def _contains_any_keyword(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _build_base_next_actions(
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_relations: list[RelationResult],
) -> list[str]:
    steps = [
        "人工阅读 evidence chunk 与 PDF 页码，确认问题、方法和局限是否真实存在。",
        "必要时为关键 chunk / note 补充 task、method、problem、limitation、metric 标签。",
        "必要时手动创建带 evidence_chunk_id 的 knowledge_relations。",
    ]
    if evidence_chunks and related_notes and related_relations:
        steps.append("证据链已具备初步结构，可在后续 Phase 8B 再接入受控生成或人工撰写候选假设。")
    else:
        steps.append("当前只输出证据准备报告，不生成候选研究假设。")
    return steps


def _merge_evidence_gaps(
    evidence_report: HypothesisDryRunReport,
    library_results: list[FusedRetrievalResult],
    excluded: list[EvidenceHygieneIssue],
) -> list[str]:
    gaps = list(evidence_report.evidence_gaps)
    if not library_results:
        gaps.append("已读库检索未返回可用于真实研究判断的有效结果，Research Session 只能输出证据不足报告。")
    if excluded and not library_results:
        gaps.append("mock/test/acceptance evidence 已被隔离，未计入真实 Research Session 证据。")
    return gaps


def _build_session_next_actions(
    base_actions: list[str],
    summary: RetrievalQualitySummary,
    readiness: EvidenceReadiness,
    library_results: list[FusedRetrievalResult],
) -> list[str]:
    actions = list(base_actions)
    if summary.total_results == 0:
        actions.extend(
            [
                "用更具体的研究问题重新检索，例如补充方法名、数据集、指标或章节关键词。",
                "补充已读资料入库，或确认相关论文是否已经标记为 read/mastered。",
            ]
        )
    if not summary.vector_index_available:
        actions.append("重建 vector index，确认向量检索没有降级。")
    if summary.tag_or_relation_supported_count == 0:
        actions.extend(
            [
                "为关键 chunk 补充 method / dataset / metric / limitation 标签。",
                "为关键 evidence chunk 补充 knowledge relation，说明方法、问题、数据集或指标之间的关系。",
            ]
        )
    if summary.high_confidence_count == 0 and library_results:
        actions.append("为最相关 chunk 增加 note-link，连接个人读书笔记、实验日志或组会记录。")
    if readiness.warning_gaps or readiness.blocking_gaps:
        joined_gaps = "\n".join(readiness.warning_gaps + readiness.blocking_gaps).lower()
        if "limitation" in joined_gaps or "ablation" in joined_gaps or "failure" in joined_gaps or "局限" in joined_gaps:
            actions.append("检查相关论文的 limitation / ablation / failure case 章节，并补充对应标签或关系。")
    return _dedupe_actions(actions)


def _dedupe_actions(actions: list[str]) -> list[str]:
    deduped: list[str] = []
    forbidden_terms = [
        "候选创新点",
        "最终创新点",
        "candidate_hypotheses",
        "innovation_points",
        "research_ideas",
    ]
    for action in actions:
        normalized = action.strip()
        if not normalized:
            continue
        if any(term in normalized for term in forbidden_terms):
            continue
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _build_retrieval_quality_summary(
    retrieval_report: RetrievalFusionReport,
    degraded_reason: str | None,
) -> RetrievalQualitySummary:
    results = retrieval_report.results
    return RetrievalQualitySummary(
        total_results=len(results),
        high_confidence_count=sum(1 for result in results if _is_high_confidence_result(result)),
        evidence_backed_count=sum(1 for result in results if _is_evidence_backed_result(result)),
        tag_or_relation_supported_count=sum(1 for result in results if _has_tag_or_relation_support(result)),
        vector_index_available=not any(
            reason.lower().startswith("vector search skipped")
            for reason in retrieval_report.local_degraded_reasons
        ),
        degraded_reason=degraded_reason,
    )


def _build_evidence_readiness(
    summary: RetrievalQualitySummary,
    evidence_gaps: list[str],
    library_results: list[FusedRetrievalResult],
) -> EvidenceReadiness:
    blocking_gaps: list[str] = []
    warning_gaps: list[str] = []

    if summary.total_results == 0:
        blocking_gaps.append("已读库没有可用检索结果，不能进入后续假设生成。")
    if summary.evidence_backed_count == 0:
        blocking_gaps.append("检索结果缺少可回溯的 evidence chunk。")
    if summary.total_results > 0 and summary.high_confidence_count == 0:
        blocking_gaps.append("检索结果缺少高置信度多通道证据，暂不应进入后续假设生成。")
    if library_results and all(_is_low_confidence_single_channel(result) for result in library_results):
        blocking_gaps.append("当前结果主要来自低置信度单通道召回，不能视为充分证据。")

    for gap in evidence_gaps:
        if _is_blocking_gap(gap):
            if gap not in blocking_gaps:
                blocking_gaps.append(gap)
        elif gap not in warning_gaps:
            warning_gaps.append(gap)

    if not summary.vector_index_available and summary.degraded_reason:
        warning_gaps.append(f"向量索引不可用或降级：{summary.degraded_reason}")

    ready = (
        summary.total_results > 0
        and summary.evidence_backed_count > 0
        and summary.high_confidence_count > 0
        and not blocking_gaps
    )
    return EvidenceReadiness(
        ready_for_hypothesis_dry_run=ready,
        blocking_gaps=blocking_gaps,
        warning_gaps=warning_gaps,
    )


def _is_high_confidence_result(result: FusedRetrievalResult) -> bool:
    channels = set(result.source_channels)
    if _is_low_confidence_single_channel(result):
        return False
    has_structural_support = (
        "note_link" in channels
        and (
            "tag" in channels
            or "relation" in channels
            or result.tag_match_count > 0
            or result.relation_count > 0
        )
    )
    has_evidence_network = result.related_note_count > 0 and (
        result.relation_count > 0 or result.tags or "tag" in channels or "relation" in channels
    )
    return (has_structural_support or has_evidence_network or len(channels) >= 3) and result.fusion_score >= 3.0


def _is_evidence_backed_result(result: FusedRetrievalResult) -> bool:
    return result.chunk_id is not None and result.document_id is not None and bool(result.source_channels)


def _has_tag_or_relation_support(result: FusedRetrievalResult) -> bool:
    channels = set(result.source_channels)
    return bool(result.tags) or result.relation_count > 0 or "tag" in channels or "relation" in channels


def _is_low_confidence_single_channel(result: FusedRetrievalResult) -> bool:
    channels = set(result.source_channels)
    return channels in ({"keyword"}, {"vector"})


def _is_blocking_gap(gap: str) -> bool:
    lowered = gap.lower()
    blocking_terms = [
        "mock/test/acceptance",
        "没有可用于真实研究判断",
        "证据不足",
        "未检索到",
        "不能生成",
    ]
    return any(term in lowered for term in blocking_terms)


def _combine_degraded_reason(retrieval_report: RetrievalFusionReport) -> str | None:
    reasons = []
    if retrieval_report.degraded_reason:
        reasons.append(retrieval_report.degraded_reason)
    reasons.extend(retrieval_report.local_degraded_reasons)
    return "; ".join(reason for reason in reasons if reason) or None


def _build_privacy_mode(config: ExternalApiConfig, external_called: bool) -> str:
    if external_called:
        return "external_snippet_only"
    if config.external_api_enabled:
        return "local_only_external_available"
    return "local_only"
