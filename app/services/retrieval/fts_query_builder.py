from __future__ import annotations

from typing import Any

from app.schemas.retrieval_search import RetrievalSearchFilters
from app.services.retrieval.query_normalizer import NormalizedQuery


def build_filter_clause(
    filters: RetrievalSearchFilters,
    *,
    table_alias: str = "r",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []

    _append_in_filter(clauses, parameters, f"{table_alias}.source_type", filters.source_type)
    _append_in_filter(clauses, parameters, f"{table_alias}.origin_kind", filters.origin_kind)
    _append_in_filter(clauses, parameters, f"{table_alias}.document_id", filters.document_id)
    _append_equal(clauses, parameters, f"{table_alias}.zotero_item_key", filters.zotero_item_key)
    _append_equal(
        clauses,
        parameters,
        f"{table_alias}.zotero_attachment_key",
        filters.zotero_attachment_key,
    )
    _append_equal(
        clauses,
        parameters,
        f"{table_alias}.zotero_annotation_key",
        filters.zotero_annotation_key,
    )
    if filters.collection:
        clauses.append(f"instr(lower({table_alias}.collections_text), lower(?)) > 0")
        parameters.append(filters.collection)
    if filters.tag:
        clauses.append(f"instr(lower({table_alias}.tags_text), lower(?)) > 0")
        parameters.append(filters.tag)
    if filters.year is not None:
        clauses.append(f"{table_alias}.year = ?")
        parameters.append(filters.year)
    if filters.year_from is not None:
        clauses.append(f"{table_alias}.year >= ?")
        parameters.append(filters.year_from)
    if filters.year_to is not None:
        clauses.append(f"{table_alias}.year <= ?")
        parameters.append(filters.year_to)
    if filters.has_note_comment is not None:
        clauses.append(f"{table_alias}.has_note_comment = ?")
        parameters.append(int(filters.has_note_comment))
    if filters.has_zotero_uri is not None:
        clauses.append(f"{table_alias}.has_zotero_uri = ?")
        parameters.append(int(filters.has_zotero_uri))

    return (
        (" AND " + " AND ".join(clauses)) if clauses else "",
        parameters,
    )


def build_unicode_expression(
    plan: NormalizedQuery,
    *,
    mode: str,
    alias_terms: list[str],
) -> str | None:
    original_parts = _query_parts(plan)
    if plan.short_query:
        original_parts = []
    if mode == "precision":
        if not original_parts:
            return None
        if len(original_parts) == 1:
            variants = [*original_parts, *plan.identifier_variants]
            return _or_expression(variants)
        return " AND ".join(_fts_quote(part) for part in original_parts)

    coverage_parts = [*original_parts, *alias_terms]
    return _or_expression(coverage_parts) if coverage_parts else None


def build_trigram_expression(
    plan: NormalizedQuery,
    *,
    mode: str,
) -> str | None:
    if not plan.contains_cjk:
        return None
    cjk_terms = [
        token
        for token in [plan.normalized_query, *plan.phrases, *plan.terms]
        if len("".join(character for character in token if character.isalnum())) >= 3
    ]
    if not cjk_terms:
        return None
    if mode == "precision":
        return " AND ".join(_fts_quote(term) for term in _unique(cjk_terms))
    return _or_expression(cjk_terms)


def _query_parts(plan: NormalizedQuery) -> list[str]:
    if plan.phrases:
        return _unique([*plan.phrases, *plan.terms])
    return list(plan.terms)


def _or_expression(parts: list[str] | tuple[str, ...]) -> str | None:
    unique = _unique(parts)
    if not unique:
        return None
    return " OR ".join(_fts_quote(part) for part in unique)


def _fts_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _append_equal(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    value: Any,
) -> None:
    if value is not None:
        clauses.append(f"{column} = ?")
        parameters.append(value)


def _append_in_filter(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    value: Any,
) -> None:
    if value is None:
        return
    values = value if isinstance(value, list) else [value]
    if not values:
        return
    clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
    parameters.extend(values)


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
