"""Pure result filtering, matching, and ranking helpers."""

from __future__ import annotations

import re
from typing import Any

from app.domains.database_search.pagination import _page_sort


def _rank_rows(
    rows: list[dict[str, Any]],
    terms: list[str],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    scored = [(_score_row(row, terms, fields), row) for row in rows]
    return [
        row
        for score, row in sorted(
            scored,
            key=lambda pair: (
                -pair[0],
                _page_sort(pair[1]),
                str(pair[1].get("id") or pair[1].get("candidate_temp_id") or ""),
            ),
        )
        if score > 0
    ]


def _rank_result_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"direct_match": 0, "document_scope_fallback": 1, "nearby_chunk": 2}
    return sorted(
        items,
        key=lambda item: (
            -int(item.get("score") or 0),
            priority.get(str(item.get("context_reason") or ""), 3),
            int(item.get("page") or item.get("pdf_page") or 0),
            str(item.get("id") or ""),
        ),
    )


def _score_row(
    row: dict[str, Any],
    terms: list[str],
    fields: tuple[str, ...],
) -> int:
    text = " ".join(str(row.get(field) or "") for field in fields).lower()
    score = 0
    for term in terms:
        if not term:
            continue
        hits = _term_occurrences(text, term)
        if hits:
            score += max(1, hits) * (5 if " " in term else 3)
    return score


def _match_reason(
    row: dict[str, Any],
    terms: list[str],
    fields: tuple[str, ...],
) -> str:
    for term in terms:
        for field in fields:
            if _term_occurrences(str(row.get(field) or ""), term):
                return f"keyword:{term} in {field}"
    return "keyword_fallback"


def _term_occurrences(text: str, term: str) -> int:
    clean_term = str(term or "").casefold()
    if not clean_term:
        return 0
    clean_text = str(text or "").casefold()
    if len(clean_term) <= 4 and re.fullmatch(r"[a-z0-9_]+", clean_term):
        pattern = rf"(?<![a-z0-9_]){re.escape(clean_term)}(?![a-z0-9_])"
        return len(re.findall(pattern, clean_text))
    return clean_text.count(clean_term)


def _term_first_index(text: str, term: str) -> int:
    clean_term = str(term or "").casefold()
    if not clean_term:
        return -1
    clean_text = str(text or "").casefold()
    if len(clean_term) <= 4 and re.fullmatch(r"[a-z0-9_]+", clean_term):
        match = re.search(
            rf"(?<![a-z0-9_]){re.escape(clean_term)}(?![a-z0-9_])",
            clean_text,
        )
        return match.start() if match else -1
    return clean_text.find(clean_term)


def _item_contains_any_term(item: dict[str, Any], terms: list[str]) -> bool:
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            "text",
            "title",
            "heading_path",
            "snippet",
            "note_text",
            "selected_text",
        )
    )
    return any(_term_occurrences(text, term) > 0 for term in terms)


def _items_contain_any_term(
    items: list[dict[str, Any]],
    terms: list[str],
) -> bool:
    return any(_item_contains_any_term(item, terms) for item in items)


def _item_matches_anchor_groups(
    item: dict[str, Any],
    groups: list[dict[str, Any]],
) -> bool:
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            "text",
            "title",
            "heading_path",
            "snippet",
            "note_text",
            "selected_text",
        )
    )
    for group in groups:
        terms = [str(term) for term in group.get("terms") or []]
        matches = [_term_occurrences(text, term) > 0 for term in terms]
        if group.get("match_policy") == "all" and matches and all(matches):
            return True
        if group.get("match_policy") != "all" and any(matches):
            return True
    return False


def _items_match_anchor_groups(
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> bool:
    return any(_item_matches_anchor_groups(item, groups) for item in items)


__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")]
