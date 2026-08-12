from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.pdf_layout_service import classify_layout_line_role, normalize_ocr_line_text_for_display as _layout_display_text


FILTERED_LINE_ROLES = frozenset({"header", "page_number", "footer", "unknown"})
PARAGRAPH_LINE_ROLES = frozenset({"body", "heading"})
FORMULA_LINE_ROLES = frozenset({"formula", "figure_caption"})

# Fix5O policy freeze: Marker may contribute structure metadata, never candidate text.
MARKER_BODY_TEXT_INJECTION_ENABLED = False
MARKER_FORMULA_REPLACEMENT_ENABLED = False
MARKER_HEADING_HINT_ENABLED = True


@dataclass(frozen=True)
class OcrChunkLine:
    pdf_page: int
    line_index: int
    raw_text: str
    display_text: str
    search_text: str
    import_text: str
    bbox: dict[str, float]
    role: str
    confidence: float | None = None
    source_line_id: int | None = None
    source_line_key: str = ""


@dataclass(frozen=True)
class OcrLayoutChunk:
    node_order_index: int
    chunk_index: int
    heading_path: str
    chunk_text: str
    char_count: int
    token_count: int | None
    overlap_before: str | None
    overlap_after: str | None
    pdf_page_start: int | None
    pdf_page_end: int | None
    pdf_path: str | None
    search_text: str
    display_text: str
    source_line_ids: list[int] = field(default_factory=list)
    source_line_keys: list[str] = field(default_factory=list)
    source_line_start: int | None = None
    source_line_end: int | None = None
    confidence_summary: dict[str, Any] = field(default_factory=dict)
    section_title: str | None = None
    role: str = "body"


def normalize_ocr_line_text_for_display(text: Any) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"<\s*sub\s*>(.*?)<\s*/\s*sub\s*>", r"_\1", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<\s*sup\s*>(.*?)<\s*/\s*sup\s*>", r"^\1", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"</?\s*math\s*[^>]*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    replacements = {
        r"\rho": "ρ",
        r"\varrho": "ρ",
        r"\delta": "δ",
        r"\Delta": "Δ",
        r"\infty": "∞",
        r"\in": "∈",
        r"\sum": "∑",
        r"\leq": "≤",
        r"\le": "≤",
        r"\geq": "≥",
        r"\ge": "≥",
        r"\cdots": "…",
        r"\ldots": "…",
        r"\dots": "…",
        r"\times": "×",
        r"\cdot": "·",
        r"\to": "→",
        r"\rightarrow": "→",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = _layout_display_text(value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([，。；：,.!?;:)）])", r"\1", value)
    value = re.sub(r"([（(])\s+", r"\1", value)
    value = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", value)
    return value.strip()


def normalize_ocr_line_text_for_import(text: Any) -> str:
    return normalize_ocr_line_text_for_display(text)


def normalize_ocr_line_text_for_search(text: Any) -> str:
    value = normalize_ocr_line_text_for_display(text).casefold()
    value = re.sub(r"[`*#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def chunk_ocr_layout_lines(
    lines: list[Any],
    *,
    page_width: float | None = None,
    page_height: float | None = None,
    heading_path: str = "OCR Layout First",
    section_title: str | None = None,
    pdf_path: str | None = None,
    chunk_size_target: int = 700,
) -> list[OcrLayoutChunk]:
    prepared = prepare_ocr_chunk_lines(lines, page_width=page_width, page_height=page_height)
    eligible = [line for line in prepared if line.role not in FILTERED_LINE_ROLES and line.import_text]
    groups = _group_lines_into_units(eligible, chunk_size_target=max(120, int(chunk_size_target or 700)))
    chunks: list[OcrLayoutChunk] = []
    current_heading = section_title
    for group in groups:
        if not group:
            continue
        first_role = group[0].role
        if first_role == "heading":
            current_heading = group[0].display_text
        text = _join_group_text(group)
        if not text:
            continue
        source_ids = [line.source_line_id for line in group if line.source_line_id is not None]
        source_keys = [line.source_line_key for line in group if line.source_line_key]
        confidences = [line.confidence for line in group if line.confidence is not None]
        chunk_index = len(chunks)
        pages = [line.pdf_page for line in group]
        line_indexes = [line.line_index for line in group]
        chunk_heading = heading_path if not current_heading else f"{heading_path} / {current_heading}"
        chunks.append(
            OcrLayoutChunk(
                node_order_index=chunk_index,
                chunk_index=chunk_index,
                heading_path=chunk_heading,
                chunk_text=text,
                char_count=len(text),
                token_count=None,
                overlap_before=None,
                overlap_after=None,
                pdf_page_start=min(pages) if pages else None,
                pdf_page_end=max(pages) if pages else None,
                pdf_path=pdf_path,
                search_text=normalize_ocr_line_text_for_search(text),
                display_text=text,
                source_line_ids=source_ids,
                source_line_keys=source_keys,
                source_line_start=min(line_indexes) if line_indexes else None,
                source_line_end=max(line_indexes) if line_indexes else None,
                confidence_summary=_confidence_summary(confidences),
                section_title=current_heading,
                role=first_role if first_role in FORMULA_LINE_ROLES else "body",
            )
        )
    return chunks


def prepare_ocr_chunk_lines(
    lines: list[Any],
    *,
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[OcrChunkLine]:
    prepared: list[OcrChunkLine] = []
    for line in sorted(lines, key=lambda item: (_line_value(item, "pdf_page", 0), _line_value(item, "line_index", 0))):
        raw_text = str(_line_value(line, "text", "") or "")
        display_text = normalize_ocr_line_text_for_display(raw_text)
        bbox = _line_bbox(line)
        role = classify_layout_line_role(line, page_width, page_height)
        if role in {"body", "heading"} and _looks_like_running_header(display_text):
            role = "header"
        if role == "footer" and _looks_like_footer_position_body_content(display_text):
            role = "body"
        if role == "body" and _looks_like_stray_page_number(display_text, bbox, page_width):
            role = "page_number"
        if role == "body" and _looks_like_heading(display_text, bbox, page_width):
            role = "heading"
        pdf_page = int(_line_value(line, "pdf_page", 0) or 0)
        line_index = int(_line_value(line, "line_index", 0) or 0)
        source_id = _optional_int(_line_value(line, "id", None) or _line_value(line, "line_id", None))
        prepared.append(
            OcrChunkLine(
                pdf_page=pdf_page,
                line_index=line_index,
                raw_text=raw_text,
                display_text=display_text,
                search_text=normalize_ocr_line_text_for_search(display_text),
                import_text=normalize_ocr_line_text_for_import(display_text),
                bbox=bbox,
                role=role,
                confidence=_optional_float(_line_value(line, "confidence", None)),
                source_line_id=source_id,
                source_line_key=f"{pdf_page}:{line_index}",
            )
        )
    return prepared


def count_filtered_lines(lines: list[Any], *, page_width: float | None = None, page_height: float | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in prepare_ocr_chunk_lines(lines, page_width=page_width, page_height=page_height):
        counts[line.role] = counts.get(line.role, 0) + 1
    return {role: counts.get(role, 0) for role in sorted(counts)}


def filtered_line_diagnostics(
    lines: list[Any],
    *,
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "source_line_id": line.source_line_id,
            "source_line_key": line.source_line_key,
            "role": line.role,
            "text": line.display_text,
        }
        for line in prepare_ocr_chunk_lines(lines, page_width=page_width, page_height=page_height)
        if line.role in FILTERED_LINE_ROLES
    ]


def build_hybrid_candidates(
    candidates: list[dict[str, Any]],
    marker_structure: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply bounded Marker structure hints without losing Surya location anchors."""
    marker = marker_structure or {}
    headings = list(marker.get("headings") or [])
    formulas = list(marker.get("formula_blocks") or [])
    repetition_rejected = bool(marker.get("marker_rejected_repetition"))
    conflicts: list[dict[str, Any]] = []
    if repetition_rejected:
        conflicts.append(
            {
                "reason": "marker_rejected_repetition",
                "samples": list(marker.get("repetition_samples") or []),
            }
        )
    output: list[dict[str, Any]] = []
    unlocatable: list[int | None] = []
    formula_substitutions = 0
    heading_hints_applied = 0
    for candidate in candidates:
        source_ids = list(candidate.get("source_line_ids") or [])
        if not source_ids:
            unlocatable.append(candidate.get("candidate_id"))
            continue
        text = str(candidate.get("corrected_text") or candidate.get("chunk_text") or "")
        section_title = candidate.get("section_title")
        marker_heading = _matching_marker_heading(text, headings) if MARKER_HEADING_HINT_ENABLED else None
        if marker_heading:
            section_title = marker_heading
            heading_hints_applied += 1
        formula_role = candidate.get("role") == "formula" or bool(candidate.get("formula_role"))
        if formula_role and formulas and MARKER_FORMULA_REPLACEMENT_ENABLED and not repetition_rejected:
            marker_formula = str(formulas[0].get("text") or "")
            if _formula_hint_compatible(text, marker_formula):
                text = marker_formula
                formula_substitutions += 1
            else:
                conflicts.append(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "reason": "marker_surya_text_disagreement",
                        "marker_text": marker_formula[:160],
                    }
                )
        output.append(
            {
                **candidate,
                "chunk_text": text,
                "corrected_text": text,
                "section_title": section_title,
                "source_line_ids": source_ids,
                "source_line_keys": list(candidate.get("source_line_keys") or []),
                "marker_heading_applied": marker_heading,
                "marker_formula_applied": formula_role and formula_substitutions > 0 and text == str(formulas[0].get("text") or "") if formulas else False,
            }
        )
    return {
        "candidates": output,
        "marker_structure_available": bool(marker.get("marker_structure_available")),
        "marker_conflicts": conflicts,
        "marker_conflict_count": len(conflicts),
        "marker_rejected_repetition_count": int(marker.get("marker_rejected_repetition_count") or 0),
        "heading_hints_applied": heading_hints_applied,
        "formula_substitutions": formula_substitutions,
        "paragraph_break_hints_available": len(marker.get("candidate_paragraph_breaks") or []),
        "unlocatable_candidate_ids_rejected": unlocatable,
        "policy": {
            "marker_body_text_injection_enabled": MARKER_BODY_TEXT_INJECTION_ENABLED,
            "marker_formula_replacement_enabled": MARKER_FORMULA_REPLACEMENT_ENABLED,
            "marker_heading_hint_enabled": MARKER_HEADING_HINT_ENABLED,
            "text_source": "corrected_surya_candidate",
        },
    }


def _matching_marker_heading(text: str, headings: list[dict[str, Any]]) -> str | None:
    normalized_text = normalize_ocr_line_text_for_search(text)
    for heading in headings:
        value = str(heading.get("text") or "").strip()
        normalized = normalize_ocr_line_text_for_search(value)
        if normalized and normalized in normalized_text:
            return value
    return None


def _formula_hint_compatible(surya_text: str, marker_text: str) -> bool:
    surya = normalize_ocr_line_text_for_search(surya_text).replace("$", "")
    marker = normalize_ocr_line_text_for_search(marker_text).replace("$", "")
    identifiers = set(re.findall(r"[a-zA-Z]+|[δΔρ∞]", surya))
    marker_identifiers = set(re.findall(r"[a-zA-Z]+|[δΔρ∞]", marker))
    return bool(identifiers.intersection(marker_identifiers))


def _group_lines_into_units(lines: list[OcrChunkLine], *, chunk_size_target: int) -> list[list[OcrChunkLine]]:
    groups: list[list[OcrChunkLine]] = []
    current: list[OcrChunkLine] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            groups.append(current)
        current = []
        current_len = 0

    previous: OcrChunkLine | None = None
    for line in lines:
        line_len = len(line.import_text)
        starts_new = False
        if previous and line.pdf_page != previous.pdf_page:
            starts_new = True
        if line.role == "heading":
            starts_new = True
        elif line.role in FORMULA_LINE_ROLES:
            starts_new = bool(current)
        elif previous and previous.role in FORMULA_LINE_ROLES:
            starts_new = True
        elif current_len and current_len + line_len > chunk_size_target:
            starts_new = True
        if starts_new:
            flush()
        current.append(line)
        current_len += line_len + 1
        if line.role in FORMULA_LINE_ROLES:
            flush()
        previous = line
    flush()
    return groups


def _join_group_text(lines: list[OcrChunkLine]) -> str:
    if not lines:
        return ""
    if any(line.role in FORMULA_LINE_ROLES for line in lines):
        return "\n".join(line.import_text for line in lines if line.import_text).strip()
    if lines[0].role == "heading" and len(lines) > 1:
        heading = lines[0].import_text.strip()
        body = "".join(_line_text_with_joiner(line, index) for index, line in enumerate(lines[1:])).strip()
        body = re.sub(r"\s+", " ", body)
        return f"{heading}\n{body}".strip()
    text = "".join(_line_text_with_joiner(line, index) for index, line in enumerate(lines)).strip()
    return re.sub(r"\s+", " ", text)


def _line_text_with_joiner(line: OcrChunkLine, index: int) -> str:
    text = line.import_text
    if index == 0:
        return text
    if not text:
        return ""
    if re.match(r"^[，。；：,.!?;:)）]", text):
        return text
    if re.search(r"[\u4e00-\u9fff]$", text):
        return text
    return " " + text


def _looks_like_heading(text: str, bbox: dict[str, float], page_width: float | None) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 24:
        return False
    if re.search(r"[。；;,.，]", compact):
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
    if cjk_count < 2:
        return False
    width = float(page_width or 0) or max(float(bbox.get("x1", 0.0)), 1.0)
    line_width = max(0.0, float(bbox.get("x1", 0.0)) - float(bbox.get("x0", 0.0)))
    return line_width <= width * 0.45


def _looks_like_stray_page_number(text: str, bbox: dict[str, float], page_width: float | None) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"\d{1,4}|[ivxlcdmIVXLCDM]{1,8}", compact):
        return False
    width = float(page_width or 0) or max(float(bbox.get("x1", 0.0)), 1.0)
    center_ratio = ((float(bbox.get("x0", 0.0)) + float(bbox.get("x1", 0.0))) / 2.0) / width
    return center_ratio >= 0.72 or center_ratio <= 0.18


def _looks_like_running_header(text: str) -> bool:
    compact = re.sub(r"[\s·・•]+", "", text)
    section = r"第[一二三四五六七八九十百零〇0-9]+部分"
    page_prefix = r"\d{1,4}"
    header_title = r"(?:图算法|算法导论|高级设计和分析技术)"
    return bool(
        re.fullmatch(rf"{page_prefix}(?:{section})?{header_title}", compact)
        or re.fullmatch(rf"{page_prefix}{section}", compact)
        or re.fullmatch(rf"{section}{header_title}", compact)
    )


def _looks_like_footer_position_body_content(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    return bool(
        re.search(r"[，。；：,:]|\\subseteq|⊆|∈|=", text)
        or re.match(r"^\d+[.、]\s*\S", compact)
    )


def _confidence_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "avg": None}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "avg": round(sum(values) / len(values), 4),
    }


def _line_value(line: Any, key: str, default: Any = None) -> Any:
    if isinstance(line, dict):
        return line.get(key, default)
    return getattr(line, key, default)


def _line_bbox(line: Any) -> dict[str, float]:
    raw = _line_value(line, "bbox", {}) or {}
    if isinstance(raw, str):
        return {}
    try:
        return {key: float(raw[key]) for key in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return {}


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
