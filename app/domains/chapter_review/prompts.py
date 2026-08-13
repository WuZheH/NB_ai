"""Pure prompt formatting helpers and lazy prompt entry-point exports."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping


def _sanitize_context_markdown_for_prompt(
    markdown: str,
    *,
    canonical_heading: str,
    force_canonical: bool,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    lines: list[str] = []
    last_heading = ""
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            raw_heading = line[3:].strip()
            clean_heading = (
                canonical_heading
                if force_canonical and canonical_heading
                else _clean_prompt_heading_path(raw_heading, canonical_heading)
            )
            if raw_heading != clean_heading:
                warnings.append(f"heading_sanitized:{raw_heading}->{clean_heading}")
            if clean_heading and clean_heading != last_heading:
                lines.append(f"## {clean_heading}")
                last_heading = clean_heading
            continue
        lines.append(line)
    return "\n".join(lines).strip(), list(dict.fromkeys(warnings))


def _clean_prompt_heading_path(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    clean_segments: list[str] = []
    for segment in [part.strip() for part in text.split("/") if part.strip()]:
        clean = _clean_prompt_heading_segment(segment)
        if clean and clean not in clean_segments:
            clean_segments.append(clean)
    if clean_segments:
        return " / ".join(clean_segments)
    return fallback or text


def _clean_prompt_heading_segment(segment: str) -> str | None:
    text = re.sub(r"\s+", " ", str(segment or "")).strip(" .")
    chapter_match = re.match(
        r"^(?P<chapter>\d+)[\.)]\s*(?P<title>Optimization)\b.*$",
        text,
        flags=re.IGNORECASE,
    )
    if chapter_match:
        return f"{chapter_match.group('chapter')} Optimization"
    section_match = re.match(
        r"^(?P<chapter>\d+)\.(?P<section>\d{1,2})(?:\.\d+)*\.?\s+(?P<title>.+)$",
        text,
    )
    if section_match:
        section = int(section_match.group("section"))
        if section > 20:
            return None
        title = re.sub(r"\s+\d+(?:\.\d+)*$", "", section_match.group("title")).strip(" .")
        return f"{section_match.group('chapter')}.{section} {title}"
    return None


def _prompt_text_excerpt(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + "\n[truncated]"


def _prompt_text_was_truncated(value: Any, limit: int) -> bool:
    return len(str(value or "").strip()) > limit


def _prompt_context_excerpt(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    marker = (
        "\n[local_context truncated; candidate-level selected_text/note_text/"
        "chunk_evidence_text preserved]\n"
    )
    remaining = max(0, limit - len(marker))
    head_len = remaining // 2
    tail_len = remaining - head_len
    return text[:head_len].rstrip() + marker + text[-tail_len:].lstrip()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _anchor_note_key(anchor: Mapping[str, Any]) -> str:
    return str(
        anchor.get("zotero_annotation_key")
        or anchor.get("server_note_id")
        or anchor.get("client_note_id")
        or anchor.get("note_anchor_id")
        or ""
    ).strip()


def _note_key(note: Mapping[str, Any]) -> str:
    return str(
        note.get("zotero_annotation_key")
        or note.get("server_note_id")
        or note.get("client_note_id")
        or note.get("note_id")
        or ""
    ).strip()


def _page_range_label(chunk: Mapping[str, Any]) -> str:
    start = chunk.get("pdf_page_start")
    end = chunk.get("pdf_page_end")
    if start is None and end is None:
        return "unknown"
    if end is None or str(start) == str(end):
        return str(start)
    return f"{start}-{end}"


def _supporting_evidence_payload(note: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_role": "evidence_only_annotation",
        "source_note_id": note.get("note_id"),
        "note_anchor_id": note.get("note_anchor_id"),
        "server_note_id": note.get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "zotero_annotation_key": note.get("zotero_annotation_key"),
        "page": note.get("page"),
        "page_label": note.get("page_label"),
        "selected_text_preview": note.get("selected_text_preview"),
        "selected_text": note.get("selected_text"),
        "matched_chunk_id": note.get("matched_chunk_id"),
        "matched_chunk_ids": note.get("matched_chunk_ids"),
        "anchor_method": note.get("anchor_method"),
        "chunk_evidence_text": note.get("chunk_evidence_text"),
        "warnings": note.get("warnings") or [],
    }


def _candidate_preview(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "note_id": item.get("note_id"),
        "note_anchor_id": item.get("note_anchor_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "page": item.get("page"),
        "page_label": item.get("page_label"),
        "note_text_preview": _preview(item.get("note_text"), 220),
        "selected_text_preview": _preview(
            item.get("selected_text_preview") or item.get("selected_text"),
            220,
        ),
        "matched_chunk_id": item.get("matched_chunk_id"),
        "anchor_method": item.get("anchor_method"),
        "warnings": item.get("warnings") or [],
        "reviewer_warning": item.get("reviewer_warning"),
    }


def _supporting_evidence_preview(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_note_id": item.get("source_note_id"),
        "note_anchor_id": item.get("note_anchor_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "page": item.get("page"),
        "page_label": item.get("page_label"),
        "selected_text_preview": _preview(
            item.get("selected_text_preview") or item.get("selected_text"),
            220,
        ),
        "matched_chunk_id": item.get("matched_chunk_id"),
        "anchor_method": item.get("anchor_method"),
        "warnings": item.get("warnings") or [],
    }


def _warning_item(
    package: Mapping[str, Any],
    zotero_annotation_key: str,
) -> dict[str, Any] | None:
    for item in package.get("correction_candidates") or []:
        if item.get("zotero_annotation_key") == zotero_annotation_key:
            return _candidate_preview(item)
    return None


def _matched_chunk_ids(note: Mapping[str, Any]) -> list[int]:
    values = _json_list(note.get("matched_chunk_ids_json"))
    result = [_int_or_none(value) for value in values]
    clean = [value for value in result if value is not None]
    matched_chunk_id = _int_or_none(note.get("matched_chunk_id"))
    if matched_chunk_id is not None and matched_chunk_id not in clean:
        clean.insert(0, matched_chunk_id)
    return clean


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


_PROMPT_SERVICE_EXPORTS = {
    "build_note_correction_copy_ready_prompt",
    "build_note_correction_scoped_copy_ready_prompt",
    "write_chapter_note_correction_prompt_package",
}
_PIPELINE_SERVICE_EXPORTS = {
    "build_note_classification_copy_ready_prompt",
    "build_note_classification_copy_ready_prompt_legacy",
    "build_phase7a_classification_prompt_preview",
    "build_phase7a_classification_validator_contract",
}


def __getattr__(name: str) -> Any:
    if name in _PROMPT_SERVICE_EXPORTS:
        from app.services import chapter_note_correction_prompt_service

        return getattr(chapter_note_correction_prompt_service, name)
    if name in _PIPELINE_SERVICE_EXPORTS:
        from app.services import chapter_review_pipeline_service

        return getattr(chapter_review_pipeline_service, name)
    raise AttributeError(name)


__all__ = sorted(_PROMPT_SERVICE_EXPORTS | _PIPELINE_SERVICE_EXPORTS)
