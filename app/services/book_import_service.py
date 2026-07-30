from __future__ import annotations

import hashlib
import inspect
import gc
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.paths import DATA_PROJECT_ROOT, DEFAULT_DB_PATH, RUNTIME_STATE_DIR
from app.services.book_import_contract import OBJECT_IMPORT_MODE_CHAPTERED, PdfLayoutBlock, PdfLayoutLine, PdfLayoutSpan
from app.services.chunk_splitter import TextChunk, split_nodes
from app.services.markdown_parser import PDF_PAGE_RE, ParsedMarkdownNode, parse_markdown
from app.services.pdf_layout_service import insert_pdf_page_text_layer_cache, persist_layout_blocks_and_links
from app.services.pdf_parser_backends import (
    MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    PdfParseResult,
    parse_pdf_to_markdown,
)
from app.services.pdf_backend_service import PdfBackendUnavailableError, load_fitz_backend


HIGH_RISK_WARNING_PREFIXES = ("duplicate_", "synthetic_full_text", "parser_failed")


def _callable_accepts_keyword(fn: Callable[..., Any], keyword: str) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    if keyword in signature.parameters:
        return True
    return any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())


def _parse_pdf_to_markdown_with_optional_device(
    *args: Any,
    device: str | None = None,
    **kwargs: Any,
) -> PdfParseResult:
    if device is not None and _callable_accepts_keyword(parse_pdf_to_markdown, "device"):
        return parse_pdf_to_markdown(*args, device=device, **kwargs)
    return parse_pdf_to_markdown(*args, **kwargs)


@dataclass(frozen=True)
class DetectedChapter:
    chapter_index: int
    title: str
    heading_path: str
    pdf_page_start: int | None
    pdf_page_end: int | None = None
    detection_method: str = "heading_pattern"
    source: str = "section_header"
    confidence: float = 0.5
    suspicious_reason: str | None = None


@dataclass(frozen=True)
class ChapterCandidate:
    title: str
    source: str
    page_start: int | None
    confidence: float
    suspicious_reason: str | None = None
    rejected_reason: str | None = None


@dataclass(frozen=True)
class PreparedBookImport:
    pdf_path: Path
    title: str
    backend: str
    parse_result: PdfParseResult
    markdown_text: str
    detection_method: str
    chapters: list[DetectedChapter]
    chunks: list[TextChunk]
    chunk_chapter_indexes: list[int | None]
    binding_rate: float
    warnings: list[str] = field(default_factory=list)
    rejected_candidates_summary: dict[str, int] = field(default_factory=dict)
    rejected_candidates: list[ChapterCandidate] = field(default_factory=list)
    layout_blocks: list[PdfLayoutBlock] = field(default_factory=list)
    layout_lines: list[PdfLayoutLine] = field(default_factory=list)
    layout_spans: list[PdfLayoutSpan] = field(default_factory=list)
    object_import_mode: str = OBJECT_IMPORT_MODE_CHAPTERED
    ocr_text_layer_cache: dict[int, str] = field(default_factory=dict)

    @property
    def page_marker_count(self) -> int:
        return int(self.parse_result.block_stats.get("page_marker_count") or _count_page_markers(self.markdown_text))

    @property
    def estimated_chunk_count(self) -> int:
        return len(self.chunks)


def prepare_book_import_from_markdown(
    pdf_path: str | Path,
    markdown_text: str,
    *,
    title: str | None = None,
    backend: str = MARKER_SURYA_PAGE_BLOCKS_BACKEND,
) -> PreparedBookImport:
    """Prepare an import from already converted, SHA-verified Markdown."""

    pdf = resolve_pdf_path(pdf_path)
    normalized = _ensure_pdf_path_marker(str(markdown_text), pdf)
    page_count = _count_page_markers(normalized)
    parse_result = PdfParseResult(
        markdown_text=normalized,
        page_markers_present=page_count > 0,
        page_count=page_count,
        parser_backend=backend,
        warnings=[],
    )
    detection = detect_book_chapters(
        normalized,
        pdf_path=pdf,
        page_count=page_count,
    )
    chapters = list(detection["chapters"])
    detection_method = str(detection["detection_method"])
    warnings: list[str] = []
    if not chapters:
        detection_method = "synthetic_full_text"
        chapters = [
            DetectedChapter(
                chapter_index=1,
                title=title or pdf.stem,
                heading_path=title or pdf.stem,
                pdf_page_start=1,
                pdf_page_end=page_count or None,
                detection_method=detection_method,
                source="synthetic",
                confidence=0.0,
            )
        ]
        warnings.append("synthetic_full_text: no chapter heading candidates detected")
    chapters = _with_page_ends(chapters, page_count)
    parsed = parse_markdown(normalized, source_path=str(pdf))
    parsed_nodes = parsed.nodes or _plain_text_page_nodes(
        normalized,
        chapters=chapters,
        pdf_path=pdf,
    )
    if not parsed.nodes and parsed_nodes:
        warnings.append("plain_text_page_fallback_used")
    chunks = split_nodes(parsed_nodes)
    chunk_chapter_indexes, binding_warnings = _bind_chunks_to_outline_units(
        chunks, chapters
    )
    warnings.extend(binding_warnings)
    bound_count = sum(
        1 for chapter_index in chunk_chapter_indexes if chapter_index is not None
    )
    return PreparedBookImport(
        pdf_path=pdf,
        title=title or _guess_title(parsed.title, pdf),
        backend=backend,
        parse_result=parse_result,
        markdown_text=normalized,
        detection_method=detection_method,
        chapters=chapters,
        chunks=chunks,
        chunk_chapter_indexes=chunk_chapter_indexes,
        binding_rate=bound_count / len(chunks) if chunks else 0.0,
        warnings=warnings,
        rejected_candidates_summary=dict(
            detection.get("rejected_candidates_summary") or {}
        ),
        rejected_candidates=list(detection.get("rejected_candidates") or []),
    )


def prepare_book_import(
    pdf_path: str | Path,
    *,
    title: str | None = None,
    backend: str = MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    max_pages: int | None = None,
    max_chapters: int | None = None,
    selected_chapter_indexes: list[int] | tuple[int, ...] | None = None,
    import_granularity: str | None = None,
    include_front_matter: bool = False,
    include_back_matter: bool = False,
    job_progress_callback: Callable[[str, int, str, dict[str, Any] | None], None] | None = None,
    job_id: str | None = None,
    parser_device: str | None = None,
) -> PreparedBookImport:
    pdf = resolve_pdf_path(pdf_path)
    warnings: list[str] = []
    rejected_candidates_summary: dict[str, int] = {}
    rejected_candidates: list[ChapterCandidate] = []

    if import_granularity and max_pages is None:
        range_prepared = prepare_book_import_by_outline_ranges(
            pdf,
            title=title,
            backend=backend,
            import_granularity=import_granularity,
            max_chapters=max_chapters,
            selected_chapter_indexes=selected_chapter_indexes,
            include_front_matter=include_front_matter,
            include_back_matter=include_back_matter,
            job_progress_callback=job_progress_callback,
            job_id=job_id,
            parser_device=parser_device,
        )
        if range_prepared is not None:
            return range_prepared

    parse_result = _parse_pdf_to_markdown_with_optional_device(
        pdf,
        backend=backend,
        max_pages=max_pages,
        device=parser_device,
    )
    markdown_text = _ensure_pdf_path_marker(parse_result.markdown_text, pdf)

    # ── Chapter detection: outline-based or traditional ──
    outline_units_used = False
    if import_granularity:
        try:
            preview = build_chaptered_preview_from_outline(pdf, title_hint=title)
            outline_tree = preview.get("outline_tree") or []
            if outline_tree and preview.get("detection_method") == "pdf_outline":
                units = _collect_import_units(outline_tree, import_granularity)
                if units:
                    outline_units_used = True
                    chapters = [
                        DetectedChapter(
                            chapter_index=u["chapter_index"],
                            title=u["title"],
                            heading_path=u["title"],
                            pdf_page_start=u["pdf_page_start"],
                            pdf_page_end=u.get("pdf_page_end"),
                            detection_method="pdf_outline_granularity",
                            source="outline_granularity",
                            confidence=u.get("confidence", 0.8),
                        )
                        for u in units
                    ]
                    detection_method = f"pdf_outline_{import_granularity}"
                    warnings.append(f"outline_units_used: {len(chapters)} {import_granularity} units from PDF outline")
        except Exception:
            pass

    if not outline_units_used:
        detection = detect_book_chapters(markdown_text, pdf_path=pdf, page_count=parse_result.page_count)
        chapters = detection["chapters"]
        detection_method = str(detection["detection_method"])
        rejected_candidates_summary = dict(detection.get("rejected_candidates_summary") or {})
        rejected_candidates = list(detection.get("rejected_candidates") or [])
        if not chapters:
            detection_method = "synthetic_full_text"
            chapters = [
                DetectedChapter(
                    chapter_index=1,
                    title=title or pdf.stem,
                    heading_path=title or pdf.stem,
                    pdf_page_start=1,
                    pdf_page_end=parse_result.page_count or None,
                    detection_method=detection_method,
                    source="synthetic",
                    confidence=0.0,
                )
            ]
            warnings.append("synthetic_full_text: no chapter heading candidates detected")

    if max_chapters is not None:
        chapters = chapters[:max_chapters]
    if selected_chapter_indexes:
        selected = {int(index) for index in selected_chapter_indexes}
        chapters = [chapter for chapter in chapters if int(chapter.chapter_index) in selected]
        if not chapters:
            raise ValueError("selected_chapter_indexes did not match any detected chapters")
        warnings.append(f"selected_chapter_indexes_used: {sorted(selected)}")
    chapters = _with_page_ends(chapters, parse_result.page_count)

    parsed = parse_markdown(markdown_text, source_path=str(pdf))
    parsed_nodes = parsed.nodes or _plain_text_page_nodes(
        markdown_text,
        chapters=chapters,
        pdf_path=pdf,
    )
    if not parsed.nodes and parsed_nodes:
        warnings.append("plain_text_page_fallback_used")
    chunks = split_nodes(parsed_nodes)
    chunk_chapter_indexes, binding_warnings = _bind_chunks_to_outline_units(chunks, chapters)
    warnings.extend(binding_warnings)
    bound_count = sum(1 for chapter_index in chunk_chapter_indexes if chapter_index is not None)
    binding_rate = bound_count / len(chunks) if chunks else 0.0
    return PreparedBookImport(
        pdf_path=pdf,
        title=title or _guess_title(parsed.title, pdf),
        backend=backend,
        parse_result=parse_result,
        markdown_text=markdown_text,
        detection_method=detection_method,
        chapters=chapters,
        chunks=chunks,
        chunk_chapter_indexes=chunk_chapter_indexes,
        binding_rate=binding_rate,
        warnings=warnings + list(parse_result.warnings),
        rejected_candidates_summary=rejected_candidates_summary,
        rejected_candidates=rejected_candidates,
        layout_blocks=list(parse_result.layout_blocks),
        layout_lines=list(parse_result.layout_lines),
        layout_spans=list(parse_result.layout_spans),
    )


def prepare_book_import_by_outline_ranges(
    pdf_path: str | Path,
    *,
    title: str | None = None,
    backend: str = MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    import_granularity: str = "chapter",
    selected_outline_level: int | None = None,
    max_chapters: int | None = None,
    selected_chapter_indexes: list[int] | tuple[int, ...] | None = None,
    include_front_matter: bool = False,
    include_back_matter: bool = False,
    job_progress_callback: Callable[[str, int, str, dict[str, Any] | None], None] | None = None,
    job_id: str | None = None,
    parser_device: str | None = None,
) -> PreparedBookImport | None:
    """Prepare a book import by parsing each selected outline unit separately.

    Returns None when the PDF has no usable outline units, allowing callers to
    fall back to the legacy full-book parser.
    """
    pdf = resolve_pdf_path(pdf_path)
    granularity = f"outline_level_{selected_outline_level}" if selected_outline_level else import_granularity
    preview = build_chaptered_preview_from_outline(pdf, title_hint=title)
    outline_tree = preview.get("outline_tree") or []
    if not outline_tree or preview.get("detection_method") != "pdf_outline":
        return None

    units = _collect_import_units(outline_tree, granularity)
    if not units:
        return None
    if selected_chapter_indexes:
        selected = {int(index) for index in selected_chapter_indexes}
        units = [unit for unit in units if int(unit.get("chapter_index") or 0) in selected]
        if not units:
            raise ValueError("selected_chapter_indexes did not match any importable outline units")
    if max_chapters is not None:
        units = units[:max_chapters]

    chapters = [
        DetectedChapter(
            chapter_index=index,
            title=str(unit["title"]),
            heading_path=str(unit["title"]),
            pdf_page_start=unit.get("pdf_page_start"),
            pdf_page_end=unit.get("pdf_page_end"),
            detection_method="pdf_outline_granularity_range",
            source=str(unit.get("source") or "pdf_outline"),
            confidence=float(unit.get("confidence", 0.8)),
        )
        for index, unit in enumerate(units, start=1)
    ]

    total_units = len(chapters)
    all_chunks: list[TextChunk] = []
    chunk_chapter_indexes: list[int | None] = []
    markdown_parts: list[str] = []
    layout_blocks: list[PdfLayoutBlock] = []
    layout_lines: list[PdfLayoutLine] = []
    layout_spans: list[PdfLayoutSpan] = []
    warnings: list[str] = [f"outline_units_used: {total_units} {granularity} units from PDF outline"]
    if selected_chapter_indexes:
        warnings.append(f"selected_chapter_indexes_used: {sorted(int(index) for index in selected_chapter_indexes)}")
    selected_page_count = sum(
        max(0, int((chapter.pdf_page_end or chapter.pdf_page_start or 0)) - int(chapter.pdf_page_start or 0) + 1)
        for chapter in chapters
        if chapter.pdf_page_start is not None
    )
    detection_method = f"pdf_outline_{granularity}_range"
    slice_dir = RUNTIME_STATE_DIR / "marker_range_slices" / (job_id or "manual")

    for zero_index, chapter in enumerate(chapters):
        unit_index = zero_index + 1
        page_start = chapter.pdf_page_start
        page_end = chapter.pdf_page_end or page_start
        if page_start is None or page_end is None:
            raise ValueError(f"outline unit is missing page range: {chapter.title}")
        progress = 15 + int((zero_index / max(total_units, 1)) * 55)
        message = f"正在解析第 {unit_index}/{total_units} 章：{chapter.title} (p.{page_start}-{page_end})"
        _emit_progress(
            job_progress_callback,
            "parsing_pdf",
            progress,
            message,
            {
                "current_unit_index": unit_index,
                "total_units": total_units,
                "current_unit_title": chapter.title,
                "current_page_start": page_start,
                "current_page_end": page_end,
            },
        )
        try:
            parse_result = _parse_pdf_to_markdown_with_optional_device(
                pdf,
                backend=backend,
                page_start=int(page_start),
                page_end=int(page_end),
                slice_dir=slice_dir,
                device=parser_device,
            )
            markdown_text = _ensure_pdf_path_marker(parse_result.markdown_text, pdf)
            parsed = parse_markdown(markdown_text, source_path=str(pdf))
            chunks = split_nodes(parsed.nodes)
            all_chunks.extend(chunks)
            chunk_chapter_indexes.extend([chapter.chapter_index] * len(chunks))
            markdown_parts.append(markdown_text)
            layout_blocks.extend(parse_result.layout_blocks)
            layout_lines.extend(parse_result.layout_lines)
            layout_spans.extend(parse_result.layout_spans)
            warnings.extend(parse_result.warnings)
            _emit_progress(
                job_progress_callback,
                "parsing_pdf",
                min(70, 15 + int((unit_index / max(total_units, 1)) * 55)),
                f"已解析第 {unit_index}/{total_units} 章：{chapter.title}",
                {
                    "current_unit_index": unit_index,
                    "total_units": total_units,
                    "current_unit_title": chapter.title,
                    "current_page_start": page_start,
                    "current_page_end": page_end,
                },
            )
        except MemoryError as exc:
            raise MemoryError(
                f"MemoryError while parsing chapter range: title={chapter.title!r}, "
                f"page_start={page_start}, page_end={page_end}, backend={backend}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"failed to parse chapter range: title={chapter.title!r}, "
                f"page_start={page_start}, page_end={page_end}, backend={backend}: {exc}"
            ) from exc
        finally:
            try:
                del parse_result
            except UnboundLocalError:
                pass
            try:
                del markdown_text
            except UnboundLocalError:
                pass
            gc.collect()
            _empty_torch_cuda_cache()

    bound_count = sum(1 for chapter_index in chunk_chapter_indexes if chapter_index is not None)
    binding_rate = bound_count / len(all_chunks) if all_chunks else 0.0
    combined_markdown = "\n\n".join(markdown_parts)
    aggregate_parse_result = PdfParseResult(
        markdown_text=combined_markdown,
        page_markers_present="<!-- PDF_PAGE:" in combined_markdown,
        page_count=selected_page_count,
        parser_backend=backend,
        warnings=[],
        artifacts={
            "outline_units_used": True,
            "import_granularity": granularity,
            "range_mode": True,
            "parser_device": parser_device or "auto",
        },
        block_stats={"page_marker_count": _count_page_markers(combined_markdown)},
        elapsed_seconds=None,
    )
    return PreparedBookImport(
        pdf_path=pdf,
        title=title or preview.get("title") or pdf.stem,
        backend=backend,
        parse_result=aggregate_parse_result,
        markdown_text=combined_markdown,
        detection_method=detection_method,
        chapters=chapters,
        chunks=all_chunks,
        chunk_chapter_indexes=chunk_chapter_indexes,
        binding_rate=binding_rate,
        warnings=warnings,
        rejected_candidates_summary={},
        rejected_candidates=[],
        layout_blocks=layout_blocks,
        layout_lines=layout_lines,
        layout_spans=layout_spans,
    )


def evaluate_auto_apply_safety(
    prepared: PreparedBookImport,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    document_type: str = "book",
) -> dict[str, Any]:
    normalized_document_type = str(document_type or "book").strip() or "book"
    is_journal_article = normalized_document_type == "journalArticle"
    reasons: list[str] = []
    structured_blockers: list[dict[str, Any]] = []
    db = Path(db_path)
    if not prepared.pdf_path.exists():
        reasons.append("pdf_missing")
        structured_blockers.append({"code": "pdf_missing"})
    if prepared.parse_result.parser_backend != prepared.backend:
        reasons.append("parser_backend_mismatch")
        structured_blockers.append({"code": "parser_backend_mismatch"})
    if not prepared.parse_result.markdown_text.strip():
        reasons.append("parser_empty_output")
        structured_blockers.append({"code": "parser_empty_output"})
    page_marker_count = prepared.page_marker_count
    parsed_page_count = prepared.parse_result.page_count
    if parsed_page_count and page_marker_count < int(parsed_page_count * 0.95):
        reasons.append("page_marker_count_below_95_percent")
        structured_blockers.append(
            {
                "code": "page_marker_count_below_95_percent",
                "page_marker_count": page_marker_count,
                "parsed_page_count": parsed_page_count,
            }
        )
    if not is_journal_article and prepared.detection_method == "synthetic_full_text":
        reasons.append("synthetic_full_text_not_apply_safe")
        structured_blockers.append({"code": "synthetic_full_text_not_apply_safe"})
    if is_journal_article:
        chapter_safety = {
            "book_safety_decision": "allowed",
            "book_safety_blockers": [],
            "book_safety_warnings": [],
            "detected_chapter_count": len(prepared.chapters),
            "chapter_title_quality": "not_applicable",
        }
    else:
        chapter_safety = evaluate_book_chapter_safety(prepared)
        for blocker in chapter_safety["book_safety_blockers"]:
            reasons.append(str(blocker.get("legacy_reason") or blocker.get("code")))
            structured_blockers.append(blocker)
        if len(prepared.chapters) > 120:
            reasons.append("chapter_count_above_120")
            structured_blockers.append(
                {
                    "code": "chapter_count_above_120",
                    "detected_chapter_count": len(prepared.chapters),
                    "legacy_reason": "chapter_count_above_120",
                }
            )
        if prepared.chapters and all(
            chapter.source == "section_header" for chapter in prepared.chapters
        ):
            if max(chapter.confidence for chapter in prepared.chapters) < 0.80:
                reasons.append("low_confidence_section_header_only_detection")
                structured_blockers.append(
                    {
                        "code": "selected_outline_unreliable",
                        "legacy_reason": "low_confidence_section_header_only_detection",
                    }
                )
    if len(prepared.chunks) <= 0:
        reasons.append("chunk_count_zero")
        structured_blockers.append({"code": "chunk_count_zero"})
    if len(prepared.chunks) > 8000:
        reasons.append("chunk_count_above_8000")
        structured_blockers.append({"code": "chunk_count_above_8000"})
    if prepared.binding_rate < 0.80:
        reasons.append("chunk_binding_rate_below_80_percent")
        structured_blockers.append(
            {
                "code": "chunk_binding_rate_below_80_percent",
                "binding_rate": prepared.binding_rate,
            }
        )
    duplicate = find_duplicate_book(db, prepared.pdf_path, prepared.title)
    if duplicate:
        reasons.append(
            f"duplicate_book:{duplicate['reason']}:document_id={duplicate['document_id']}"
        )
        structured_blockers.append({"code": "duplicate_book", **duplicate})
    for warning in prepared.warnings:
        if not warning.startswith(HIGH_RISK_WARNING_PREFIXES):
            continue
        if (
            is_journal_article
            and warning
            == "synthetic_full_text: no chapter heading candidates detected"
        ):
            continue
        reasons.append(f"high_risk_warning:{warning}")
        structured_blockers.append(
            {"code": "high_risk_warning", "warning": warning}
        )
    decision = "blocked" if reasons else chapter_safety["book_safety_decision"]
    return {
        "auto_apply_eligible": not reasons,
        "reasons": reasons,
        "duplicate": duplicate,
        "book_safety_decision": decision,
        "book_safety_blockers": structured_blockers,
        "book_safety_warnings": chapter_safety["book_safety_warnings"],
        "detected_chapter_count": len(prepared.chapters),
        "chapter_title_quality": (
            "blocked" if reasons else chapter_safety["chapter_title_quality"]
        ),
    }


def evaluate_book_chapter_safety(prepared: PreparedBookImport) -> dict[str, Any]:
    return evaluate_chapter_title_safety(
        book_title=prepared.title,
        chapters=prepared.chapters,
        rejected_candidates=prepared.rejected_candidates,
    )


def evaluate_chapter_title_safety(
    *,
    book_title: str,
    chapters: list[DetectedChapter],
    rejected_candidates: list[ChapterCandidate] | None = None,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    chapters = list(chapters)
    rejected_candidates = list(rejected_candidates or [])
    titles = [str(chapter.title or "").strip() for chapter in chapters]
    chapter_count = len(chapters)

    if chapter_count < 5:
        blockers.append(
            {
                "code": "chapter_count_below_minimum_for_full_book",
                "detected_chapter_count": chapter_count,
                "legacy_reason": "chapter_count_below_5",
            }
        )

    empty_titles = [index + 1 for index, title in enumerate(titles) if not title]
    if empty_titles and (len(empty_titles) >= 2 or len(empty_titles) / max(chapter_count, 1) >= 0.30):
        blockers.append(
            {
                "code": "empty_or_missing_chapter_titles_high_ratio",
                "count": len(empty_titles),
                "chapter_indexes": empty_titles[:10],
                "legacy_reason": "empty_or_missing_chapter_titles_high_ratio",
            }
        )

    duplicates = _exact_duplicate_title_groups(titles)
    if duplicates:
        duplicate_total = sum(len(group) for group in duplicates.values())
        if duplicate_total / max(chapter_count, 1) >= 0.20 or any(len(group) >= 3 for group in duplicates.values()):
            blockers.append(
                {
                    "code": "exact_duplicate_chapter_titles_high_ratio",
                    "titles": list(duplicates.keys())[:5],
                    "duplicate_count": duplicate_total,
                    "legacy_reason": "exact_duplicate_chapter_titles_high_ratio",
                }
            )

    boilerplate = _boilerplate_title_groups(titles, book_title)
    if boilerplate:
        repeated_total = sum(len(group) for group in boilerplate.values())
        if repeated_total / max(chapter_count, 1) >= 0.30:
            blockers.append(
                {
                    "code": "boilerplate_title_repeated_high_ratio",
                    "titles": list(boilerplate.keys())[:5],
                    "legacy_reason": "boilerplate_title_repeated_high_ratio",
                }
            )

    missing_ranges = [
        chapter.chapter_index
        for chapter in chapters
        if chapter.pdf_page_start is None or chapter.pdf_page_end is None
    ]
    if missing_ranges and (len(missing_ranges) >= 2 or len(missing_ranges) / max(chapter_count, 1) >= 0.30):
        blockers.append(
            {
                "code": "missing_page_ranges_high_ratio",
                "count": len(missing_ranges),
                "chapter_indexes": missing_ranges[:10],
                "legacy_reason": "missing_page_ranges_high_ratio",
            }
        )

    page_ranges_monotonic = not _has_non_monotonic_page_ranges(chapters)
    if not page_ranges_monotonic:
        blockers.append(
            {
                "code": "non_monotonic_page_ranges",
                "legacy_reason": "non_monotonic_page_ranges",
            }
        )

    number_sequence = _chapter_number_sequence_state(titles)
    if number_sequence["non_increasing"]:
        blockers.append(
            {
                "code": "chapter_numbers_not_increasing",
                "chapter_numbers": number_sequence["numbers"][:20],
                "legacy_reason": "chapter_numbers_not_increasing",
            }
        )

    low_risk_numbered_outline = _is_low_risk_numbered_outline(
        chapters,
        titles,
        page_ranges_monotonic=page_ranges_monotonic,
        number_sequence=number_sequence,
    )
    suspicious_accepted_titles = [
        chapter.title
        for chapter in chapters
        if chapter.suspicious_reason or is_suspicious_chapter_title(chapter.title)
    ]
    suspicious_rejected_titles = [
        candidate.title
        for candidate in rejected_candidates
        if candidate.rejected_reason == "pseudocode"
    ]
    suspicious_titles = suspicious_accepted_titles + suspicious_rejected_titles
    if suspicious_titles:
        if not suspicious_accepted_titles and low_risk_numbered_outline:
            warnings.append(
                {
                    "code": "possible_outline_title_noise",
                    "titles": suspicious_titles[:5],
                    "message": "检测到疑似正文噪声标题，但已选择的章节编号和页码范围正常，不阻断导入。",
                }
            )
        else:
            blockers.append(
                {
                    "code": "suspicious_chapter_titles",
                    "titles": suspicious_titles[:5],
                    "legacy_reason": "suspicious_chapter_titles:" + " | ".join(suspicious_titles[:5]),
                }
            )

    similar_groups = _similar_title_prefix_groups(titles)
    for group in similar_groups:
        warnings.append(
            {
                "code": "similar_chapter_title_prefixes",
                "titles": group[:5],
                "message": "章节标题相似，请确认。",
            }
        )

    decision = "blocked" if blockers else "allowed_with_warnings" if warnings else "allowed"
    return {
        "book_safety_decision": decision,
        "book_safety_blockers": blockers,
        "book_safety_warnings": warnings,
        "detected_chapter_count": chapter_count,
        "chapter_title_quality": "blocked" if blockers else "acceptable",
    }


def apply_prepared_book_import(
    prepared: PreparedBookImport,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    backup: bool = True,
    document_type: str = "book",
) -> dict[str, Any]:
    safety = evaluate_auto_apply_safety(
        prepared,
        db_path=db_path,
        document_type=document_type,
    )
    if not safety["auto_apply_eligible"]:
        raise ValueError("book import is not safe to apply: " + "; ".join(safety["reasons"]))

    db = Path(db_path)
    backup_info = backup_database(db) if backup else None
    before_counts = count_core_tables(db)
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    chapter_ids: list[int] = []
    chunk_ids: list[int] = []
    layout_result: dict[str, Any] = {
        "inserted_layout_blocks": 0,
        "inserted_chunk_layout_links": 0,
        "layout_match_rate": 0.0,
    }
    chapter_id_by_index: dict[int, int] = {}
    with sqlite3.connect(db) as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (
                title, document_type, content_layer, source_path, pdf_path, read_status,
                object_import_mode, object_import_status, created_at, updated_at
            )
            VALUES (?, ?, 'evidence', ?, ?, 'read', ?, 'open', ?, ?)
            """,
            (
                prepared.title,
                str(document_type or "book"),
                str(prepared.pdf_path),
                str(prepared.pdf_path),
                prepared.object_import_mode,
                now,
                now,
            ),
        )
        document_id = int(cursor.lastrowid)
        for chapter in prepared.chapters:
            cursor = connection.execute(
                """
                INSERT INTO book_chapters (
                    document_id, chapter_index, title, heading_path, pdf_page_start,
                    pdf_page_end, object_import_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'not_started', ?, ?)
                """,
                (
                    document_id,
                    chapter.chapter_index,
                    chapter.title,
                    chapter.heading_path,
                    chapter.pdf_page_start,
                    chapter.pdf_page_end,
                    now,
                    now,
                ),
            )
            chapter_id = int(cursor.lastrowid)
            chapter_ids.append(chapter_id)
            chapter_id_by_index[chapter.chapter_index] = chapter_id

        for global_index, (chunk, chapter_index) in enumerate(
            zip(prepared.chunks, prepared.chunk_chapter_indexes)
        ):
            chapter_id = chapter_id_by_index.get(chapter_index or -1)
            cursor = connection.execute(
                """
                INSERT INTO knowledge_chunks (
                    document_id, node_id, chunk_index, heading_path, chunk_text, char_count,
                    token_count, overlap_before, overlap_after, content_hash, embedding_id,
                    pdf_path, pdf_page_start, pdf_page_end, chapter_id, zotero_open_url,
                    created_at, updated_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    document_id,
                    global_index,
                    chunk.heading_path,
                    chunk.chunk_text,
                    chunk.char_count,
                    chunk.overlap_before,
                    chunk.overlap_after,
                    _chunk_hash(f"{document_id}:{global_index}:{chunk.chunk_text}"),
                    str(prepared.pdf_path),
                    chunk.pdf_page_start,
                    chunk.pdf_page_end,
                    chapter_id,
                    now,
                    now,
                ),
            )
            chunk_ids.append(int(cursor.lastrowid))
        layout_result = persist_layout_blocks_and_links(
            connection,
            document_id=document_id,
            chunks=prepared.chunks,
            chunk_ids=chunk_ids,
            layout_blocks=prepared.layout_blocks,
            layout_lines=prepared.layout_lines,
            layout_spans=prepared.layout_spans,
            created_at=now,
        )
        text_cache_written = 0
        for pdf_page, extracted_text in prepared.ocr_text_layer_cache.items():
            text_cache_written += insert_pdf_page_text_layer_cache(
                connection,
                document_id=document_id,
                pdf_page=int(pdf_page),
                source="surya_ocr",
                extracted_text=str(extracted_text or ""),
                created_at=now,
            )
        connection.commit()

    after_counts = count_core_tables(db)
    return {
        "status": "APPLIED",
        "backup": backup_info,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "document_id": document_id,
        "document_type": str(document_type or "book"),
        "book_chapter_ids": chapter_ids,
        "knowledge_chunk_ids": chunk_ids,
        "inserted_chapters": len(chapter_ids),
        "inserted_chunks": len(chunk_ids),
        "inserted_objects": count_object_candidates_for_document(db, document_id),
        "chapter_id_binding_rate": prepared.binding_rate,
        "inserted_pdf_page_text_layer_cache": text_cache_written,
        "book_safety_decision": safety.get("book_safety_decision"),
        "book_safety_blockers": safety.get("book_safety_blockers", []),
        "book_safety_warnings": safety.get("book_safety_warnings", []),
        "detected_chapter_count": safety.get("detected_chapter_count", len(prepared.chapters)),
        "chapter_title_quality": safety.get("chapter_title_quality"),
        **layout_result,
    }


def backup_database(db_path: str | Path) -> dict[str, Any]:
    db = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"research_memory_before_z110c_book_import_{timestamp}.db"
    shutil.copy2(db, backup_path)
    return {"path": str(backup_path), "size_bytes": backup_path.stat().st_size}


def count_core_tables(db_path: str | Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("documents", "book_chapters", "knowledge_chunks", "object_candidates")
        }


def find_duplicate_book(db_path: str | Path, pdf_path: Path, title: str) -> dict[str, Any] | None:
    db = Path(db_path)
    if not db.exists():
        return None
    normalized_pdf = str(pdf_path)
    normalized_title = _normalize_title(title)
    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT id, title, pdf_path FROM documents WHERE document_type = 'book'"
        ).fetchall()
    for document_id, existing_title, existing_pdf_path in rows:
        if existing_pdf_path and str(existing_pdf_path) == normalized_pdf:
            return {"document_id": int(document_id), "reason": "pdf_path"}
        if _normalize_title(str(existing_title or "")) == normalized_title:
            return {"document_id": int(document_id), "reason": "title"}
    return None


def count_object_candidates_for_document(db_path: str | Path, document_id: int) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM object_candidates WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0]
        )


def detect_book_chapters(
    markdown_text: str,
    *,
    pdf_path: str | Path | None = None,
    page_count: int = 0,
) -> dict[str, Any]:
    rejected: list[ChapterCandidate] = []
    outline_candidates = extract_pdf_outline_chapter_candidates(pdf_path) if pdf_path else []
    outline_chapters = _accepted_chapters(outline_candidates, rejected)
    if len(outline_chapters) >= 5:
        return _chapter_detection_payload("pdf_outline", outline_chapters, rejected)

    toc_candidates = extract_toc_chapter_candidates(markdown_text)
    toc_chapters = _accepted_chapters(toc_candidates, rejected)
    section_candidates = extract_section_header_chapter_candidates(markdown_text)
    section_chapters = _accepted_chapters(section_candidates, rejected)
    if len(toc_chapters) >= 5:
        if len(section_chapters) >= 5 and _page_start_completeness(section_chapters) > _page_start_completeness(toc_chapters):
            return _chapter_detection_payload("section_header", section_chapters, rejected)
        return _chapter_detection_payload("toc", toc_chapters, rejected)

    return _chapter_detection_payload("section_header", section_chapters, rejected)


def _page_start_completeness(chapters: list[DetectedChapter]) -> float:
    if not chapters:
        return 0.0
    with_starts = sum(1 for chapter in chapters if chapter.pdf_page_start is not None)
    return with_starts / len(chapters)


def _classify_outline_title(title: str, level: int, page: int, context: dict | None = None) -> dict[str, Any]:
    """Classify a single outline title into semantic type: front_matter/part/chapter/section/back_matter/unknown.

    Uses title text pattern matching (Chinese + English), NOT hardcoded level rules.
    """
    import re
    normalized = title.strip()
    lower = normalized.lower()

    # ── front_matter ──
    fm_patterns = [
        r"^(目录|目次|前言|序言|序|致谢|鸣谢|谢辞|出版说明|作者简介|译者序)$",
        r"^(网站|数学符号|符号说明|符号表|记号|notation|notational convention)$",
        r"^(preface|foreword|contents?|acknowledg?ments?|notation|conventions?)$",
    ]
    for pat in fm_patterns:
        if re.match(pat, lower):
            return {"semantic_type": "front_matter", "confidence": 0.95, "reasons": [f"matched front_matter pattern: {pat}"]}

    # ── back_matter ──
    bm_patterns = [
        r"^(参考文献|参考资料|引用文献|术语|术语表|名词索引|索引|附录[ A-Za-z]*|后记|跋)$",
        r"^(references?|bibliography|glossary|index|appendix [a-z]?|appendices|afterword|colophon)$",
    ]
    for pat in bm_patterns:
        if re.match(pat, lower):
            return {"semantic_type": "back_matter", "confidence": 0.95, "reasons": [f"matched back_matter pattern: {pat}"]}

    # ── part ──
    part_patterns = [
        r"^第[一二三四五六七八九十百]+部分",
        r"^part\s+(i{1,3}|iv|v|vi{0,3}|ix|[1-9]\d*)\b",
        r"^unit\s+[ivxlcdm1-9]",
        r"^book\s+[ivxlcdm1-9]",
    ]
    for pat in part_patterns:
        if re.match(pat, lower):
            return {"semantic_type": "part", "confidence": 0.90, "reasons": [f"matched part pattern: {pat}"]}

    # ── chapter ──
    ch_patterns = [
        r"^第[一二三四五六七八九十百零\d]+章\b",
        r"^chapter\s+\d+\b",
        r"^chapter\s+[ivxlcdm]+\b",
        r"^[Cc][Hh][Aa][Pp][Tt][Ee][Rr]\s+\d+",
    ]
    for pat in ch_patterns:
        if re.match(pat, lower):
            return {"semantic_type": "chapter", "confidence": 0.95, "reasons": [f"matched chapter pattern: {pat}"]}

    # ── numeric-prefix chapter (e.g. "1 Introduction", "10 Sequence Modeling") ──
    # Must start with 1-2 digit number + space + capitalized word, not dotted (1.1 is section)
    if re.match(r"^(\d{1,2})\s+[A-Z\u4e00-\u9fff]", normalized) and "." not in normalized[:5]:
        num = int(re.match(r"^(\d{1,2})", normalized).group(1))
        if 1 <= num <= 99:
            # Only if it looks like a chapter title (has meaningful words after number)
            rest = normalized[len(str(num)):].strip()
            if len(rest) >= 4:
                return {"semantic_type": "chapter", "confidence": 0.75, "reasons": [f"numeric chapter prefix: {num}"]}

    # ── section / subsection ──
    if re.match(r"^\d+\.\d+(\.\d+)?\s+[A-Z\u4e00-\u9fff]", normalized):
        dots = normalized.split(".")[0].count(".") + 1
        if normalized.count(".") >= 2:
            return {"semantic_type": "subsection", "confidence": 0.80, "reasons": ["subsection dotted pattern"]}
        return {"semantic_type": "section", "confidence": 0.85, "reasons": ["section dotted pattern"]}

    # ── alphabetic section (A.1, B.2, etc.) ──
    if re.match(r"^[A-Z]\.\d+(\.\d+)?\s+[A-Z\u4e00-\u9fff]", normalized):
        return {"semantic_type": "section", "confidence": 0.70, "reasons": ["alphabetic section pattern"]}

    return {"semantic_type": "unknown", "confidence": 0.3, "reasons": ["no pattern matched"]}


def _build_outline_tree(toc: list[tuple[int, str, int]], page_count: int) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """Build a tree structure from flat PDF TOC, classify each node."""
    import re

    nodes: list[dict[str, Any]] = []
    node_ids: dict[int, str] = {}  # index -> id
    id_counter = [0]

    def _new_id() -> str:
        id_counter[0] += 1
        return f"outline-{id_counter[0]}"

    # First pass: create all nodes
    for idx, (level, raw_title, page) in enumerate(toc):
        cleaned = _clean_toc_title(str(raw_title))
        # Classify on lightly-cleaned title (not _clean_toc_title which strips chapter numbers)
        classify_title = _clean_heading_title(str(raw_title))
        classification = _classify_outline_title(classify_title, level, page)
        node = {
            "id": _new_id(),
            "level": level,
            "raw_title": str(raw_title),
            "title": cleaned,
            "normalized_title": re.sub(r"\s+", "", cleaned).casefold(),
            "semantic_type": classification["semantic_type"],
            "pdf_page_start": int(page),
            "pdf_page_end": page_count,
            "children": [],
            "source": "pdf_outline",
            "confidence": classification["confidence"],
        }
        nodes.append(node)
        node_ids[idx] = node["id"]

    # Compute page ends (next node's start - 1)
    for i, node in enumerate(nodes):
        if i + 1 < len(nodes):
            node["pdf_page_end"] = max(node["pdf_page_start"], nodes[i + 1]["pdf_page_start"] - 1)
        else:
            node["pdf_page_end"] = page_count

    # Second pass: build tree structure
    root_nodes: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []  # (node, level)

    for node in nodes:
        level = node["level"]
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            root_nodes.append(node)
        stack.append(node)

    # Compute counts
    level_counts: dict[str, int] = {}
    semantic_counts: dict[str, int] = {}
    for node in nodes:
        lk = str(node["level"])
        level_counts[lk] = level_counts.get(lk, 0) + 1
        sk = node["semantic_type"]
        semantic_counts[sk] = semantic_counts.get(sk, 0) + 1

    return root_nodes, level_counts, semantic_counts


def _recommend_import_granularity(
    semantic_counts: dict[str, int],
    outline_tree: list[dict[str, Any]],
    total_page_count: int,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Recommend import granularity and generate import_granularity_options."""
    chapter_count = semantic_counts.get("chapter", 0)
    section_count = semantic_counts.get("section", 0)
    part_count = semantic_counts.get("part", 0)
    warnings: list[str] = []

    options: list[dict[str, Any]] = []

    # Chapter option
    if chapter_count > 0:
        options.append({
            "value": "chapter",
            "label": "按章导入",
            "description": f"导入 {chapter_count} 章",
            "estimated_unit_count": chapter_count,
            "recommended": False,
        })

    # Part option
    if part_count > 0:
        options.append({
            "value": "part",
            "label": "按部分导入",
            "description": f"导入 {part_count} 个部分",
            "estimated_unit_count": part_count,
            "recommended": False,
        })

    # Section option
    if section_count > 0:
        options.append({
            "value": "section",
            "label": "按节导入",
            "description": f"导入 {section_count} 节",
            "estimated_unit_count": section_count,
            "recommended": False,
        })

    # ── Recommendation logic ──
    recommended = "manual_review_required"

    if 1 <= chapter_count <= 80:
        recommended = "chapter"
        for opt in options:
            if opt["value"] == "chapter":
                opt["recommended"] = True
    elif chapter_count > 80:
        warnings.append("chapter count is large; outline may include sections/subsections")
        # Still recommend chapter but with warning
        recommended = "chapter"
        for opt in options:
            if opt["value"] == "chapter":
                opt["recommended"] = True
    elif part_count >= 2 and chapter_count == 0:
        recommended = "part"
        warnings.append("only part-level outline detected; import units may be too coarse")
        for opt in options:
            if opt["value"] == "part":
                opt["recommended"] = True
    elif section_count > 0:
        if section_count <= 80:
            recommended = "section"
            for opt in options:
                if opt["value"] == "section":
                    opt["recommended"] = True
        else:
            warnings.append("section count is very large; import units may be too fine-grained")
            recommended = "section"  # still offer it
            for opt in options:
                if opt["value"] == "section":
                    opt["recommended"] = True

    if not options:
        warnings.append("no importable units found in outline")
        recommended = "manual_review_required"

    return recommended, options, warnings


def _collect_import_units(
    outline_tree: list[dict[str, Any]],
    granularity: str,
) -> list[dict[str, Any]]:
    """Collect all nodes matching the selected granularity from the outline tree."""

    def _node_end(node: dict[str, Any]) -> int | None:
        candidates = [node.get("pdf_page_end")]
        for child in node.get("children", []) or []:
            candidates.append(_node_end(child))
        numeric = [int(value) for value in candidates if value is not None]
        return max(numeric) if numeric else None

    matched_nodes: list[dict[str, Any]] = []

    def _walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            st = node.get("semantic_type", "")
            matched = False
            if granularity == "chapter" and st == "chapter":
                matched = True
            elif granularity == "section" and st in ("section", "subsection"):
                matched = True
            elif granularity == "part" and st == "part":
                matched = True
            elif granularity.startswith("outline_level_"):
                target_level = int(granularity.split("_")[-1])
                if node["level"] == target_level:
                    matched = True

            if matched:
                matched_nodes.append({
                    "title": node["title"],
                    "pdf_page_start": node["pdf_page_start"],
                    "pdf_page_end": _node_end(node),
                    "confidence": node["confidence"],
                    "source": node["source"],
                    "semantic_type": st,
                })
            _walk(node.get("children", []))

    _walk(outline_tree)
    matched_nodes.sort(key=lambda item: (int(item.get("pdf_page_start") or 10**9), str(item.get("title") or "")))
    result: list[dict[str, Any]] = []
    for index, node in enumerate(matched_nodes):
        start = int(node["pdf_page_start"]) if node.get("pdf_page_start") is not None else None
        raw_end = int(node["pdf_page_end"]) if node.get("pdf_page_end") is not None else start
        next_start = (
            int(matched_nodes[index + 1]["pdf_page_start"])
            if index + 1 < len(matched_nodes) and matched_nodes[index + 1].get("pdf_page_start") is not None
            else None
        )
        page_end = max(start or 1, next_start - 1) if next_start is not None else raw_end
        result.append({
            **node,
            "chapter_index": index + 1,
            "pdf_page_start": start,
            "pdf_page_end": page_end,
        })
    return result


def build_chaptered_preview_from_outline(
    pdf_path: str | Path,
    *,
    title_hint: str | None = None,
) -> dict[str, Any]:
    """Generate a chaptered preview from PDF outline/bookmarks only.

    Does NOT call Marker/Surya or any full-text parser.
    Returns a dict compatible with the /chaptered/preview API response.
    """
    import logging
    logger = logging.getLogger(__name__)

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf}")

    try:
        fitz = load_fitz_backend()
        with fitz.open(pdf) as document:
            page_count = len(document)
            metadata_title = str((document.metadata or {}).get("title") or "").strip()
            toc = document.get_toc(simple=True)
    except PdfBackendUnavailableError:
        raise
    except Exception as exc:
        logger.exception("Failed to open PDF for outline preview: %s", pdf)
        raise ValueError(f"Failed to read PDF: {exc}") from exc

    title = title_hint or metadata_title or pdf.stem

    base = {
        "status": "ok",
        "detection_method": "outline_unavailable",
        "chapter_count": 0,
        "estimated_chunk_count": None,
        "page_marker_count": page_count,
        "chunk_binding_rate": None,
        "suspicious_chapter_titles_count": 0,
        "book_safety_decision": "blocked",
        "book_safety_blockers": [{"code": "selected_outline_unreliable"}],
        "book_safety_warnings": [],
        "detected_chapter_count": 0,
        "chapter_title_quality": "blocked",
        "accepted_chapters": [],
        "truncated_chapters": 0,
        "warnings": [],
        "auto_apply_eligible": False,
        "auto_apply_reasons": [],
        "duplicate": False,
        "preview_is_outline_only": True,
        "full_text_parse_performed": False,
        "db_write_performed": False,
        "external_llm_called": False,
        "outline_tree": [],
        "outline_level_counts": {},
        "semantic_type_counts": {},
        "recommended_import_granularity": "manual_review_required",
        "import_granularity_options": [],
    }

    if not toc:
        base["warnings"].append("PDF outline unavailable; full parser preview is required before import")
        base["auto_apply_reasons"] = ["outline_unavailable"]
        return base

    outline_tree, level_counts, semantic_counts = _build_outline_tree(toc, page_count)
    recommended, options, warnings = _recommend_import_granularity(semantic_counts, outline_tree, page_count)

    accepted = _collect_import_units(outline_tree, recommended) if recommended != "manual_review_required" else []
    chapter_count = len(accepted)
    preview_chapters = [
        DetectedChapter(
            chapter_index=int(item.get("chapter_index") or index),
            title=str(item.get("title") or ""),
            heading_path=str(item.get("title") or ""),
            pdf_page_start=item.get("pdf_page_start"),
            pdf_page_end=item.get("pdf_page_end"),
            detection_method="pdf_outline_preview",
            source=str(item.get("source") or "pdf_outline"),
            confidence=float(item.get("confidence", 0.8)),
        )
        for index, item in enumerate(accepted, start=1)
    ]
    safety = evaluate_chapter_title_safety(book_title=title, chapters=preview_chapters)
    auto_apply_reasons = [] if chapter_count > 0 else ["no_importable_units"]
    auto_apply_reasons.extend(blocker.get("code", "book_safety_blocker") for blocker in safety["book_safety_blockers"])

    return {
        **base,
        "detection_method": "pdf_outline",
        "chapter_count": chapter_count,
        "suspicious_chapter_titles_count": len(safety["book_safety_blockers"]),
        "accepted_chapters": accepted[:50],
        "truncated_chapters": max(0, chapter_count - 50),
        "warnings": warnings,
        "auto_apply_eligible": chapter_count > 0 and recommended != "manual_review_required" and safety["book_safety_decision"] != "blocked",
        "auto_apply_reasons": auto_apply_reasons,
        "book_safety_decision": safety["book_safety_decision"],
        "book_safety_blockers": safety["book_safety_blockers"],
        "book_safety_warnings": safety["book_safety_warnings"],
        "detected_chapter_count": chapter_count,
        "chapter_title_quality": safety["chapter_title_quality"],
        "outline_tree": outline_tree,
        "outline_level_counts": level_counts,
        "semantic_type_counts": semantic_counts,
        "recommended_import_granularity": recommended,
        "import_granularity_options": options,
    }


def extract_pdf_outline_chapter_candidates(pdf_path: str | Path | None) -> list[ChapterCandidate]:
    if pdf_path is None:
        return []
    pdf = Path(pdf_path)
    if not pdf.exists():
        return []
    try:
        fitz = load_fitz_backend()
        with fitz.open(pdf) as document:
            toc = document.get_toc(simple=True)
    except Exception:
        return []
    candidates: list[ChapterCandidate] = []
    for level, raw_title, page in toc:
        title = _clean_toc_title(str(raw_title))
        if _is_front_matter_title(title):
            continue
        confidence = 0.95 if level <= 2 else 0.80
        candidates.append(ChapterCandidate(title=title, source="pdf_outline", page_start=int(page), confidence=confidence))
    return candidates


def extract_toc_chapter_candidates(markdown_text: str) -> list[ChapterCandidate]:
    current_page: int | None = None
    candidates: list[ChapterCandidate] = []
    for line in markdown_text.splitlines():
        page_match = PDF_PAGE_RE.search(line)
        if page_match:
            current_page = int(page_match.group(1))
            if current_page > 120:
                break
            continue
        if current_page is not None and current_page > 120:
            continue
        title = _extract_toc_title(line)
        if not title or _is_front_matter_title(title):
            continue
        candidates.append(ChapterCandidate(title=title, source="toc", page_start=None, confidence=0.85))
    return candidates


def extract_section_header_chapter_candidates(markdown_text: str) -> list[ChapterCandidate]:
    current_page: int | None = None
    candidates: list[ChapterCandidate] = []
    for line in markdown_text.splitlines():
        page_match = PDF_PAGE_RE.search(line)
        if page_match:
            current_page = int(page_match.group(1))
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not heading_match:
            continue
        title = _clean_heading_title(heading_match.group(2))
        candidates.append(
            ChapterCandidate(
                title=title,
                source="section_header",
                page_start=current_page,
                confidence=_section_header_confidence(title),
            )
        )
    return candidates


def _accepted_chapters(candidates: list[ChapterCandidate], rejected: list[ChapterCandidate]) -> list[DetectedChapter]:
    accepted: list[ChapterCandidate] = []
    for candidate in candidates:
        reason = reject_chapter_candidate(candidate.title, source=candidate.source)
        if reason:
            rejected.append(
                ChapterCandidate(
                    title=candidate.title,
                    source=candidate.source,
                    page_start=candidate.page_start,
                    confidence=candidate.confidence,
                    suspicious_reason=reason if reason == "pseudocode" else None,
                    rejected_reason=reason,
                )
            )
            continue
        accepted.append(candidate)
    return _dedupe_chapter_candidates(
        [
            DetectedChapter(
                chapter_index=index,
                title=candidate.title,
                heading_path=candidate.title,
                pdf_page_start=candidate.page_start,
                detection_method=candidate.source,
                source=candidate.source,
                confidence=candidate.confidence,
                suspicious_reason=None,
            )
            for index, candidate in enumerate(accepted, start=1)
        ]
    )


def _chapter_detection_payload(
    method: str,
    chapters: list[DetectedChapter],
    rejected: list[ChapterCandidate],
) -> dict[str, Any]:
    return {
        "detection_method": method,
        "chapters": chapters,
        "rejected_candidates": rejected,
        "rejected_candidates_summary": _rejected_summary(rejected),
    }


def _plain_text_page_nodes(
    markdown_text: str,
    *,
    chapters: list[DetectedChapter],
    pdf_path: Path,
) -> list[ParsedMarkdownNode]:
    """Turn page-marked native text into chunkable nodes when it has no headings."""

    matches = list(PDF_PAGE_RE.finditer(markdown_text))
    if not matches:
        return []
    pages: list[tuple[int, list[str]]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        lines = [
            re.sub(r"[ \t]+", " ", line).strip()
            for line in markdown_text[start:end].splitlines()
            if line.strip() and not line.lstrip().startswith("<!-- PDF_PATH:")
        ]
        pages.append((int(match.group(1)), lines))

    repeated = _repeated_page_edge_lines(pages)
    nodes: list[ParsedMarkdownNode] = []
    for page_number, lines in pages:
        cleaned_lines = [
            line
            for line in lines
            if _normalized_repeated_line(line) not in repeated
        ]
        content = "\n".join(cleaned_lines).strip()
        if not content:
            continue
        chapter = _chapter_for_page(page_number, chapters)
        heading = chapter.title if chapter else pdf_path.stem
        nodes.append(
            ParsedMarkdownNode(
                order_index=len(nodes),
                parent_order_index=None,
                heading_level=1,
                heading_title=heading,
                heading_path=heading,
                raw_content=content,
                pdf_page_start=page_number,
                pdf_path=str(pdf_path),
            )
        )
    return nodes


def _repeated_page_edge_lines(
    pages: list[tuple[int, list[str]]],
) -> set[str]:
    occurrences: dict[str, int] = {}
    for _page_number, lines in pages:
        edges = [*lines[:2], *lines[-2:]]
        for normalized in {
            _normalized_repeated_line(line)
            for line in edges
            if 4 <= len(line.strip()) <= 160
        }:
            occurrences[normalized] = occurrences.get(normalized, 0) + 1
    threshold = max(3, int(len(pages) * 0.35))
    return {
        line
        for line, count in occurrences.items()
        if line and count >= threshold
    }


def _normalized_repeated_line(value: str) -> str:
    return re.sub(r"\d+", "#", value.strip().casefold())


def _bind_chunks_to_outline_units(
    chunks: list[TextChunk],
    chapters: list[DetectedChapter],
) -> tuple[list[int | None], list[str]]:
    """Bind chunks to chapters using page-range-based matching.

    If a chunk falls outside all chapter page ranges, try nearest-chapter fallback.
    Records warnings for chunks that could not be cleanly bound.
    """
    if not chapters:
        return [None for _ in chunks], ["synthetic_full_text: no chapters available for binding"]
    warnings: list[str] = []
    chapter_indexes: list[int | None] = []
    for chunk in chunks:
        matched = _chapter_for_page(chunk.pdf_page_start, chapters)
        if matched is None:
            matched = _nearest_chapter(chunk.pdf_page_start, chapters)
            if matched is not None:
                warnings.append(f"nearest_chapter_binding: chunk_page={chunk.pdf_page_start} → chapter {matched.chapter_index}")
        chapter_indexes.append(matched.chapter_index if matched else None)
    return chapter_indexes, warnings[:20]


def bind_chunks_to_chapters(
    chunks: list[TextChunk],
    chapters: list[DetectedChapter],
) -> tuple[list[int | None], list[str]]:
    if not chapters:
        return [None for _ in chunks], ["synthetic_full_text: no chapters available for binding"]
    warnings: list[str] = []
    chapter_indexes: list[int | None] = []
    for chunk in chunks:
        matched = _chapter_for_page(chunk.pdf_page_start, chapters)
        if matched is None:
            matched = _nearest_chapter(chunk.pdf_page_start, chapters)
            if matched is not None:
                warnings.append(f"nearest_chapter_binding: chunk_page={chunk.pdf_page_start}")
        chapter_indexes.append(matched.chapter_index if matched else None)
    return chapter_indexes, warnings[:20]


def _chapter_for_page(page: int | None, chapters: list[DetectedChapter]) -> DetectedChapter | None:
    if page is None:
        return None
    for chapter in chapters:
        start = chapter.pdf_page_start
        end = chapter.pdf_page_end
        if start is not None and end is not None and start <= page <= end:
            return chapter
    return None


def _nearest_chapter(page: int | None, chapters: list[DetectedChapter]) -> DetectedChapter | None:
    if page is None:
        return chapters[0] if chapters else None
    with_pages = [chapter for chapter in chapters if chapter.pdf_page_start is not None]
    if not with_pages:
        return chapters[0]
    previous = [chapter for chapter in with_pages if (chapter.pdf_page_start or 0) <= page]
    if previous:
        return previous[-1]
    return with_pages[0]


def _with_page_ends(chapters: list[DetectedChapter], page_count: int) -> list[DetectedChapter]:
    ordered = sorted(chapters, key=lambda chapter: (chapter.pdf_page_start or 10**9, chapter.chapter_index))
    output: list[DetectedChapter] = []
    for index, chapter in enumerate(ordered):
        next_start = ordered[index + 1].pdf_page_start if index + 1 < len(ordered) else None
        page_end = (next_start - 1) if next_start else (page_count or None)
        output.append(
            DetectedChapter(
                chapter_index=index + 1,
                title=chapter.title,
                heading_path=chapter.heading_path,
                pdf_page_start=chapter.pdf_page_start,
                pdf_page_end=page_end,
                detection_method=chapter.detection_method,
                source=chapter.source,
                confidence=chapter.confidence,
                suspicious_reason=chapter.suspicious_reason,
            )
        )
    return output


def _dedupe_chapter_candidates(candidates: list[DetectedChapter]) -> list[DetectedChapter]:
    by_title: dict[str, DetectedChapter] = {}
    for candidate in candidates:
        by_title[_normalize_title(candidate.title)] = candidate
    ordered = sorted(by_title.values(), key=lambda chapter: (chapter.pdf_page_start or 10**9, chapter.title))
    return [
        DetectedChapter(
            chapter_index=index,
            title=chapter.title,
            heading_path=chapter.heading_path,
            pdf_page_start=chapter.pdf_page_start,
            detection_method=chapter.detection_method,
            source=chapter.source,
            confidence=chapter.confidence,
            suspicious_reason=chapter.suspicious_reason,
        )
        for index, chapter in enumerate(ordered, start=1)
    ]


def reject_chapter_candidate(title: str, *, source: str = "section_header") -> str | None:
    if is_suspicious_chapter_title(title):
        return "pseudocode"
    if _is_page_header_or_footer(title):
        return "page_header"
    if _is_toc_noise(title):
        return "toc_noise"
    if _is_too_short_for_chapter(title):
        return "too_short"
    if not is_main_chapter_title(title):
        return "body_fragment"
    if source == "section_header" and _looks_like_weak_numeric_fragment(title):
        return "body_fragment"
    return None


def is_main_chapter_title(title: str) -> bool:
    if re.match(r"^第\s*[0-9一二三四五六七八九十百零〇]+\s*章\b", title):
        return True
    if re.match(r"^Chapter\s+\d+\b", title, flags=re.IGNORECASE):
        return True
    numeric_match = re.match(r"^(\d{1,2})\s+([A-Za-z][^\n]{2,120})$", title)
    if numeric_match and not re.match(r"^\d+\.\d+", title):
        chapter_number = int(numeric_match.group(1))
        tail = numeric_match.group(2)
        if 1 <= chapter_number <= 60 and not _looks_like_code_or_page_fragment(tail):
            return True
    return False


def is_suspicious_chapter_title(title: str) -> bool:
    if _looks_like_code_or_page_fragment(title):
        return True
    if re.match(r"^\d{3,}\s+", title):
        return True
    return False


def _looks_like_code_or_page_fragment(title: str) -> bool:
    stripped = title.strip()
    lower = stripped.lower()
    code_verbs = (
        "return", "while", "if", "else", "elseif", "repeat", "until", "do",
        "then", "break", "continue", "goto", "print", "let", "set", "swap", "call",
        "insert", "delete", "extract", "decrease-key", "min-heapify", "build-max-heap",
        "build-max-heap(a)", "sync",
    )
    if re.match(rf"^\d+\s+({'|'.join(re.escape(verb) for verb in code_verbs)})(\s|$|\()", lower):
        return True
    if re.match(r"^\d+\s+for\s+.+(?:=|<|>|←|;|\bto\b|\bin\b)", lower):
        return True
    if title.upper().startswith(("BUILD-MAX-HEAP", "MAX-HEAPIFY", "MIN-HEAPIFY")):
        return True
    code_symbol_count = sum(title.count(symbol) for symbol in ("|", "<=", ">=", "←", "==", "++", "--", "=", "<", ">", ";", "{", "}", "[", "]"))
    if code_symbol_count >= 1 and re.match(r"^\d+\s+", title):
        return True
    if code_symbol_count >= 2:
        return True
    if re.search(r"^\d+\s+\S{1,3}$", title):
        return True
    return False


def _exact_duplicate_title_groups(titles: list[str]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index, title in enumerate(titles, start=1):
        normalized = " ".join(title.strip().casefold().split())
        if not normalized:
            continue
        groups.setdefault(normalized, []).append(index)
    return {title: indexes for title, indexes in groups.items() if len(indexes) > 1}


def _boilerplate_title_groups(titles: list[str], book_title: str) -> dict[str, list[int]]:
    boilerplate = {
        "contents",
        "table of contents",
        "copyright",
        "references",
        "bibliography",
        "index",
        "目录",
        "版权",
        "参考文献",
    }
    book_normalized = " ".join(str(book_title or "").casefold().split())
    groups: dict[str, list[int]] = {}
    for index, title in enumerate(titles, start=1):
        normalized = " ".join(title.strip().casefold().split())
        if normalized in boilerplate or (book_normalized and normalized == book_normalized):
            groups.setdefault(normalized, []).append(index)
    return {title: indexes for title, indexes in groups.items() if len(indexes) > 1}


def _has_non_monotonic_page_ranges(chapters: list[DetectedChapter]) -> bool:
    previous_start: int | None = None
    for chapter in chapters:
        start = chapter.pdf_page_start
        end = chapter.pdf_page_end
        if start is None or end is None:
            continue
        if end < start:
            return True
        if previous_start is not None and start <= previous_start:
            return True
        previous_start = start
    return False


def _chapter_number_sequence_state(titles: list[str]) -> dict[str, Any]:
    numbers: list[int] = []
    for title in titles:
        number = _chapter_number_from_title(title)
        if number is not None:
            numbers.append(number)
    non_increasing = any(right <= left for left, right in zip(numbers, numbers[1:]))
    return {
        "numbers": numbers,
        "numbered_count": len(numbers),
        "non_increasing": len(numbers) >= 3 and non_increasing,
        "increasing": bool(numbers) and not non_increasing,
    }


def _chapter_number_from_title(title: str) -> int | None:
    text = " ".join(str(title or "").strip().split())
    match = re.match(r"^(?:chapter|ch\.)\s+(\d{1,3})\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.match(r"^(\d{1,3})\s*[:.\-]?\s+\S", text)
    if match:
        return int(match.group(1))
    return None


def _is_low_risk_numbered_outline(
    chapters: list[DetectedChapter],
    titles: list[str],
    *,
    page_ranges_monotonic: bool,
    number_sequence: dict[str, Any],
) -> bool:
    chapter_count = len(chapters)
    if chapter_count < 5:
        return False
    if not page_ranges_monotonic:
        return False
    if number_sequence["numbered_count"] < max(3, int(chapter_count * 0.60)):
        return False
    if not number_sequence["increasing"]:
        return False
    if any(not title.strip() for title in titles):
        return False
    if any(chapter.pdf_page_start is None or chapter.pdf_page_end is None for chapter in chapters):
        return False
    if not _titles_have_discriminative_content(titles):
        return False
    return any(chapter.source in {"pdf_outline", "outline_granularity"} for chapter in chapters)


def _titles_have_discriminative_content(titles: list[str]) -> bool:
    for title in titles:
        cleaned = _strip_chapter_number_prefix(title)
        words = [word.strip(":-,.;()[]") for word in cleaned.split()]
        meaningful = [word for word in words if len(word) >= 3]
        if not meaningful:
            return False
    return True


def _similar_title_prefix_groups(titles: list[str]) -> list[list[str]]:
    cleaned = [_strip_chapter_number_prefix(title) for title in titles]
    groups: list[list[str]] = []
    index = 0
    while index < len(cleaned):
        group = [titles[index]]
        current_words = cleaned[index].split()
        next_index = index + 1
        while next_index < len(cleaned):
            common_len = _common_prefix_word_count(current_words, cleaned[next_index].split())
            if common_len < 3:
                break
            if not _has_discriminative_suffix(current_words, common_len):
                break
            if not _has_discriminative_suffix(cleaned[next_index].split(), common_len):
                break
            group.append(titles[next_index])
            next_index += 1
        if len(group) >= 3:
            groups.append(group)
            index = next_index
        else:
            index += 1
    return groups


def _strip_chapter_number_prefix(title: str) -> str:
    text = " ".join(str(title or "").strip().split())
    text = re.sub(r"^(chapter|ch\.)\s+\d+\s*[:.\-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d{1,3}\s*[:.\-]?\s+", "", text)
    text = re.sub(r"^第\s*[0-9一二三四五六七八九十百零〇]+\s*章\s*", "", text)
    return text


def _common_prefix_word_count(left: list[str], right: list[str]) -> int:
    count = 0
    for left_word, right_word in zip(left, right):
        if left_word.casefold() != right_word.casefold():
            break
        count += 1
    return count


def _has_discriminative_suffix(words: list[str], prefix_len: int) -> bool:
    suffix = words[prefix_len:]
    return bool(suffix and any(len(word.strip(":-,.;()[]")) >= 3 for word in suffix))


def resolve_pdf_path(pdf_path: str | Path) -> Path:
    pdf = Path(pdf_path)
    if pdf.exists() and pdf.is_file():
        return pdf
    parent = pdf.parent if str(pdf.parent) else Path(".")
    if parent.exists():
        normalized_name = re.sub(r"\s+", " ", pdf.name).strip()
        for candidate in parent.glob("*.pdf"):
            if re.sub(r"\s+", " ", candidate.name).strip() == normalized_name:
                return candidate
    raise FileNotFoundError(f"PDF file not found: {pdf}")


def _extract_toc_title(line: str) -> str | None:
    text = _clean_heading_title(re.sub(r"^#{1,6}\s+", "", line))
    if not text:
        return None
    match = re.match(r"(.+?)(?:\s*\.{2,}|\s+)(\d{1,4})\s*$", text)
    if not match:
        return None
    return _clean_toc_title(match.group(1))


def _clean_toc_title(title: str) -> str:
    cleaned = re.sub(r"\s*\.{2,}\s*\d{1,4}\s*$", "", title)
    cleaned = re.sub(r"\s+\d{1,4}\s*$", "", cleaned)
    cleaned = re.sub(r"\s*\.{2,}\s*$", "", cleaned)
    return _clean_heading_title(cleaned)


def _is_front_matter_title(title: str) -> bool:
    normalized = title.strip().casefold()
    front_matter = (
        "contents", "table of contents", "preface", "foreword", "acknowledgments",
        "译者序", "前言", "目录", "出版说明", "序", "致谢",
    )
    return normalized in {item.casefold() for item in front_matter}


def _is_page_header_or_footer(title: str) -> bool:
    return bool(re.match(r"^\d+\s*$", title) or re.match(r"^第\s*\d+\s*页$", title))


def _is_toc_noise(title: str) -> bool:
    return bool(re.search(r"\.{3,}", title) or title.strip().lower() in {"contents", "目录"})


def _is_too_short_for_chapter(title: str) -> bool:
    stripped = title.strip()
    if re.match(r"^(第\s*[0-9一二三四五六七八九十百零〇]+\s*章|Chapter\s+\d+)\b", stripped, flags=re.IGNORECASE):
        return False
    return len(stripped) < 5


def _looks_like_weak_numeric_fragment(title: str) -> bool:
    return bool(re.match(r"^\d{1,2}\s+[\u4e00-\u9fff]", title))


def _section_header_confidence(title: str) -> float:
    if re.match(r"^(第\s*[0-9一二三四五六七八九十百零〇]+\s*章|Chapter\s+\d+)\b", title, flags=re.IGNORECASE):
        return 0.82
    return 0.65


def _rejected_summary(rejected: list[ChapterCandidate]) -> dict[str, int]:
    keys = ("pseudocode", "page_header", "body_fragment", "too_short", "toc_noise")
    summary = {f"rejected_{key}": 0 for key in keys}
    for candidate in rejected:
        key = candidate.rejected_reason or "body_fragment"
        if key not in keys:
            key = "body_fragment"
        summary[f"rejected_{key}"] += 1
    return summary


def _clean_heading_title(title: str) -> str:
    return " ".join(title.strip().strip("#").split())


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()


def _guess_title(parsed_title: str, pdf: Path) -> str:
    if parsed_title and parsed_title != pdf.stem:
        return parsed_title
    return pdf.stem


def _ensure_pdf_path_marker(markdown_text: str, pdf_path: Path) -> str:
    if "<!-- PDF_PATH:" in markdown_text:
        return markdown_text
    try:
        display_path = str(pdf_path.resolve().relative_to(DATA_PROJECT_ROOT))
    except ValueError:
        display_path = str(pdf_path.resolve())
    return f"<!-- PDF_PATH: {display_path} -->\n\n{markdown_text}"


def _emit_progress(
    callback: Callable[[str, int, str, dict[str, Any] | None], None] | None,
    stage: str,
    progress_percent: int,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    callback(stage, progress_percent, message, extra)


def _empty_torch_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _count_page_markers(markdown_text: str) -> int:
    return len(re.findall(r"<!--\s*PDF_PAGE:\s*\d+\s*-->", markdown_text))


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
