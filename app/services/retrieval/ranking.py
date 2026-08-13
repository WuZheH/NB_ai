from __future__ import annotations

from typing import Any


SOURCE_TYPE_BOOSTS = {
    "pdf_chunk": 0.0,
    "zotero_highlight": 0.45,
    "zotero_annotation_comment": 0.55,
    "zotero_child_note": 0.35,
    "zotero_inspiration_note": 0.45,
    "personal_note": 0.35,
    "markdown_note": 0.25,
}

USER_NOTE_OR_HIGHLIGHT_TYPES = {
    "zotero_highlight",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
    "personal_note",
    "markdown_note",
}


def score_candidate(
    candidate: dict[str, Any],
    signals: dict[str, Any],
) -> dict[str, Any]:
    base_score = float(candidate.get("_base_score") or 0.0)
    source_type = str(candidate.get("source_type") or "")
    source_boost = SOURCE_TYPE_BOOSTS.get(source_type, 0.0)
    user_note_boost = 0.35 if source_type in USER_NOTE_OR_HIGHLIGHT_TYPES else 0.0
    breakdown = {
        "bm25": base_score,
        "exact_phrase_match": 4.0 if signals["exact_phrase_match"] else 0.0,
        "exact_identifier_match": 5.0 if signals["exact_identifier_match"] else 0.0,
        "term_coverage": 2.5 * float(signals["term_coverage"]),
        "title_match": 2.0 if signals["title_match"] else 0.0,
        "section_match": 1.25 if signals["section_match"] else 0.0,
        "tag_match": 1.25 if signals["tag_match"] else 0.0,
        "curated_alias_match": 1.0 if signals["curated_alias_match"] else 0.0,
        "source_type_boost": source_boost,
        "user_note_or_highlight_boost": user_note_boost,
        "coverage_diversification": 0.0,
    }
    reasons = []
    for name in (
        "exact_phrase_match",
        "exact_identifier_match",
        "term_coverage",
        "title_match",
        "section_match",
        "tag_match",
        "curated_alias_match",
    ):
        value = signals.get(name)
        if value:
            reasons.append(name)
    if source_boost:
        reasons.append("source_type_boost")
    if user_note_boost:
        reasons.append("user_note_or_highlight_boost")
    score = sum(breakdown.values())
    return {
        **candidate,
        "score": score,
        "base_bm25_score": base_score,
        "score_breakdown": breakdown,
        "match_reasons": reasons,
        "_match_signals": signals,
    }


def sort_scored_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -float(item.get("base_bm25_score") or 0.0),
            int(item.get("base_bm25_rank") or 2**31),
            str(item.get("fragment_id") or ""),
        ),
    )
