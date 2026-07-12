from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import PROJECT_ROOT
from app.services.pdf_backend_service import load_fitz_backend
from app.services import library_service, zotero_source_cache_service


STAGING_ROOT = PROJECT_ROOT / "outputs" / "import_staging"
PREVIEW_CHARS = 4000
SECTION_PREVIEW_LIMIT = 10
JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,80}$")
MARKDOWN_SOURCE_TYPES = {"converted_md", "marker_output", "manual_md_import"}

# Heading normalization patterns.
# Matches "1.", "3.1.", "4.2.1." — first component 1-99, rest 0-99, max 3 levels.
_HEADING_RE = re.compile(
    r"(?<!\d)(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+(?=[A-Z])",
)
# Reject numbers that look like floats/table data (≥4 decimal digits).
_PSEUDO_NUMBER_RE = re.compile(r"^\d+\.\d{4,}")

# "Abstract" — word boundary + followed by capital letter.
_ABSTRACT_RE = re.compile(
    r"\b(Abstract)\s+(?=[A-Z])",
)

# "References" — word boundary + at end of text or followed by newline/ref bracket.
_REFERENCES_RE = re.compile(
    r"\b(References)\s*(?:$|(?=\n\s*\[))",
)

# Minimal title capture: heading number + capitalized words until a lowercase word boundary.
# We stop at the first clearly non-title token (lowercase article, verb, preposition start).
_TITLE_STOP_TOKENS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "can", "could", "will", "would", "may",
    "might", "shall", "should", "must", "in", "on", "at", "to",
    "for", "of", "by", "with", "from", "as", "it", "its", "we",
    "they", "he", "she", "this", "that", "these", "those",
    "use", "using", "used", "based", "proposed", "recently",
    "aims", "first", "early", "previous", "several", "many",
    "some", "all", "each", "every", "both", "other", "such",
    "which", "not", "also", "however", "therefore", "thus",
})
_TITLE_TERMINATORS_RE = re.compile(
    r"(?<!\.[\s])\.\s+[A-Z][a-z]"
)


class ImportPreviewError(ValueError):
    pass


def create_import_preview(payload: dict[str, Any]) -> dict[str, Any]:
    source_type, payload, source_path = _resolve_import_preview_source(payload)
    source_sha256 = _sha256(source_path)
    import_job_id = _new_job_id(source_sha256)
    job_dir = _job_dir(import_job_id)
    job_dir.mkdir(parents=True, exist_ok=False)

    created_at = _now()
    title = _title(payload.get("title_hint"), source_path)
    if source_type in MARKDOWN_SOURCE_TYPES:
        extraction = _extract_converted_markdown(source_path, source_type=source_type)
    else:
        extraction = _extract_pdf_markdown(source_path)
    heading_result = _normalize_headings(extraction["markdown"])

    paper_md_path = job_dir / "paper.md"
    notes_md_path = job_dir / "notes.md"
    manifest_path = job_dir / "import_manifest.json"
    source_trace_path = job_dir / "source_trace.json"

    paper_md_path.write_text(
        _paper_markdown(
            import_job_id=import_job_id,
            title=title,
            source_type=source_type,
            pdf_sha256=source_sha256,
            extraction=extraction,
            heading_result=heading_result,
        ),
        encoding="utf-8",
    )
    notes_md_path.write_text(
        _notes_markdown(
            import_job_id=import_job_id,
            notes_payload=payload.get("notes_payload"),
        ),
        encoding="utf-8",
    )

    manifest = {
        "import_job_id": import_job_id,
        "status": "preview_created",
        "source_type": source_type,
        "title_hint": payload.get("title_hint"),
        "zotero_pdf_source_id": payload.get("zotero_pdf_source_id"),
        "zotero_source_id": payload.get("zotero_source_id"),
        "zotero_item_key": payload.get("zotero_item_key"),
        "zotero_attachment_key": payload.get("zotero_attachment_key"),
        "zotero_select_uri": payload.get("zotero_select_uri"),
        "zotero_open_pdf_uri": payload.get("zotero_open_pdf_uri"),
        "pdf_sha256": source_sha256,
        "source_sha256": source_sha256,
        "created_at": created_at,
        "files": {
            "paper_md": _relative(paper_md_path),
            "notes_md": _relative(notes_md_path),
            "source_trace": _relative(source_trace_path),
        },
        "heading_normalization": heading_result["normalization"],
        "safety": _safety(),
    }
    source_trace = {
        "source_type": source_type,
        "zotero_item_key": payload.get("zotero_item_key"),
        "zotero_attachment_key": payload.get("zotero_attachment_key"),
        "zotero_select_uri": payload.get("zotero_select_uri"),
        "zotero_open_pdf_uri": payload.get("zotero_open_pdf_uri"),
        "zotero_pdf_source_id": payload.get("zotero_pdf_source_id"),
        "zotero_source_id": payload.get("zotero_source_id"),
        "pdf_sha256": source_sha256,
        "source_sha256": source_sha256,
        "source_pdf_path": _display_path(source_path) if source_type not in MARKDOWN_SOURCE_TYPES else None,
        "source_markdown_path": _display_path(source_path) if source_type in MARKDOWN_SOURCE_TYPES else None,
        "raw_local_path_returned_to_frontend": False,
        "page_extraction_summary": extraction["summary"],
        "sections": heading_result["sections"],
        "annotation_refs": [],
    }
    _write_json(manifest_path, manifest)
    _write_json(source_trace_path, source_trace)

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "paper_md_path": _relative(paper_md_path),
        "notes_md_path": _relative(notes_md_path),
        "manifest_path": _relative(manifest_path),
        "source_trace_path": _relative(source_trace_path),
        **_safety_response(),
    }


def get_import_preview(import_job_id: str) -> dict[str, Any]:
    job_dir = _existing_job_dir(import_job_id)
    manifest_path = job_dir / "import_manifest.json"
    paper_md_path = job_dir / "paper.md"
    notes_md_path = job_dir / "notes.md"
    if not manifest_path.is_file() or not paper_md_path.is_file() or not notes_md_path.is_file():
        raise ImportPreviewError("Import preview files are incomplete.")
    manifest = _read_json(manifest_path)
    source_trace_path = job_dir / "source_trace.json"
    source_trace = _read_json(source_trace_path) if source_trace_path.is_file() else {}
    heading_norm = manifest.get("heading_normalization", {})
    sections = source_trace.get("sections", [])
    section_preview = [
        {"section_id": s["section_id"], "title": s["title"], "level": s["level"], "pdf_page": s.get("pdf_page")}
        for s in sections[:SECTION_PREVIEW_LIMIT]
    ]
    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "manifest": manifest,
        "paper_md_preview": _preview(paper_md_path),
        "notes_md_preview": _preview(notes_md_path),
        "headings_detected": heading_norm.get("headings_detected", 0),
        "sections_detected": heading_norm.get("sections_detected", 0),
        "section_preview": section_preview,
        **_safety_response(),
    }


def append_import_note(import_job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_dir = _existing_job_dir(import_job_id)
    notes_md_path = job_dir / "notes.md"
    source_trace_path = job_dir / "source_trace.json"
    if not notes_md_path.is_file() or not source_trace_path.is_file():
        raise ImportPreviewError("Import preview files are incomplete.")

    now = _now()
    raw_note = str(payload.get("raw_note") or "").strip()
    user_judgement = str(payload.get("user_judgement") or "").strip()
    annotation = payload.get("zotero_annotation")
    notes_md_path.write_text(
        notes_md_path.read_text(encoding="utf-8")
        + _note_append_block(now=now, raw_note=raw_note, user_judgement=user_judgement, annotation=annotation),
        encoding="utf-8",
    )

    source_trace = _read_json(source_trace_path)
    if isinstance(annotation, dict):
        refs = list(source_trace.get("annotation_refs") or [])
        refs.append(
            {
                "captured_at": now,
                "annotation_id": annotation.get("annotation_id"),
                "pdf_page": annotation.get("pdf_page"),
                "selected_text": annotation.get("selected_text"),
                "annotation_comment": annotation.get("annotation_comment"),
            }
        )
        source_trace["annotation_refs"] = refs
        _write_json(source_trace_path, source_trace)

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "notes_md_path": _relative(notes_md_path),
        "source_trace_path": _relative(source_trace_path),
        **_safety_response(),
    }


def commit_import_preview(import_job_id: str) -> dict[str, Any]:
    _existing_job_dir(import_job_id)
    return {
        "status": "not_implemented",
        "message": "Commit import is reserved for Phase 18E-ImportCommit-1.",
        "core_db_write_performed": False,
        "committed_to_library": False,
        "external_llm_called": False,
    }


def _resolve_preview_pdf_path(value: Any) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ImportPreviewError("pdf_path is required.")
    forbidden_scheme = "file" + "://"
    if raw.lower().startswith(forbidden_scheme):
        raise ImportPreviewError("pdf_path must be a safe local project path, not a URI.")
    resolved = library_service.resolve_safe_pdf_path(raw)
    if resolved is None:
        raise ImportPreviewError("pdf_path is outside allowed PDF roots or is not a PDF.")
    if not resolved.is_file():
        raise ImportPreviewError("PDF file does not exist.")
    return resolved


def _resolve_import_preview_source(payload: dict[str, Any]) -> tuple[str, dict[str, Any], Path]:
    source_type = str(payload.get("source_type") or "").strip()
    if source_type == "local_pdf":
        return source_type, payload, _resolve_preview_pdf_path(payload.get("pdf_path"))
    if source_type == "zotero_pdf":
        source = _zotero_preview_source(payload)
        resolved_payload = {
            **payload,
            "pdf_path": source["resolved_pdf_path"],
            "title_hint": payload.get("title_hint") or source["title_hint"],
            "zotero_pdf_source_id": source["zotero_pdf_source_id"],
            "zotero_source_id": source["zotero_pdf_source_id"],
            "zotero_item_key": source["zotero_item_key"],
            "zotero_attachment_key": source["zotero_attachment_key"],
            "zotero_select_uri": source["zotero_select_uri"],
            "zotero_open_pdf_uri": source["zotero_open_pdf_uri"],
        }
        return source_type, resolved_payload, Path(source["resolved_pdf_path"])
    if source_type in MARKDOWN_SOURCE_TYPES:
        md_path = (
            payload.get("converted_md_path")
            or payload.get("markdown_path")
            or payload.get("marker_output_path")
            or payload.get("pdf_path")
        )
        resolved = _resolve_preview_markdown_path(md_path)
        return source_type, {**payload, "converted_md_path": str(resolved)}, resolved
    raise ImportPreviewError("source_type must be local_pdf, zotero_pdf, converted_md, marker_output, or manual_md_import.")


def _resolve_preview_markdown_path(value: Any) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ImportPreviewError("converted_md_path is required.")
    forbidden_scheme = "file" + "://"
    if raw.lower().startswith(forbidden_scheme):
        raise ImportPreviewError("converted_md_path must be a safe local project path, not a URI.")
    path = Path(raw)
    if ".." in path.parts:
        raise ImportPreviewError("converted_md_path path traversal is not allowed.")
    resolved = path.resolve(strict=False)
    if resolved.suffix.lower() != ".md":
        raise ImportPreviewError("converted_md_path must point to a Markdown file.")
    if not resolved.is_file():
        raise ImportPreviewError("converted Markdown file does not exist.")
    if not _is_relative_to(resolved, PROJECT_ROOT.resolve(strict=False)):
        raise ImportPreviewError("converted_md_path is outside the project root.")
    return resolved


def _zotero_preview_source(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = payload.get("zotero_pdf_source_id")
    if source_id is None:
        raise ImportPreviewError("zotero_pdf_source_id is required for zotero_pdf import preview.")
    try:
        source_id_int = int(source_id)
    except (TypeError, ValueError) as exc:
        raise ImportPreviewError("zotero_pdf_source_id must be an integer.") from exc
    try:
        source = zotero_source_cache_service.get_pdf_source(source_id_int)
    except ValueError as exc:
        raise ImportPreviewError(str(exc)) from exc
    if not source.path_exists or source.cache_status not in {"available", "duplicate"}:
        raise ImportPreviewError("Selected Zotero PDF source is not available.")
    if not source.resolved_pdf_path:
        raise ImportPreviewError("Selected Zotero PDF source has no resolved PDF path.")
    pdf_path = Path(source.resolved_pdf_path).resolve(strict=False)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.is_file():
        raise ImportPreviewError("Selected Zotero PDF source is not a readable PDF.")
    return {
        "resolved_pdf_path": str(pdf_path),
        "title_hint": source.title,
        "zotero_item_key": source.zotero_item_key,
        "zotero_attachment_key": source.zotero_attachment_key,
        "zotero_select_uri": source.zotero_select_uri,
        "zotero_open_pdf_uri": source.zotero_open_pdf_uri,
        "zotero_pdf_source_id": source.id,
    }


def _extract_pdf_markdown(pdf_path: Path) -> dict[str, Any]:
    fitz = load_fitz_backend()
    pages: list[str] = []
    page_count = 0
    pages_with_text = 0
    try:
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
            for index, page in enumerate(document, start=1):
                text = " ".join((page.get_text("text") or "").split())
                if text:
                    pages_with_text += 1
                pages.append(f"<!-- PDF_PAGE: {index} -->\n\n{text or '[No extractable text on this page]'}")
    except Exception as exc:
        raise ImportPreviewError(f"PDF text extraction failed: {exc}") from exc
    return {
        "markdown": "\n\n".join(pages).strip() + "\n",
        "summary": {
            "backend": "pymupdf_text",
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "fallback_reason": None,
        },
    }


def _extract_converted_markdown(md_path: Path, *, source_type: str) -> dict[str, Any]:
    markdown = md_path.read_text(encoding="utf-8", errors="replace").strip()
    if not markdown:
        raise ImportPreviewError("converted Markdown file is empty.")
    marker_count = len(re.findall(r"<!--\s*PDF_PAGE:\s*\d+\s*-->", markdown))
    return {
        "markdown": markdown + "\n",
        "summary": {
            "backend": source_type,
            "page_count": marker_count or None,
            "pages_with_text": marker_count or None,
            "fallback_reason": None,
            "converted_md_path": _relative(md_path),
        },
    }


def _paper_markdown(
    import_job_id: str,
    title: str,
    source_type: str,
    pdf_sha256: str,
    extraction: dict[str, Any],
    heading_result: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "---",
            f"import_job_id: {import_job_id}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"source_type: {source_type}",
            "status: preview",
            f"pdf_sha256: {pdf_sha256}",
            "---",
            "",
            f"# {title}",
            "",
            "## Extraction Summary",
            "",
            f"- backend: {extraction['summary'].get('backend')}",
            f"- page_count: {extraction['summary'].get('page_count')}",
            f"- pages_with_text: {extraction['summary'].get('pages_with_text')}",
            f"- headings_detected: {heading_result['normalization']['headings_detected']}",
            f"- sections_detected: {heading_result['normalization']['sections_detected']}",
            "",
            "## Extracted Text",
            "",
            heading_result["normalized_text"].strip(),
            "",
        ]
    )


def _normalize_headings(raw_text: str) -> dict[str, Any]:
    """Apply lightweight heading normalization to extracted PDF text.

    Detects Abstract, numbered sections (1., 2., 3.1., etc.), and
    References, and converts them to Markdown headings (## / ### / ####).

    Returns the normalized text, a section map, and normalization metadata.
    """
    page_blocks, page_numbers = _split_page_blocks(raw_text)
    normalized_blocks: list[str] = []
    sections: list[dict[str, Any]] = []
    section_count = 0
    heading_count = 0
    seen_page_markers: set[int] = set()
    seen_level2_numbers: set[str] = set()

    for block_text, page_num in zip(page_blocks, page_numbers):
        marker = f"<!-- PDF_PAGE: {page_num} -->"
        if page_num in seen_page_markers:
            marker = ""
        else:
            seen_page_markers.add(page_num)
        if not block_text.strip():
            normalized_blocks.append(f"{marker}\n\n[No extractable text on this page]")
            continue

        normalized_text, block_sections = _normalize_heading_block(block_text, page_num)
        heading_count += len(block_sections)

        if block_sections:
            for sec in block_sections:
                # Global level-2 dedup: only first occurrence of each number across all pages.
                if sec["level"] == 2 and sec["number"] and sec["number"] in seen_level2_numbers:
                    continue
                if sec["level"] == 2 and sec["number"]:
                    seen_level2_numbers.add(sec["number"])
                section_count += 1
                section_id = _section_id(sec["number"], sec["title"], section_count)
                # Ensure section_id uniqueness — append suffix if duplicate.
                base_id = section_id
                suffix = 2
                while any(s.get("section_id") == section_id for s in sections):
                    section_id = f"{base_id}_{suffix}"
                    suffix += 1
                sections.append({
                    "section_id": section_id,
                    "number": sec["number"],
                    "title": sec["title"],
                    "level": sec["level"],
                    "pdf_page": sec["pdf_page"],
                    "heading_text": sec["heading_text"],
                    "markdown_heading": sec["markdown_heading"],
                })

        marker_prefix = f"{marker}\n\n" if marker else ""
        normalized_blocks.append(f"{marker_prefix}{normalized_text}")

    return {
        "normalized_text": "\n\n".join(normalized_blocks).strip() + "\n",
        "sections": sections,
        "normalization": {
            "enabled": True,
            "headings_detected": heading_count,
            "sections_detected": section_count,
            "strategy": "local_rule_based",
            "external_llm_called": False,
        },
    }


def _split_page_blocks(raw_text: str) -> tuple[list[str], list[int]]:
    """Split raw extraction text by PDF_PAGE markers."""
    pattern = re.compile(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->\s*")
    parts = pattern.split(raw_text)
    # parts[0] = before first marker, parts[1] = page num, parts[2] = text, ...
    blocks: list[str] = []
    pages: list[int] = []
    # Skip preamble before first marker
    idx = 0
    if parts and not pattern.fullmatch(parts[0] or ""):
        # text before first page marker - treat as page 1
        if parts[0].strip():
            blocks.append(parts[0])
            pages.append(1)
        idx = 1
    for i in range(idx, len(parts) - 1, 2):
        page_num = int(parts[i])
        text = parts[i + 1] if i + 1 < len(parts) else ""
        blocks.append(text)
        pages.append(page_num)
    return blocks, pages


def _normalize_heading_block(text: str, page_num: int) -> tuple[str, list[dict[str, Any]]]:
    """Detect and normalize headings within a single page's text block."""
    sections: list[dict[str, Any]] = []

    # Collect heading candidates: (pos, display_heading, full_heading_text, level, number)
    # display_heading = clean short line for paper.md
    # full_heading_text = richer metadata for source_trace.sections
    candidates: list[tuple[int, str, str, int, str]] = []

    # 1. Find "Abstract"
    for match in _ABSTRACT_RE.finditer(text):
        pos = match.start(1)
        candidates.append((pos, "Abstract", "Abstract", 2, ""))

    # 2. Find "References" (standalone at end of page/section)
    for match in _REFERENCES_RE.finditer(text):
        pos = match.start(1)
        candidates.append((pos, "References", "References", 2, ""))

    # 3. Find numbered sections: "1.", "3.1.", "4.2.1." etc.
    for match in _HEADING_RE.finditer(text):
        pos = match.start(0)
        number = match.group(1).rstrip(".")
        if _PSEUDO_NUMBER_RE.match(number):
            continue  # skip floats like "0.9605"
        if number.startswith("0."):
            continue  # skip "0.1" type — not a section number
        num_parts = number.count(".") + 1
        level = min(num_parts + 1, 4)  # 1.→2, 1.1.→3, 1.1.1.→4
        raw_title = _extract_raw_text(text, match.end(0), max_words=4)
        short_title = _extract_heading_line_title(raw_title)
        full_title = _extract_title_words(raw_title)
        if not _valid_heading_title(full_title, level):
            continue
        display_heading = f"{number}. {short_title}" if short_title else number
        full_heading_text = f"{number}. {full_title}" if full_title else number
        candidates.append((pos, display_heading, full_heading_text, level, number))

    if not candidates:
        return text, []

    # Sort by position, deduplicate overlapping matches
    candidates.sort(key=lambda c: (c[0], -c[3]))
    filtered: list[tuple[int, str, str, int, str]] = []
    last_end = -1
    for pos, display_heading, full_heading_text, level, number in candidates:
        if pos < last_end:
            continue
        filtered.append((pos, display_heading, full_heading_text, level, number))
        last_end = max(last_end, pos + len(display_heading) + 20)

    # Build normalized text — insert clean heading lines, body follows naturally
    result = text
    for pos, display_heading, _full_heading_text, level, number in reversed(filtered):
        prefix = "#" * level
        insert = f"\n\n{prefix} {display_heading}\n\n"
        before = result[:pos]
        after = result[pos:]
        after = _remove_heading_prefix(after, number, display_heading)
        result = before.rstrip() + insert + after.lstrip()

    # Clean up whitespace
    result = re.sub(r"\n{3,}", "\n\n", result)

    for pos, display_heading, full_heading_text, level, number in filtered:
        sections.append({
            "number": number,
            "title": full_heading_text,
            "level": level,
            "pdf_page": page_num,
            "heading_text": full_heading_text,
            "markdown_heading": f"{'#' * level} {full_heading_text}",
        })

    return result, sections


def _extract_raw_text(text: str, start_pos: int, max_words: int = 8) -> str:
    """Extract up to max_words words from text at start_pos."""
    remaining = text[start_pos:].lstrip()
    words = remaining.split()
    return " ".join(words[:max_words])


def _extract_heading_line_title(raw_title: str) -> str:
    """Extract only 2-3 title words for the clean paper.md heading line."""
    words = raw_title.split()
    if not words:
        return ""
    title_words: list[str] = []
    for word in words:
        stripped = word.rstrip(",;.!?:\"")
        if not stripped:
            continue
        lower = stripped.lower()
        # Stop at body-starters.
        if lower in _BODY_STARTERS and len(stripped) <= 3 and title_words:
            break
        # Stop at metadata words.
        if lower in _META_WORDS:
            break
        # Stop after 1 word for known single-word section names.
        if len(title_words) == 1 and title_words[0].lower().rstrip(",;.!?:") in _KNOWN_SINGLE_WORD_SECTIONS:
            break
        # Accept: capitalized, number, connector, or short lowercase content word.
        if stripped[0].isupper() or stripped[0].isdigit() or lower in (
            "and", "or", "of", "in", "on", "for", "to", "by", "with", "the",
            "a", "an", "de", "en",
        ):
            title_words.append(stripped)
            if len(title_words) >= 3:
                break
        elif len(stripped) >= 3 and len(title_words) <= 1:
            # Allow 1 lowercase content word after first capitalized word.
            title_words.append(stripped)
            break
        else:
            break
    return " ".join(title_words)


# Words whose presence as first token strongly suggests this is NOT a section heading.
_NON_SECTION_STARTERS = frozenset({
    "furthermore", "however", "therefore", "thus", "moreover", "nevertheless",
    "when", "if", "for", "in", "the", "we", "this", "that", "these", "those",
    "only", "min", "our", "each", "all", "both", "some", "several", "many",
    "where", "while", "although", "because", "since", "after", "before",
    "their", "its", "note", "see", "figure", "fig", "table", "also",
})
# Months that indicate a date rather than section.
_MONTH_NAMES = frozenset({"january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"})


# Sentence-openers that mark end of heading and start of body text.
_BODY_STARTERS = frozenset({
    "in", "to", "for", "we", "this", "that", "these", "those", "it", "they",
    "the", "a", "an", "he", "she", "our", "their", "its",
})
# Metadata words to exclude from heading titles.
_META_WORDS = frozenset({
    "figure", "fig", "table", "method", "psnr", "ssim",
})


def _extract_title_words(raw_title: str) -> str:
    """Extract title words from raw text, with conservative boundaries."""
    words = raw_title.split()
    if not words:
        return ""
    title_tokens: list[str] = []
    for i, word in enumerate(words):
        stripped = word.rstrip(",;.!?:\"").strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if i == 0:
            title_tokens.append(stripped)
            continue
        # Stop at single-word body starters followed by likely body text.
        if lower in _BODY_STARTERS and len(stripped) <= 3:
            break
        # Stop at metadata/table words.
        if lower in _META_WORDS and i >= 1:
            break
        # Stop at clearly non-title lowercase words (not connectors, not content nouns).
        if stripped[0].islower() and lower not in (
            "and", "or", "of", "in", "on", "for", "to", "by", "with", "the",
            "a", "an", "&", "vs", "via", "de", "en",
        ):
            # Allow up to 3 lowercase content words after the first capitalized.
            if i <= 3 and len(stripped) >= 3:
                title_tokens.append(stripped)
            break
        # Stop at stop tokens that are clearly not part of titles.
        if lower in _TITLE_STOP_TOKENS and i > 1 and not stripped[0].isupper():
            break
        title_tokens.append(stripped)
    return " ".join(title_tokens)


# Known single-word section titles that need no multi-word check.
_KNOWN_SINGLE_WORD_SECTIONS = frozenset({
    "abstract", "introduction", "conclusion", "references",
    "acknowledgements", "acknowledgments", "appendix", "supplementary",
    "experiments", "discussion", "results",
})

# Architecture/figure-caption fragments that indicate a pseudo heading.
_PSEUDO_HEADING_TERMS = frozenset({
    "conv relu", "conv shuffle", "conv mult", "x3 conv", "x2 conv", "x4 conv",
    "resblock conv", "upsample conv", "from scratch", "min- imizing",
    "super-resolution result", "method psnr ssim",
    "conv conv", "x2 conv conv",
    # Model names that appear in comparison tables (row labels, not sections)
    "vgg", "resnet", "mobilenet", "densenet", "efficientnet",
    "shufflenet", "squeezenet", "inception", "xception",
    "senet", "googlenet", "alexnet",
    # Metrics / hardware performance labels
    "gflops", "flops", "gflop", "params", "macs", "madds",
    "top-1", "top-5", "fps", "latency", "throughput",
    # Table / figure / body fragments
    "figure", "model parameters", "number of",
    "to allow", "to further", "to evaluate", "to compare",
})
# Broken-word spans (hyphenated split from PDF line-wrap).
_BROKEN_WORD_RE = re.compile(r"\b\w{1,3}-\s+\w+\b")


def _valid_heading_title(title: str, level: int) -> bool:
    """Check whether a detected title looks like a real section heading."""
    if not title or len(title) < 2:
        return False
    words = title.split()
    if not words:
        return False

    first_lower = words[0].lower().rstrip(",;.!?:\"")
    # Reject if first word is a non-section starter.
    if first_lower in _NON_SECTION_STARTERS:
        return False
    # Reject date-like titles (e.g., "10 Jul 2017").
    if first_lower in _MONTH_NAMES:
        return False
    if len(words) >= 2 and words[1].lower().rstrip(",;.!?") in _MONTH_NAMES:
        return False

    # Known single-word sections are always valid.
    if first_lower in _KNOWN_SINGLE_WORD_SECTIONS:
        return True

    # Reject titles with architecture/figure-caption fragments.
    title_lower = title.lower()
    if any(term in title_lower for term in _PSEUDO_HEADING_TERMS):
        return False
    # Reject broken-word titles (hyphenated mid-word splits from PDF line-wrap).
    if _BROKEN_WORD_RE.search(title):
        return False
    # Reject pure metric/table result lines: starts with a number or looks like "N.N dB/GB/M/params"
    if re.match(r'^[\d.+-]+\s*[A-Za-z]*$', title.strip()):
        return False
    # Reject titles that are single capitalized acronyms often found in tables (VGG-16, ResNet-50, etc.)
    if re.match(r'^[A-Z][a-z]*-?\d+$', title.strip()):
        return False

    # For single-number headings (level 2), require at least 2 words.
    if level == 2 and len(words) < 2:
        return False
    # For sub-section headings (level 3+), require at least 1 word.
    if level >= 3 and len(words) < 1:
        return False

    # For level 2, the title should look like a noun phrase (at least one
    # capitalized content word that is not a body starter).
    if level == 2:
        content_words = [
            w for w in words
            if len(w) > 2 and w[0].isupper()
            and w.lower().rstrip(",;.!?") not in _BODY_STARTERS
        ]
        if not content_words:
            return False

    return True


def _remove_heading_prefix(after_text: str, number: str, heading_text: str) -> str:
    """Remove the raw heading text from the beginning of after_text."""
    after = after_text.strip()
    # Try full heading_text first
    if after.startswith(heading_text):
        return after[len(heading_text):].strip()
    # Try just the number + dot
    if number and after.startswith(f"{number}. "):
        after = after[len(number) + 2:]
        return after.strip()
    if number and after.startswith(f"{number} "):
        after = after[len(number) + 1:]
        return after.strip()
    # Try Abstract
    if after.lower().startswith("abstract "):
        return after[9:].strip()
    # Try References
    if after.lower().startswith("references"):
        remaining = after[10:]
        return remaining.lstrip()
    return after


def _section_id(number: str, title: str, index: int) -> str:
    """Generate a stable section_id."""
    if number:
        return f"sec_{number.replace('.', '_')}"
    normalized_title = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_")
    if normalized_title:
        return f"sec_{normalized_title}"
    return f"sec_{index}"


def _notes_markdown(import_job_id: str, notes_payload: Any) -> str:
    lines = [
        "---",
        f"import_job_id: {import_job_id}",
        "status: draft",
        "note_source: user_or_zotero_capture",
        "---",
        "",
        "# Import Notes",
        "",
        "## 阅读判断",
        "",
        "## 重要机制",
        "",
        "## 问题 / limitation",
        "",
        "## 灵感",
        "",
        "## 疑问",
        "",
        "## Zotero annotations",
        "",
    ]
    if notes_payload:
        lines.extend(["## Raw notes payload", "", str(notes_payload), ""])
    return "\n".join(lines)


def _note_append_block(now: str, raw_note: str, user_judgement: str, annotation: Any) -> str:
    lines = ["", "## Appended note", "", f"- captured_at: {now}", ""]
    if user_judgement:
        lines.extend(["### User judgement", "", user_judgement, ""])
    if raw_note:
        lines.extend(["### Raw note", "", raw_note, ""])
    if isinstance(annotation, dict):
        lines.extend(
            [
                "### Zotero annotation",
                "",
                f"- annotation_id: {annotation.get('annotation_id')}",
                f"- pdf_page: {annotation.get('pdf_page')}",
                "",
                "#### Selected text",
                "",
                str(annotation.get("selected_text") or ""),
                "",
                "#### Annotation comment",
                "",
                str(annotation.get("annotation_comment") or ""),
                "",
            ]
        )
    return "\n".join(lines)


def _job_dir(import_job_id: str) -> Path:
    return STAGING_ROOT / import_job_id


def _existing_job_dir(import_job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(import_job_id):
        raise ImportPreviewError("Invalid import_job_id.")
    job_dir = _job_dir(import_job_id).resolve(strict=False)
    staging_root = STAGING_ROOT.resolve(strict=False)
    if not _is_relative_to(job_dir, staging_root) or not job_dir.is_dir():
        raise ImportPreviewError("Import preview job not found.")
    return job_dir


def _new_job_id(pdf_sha256: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"preview_{stamp}_{pdf_sha256[:8]}_{uuid.uuid4().hex[:8]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _title(title_hint: Any, pdf_path: Path) -> str:
    text = str(title_hint or "").strip()
    return text or pdf_path.stem.replace("_", " ").strip() or "Untitled import preview"


def _relative(path: Path) -> str:
    return str(path.resolve(strict=False).relative_to(PROJECT_ROOT)).replace("\\", "/")


def _display_path(path: Path) -> str:
    try:
        return _relative(path)
    except ValueError:
        return str(path.resolve(strict=False))


def _preview(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[: PREVIEW_CHARS - 3].rstrip() + "..."


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safety() -> dict[str, bool]:
    return {
        "core_db_write_performed": False,
        "committed_to_library": False,
        "external_llm_called": False,
    }


def get_source_trace_sections(import_job_id: str) -> list[dict[str, Any]]:
    """Return source_trace.sections for section picker UI."""
    job_dir = _existing_job_dir(import_job_id)
    source_trace_path = job_dir / "source_trace.json"
    if not source_trace_path.is_file():
        raise ImportPreviewError("source_trace.json not found.")
    st = _read_json(source_trace_path)
    sections = st.get("sections") or []
    return [
        {
            "section_id": s.get("section_id", ""),
            "title": s.get("title", s.get("section_id", "")),
            "level": s.get("level", 0),
            "pdf_page": s.get("pdf_page"),
        }
        for s in sections
        if isinstance(s, dict)
    ]


def _safety_response() -> dict[str, bool]:
    return {
        "core_db_write_performed": False,
        "committed_to_library": False,
        "external_llm_called": False,
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
