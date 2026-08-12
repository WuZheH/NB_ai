from __future__ import annotations

from typing import Any


DOCUMENT_NOVELTY_BOOST = 0.6
SOURCE_TYPE_NOVELTY_BOOST = 0.3


def diversify_coverage_results(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    seen_source_types: set[str] = set()

    while remaining:
        best_index = 0
        best_key: tuple[float, float, int, str] | None = None
        best_adjustment = 0.0
        for index, candidate in enumerate(remaining):
            document_key = _document_key(candidate)
            source_type = str(candidate.get("source_type") or "")
            adjustment = 0.0
            if selected and document_key not in seen_documents:
                adjustment += DOCUMENT_NOVELTY_BOOST
            if selected and source_type not in seen_source_types:
                adjustment += SOURCE_TYPE_NOVELTY_BOOST
            key = (
                float(candidate.get("score") or 0.0) + adjustment,
                float(candidate.get("base_bm25_score") or 0.0),
                -int(candidate.get("base_bm25_rank") or 2**31),
                str(candidate.get("fragment_id") or ""),
            )
            if best_key is None or key > best_key:
                best_key = key
                best_index = index
                best_adjustment = adjustment

        candidate = remaining.pop(best_index)
        breakdown = dict(candidate.get("score_breakdown") or {})
        breakdown["coverage_diversification"] = best_adjustment
        reasons = list(candidate.get("match_reasons") or [])
        if best_adjustment and "coverage_diversification" not in reasons:
            reasons.append("coverage_diversification")
        candidate = {
            **candidate,
            "score": float(candidate.get("score") or 0.0) + best_adjustment,
            "score_breakdown": breakdown,
            "match_reasons": reasons,
        }
        selected.append(candidate)
        seen_documents.add(_document_key(candidate))
        seen_source_types.add(str(candidate.get("source_type") or ""))

    return selected, {
        "documents": len(seen_documents),
        "source_types": len(seen_source_types),
        "document_keys": sorted(seen_documents),
        "source_type_values": sorted(seen_source_types),
        "document_novelty_boost": DOCUMENT_NOVELTY_BOOST,
        "source_type_novelty_boost": SOURCE_TYPE_NOVELTY_BOOST,
    }


def _document_key(candidate: dict[str, Any]) -> str:
    if candidate.get("document_id") is not None:
        return f"document:{candidate['document_id']}"
    if candidate.get("zotero_attachment_key"):
        return f"attachment:{candidate['zotero_attachment_key']}"
    return f"fragment:{candidate.get('fragment_id')}"
