from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.paths import PROJECT_ROOT
from app.services.pdf_backend_service import load_fitz_backend
from app.services.library_core_service import SAFE_PDF_ROOTS


SAMPLE_STRATEGY = "first_chapter_first_section_two_pages"
SELECTABLE_ROUTES = ["normal_text_layer", "ocr_layout_first_repair", "cancel_or_replace_pdf"]
PREVIEW_TOKEN_TTL_SECONDS = 15 * 60
PREVIEW_ALLOWED_PDF_ROOTS = (PROJECT_ROOT, *SAFE_PDF_ROOTS)

HTML_TAG_RE = re.compile(r"<(?!UNK\b)[A-Za-z/][^>]*>", re.IGNORECASE)
REPEATED_TOKEN_RE = re.compile(r"\b([A-Za-z]{1,20}|\d+(?:\.\d+)?)\b(?:\s+\1\b){2,}", re.IGNORECASE)
PAGE_NUMBER_RE = re.compile(r"^(?:[ivxlcdm]+|\d+)$", re.IGNORECASE)
RUNNING_HEADER_RE = re.compile(r"^(?:\d+\s+)?Chapter\s+\d+[.:]\s+.+$", re.IGNORECASE)
CHAPTER_TITLE_RE = re.compile(r"^\s*(?:chapter\s+)?1(?:\s+|:\s*).+", re.IGNORECASE)
SECTION_TITLE_RE = re.compile(r"^\s*1\.1(?:\s+|[.:]\s*).+", re.IGNORECASE)
ANY_SECTION_TITLE_RE = re.compile(r"^\s*\d+\.\d+(?:\s+|[.:]\s*).+")
MATH_SIGNAL_RE = re.compile(r"[=+\-<>]|\u2211|\u220f|\u222b|\u2264|\u2265|\u03b8|\u03bc|\u03c3|\u211d")
MATH_NOISE_RE = re.compile(r"(?:\\[A-Za-z]+|[A-Za-z]\^\{|_\{|<br\s*/?>|\ufffd)")
FIGURE_CAPTION_RE = re.compile(r"^(?:Figure|Table)\s+\d+(?:\.\d+)?[:.]", re.IGNORECASE)


@dataclass(frozen=True)
class PdfPreviewToken:
    pdf_path: Path
    expires_at: float
    sample_pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class SectionLocation:
    title: str
    physical_page_start: int
    physical_page_end: int
    detection_method: str


@dataclass(frozen=True)
class SamplePage:
    physical_page: int
    page_label: str
    page_width: float
    page_height: float
    raw_text: str
    cleaned_text: str
    text_layer_length: int
    image_count: int
    likely_scanned: bool
    header_footer_count: int


_preview_tokens: dict[str, PdfPreviewToken] = {}
_preview_tokens_lock = threading.Lock()


def issue_pdf_preview_token(
    source_path: str | Path,
    *,
    allowed_roots: tuple[Path, ...] | None = None,
    sample_pages: list[int] | tuple[int, ...] | None = None,
    ttl_seconds: int = PREVIEW_TOKEN_TTL_SECONDS,
) -> str:
    if ttl_seconds < 1:
        raise ValueError("preview token ttl_seconds must be positive")
    pdf_path = validate_pdf_preview_path(source_path, allowed_roots=allowed_roots)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + ttl_seconds
    authorized_pages = tuple(int(page) for page in (sample_pages or ()))
    with _preview_tokens_lock:
        _discard_expired_tokens_locked(time.time())
        _preview_tokens[token] = PdfPreviewToken(
            pdf_path=pdf_path,
            expires_at=expires_at,
            sample_pages=authorized_pages,
        )
    return token


def resolve_pdf_preview_token(token: str) -> Path | None:
    if not token:
        return None
    now = time.time()
    with _preview_tokens_lock:
        _discard_expired_tokens_locked(now)
        record = _preview_tokens.get(token)
    if record is None:
        return None
    try:
        current_path = validate_pdf_preview_path(record.pdf_path)
    except (FileNotFoundError, ValueError):
        return None
    return current_path if current_path == record.pdf_path else None


def resolve_pdf_preview_sample_pages(token: str, sample_pages: list[int], max_pages: int) -> Path:
    if max_pages < 1 or max_pages > 2:
        raise ValueError("repair preview max_pages must be between 1 and 2")
    requested_pages = tuple(int(page) for page in sample_pages)
    if not requested_pages or len(requested_pages) > max_pages or len(requested_pages) > 2:
        raise ValueError("repair preview requires one or two authorized sample pages")
    if len(set(requested_pages)) != len(requested_pages) or any(page < 1 for page in requested_pages):
        raise ValueError("repair preview sample_pages must be unique positive physical pages")
    pdf_path = resolve_pdf_preview_token(token)
    if pdf_path is None:
        raise ValueError("PDF preview token unavailable or expired")
    with _preview_tokens_lock:
        record = _preview_tokens.get(token)
    if record is None or not record.sample_pages:
        raise ValueError("PDF preview token is not authorized for OCR repair preview")
    if any(page not in record.sample_pages for page in requested_pages):
        raise ValueError("repair preview may only read preview-gate sample pages")
    return pdf_path


def validate_pdf_preview_path(
    source_path: str | Path,
    *,
    allowed_roots: tuple[Path, ...] | None = None,
) -> Path:
    raw_path = Path(source_path)
    if ".." in raw_path.parts:
        raise ValueError("PDF preview path traversal is not allowed")
    if raw_path.suffix.lower() != ".pdf":
        raise ValueError("PDF preview requires a .pdf file")
    pdf_path = raw_path.resolve(strict=False)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    roots = allowed_roots if allowed_roots is not None else PREVIEW_ALLOWED_PDF_ROOTS
    resolved_roots = tuple(Path(root).resolve(strict=False) for root in roots)
    if not any(_path_within_root(pdf_path, root) for root in resolved_roots):
        raise ValueError("PDF preview path is outside allowed roots")
    return pdf_path


def clear_pdf_preview_tokens() -> None:
    with _preview_tokens_lock:
        _preview_tokens.clear()


def _path_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _discard_expired_tokens_locked(now: float) -> None:
    expired = [token for token, record in _preview_tokens.items() if record.expires_at <= now]
    for token in expired:
        del _preview_tokens[token]


def build_import_preview_gate(
    *,
    pdf_path: str | Path | None = None,
    zotero_attachment_path: str | Path | None = None,
    document_id: int | None = None,
    sample_strategy: str = SAMPLE_STRATEGY,
    max_pages: int = 2,
) -> dict[str, Any]:
    if sample_strategy != SAMPLE_STRATEGY:
        raise ValueError(f"unsupported sample_strategy: {sample_strategy}")
    if max_pages < 1 or max_pages > 2:
        raise ValueError("max_pages must be between 1 and 2")
    source_path = pdf_path or zotero_attachment_path
    if not source_path:
        raise ValueError("pdf_path or zotero_attachment_path is required")
    pdf = Path(source_path).resolve(strict=False)
    if not pdf.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf}")

    fitz = load_fitz_backend()
    with fitz.open(pdf) as document:
        if document.page_count < 1:
            raise ValueError("PDF does not contain pages")
        labels = [_display_page_label(document, page_number) for page_number in range(1, document.page_count + 1)]
        section = detect_first_section(document)
        sample_pages = _sample_pages(document, section.physical_page_start, max_pages)
        preview_headings = _preview_outline_headings(document, sample_pages, section)
        md_preview = _build_markdown_preview(sample_pages, preview_headings, section.title)
        plain_text_preview = "\n\n".join(page.cleaned_text for page in sample_pages if page.cleaned_text).strip()
        metrics = _quality_metrics(sample_pages)
        route, reasons = _recommend_route(metrics)
        warnings: list[str] = []
        if route == "ocr_layout_first_repair":
            warnings.append("Sample pages do not provide a complete usable text layer; OCR layout preview is required before import.")

        return {
            "status": "OK",
            "pdf_path": str(pdf),
            "document_id": document_id,
            "sample_strategy": sample_strategy,
            "physical_page_count": document.page_count,
            "page_label_count": _decimal_page_label_count(labels),
            "page_label_semantics": "display_only_not_physical_coordinates",
            "first_section": {
                "title": section.title,
                "physical_page_start": section.physical_page_start,
                "physical_page_end": section.physical_page_end,
                "page_labels": labels[section.physical_page_start - 1 : section.physical_page_end],
                "detection_method": section.detection_method,
            },
            "sample_pages": [page.physical_page for page in sample_pages],
            "md_preview": md_preview if metrics["text_layer_coverage"] > 0 else "",
            "plain_text_preview": plain_text_preview if metrics["text_layer_coverage"] > 0 else "",
            "pdf_preview": [
                {
                    "physical_page": page.physical_page,
                    "page_label": page.page_label,
                    "page_width": page.page_width,
                    "page_height": page.page_height,
                    "text_layer_length": page.text_layer_length,
                    "image_count": page.image_count,
                    "render_hint": "pdfjs_getPage_physical",
                }
                for page in sample_pages
            ],
            "quality_metrics": metrics,
            "recommended_route": route,
            "selectable_routes": list(SELECTABLE_ROUTES),
            "route_reasons": reasons,
            "warnings": warnings,
            "db_write_performed": False,
            "vector_store_write_performed": False,
            "ocr_executed": False,
            "marker_executed": False,
            "external_llm_called": False,
        }


def detect_first_section(document: fitz.Document) -> SectionLocation:
    outline_location = _first_section_from_outline(document.get_toc(simple=True), document.page_count)
    if outline_location is not None:
        return outline_location
    text_location = _first_section_from_text(document)
    if text_location is not None:
        return text_location
    return _first_non_frontmatter_text_page(document)


def _first_section_from_outline(toc: list[list[Any]], page_count: int) -> SectionLocation | None:
    chapter_index = next(
        (
            index
            for index, entry in enumerate(toc)
            if CHAPTER_TITLE_RE.match(str(entry[1])) and not SECTION_TITLE_RE.match(str(entry[1]))
        ),
        None,
    )
    if chapter_index is None:
        section_index = next((index for index, entry in enumerate(toc) if SECTION_TITLE_RE.match(str(entry[1]))), None)
    else:
        chapter_level = int(toc[chapter_index][0])
        section_index = None
        for index in range(chapter_index + 1, len(toc)):
            if int(toc[index][0]) <= chapter_level:
                break
            if ANY_SECTION_TITLE_RE.match(str(toc[index][1])):
                section_index = index
                break
    if section_index is None:
        return None
    level, title, start_page = toc[section_index][:3]
    start = int(start_page)
    end = page_count
    for next_entry in toc[section_index + 1 :]:
        if int(next_entry[0]) <= int(level):
            end = max(start, int(next_entry[2]) - 1)
            break
    return SectionLocation(str(title).strip(), start, end, "pdf_outline")


def _first_section_from_text(document: fitz.Document) -> SectionLocation | None:
    for page_number in range(1, min(document.page_count, 100) + 1):
        for line in document.load_page(page_number - 1).get_text("text").splitlines():
            if SECTION_TITLE_RE.match(line.strip()):
                return SectionLocation(line.strip(), page_number, page_number, "text_search")
    return None


def _first_non_frontmatter_text_page(document: fitz.Document) -> SectionLocation:
    fallback_page = 1
    for page_number in range(1, document.page_count + 1):
        text = document.load_page(page_number - 1).get_text("text").strip()
        if not text:
            continue
        fallback_page = page_number
        lowered = text[:300].casefold()
        if not any(term in lowered for term in ("contents", "preface", "copyright")):
            break
    return SectionLocation("First text-bearing page", fallback_page, fallback_page, "first_non_frontmatter_text_page")


def _sample_pages(document: fitz.Document, start_page: int, max_pages: int) -> list[SamplePage]:
    pages: list[SamplePage] = []
    for page_number in range(start_page, min(document.page_count, start_page + max_pages - 1) + 1):
        page = document.load_page(page_number - 1)
        raw_text = page.get_text("text")
        cleaned_text, removed_count = _strip_running_header_footer(raw_text)
        images = page.get_images(full=True)
        pages.append(
            SamplePage(
                physical_page=page_number,
                page_label=_display_page_label(document, page_number),
                page_width=round(float(page.rect.width), 3),
                page_height=round(float(page.rect.height), 3),
                raw_text=raw_text,
                cleaned_text=cleaned_text,
                text_layer_length=len(raw_text),
                image_count=len(images),
                likely_scanned=(not raw_text.strip()) and bool(images),
                header_footer_count=removed_count,
            )
        )
    return pages


def _strip_running_header_footer(text: str) -> tuple[str, int]:
    lines = [line.rstrip() for line in text.splitlines()]
    removed = 0
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and PAGE_NUMBER_RE.match(lines[0].strip()):
        lines.pop(0)
        removed += 1
    if lines and RUNNING_HEADER_RE.match(lines[0].strip()):
        lines.pop(0)
        removed += 1
    if lines and PAGE_NUMBER_RE.match(lines[-1].strip()):
        lines.pop()
        removed += 1
    return "\n".join(lines).strip(), removed


def _preview_outline_headings(
    document: fitz.Document, pages: list[SamplePage], first_section: SectionLocation
) -> dict[int, list[str]]:
    selected_pages = {page.physical_page for page in pages}
    headings: dict[int, list[str]] = {first_section.physical_page_start: [first_section.title]}
    for _level, title, page_number in document.get_toc(simple=True):
        title = str(title).strip()
        page_number = int(page_number)
        if page_number in selected_pages and ANY_SECTION_TITLE_RE.match(title):
            values = headings.setdefault(page_number, [])
            if title not in values:
                values.append(title)
    return headings


def _matched_known_heading(lines: list[str], line_index: int, known_titles: list[str]) -> tuple[str, int] | None:
    stripped = lines[line_index]
    next_line = lines[line_index + 1] if line_index + 1 < len(lines) else ""
    for title in known_titles:
        if stripped.casefold() == title.casefold():
            return title, 1
        number, _, text = title.partition(" ")
        if stripped.rstrip(".") == number.rstrip(".") and next_line.casefold() == text.casefold():
            return title, 2
    return None


def _build_markdown_preview(
    pages: list[SamplePage], outline_headings: dict[int, list[str]], first_section_title: str
) -> str:
    blocks: list[str] = []
    for page_index, page in enumerate(pages):
        if not page.cleaned_text:
            continue
        blocks.append(f"<!-- PDF_PAGE: {page.physical_page} LABEL: {page.page_label} -->")
        paragraphs: list[str] = []
        current: list[str] = []
        first_heading_emitted = False
        lines = [line.strip() for line in page.cleaned_text.splitlines()]
        line_index = 0
        while line_index < len(lines):
            stripped = lines[line_index]
            if not stripped:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                line_index += 1
                continue
            known_heading = _matched_known_heading(lines, line_index, outline_headings.get(page.physical_page, []))
            if known_heading is not None:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                title, consumed = known_heading
                paragraphs.append(f"## {title}")
                first_heading_emitted = first_heading_emitted or title == first_section_title
                line_index += consumed
                continue
            if CHAPTER_TITLE_RE.match(stripped) and not SECTION_TITLE_RE.match(stripped):
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                paragraphs.append(f"# {stripped}")
            else:
                current.append(stripped)
            line_index += 1
        if current:
            paragraphs.append(" ".join(current))
        if page_index == 0 and not first_heading_emitted:
            paragraphs.insert(0, f"## {first_section_title}")
        blocks.append("\n\n".join(paragraphs))
    return "\n\n".join(blocks).strip()


def _quality_metrics(pages: list[SamplePage]) -> dict[str, Any]:
    content_pages = [page for page in pages if page.raw_text.strip() or page.image_count]
    denominator = len(content_pages) or len(pages) or 1
    text_layer_pages = sum(bool(page.raw_text.strip()) for page in content_pages)
    scanned_pages = sum(page.likely_scanned for page in pages)
    return {
        "sample_page_count": len(pages),
        "text_layer_coverage": text_layer_pages / denominator,
        "scan_page_ratio": scanned_pages / (len(pages) or 1),
        "html_tag_count": sum(len(HTML_TAG_RE.findall(page.raw_text)) for page in pages),
        "math_noise_count": sum(len(MATH_NOISE_RE.findall(page.raw_text)) for page in pages),
        "repeated_token_count": sum(len(REPEATED_TOKEN_RE.findall(page.raw_text)) for page in pages),
        "page_header_footer_count": sum(page.header_footer_count for page in pages),
        "formula_signal_count": sum(len(MATH_SIGNAL_RE.findall(page.raw_text)) for page in pages),
        "figure_caption_count": sum(
            1 for page in pages for line in page.raw_text.splitlines() if FIGURE_CAPTION_RE.match(line.strip())
        ),
    }


def _recommend_route(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    if metrics["text_layer_coverage"] == 1.0 and metrics["scan_page_ratio"] == 0.0:
        return "normal_text_layer", [
            "sample_pages_have_complete_text_layer",
            "no_sample_page_requires_ocr",
            "physical_pdf_pages_available_for_source_trace",
        ]
    return "ocr_layout_first_repair", [
        "sample_pages_have_missing_or_scan_like_text_layer",
        "ocr_not_run_in_preview_gate",
        "request_ocr_layout_preview_before_import",
    ]


def _display_page_label(document: fitz.Document, physical_page: int) -> str:
    return document.load_page(physical_page - 1).get_label() or str(physical_page)


def _decimal_page_label_count(labels: list[str]) -> int:
    numbers = [int(label) for label in labels if label.isdigit()]
    return max(numbers) if numbers else len(labels)


__all__ = [
    "PREVIEW_ALLOWED_PDF_ROOTS",
    "PREVIEW_TOKEN_TTL_SECONDS",
    "SAMPLE_STRATEGY",
    "SELECTABLE_ROUTES",
    "build_import_preview_gate",
    "clear_pdf_preview_tokens",
    "detect_first_section",
    "issue_pdf_preview_token",
    "resolve_pdf_preview_token",
    "resolve_pdf_preview_sample_pages",
    "validate_pdf_preview_path",
]
