from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from app.services.evidence_hygiene_service import STRONG_LEVEL, classify_mock_or_test_markers
from app.services.research_session_tag_mapping_policy import (
    FOUR_LAYER_BUCKETS,
    map_legacy_tags_to_four_layer,
)
from app.services.research_session_workflow_planner import plan_research_session_workflow


RetrievalCallable = Callable[..., Any]


def build_prepared_evidence_from_retrieval(
    research_goal: str,
    *,
    top_k: int = 5,
    rerank: str = "heuristic",
    enabled_channels: set[str] | list[str] | tuple[str, ...] | None = None,
    retriever: RetrievalCallable | None = None,
) -> list[dict[str, Any]]:
    """Run local retrieval and normalize results into Phase 14C prepared evidence."""
    if retriever is None:
        from app.services.retrieval_fusion_service import search_retrieval

        report = search_retrieval(
            research_goal,
            top_k=top_k,
            rerank=rerank,
            enabled_channels=enabled_channels,
        )
    else:
        report = retriever(
            query=research_goal,
            top_k=top_k,
            rerank=rerank,
            enabled_channels=enabled_channels,
        )
    return build_prepared_evidence_from_retrieval_results(_results_from_report(report))


def build_prepared_evidence_from_retrieval_results(results: list[Any]) -> list[dict[str, Any]]:
    """Normalize retrieval result objects without performing retrieval or writes."""
    prepared: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        row = _to_mapping(result)
        source_trace = _source_trace(row)
        gaps = _source_trace_gaps(source_trace)
        hygiene = _hygiene_classification(row)
        is_mock_or_acceptance = bool(row.get("is_mock_or_acceptance")) or hygiene.level == STRONG_LEVEL
        if is_mock_or_acceptance:
            gaps.append("mock_or_acceptance_evidence_downgraded_by_existing_hygiene_policy")
        elif hygiene.level:
            gaps.append(f"hygiene_marker_review:{hygiene.level}")

        tags, tag_gaps, tag_mapping_results = _normalize_tags(row.get("tags") or [])
        gaps.extend(tag_gaps)
        if not any(tags.values()):
            gaps.append("missing_four_layer_tags")

        retrieval_metadata = {
            "retrieval_score": row.get("retrieval_score"),
            "fusion_score": row.get("fusion_score"),
            "rerank_score": row.get("rerank_score"),
            "keyword_score": row.get("keyword_score"),
            "vector_score": row.get("vector_score"),
            "tag_score": row.get("tag_score"),
            "relation_score": row.get("relation_score"),
            "note_link_score": row.get("note_link_score"),
            "matched_terms": list(row.get("matched_terms") or []),
            "tag_mapping_results": tag_mapping_results,
        }

        prepared.append(
            {
                "evidence_id": str(row.get("evidence_id") or f"ev_{index:03d}"),
                "source_type": row.get("source_type") or row.get("result_type") or "chunk",
                "source_trace": source_trace,
                "document_id": source_trace.get("document_id"),
                "document_title": source_trace.get("document_title"),
                "chunk_id": source_trace.get("chunk_id"),
                "heading_path": source_trace.get("heading_path"),
                "pdf_path": source_trace.get("pdf_path"),
                "pdf_page": source_trace.get("pdf_page_start"),
                "pdf_page_end": source_trace.get("pdf_page_end"),
                "zotero_open_url": source_trace.get("zotero_open_url"),
                "snippet": _short_snippet(row.get("snippet") or ""),
                "source_channels": list(row.get("source_channels") or ["retrieval"]),
                "retrieval_metadata": {key: value for key, value in retrieval_metadata.items() if value is not None},
                "tags": tags,
                "related_notes": list(row.get("related_notes") or []),
                "related_relations": _normalize_related_relations(row.get("related_relations") or []),
                "evidence_strength": "weak",
                "is_mock_or_acceptance": is_mock_or_acceptance,
                "is_external_context": bool(row.get("is_external_context", False)),
                "gaps": _dedupe(
                    [
                        *gaps,
                        "evidence_strength_not_derived_from_retrieval_score",
                    ]
                ),
            }
        )
    return prepared


def run_research_session_with_retrieval(
    research_goal: str,
    *,
    top_k: int = 5,
    constraints: dict[str, Any] | None = None,
    rerank: str = "heuristic",
    enabled_channels: set[str] | list[str] | tuple[str, ...] | None = None,
    retriever: RetrievalCallable | None = None,
) -> dict[str, Any]:
    """Build retrieval-backed prepared evidence and pass it to the Phase 14C planner."""
    prepared_evidence = build_prepared_evidence_from_retrieval(
        research_goal,
        top_k=top_k,
        rerank=rerank,
        enabled_channels=enabled_channels,
        retriever=retriever,
    )
    clean_constraints = dict(constraints or {})
    clean_constraints.setdefault("max_results", top_k)
    clean_constraints.setdefault("retrieval_adapter", "phase14d")
    return plan_research_session_workflow(research_goal, prepared_evidence, clean_constraints)


def _results_from_report(report: Any) -> list[Any]:
    if isinstance(report, list):
        return report
    if isinstance(report, dict):
        return list(report.get("results") or [])
    return list(getattr(report, "results", []) or [])


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    fields = [
        "evidence_id",
        "source_type",
        "result_type",
        "document_id",
        "document_title",
        "title",
        "chunk_id",
        "id",
        "heading_path",
        "pdf_path",
        "pdf_page",
        "pdf_page_start",
        "pdf_page_end",
        "zotero_open_url",
        "snippet",
        "source_channels",
        "retrieval_score",
        "fusion_score",
        "rerank_score",
        "keyword_score",
        "vector_score",
        "tag_score",
        "relation_score",
        "note_link_score",
        "matched_terms",
        "tags",
        "related_notes",
        "related_relations",
        "is_mock_or_acceptance",
        "is_external_context",
    ]
    return {field: getattr(value, field) for field in fields if hasattr(value, field)}


def _source_trace(row: dict[str, Any]) -> dict[str, Any]:
    trace = dict(row.get("source_trace") or {})
    chunk_id = row.get("chunk_id") or row.get("id")
    trace.setdefault("document_id", row.get("document_id"))
    trace.setdefault("document_title", row.get("document_title") or row.get("title"))
    trace.setdefault("chunk_id", chunk_id)
    trace.setdefault("heading_path", row.get("heading_path"))
    trace.setdefault("pdf_path", row.get("pdf_path"))
    trace.setdefault("pdf_page_start", row.get("pdf_page_start") or row.get("pdf_page"))
    trace.setdefault("pdf_page_end", row.get("pdf_page_end"))
    trace.setdefault("zotero_open_url", row.get("zotero_open_url"))
    return trace


def _source_trace_gaps(source_trace: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for field in ("document_id", "document_title", "chunk_id", "heading_path"):
        if source_trace.get(field) in (None, ""):
            gaps.append(f"missing_source_trace_{field}")
    if source_trace.get("pdf_path") in (None, ""):
        gaps.append("missing_source_trace_pdf_path")
    if source_trace.get("pdf_page_start") is None:
        gaps.append("missing_source_trace_pdf_page_start")
    return gaps


def _normalize_tags(raw_tags: list[Any]) -> tuple[dict[str, list[str]], list[str], list[dict[str, Any]]]:
    tags = {
        "topic_tags": [],
        "problem_tags": [],
        "mechanism_tags": [],
        "inspiration_tags": [],
    }
    gaps: list[str] = []
    mapping_results = map_legacy_tags_to_four_layer(raw_tags)
    for mapping in mapping_results:
        bucket = mapping.get("target_bucket")
        name = str(mapping.get("name") or "").strip()
        source_tag = mapping.get("source_tag") or {}
        raw = source_tag.get("raw") or name
        if bucket in FOUR_LAYER_BUCKETS and mapping.get("status") == "suggested":
            if name and name not in tags[bucket]:
                tags[bucket].append(name)
        elif bucket == "evaluation_context":
            gaps.append(f"evaluation_context_tag:{raw}")
        else:
            gaps.append(f"unmapped_tag:{raw}")
    return tags, _dedupe(gaps), mapping_results


def _normalize_related_relations(raw_relations: list[Any]) -> list[Any]:
    normalized: list[Any] = []
    for relation in raw_relations:
        if isinstance(relation, (str, int)):
            normalized.append(relation)
        else:
            normalized.append(_to_mapping(relation))
    return normalized


def _hygiene_classification(row: dict[str, Any]):
    text = " ".join(
        str(part or "")
        for part in (
            row.get("title"),
            row.get("document_title"),
            row.get("heading_path"),
            row.get("snippet"),
        )
    )
    return classify_mock_or_test_markers(text)


def _short_snippet(text: str, limit: int = 280) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
