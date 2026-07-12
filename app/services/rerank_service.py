from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.external_api_config import DEFAULT_EXTERNAL_API_CONFIG, ExternalApiConfig
from app.services.external_api_guard import ExternalApiDisabledError, ExternalApiGuard


RERANK_NONE = "none"
RERANK_HEURISTIC = "heuristic"
RERANK_EXTERNAL = "external"
SUPPORTED_RERANK_MODES = {RERANK_NONE, RERANK_HEURISTIC, RERANK_EXTERNAL}


@dataclass(frozen=True)
class RerankOutcome:
    results: list[Any]
    mode: str
    external_called: bool
    audit_records: list[dict[str, Any]]
    degraded_reason: str | None


class BaseReranker(Protocol):
    def rerank(self, query: str, results: list[Any]) -> RerankOutcome:
        ...


@dataclass(frozen=True)
class NoOpReranker:
    def rerank(self, query: str, results: list[Any]) -> RerankOutcome:
        return RerankOutcome(
            results=results,
            mode=RERANK_NONE,
            external_called=False,
            audit_records=[],
            degraded_reason=None,
        )


@dataclass(frozen=True)
class HeuristicReranker:
    def rerank(self, query: str, results: list[Any]) -> RerankOutcome:
        ranked = sorted(results, key=lambda result: compute_heuristic_score(query, result), reverse=True)
        return RerankOutcome(
            results=ranked,
            mode=RERANK_HEURISTIC,
            external_called=False,
            audit_records=[],
            degraded_reason=None,
        )


@dataclass(frozen=True)
class ExternalRerankerStub:
    config: ExternalApiConfig = DEFAULT_EXTERNAL_API_CONFIG

    def rerank(self, query: str, results: list[Any]) -> RerankOutcome:
        guard = ExternalApiGuard(self.config)
        payload = _build_safe_payload(query, results, guard)
        try:
            guard.validate_external_enabled("rerank")
        except ExternalApiDisabledError as exc:
            return RerankOutcome(
                results=results,
                mode=RERANK_EXTERNAL,
                external_called=False,
                audit_records=[
                    guard.build_audit_record(
                        feature="rerank",
                        action="external_rerank_rejected",
                        provider=self.config.provider,
                        payload=payload,
                        allowed=False,
                        called=False,
                        degraded_reason=str(exc),
                    )
                ],
                degraded_reason=str(exc),
            )

        degraded_reason = "Phase 9C.0 仅提供外部 rerank stub，不执行真实外部 API 调用。"
        return RerankOutcome(
            results=results,
            mode=RERANK_EXTERNAL,
            external_called=False,
            audit_records=[
                guard.build_audit_record(
                    feature="rerank",
                    action="external_rerank_stub_degraded",
                    provider=self.config.provider,
                    payload=payload,
                    allowed=True,
                    called=False,
                    degraded_reason=degraded_reason,
                )
            ],
            degraded_reason=degraded_reason,
        )


def rerank_results(
    query: str,
    results: list[Any],
    mode: str = RERANK_NONE,
    config: ExternalApiConfig = DEFAULT_EXTERNAL_API_CONFIG,
) -> RerankOutcome:
    if mode not in SUPPORTED_RERANK_MODES:
        raise ValueError(f"unsupported rerank mode: {mode}")
    if mode == RERANK_HEURISTIC:
        return HeuristicReranker().rerank(query, results)
    if mode == RERANK_EXTERNAL:
        return ExternalRerankerStub(config=config).rerank(query, results)
    return NoOpReranker().rerank(query, results)


def compute_heuristic_score(query: str, result: Any) -> float:
    tokens = _query_tokens(query)
    title_text = _lower_join([getattr(result, "title", ""), getattr(result, "document_title", "")])
    heading_text = _lower_join([getattr(result, "heading_path", "")])
    snippet_text = _lower_join([getattr(result, "snippet", "")])
    tag_text = _lower_join(getattr(result, "tags", []) or [])
    relation_count = len(getattr(result, "related_relations", []) or [])
    related_note_count = len(getattr(result, "related_notes", []) or [])

    all_text = " ".join([title_text, heading_text, snippet_text, tag_text])
    coverage = sum(1 for token in tokens if token in all_text)
    title_match = sum(1 for token in tokens if token in title_text)
    heading_match = sum(1 for token in tokens if token in heading_text)
    tag_match = sum(1 for token in tokens if token in tag_text)
    snippet_match = sum(snippet_text.count(token) for token in tokens)
    source_channel_count = len(getattr(result, "source_channels", []) or [])
    fusion_score = float(getattr(result, "fusion_score", 0.0) or 0.0)

    return (
        coverage * 10.0
        + title_match * 6.0
        + heading_match * 5.0
        + tag_match * 4.0
        + min(snippet_match, 5) * 1.5
        + min(related_note_count, 5) * 2.0
        + min(relation_count, 5) * 2.0
        + source_channel_count * 1.0
        + fusion_score
    )


def _query_tokens(query: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", query.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if len(token) < 2 or token in tokens:
            continue
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            for ngram in _cjk_ngrams(token):
                if ngram not in tokens:
                    tokens.append(ngram)
    if not tokens and query.strip():
        return [query.strip().lower()]
    return tokens


def _cjk_ngrams(text: str, max_tokens: int = 40) -> list[str]:
    ngrams: list[str] = []
    for size in (4, 3, 2):
        for start in range(0, max(0, len(text) - size + 1)):
            ngram = text[start : start + size]
            if ngram not in ngrams:
                ngrams.append(ngram)
            if len(ngrams) >= max_tokens:
                return ngrams
    return ngrams


def _lower_join(parts: list[Any]) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def _build_safe_payload(query: str, results: list[Any], guard: ExternalApiGuard) -> dict[str, Any]:
    snippets = []
    for result in results:
        snippets.append(
            {
                "query": query,
                "snippet": getattr(result, "snippet", ""),
                "document_title": getattr(result, "document_title", None) or getattr(result, "title", None),
                "heading_path": getattr(result, "heading_path", None),
                "chunk_id": getattr(result, "id", None) if getattr(result, "result_type", None) == "chunk_result" else None,
            }
        )
    return guard.sanitize_payload(
        {
            "query": query,
            "snippet": "\n".join(item["snippet"] or "" for item in snippets),
            "document_title": "; ".join(str(item["document_title"] or "") for item in snippets) or None,
            "heading_path": "; ".join(str(item["heading_path"] or "") for item in snippets) or None,
            "chunk_id": next((item["chunk_id"] for item in snippets if item["chunk_id"] is not None), None),
        }
    )
