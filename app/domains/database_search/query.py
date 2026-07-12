"""Pure query normalization and scope-planning helpers."""

from __future__ import annotations

from typing import Any


ALL_LAYER_KEYS = ("evidence_chunks", "zotero_notes", "objects", "mechanisms")


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _scope_predicates(
    alias: str,
    *,
    document_id: int | None,
    chapter_id: int | None,
) -> tuple[list[str], list[Any]]:
    predicates: list[str] = []
    params: list[Any] = []
    if document_id is not None:
        predicates.append(f"{alias}.document_id = ?")
        params.append(document_id)
    if chapter_id is not None:
        predicates.append(f"{alias}.chapter_id = ?")
        params.append(chapter_id)
    return predicates, params


def _scope_expansion_state(
    *,
    document_id: int | None,
    chapter_id: int | None,
    anchor_terms: list[str],
) -> dict[str, Any]:
    if chapter_id is not None:
        effective_scope = "chapter"
    elif document_id is not None:
        effective_scope = "document"
    else:
        effective_scope = "library"
    return {
        "applied": False,
        "requested_document_id": document_id,
        "requested_chapter_id": chapter_id,
        "effective_evidence_scope": effective_scope,
        "reason": "topic_scope_fallback_not_required",
        "anchor_terms": list(anchor_terms),
    }


def _like_predicate(
    fields: tuple[str, ...],
    terms: list[str],
) -> tuple[str, list[Any]]:
    safe_terms = terms or [""]
    clauses: list[str] = []
    params: list[Any] = []
    for term in safe_terms:
        pattern = f"%{_escape_like(term)}%"
        for field in fields:
            clauses.append(f"LOWER(COALESCE({field}, '')) LIKE LOWER(?) ESCAPE '\\'")
            params.append(pattern)
    return f"({' OR '.join(clauses)})", params


def _requested_layers(include_layers: str | None) -> set[str]:
    if not include_layers or include_layers.strip().lower() == "all":
        return set(ALL_LAYER_KEYS)
    requested = {part.strip() for part in include_layers.split(",") if part.strip()}
    return requested.intersection(ALL_LAYER_KEYS) or set(ALL_LAYER_KEYS)


__all__ = [
    "ALL_LAYER_KEYS",
    "_compact_text",
    "_escape_like",
    "_like_predicate",
    "_requested_layers",
    "_scope_expansion_state",
    "_scope_predicates",
]
