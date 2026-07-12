from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import PROJECT_ROOT
from app.services import library_service
from app.services.book_import_contract import OBJECT_IMPORT_MODE_CHAPTERED
from app.services.book_import_service import (
    DetectedChapter,
    PreparedBookImport,
    apply_prepared_book_import,
    bind_chunks_to_chapters,
    detect_book_chapters,
)
from app.services.chunk_splitter import split_nodes
from app.services.import_preview_service import ImportPreviewError, _existing_job_dir, _read_json, _write_json
from app.services.markdown_parser import PDF_PAGE_RE, parse_markdown
from app.services.pdf_parser_backends import PdfParseResult


DB_PATH = PROJECT_ROOT / "data" / "db" / "research_memory.db"
COMMIT_MANIFEST_FILE = "commit_book_result.json"
PAPER_COMMIT_MANIFEST_FILE = "commit_result.json"
STAGING_PREVIEW_BACKEND = "staging_preview_text"
MAIN_CHAPTER_MIN_COUNT = 5
MAIN_CHAPTER_MAX_COUNT = 120


def commit_book_from_staging(import_job_id: str) -> dict[str, Any]:
    job_dir = _existing_job_dir(import_job_id)
    paper_md_path = job_dir / "paper.md"
    manifest_path = job_dir / "import_manifest.json"
    source_trace_path = job_dir / "source_trace.json"
    commit_path = job_dir / COMMIT_MANIFEST_FILE

    if not paper_md_path.is_file():
        raise ImportPreviewError("paper.md not found in import staging.")
    if not manifest_path.is_file():
        raise ImportPreviewError("import_manifest.json not found.")
    if not source_trace_path.is_file():
        raise ImportPreviewError("source_trace.json not found.")
    if (job_dir / PAPER_COMMIT_MANIFEST_FILE).is_file():
        raise ImportPreviewError("This staging job was already committed through the paper path.")

    if commit_path.is_file():
        existing = _read_json(commit_path)
        return {
            "status": "already_committed",
            "import_job_id": import_job_id,
            "document_id": existing.get("document_id"),
            "title": existing.get("title"),
            "document_type": "book",
            "object_import_mode": OBJECT_IMPORT_MODE_CHAPTERED,
            "committed_at": existing.get("committed_at"),
            "message": "This book import job has already been committed.",
            **_no_write_safety_fields(),
        }

    manifest = _read_json(manifest_path)
    source_trace = _read_json(source_trace_path)
    pdf_path = _resolve_source_pdf_path(source_trace)
    _preflight_document_sources_table(DB_PATH)

    markdown_text = paper_md_path.read_text(encoding="utf-8")
    title = _extract_title(markdown_text, manifest, pdf_path)
    page_count = _page_count(source_trace, markdown_text)
    chapters, detection_method, chapter_warnings = _detect_chapters_from_staging(
        markdown_text=markdown_text,
        source_trace=source_trace,
        pdf_path=pdf_path,
        page_count=page_count,
        book_title=title,
    )
    if not chapters:
        raise ImportPreviewError("Book commit requires detected chapter structure.")

    parsed = parse_markdown(markdown_text, source_path=str(paper_md_path))
    chunks = split_nodes(parsed.nodes)
    if not chunks:
        raise ImportPreviewError("Book commit requires non-empty knowledge chunks.")

    chunk_chapter_indexes, binding_warnings = bind_chunks_to_chapters(chunks, chapters)
    bound_count = sum(1 for value in chunk_chapter_indexes if value is not None)
    binding_rate = bound_count / len(chunks) if chunks else 0.0
    parse_result = PdfParseResult(
        markdown_text=markdown_text,
        page_markers_present=bool(PDF_PAGE_RE.search(markdown_text)),
        page_count=page_count,
        parser_backend=STAGING_PREVIEW_BACKEND,
        warnings=[],
        artifacts={
            "source": "import_preview_staging",
            "import_job_id": import_job_id,
            "paper_md_path": str(paper_md_path),
        },
        block_stats={"page_marker_count": _page_marker_count(markdown_text)},
        elapsed_seconds=None,
    )
    prepared = PreparedBookImport(
        pdf_path=pdf_path,
        title=title,
        backend=STAGING_PREVIEW_BACKEND,
        parse_result=parse_result,
        markdown_text=markdown_text,
        detection_method=detection_method,
        chapters=chapters,
        chunks=chunks,
        chunk_chapter_indexes=chunk_chapter_indexes,
        binding_rate=binding_rate,
        warnings=[*chapter_warnings, *binding_warnings],
        object_import_mode=OBJECT_IMPORT_MODE_CHAPTERED,
    )

    try:
        apply_result = apply_prepared_book_import(prepared, db_path=DB_PATH, backup=True)
    except ValueError as exc:
        raise ImportPreviewError(f"Book commit safety blocked: {exc}") from exc

    document_id = int(apply_result["document_id"])
    document_source_written = _record_document_source(DB_PATH, document_id, source_trace, pdf_path)
    _record_document_zotero_key(DB_PATH, document_id, source_trace)

    committed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    commit_data = {
        "status": "committed",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "title": title,
        "document_type": "book",
        "object_import_mode": OBJECT_IMPORT_MODE_CHAPTERED,
        "inserted_chapters": int(apply_result.get("inserted_chapters") or 0),
        "inserted_chunks": int(apply_result.get("inserted_chunks") or 0),
        "chunk_count": int(apply_result.get("inserted_chunks") or 0),
        "chapter_id_binding_rate": apply_result.get("chapter_id_binding_rate"),
        "document_source_written": document_source_written,
        "committed_at": committed_at,
        "backup": apply_result.get("backup"),
        "book_safety_decision": apply_result.get("book_safety_decision"),
        "book_safety_blockers": apply_result.get("book_safety_blockers", []),
        "book_safety_warnings": apply_result.get("book_safety_warnings", []),
        "zotero_native_notes_import": _zotero_notes_not_copied_report(
            document_id=document_id,
            source_trace=source_trace,
        ),
        **_write_safety_fields(),
    }
    _write_json(commit_path, commit_data)

    manifest["status"] = "committed"
    manifest.setdefault("commit_info", {})
    manifest["commit_info"].update(
        {
            "endpoint": "commit-book",
            "document_id": document_id,
            "document_type": "book",
            "object_import_mode": OBJECT_IMPORT_MODE_CHAPTERED,
            "committed_at": committed_at,
            "zotero_notes_copied": False,
            "zotero_db_write_performed": False,
        }
    )
    _write_json(manifest_path, manifest)

    return commit_data


def _resolve_source_pdf_path(source_trace: dict[str, Any]) -> Path:
    raw = str(source_trace.get("source_pdf_path") or "").strip()
    if not raw:
        raise ImportPreviewError("Book commit requires a source PDF path in source_trace.")
    if raw.lower().startswith("file://"):
        raise ImportPreviewError("source_pdf_path must be a safe local path, not a URI.")
    resolved = library_service.resolve_safe_pdf_path(raw)
    if resolved is None:
        raise ImportPreviewError("source_pdf_path is outside allowed PDF roots or is not a PDF.")
    pdf_path = Path(resolved).resolve(strict=False)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.is_file():
        raise ImportPreviewError("source_pdf_path must point to an existing PDF file.")
    return pdf_path


def _extract_title(markdown_text: str, manifest: dict[str, Any], pdf_path: Path) -> str:
    hint = str(manifest.get("title_hint") or "").strip()
    if hint:
        return hint
    if markdown_text.startswith("---"):
        end = markdown_text.find("---", 3)
        if end > 0:
            front = markdown_text[3:end]
            for line in front.splitlines():
                stripped = line.strip()
                if stripped.startswith("title:"):
                    value = stripped[len("title:") :].strip().strip("\"'")
                    if value:
                        return value
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return pdf_path.stem


def _page_count(source_trace: dict[str, Any], markdown_text: str) -> int:
    summary = source_trace.get("page_extraction_summary") or {}
    candidates = [
        _safe_int(summary.get("page_count")),
        _safe_int(summary.get("pages_with_text")),
        _max_page_marker(markdown_text),
        _max_section_page(source_trace),
    ]
    return max([value for value in candidates if value is not None] or [1])


def _detect_chapters_from_staging(
    *,
    markdown_text: str,
    source_trace: dict[str, Any],
    pdf_path: Path,
    page_count: int,
    book_title: str,
) -> tuple[list[DetectedChapter], str, list[str]]:
    warnings: list[str] = []
    detected = _detect_existing_main_chapters(
        markdown_text=markdown_text,
        pdf_path=pdf_path,
        page_count=page_count,
        book_title=book_title,
    )
    if _normalization_is_apply_candidate(detected):
        return detected["chapters"], str(detected["detection_method"]), list(detected["warnings"])
    warnings.extend(f"pdf_outline_normalization:{warning}" for warning in detected["warnings"])

    sections = normalize_main_book_chapters(
        source_trace.get("sections") or [],
        page_count=page_count,
        source="staging_preview_sections",
        book_title=book_title,
    )
    if _normalization_is_apply_candidate(sections):
        return sections["chapters"], "staging_preview_main_chapters", [*warnings, *sections["warnings"]]
    warnings.extend(f"source_trace_normalization:{warning}" for warning in sections["warnings"])

    fallback_chapters = detected["chapters"] or sections["chapters"]
    return fallback_chapters, str(detected.get("detection_method") or "main_chapter_normalization"), warnings


def _chapters_from_source_trace_sections(
    source_trace: dict[str, Any],
    *,
    page_count: int,
    book_title: str,
) -> list[DetectedChapter]:
    return normalize_main_book_chapters(
        source_trace.get("sections") or [],
        page_count=page_count,
        source="staging_preview_sections",
        book_title=book_title,
    )["chapters"]


def _detect_existing_main_chapters(
    *,
    markdown_text: str,
    pdf_path: Path,
    page_count: int,
    book_title: str,
) -> dict[str, Any]:
    try:
        detection = detect_book_chapters(markdown_text, pdf_path=pdf_path, page_count=page_count)
    except Exception as exc:
        return {
            "chapters": [],
            "detection_method": "pdf_outline_unavailable",
            "warnings": [f"detect_book_chapters_failed:{type(exc).__name__}"],
            "validation": {"apply_candidate": False},
        }
    result = normalize_main_book_chapters(
        list(detection.get("chapters") or []),
        page_count=page_count,
        source=str(detection.get("detection_method") or "detected_chapters"),
        book_title=book_title,
    )
    result["detection_method"] = f"{detection.get('detection_method') or 'detected'}_main_chapters"
    return result


def normalize_main_book_chapters(
    items: list[Any],
    *,
    page_count: int,
    source: str = "chapter_candidates",
    book_title: str = "",
) -> dict[str, Any]:
    """Collapse outline/heading-like items to numbered top-level book chapters.

    The import preview can contain hundreds of subsections, repeated page headers,
    TOC fragments, and index terms. This function keeps only titles that look like
    top-level numbered chapters, de-duplicates by chapter number, and validates the
    resulting main-chapter sequence before commit-book uses it.
    """
    raw_items = list(items or [])
    candidates: list[dict[str, Any]] = []
    rejected_count = 0
    for order, item in enumerate(raw_items):
        candidate = _main_chapter_candidate(item, order=order, default_source=source)
        if candidate is None:
            rejected_count += 1
            continue
        if _normalize_title(candidate["title"]) == _normalize_title(book_title):
            rejected_count += 1
            continue
        candidates.append(candidate)

    by_number: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        number = int(candidate["chapter_number"])
        existing = by_number.get(number)
        if existing is None or _prefer_main_chapter_candidate(candidate, existing):
            by_number[number] = candidate

    ordered = sorted(by_number.values(), key=lambda candidate: int(candidate.get("order") or 0))
    chapters = _with_page_ends(
        [
            DetectedChapter(
                chapter_index=int(candidate["chapter_number"]),
                title=str(candidate["title"]),
                heading_path=str(candidate["title"]),
                pdf_page_start=candidate["pdf_page_start"],
                pdf_page_end=None,
                detection_method=str(candidate["source"]),
                source=str(candidate["source"]),
                confidence=float(candidate.get("confidence") or 0.75),
            )
            for candidate in ordered
        ],
        page_count=page_count,
    )
    validation = _validate_normalized_main_chapters(chapters)
    warnings: list[str] = []
    if rejected_count:
        warnings.append(f"ignored_non_main_outline_nodes:{rejected_count}")
    if candidates and len(candidates) != len(chapters):
        warnings.append(f"deduped_main_chapter_candidates:{len(candidates) - len(chapters)}")
    warnings.extend(validation["warnings"])
    return {
        "chapters": chapters,
        "input_count": len(raw_items),
        "candidate_count": len(candidates),
        "normalized_chapter_count": len(chapters),
        "ignored_count": rejected_count,
        "detection_method": f"{source}_main_chapters",
        "warnings": warnings,
        "validation": validation,
    }


def _main_chapter_candidate(item: Any, *, order: int, default_source: str) -> dict[str, Any] | None:
    if isinstance(item, DetectedChapter):
        title = item.title
        page = item.pdf_page_start
        confidence = item.confidence
        source = item.source or default_source
    elif isinstance(item, dict):
        title = item.get("title") or item.get("heading_text") or item.get("raw_title")
        page = item.get("pdf_page_start")
        if page is None:
            page = item.get("pdf_page")
        if page is None:
            page = item.get("page")
        confidence = item.get("confidence") or 0.75
        source = item.get("source") or default_source
    else:
        title = str(item or "")
        page = None
        confidence = 0.75
        source = default_source

    parsed = _parse_main_chapter_title(str(title or ""))
    page_start = _safe_int(page)
    if parsed is None or page_start is None:
        return None
    number, normalized_title = parsed
    return {
        "order": order,
        "chapter_number": number,
        "title": normalized_title,
        "pdf_page_start": page_start,
        "confidence": float(confidence or 0.75),
        "source": str(source or default_source),
    }


def _parse_main_chapter_title(title: str) -> tuple[int, str] | None:
    text = _clean_title(title)
    if not text:
        return None
    if re.match(r"^\d{1,3}\.\d+", text):
        return None

    chapter_match = re.match(r"^(?:chapter|ch\.)\s+(\d{1,3})[.:\-]?\s+(.+)$", text, flags=re.IGNORECASE)
    numeric_match = re.match(r"^(\d{1,3})[.:\-]?\s+(.+)$", text)
    match = chapter_match or numeric_match
    if not match:
        return None
    number = int(match.group(1))
    if number < 1 or number > MAIN_CHAPTER_MAX_COUNT:
        return None
    rest = _clean_main_chapter_rest(match.group(2))
    if not rest or _is_main_chapter_rest_noise(rest):
        return None
    return number, f"{number} {rest}"


def _clean_main_chapter_rest(value: str) -> str:
    rest = _clean_title(value).strip(" .:-")
    rest = re.sub(r"\s+\*?\s+\d{1,4}(?:\s+\d{1,3})?$", "", rest).strip()
    rest = re.sub(r"\s+", " ", rest).strip()
    return rest


def _is_main_chapter_rest_noise(rest: str) -> bool:
    lower = rest.lower()
    if len(rest) < 3:
        return True
    if re.match(r"^chapter\s+\d+\b", lower):
        return True
    return any(
        marker in lower
        for marker in [
            "generated by",
            "cluster ",
            "proceedings of",
            "associa- tion",
        ]
    )


def _prefer_main_chapter_candidate(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    candidate_source = str(candidate.get("source") or "")
    existing_source = str(existing.get("source") or "")
    if "pdf_outline" in candidate_source and "pdf_outline" not in existing_source:
        return True
    if "pdf_outline" not in candidate_source and "pdf_outline" in existing_source:
        return False
    candidate_confidence = float(candidate.get("confidence") or 0.0)
    existing_confidence = float(existing.get("confidence") or 0.0)
    if candidate_confidence != existing_confidence:
        return candidate_confidence > existing_confidence
    return int(candidate.get("order") or 0) < int(existing.get("order") or 0)


def _validate_normalized_main_chapters(chapters: list[DetectedChapter]) -> dict[str, Any]:
    warnings: list[str] = []
    blockers: list[str] = []
    if len(chapters) < MAIN_CHAPTER_MIN_COUNT:
        blockers.append("main_chapter_count_below_minimum")
    if len(chapters) > MAIN_CHAPTER_MAX_COUNT:
        blockers.append("main_chapter_count_above_maximum")

    numbers = [int(chapter.chapter_index) for chapter in chapters]
    if any(right <= left for left, right in zip(numbers, numbers[1:])):
        blockers.append("main_chapter_numbers_not_increasing")
    page_starts = [chapter.pdf_page_start for chapter in chapters if chapter.pdf_page_start is not None]
    if len(page_starts) != len(chapters):
        missing = len(chapters) - len(page_starts)
        warnings.append(f"main_chapter_missing_page_start:{missing}")
        if missing / max(len(chapters), 1) >= 0.30:
            blockers.append("main_chapter_page_start_missing_high_ratio")
    if any(right <= left for left, right in zip(page_starts, page_starts[1:])):
        blockers.append("main_chapter_page_starts_not_increasing")
    if numbers:
        number_span = numbers[-1] - numbers[0] + 1
        if number_span > len(numbers) + max(2, int(len(numbers) * 0.30)):
            warnings.append(f"main_chapter_number_gaps:span={number_span}:count={len(numbers)}")

    return {
        "apply_candidate": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "chapter_numbers": numbers,
        "page_starts": page_starts,
    }


def _normalization_is_apply_candidate(result: dict[str, Any]) -> bool:
    return bool((result.get("validation") or {}).get("apply_candidate")) and len(result.get("chapters") or []) >= MAIN_CHAPTER_MIN_COUNT


def _with_page_ends(chapters: list[DetectedChapter], *, page_count: int) -> list[DetectedChapter]:
    ordered = list(chapters)
    output: list[DetectedChapter] = []
    for index, chapter in enumerate(ordered):
        next_start = ordered[index + 1].pdf_page_start if index + 1 < len(ordered) else None
        start = chapter.pdf_page_start
        if start is not None and next_start is not None:
            end = max(start, next_start - 1)
        elif start is not None:
            end = max(start, page_count)
        else:
            end = None
        output.append(
            DetectedChapter(
                chapter_index=chapter.chapter_index,
                title=chapter.title,
                heading_path=chapter.heading_path,
                pdf_page_start=start,
                pdf_page_end=end,
                detection_method=chapter.detection_method,
                source=chapter.source,
                confidence=chapter.confidence,
                suspicious_reason=chapter.suspicious_reason,
            )
        )
    return output


def _record_document_source(
    db_path: str | Path,
    document_id: int,
    source_trace: dict[str, Any],
    pdf_path: Path,
) -> bool:
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    trace = {**source_trace, "source_pdf_path": str(pdf_path)}
    with sqlite3.connect(db_path) as connection:
        columns = _columns(connection, "document_sources")
        if not columns:
            raise ImportPreviewError("document_sources table is required for book commit.")
        values: dict[str, Any] = {
            "document_id": document_id,
            "source_type": source_trace.get("source_type") or "local_pdf",
            "zotero_item_key": source_trace.get("zotero_item_key"),
            "zotero_attachment_key": source_trace.get("zotero_attachment_key"),
            "zotero_source_id": source_trace.get("zotero_source_id") or source_trace.get("zotero_pdf_source_id"),
            "zotero_select_uri": source_trace.get("zotero_select_uri"),
            "zotero_open_pdf_uri": source_trace.get("zotero_open_pdf_uri"),
            "source_trace_json": json.dumps(trace, ensure_ascii=False),
            "pdf_path": str(pdf_path),
            "created_at": now,
        }
        selected = [name for name in values if name in columns]
        if "document_id" not in selected or "source_type" not in selected:
            raise ImportPreviewError("document_sources table lacks required book commit columns.")
        placeholders = ", ".join("?" for _ in selected)
        connection.execute(
            f"INSERT INTO document_sources ({', '.join(selected)}) VALUES ({placeholders})",
            [values[name] for name in selected],
        )
        connection.commit()
    return True


def _record_document_zotero_key(db_path: str | Path, document_id: int, source_trace: dict[str, Any]) -> None:
    zotero_key = str(source_trace.get("zotero_item_key") or "").strip()
    if not zotero_key:
        return
    with sqlite3.connect(db_path) as connection:
        if "zotero_key" not in _columns(connection, "documents"):
            return
        connection.execute("UPDATE documents SET zotero_key = ? WHERE id = ?", (zotero_key, document_id))
        connection.commit()


def _preflight_document_sources_table(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as connection:
        columns = _columns(connection, "document_sources")
    if not columns:
        raise ImportPreviewError("document_sources table is required for book commit.")
    if "document_id" not in columns or "source_type" not in columns:
        raise ImportPreviewError("document_sources table lacks required book commit columns.")


def _columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[1]) for row in rows}


def _zotero_notes_not_copied_report(*, document_id: int, source_trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "skipped",
        "attempted": False,
        "apply": False,
        "document_id": document_id,
        "zotero_attachment_key": source_trace.get("zotero_attachment_key"),
        "zotero_item_key": source_trace.get("zotero_item_key"),
        "imported_count": 0,
        "skipped_existing_count": 0,
        "blocked_count": 0,
        "message": "Book commit does not copy Zotero native notes; Zotero remains read-only.",
        "db_write_performed": False,
        "core_db_write_performed": False,
        "zotero_db_write_performed": False,
        "zotero_notes_write_performed": False,
        "llm_called": False,
        "external_llm_called": False,
    }


def _write_safety_fields() -> dict[str, bool]:
    return {
        "db_write_performed": True,
        "core_db_write_performed": True,
        "vector_store_write_performed": False,
        "vector_index_write_performed": False,
        "lancedb_write_performed": False,
        "zotero_db_write_performed": False,
        "zotero_notes_write_performed": False,
        "llm_called": False,
        "external_llm_called": False,
        "ocr_or_marker_performed": False,
        "object_candidates_generated": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "seed_apply_performed": False,
        "final_hypothesis_created": False,
    }


def _no_write_safety_fields() -> dict[str, bool]:
    value = _write_safety_fields()
    value["db_write_performed"] = False
    value["core_db_write_performed"] = False
    return value


def _clean_title(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _page_marker_count(markdown_text: str) -> int:
    return len(PDF_PAGE_RE.findall(markdown_text))


def _max_page_marker(markdown_text: str) -> int | None:
    values = [_safe_int(value) for value in PDF_PAGE_RE.findall(markdown_text)]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _max_section_page(source_trace: dict[str, Any]) -> int | None:
    sections = source_trace.get("sections") or []
    if not isinstance(sections, list):
        return None
    values = [
        _safe_int(section.get("pdf_page"))
        for section in sections
        if isinstance(section, dict)
    ]
    values = [value for value in values if value is not None]
    return max(values) if values else None
