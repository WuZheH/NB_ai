from __future__ import annotations

from typing import Any

from app.services.retrieval.query_normalizer import (
    NormalizedQuery,
    compact_identifier,
    normalize_for_search,
)


def evaluate_match_signals(
    candidate: dict[str, Any],
    *,
    plan: NormalizedQuery,
    alias_terms: list[str],
) -> dict[str, Any]:
    fields = {
        "title": normalize_for_search(candidate.get("title") or ""),
        "section": normalize_for_search(candidate.get("section") or ""),
        "tags": normalize_for_search(candidate.get("tags_text") or ""),
        "text": normalize_for_search(candidate.get("text") or ""),
        "note_comment": normalize_for_search(candidate.get("note_comment") or ""),
        "context": normalize_for_search(candidate.get("context_text") or ""),
    }
    field_values = list(fields.values())
    combined = " ".join(value for value in field_values if value)
    expected_phrases = list(plan.phrases) or (
        [plan.normalized_query] if " " in plan.normalized_query else []
    )
    exact_phrase = bool(expected_phrases) and all(
        any(phrase in value for value in field_values)
        for phrase in expected_phrases
    )
    identifier = _identifier_match(fields, plan.identifier_variants)
    matched_terms = [
        term
        for term in plan.terms
        if term and any(term in value for value in field_values)
    ]
    term_coverage = (
        len(matched_terms) / len(plan.terms)
        if plan.terms
        else (1.0 if exact_phrase or identifier else 0.0)
    )
    title_match = _matches_any(fields["title"], [plan.normalized_query, *plan.terms])
    section_match = _matches_any(fields["section"], [plan.normalized_query, *plan.terms])
    tag_match = _matches_any(fields["tags"], [plan.normalized_query, *plan.terms])
    matched_alias_terms = [
        term
        for term in alias_terms
        if term and any(term in value for value in field_values)
    ]
    return {
        "exact_phrase_match": exact_phrase,
        "exact_identifier_match": identifier,
        "term_coverage": term_coverage,
        "matched_terms": matched_terms,
        "title_match": title_match,
        "section_match": section_match,
        "tag_match": tag_match,
        "curated_alias_match": bool(matched_alias_terms),
        "matched_alias_terms": matched_alias_terms,
        "combined_match_text": combined,
    }


def _identifier_match(
    fields: dict[str, str],
    variants: tuple[str, ...],
) -> bool:
    for variant in variants:
        if not variant:
            continue
        padded = f" {variant} "
        if any(padded in f" {value} " for value in fields.values()):
            return True
        compact = compact_identifier(variant)
        if len(compact) >= 3 and any(
            compact in compact_identifier(value)
            for value in fields.values()
        ):
            return True
    return False


def _matches_any(value: str, terms: list[str]) -> bool:
    return bool(value) and any(term and term in value for term in terms)
