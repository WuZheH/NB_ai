from __future__ import annotations

from typing import Any, Mapping


SOURCE_MODES = {"note_led", "source_led", "joint_led", "unknown"}

SOURCE_BALANCE_POLICY: dict[str, bool] = {
    "treat_user_note_as_primary": True,
    "treat_source_excerpt_as_primary": True,
    "do_not_force_note_to_dominate_source": True,
    "do_not_reduce_source_to_citation_only": True,
    "preserve_original_note_text": True,
}

SOURCE_COVERAGE_REQUIREMENTS = [
    "explain_user_note_contribution_when_note_present",
    "explain_source_excerpt_contribution_when_source_present",
    "explain_linked_object_contribution_when_objects_present",
    "report_source_balance_warnings_when_one_primary_source_is_missing_or_dominates",
    "do_not_emit_raw_note_text_as_rewritten_output",
]

SOURCE_BALANCE_WARNING_CODES = {
    "missing_user_note_contribution",
    "missing_source_excerpt_contribution",
    "missing_linked_object_contribution",
    "source_imbalance_user_note_dominates",
    "source_imbalance_source_excerpt_dominates",
    "source_conflict_unresolved",
    "unsupported_mechanism_claim",
}


def infer_source_mode(
    *,
    note_text: Any = None,
    selected_text: Any = None,
    chunk_text: Any = None,
) -> str:
    note_present = bool(_clean(note_text))
    source_present = bool(_clean(selected_text) or _clean(chunk_text))
    if note_present and source_present:
        return "joint_led"
    if note_present:
        return "note_led"
    if source_present:
        return "source_led"
    return "unknown"


def build_mechanism_source_pack(
    *,
    note: Mapping[str, Any] | None = None,
    source_note_id: Any = None,
    note_text: Any = None,
    selected_text: Any = None,
    tags: list[Any] | None = None,
    source_excerpt: Mapping[str, Any] | None = None,
    matched_chunks: list[Mapping[str, Any]] | None = None,
    nearby_chunks: list[Mapping[str, Any]] | None = None,
    linked_objects: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    note = note or {}
    source_excerpt = source_excerpt or {}
    primary_note_text = note_text if note_text is not None else note.get("note_text")
    primary_selected_text = (
        selected_text
        if selected_text is not None
        else note.get("selected_text") or source_excerpt.get("selected_text")
    )
    primary_chunk_text = source_excerpt.get("chunk_text")
    source_mode = infer_source_mode(
        note_text=primary_note_text,
        selected_text=primary_selected_text,
        chunk_text=primary_chunk_text,
    )
    return {
        "source_mode": source_mode,
        "primary_user_note": {
            "note_id": source_note_id or note.get("server_note_id") or note.get("client_note_id"),
            "note_text": primary_note_text,
            "tags": list(tags if tags is not None else note.get("user_tags") or []),
            "role": "primary_source",
        },
        "primary_source_excerpt": {
            "selected_text": primary_selected_text,
            "chunk_text": primary_chunk_text,
            "chunk_id": source_excerpt.get("chunk_id"),
            "document_id": source_excerpt.get("document_id"),
            "document_title": source_excerpt.get("document_title"),
            "chapter_id": source_excerpt.get("chapter_id"),
            "chapter_title": source_excerpt.get("chapter_title"),
            "pdf_page": source_excerpt.get("pdf_page"),
            "page_label": source_excerpt.get("page_label") or note.get("page_label"),
            "role": "primary_source",
        },
        "context_sources": {
            "nearby_chunks": list(nearby_chunks or []),
            "matched_chunks": list(matched_chunks or []),
        },
        "linked_knowledge": {
            "objects": list(linked_objects or []),
            "role": "semantic_support",
        },
        "source_balance_policy": dict(SOURCE_BALANCE_POLICY),
        "source_coverage_requirements": list(SOURCE_COVERAGE_REQUIREMENTS),
    }


def contribution_warnings(
    candidate: Mapping[str, Any],
    *,
    linked_objects_present: bool = False,
) -> list[str]:
    warnings: list[str] = []
    source_mode = str(candidate.get("source_mode") or "").strip()
    user_note_contribution = _clean(candidate.get("user_note_contribution"))
    source_excerpt_contribution = _clean(candidate.get("source_excerpt_contribution"))
    linked_object_contribution = _clean(candidate.get("linked_object_contribution"))
    if not user_note_contribution:
        warnings.append("missing_user_note_contribution")
    if not source_excerpt_contribution:
        warnings.append("missing_source_excerpt_contribution")
    if linked_objects_present and not linked_object_contribution:
        warnings.append("missing_linked_object_contribution")
    if source_mode == "note_led" and not source_excerpt_contribution:
        warnings.append("source_imbalance_user_note_dominates")
    if source_mode == "source_led" and not user_note_contribution:
        warnings.append("source_imbalance_source_excerpt_dominates")
    return list(dict.fromkeys(warnings))


def _clean(value: Any) -> str:
    return str(value or "").strip()
