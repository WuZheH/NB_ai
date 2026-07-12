from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.paths import PROJECT_ROOT
from app.services.import_service import ImportResult, import_markdown_file


def _find_marker_single() -> str | None:
    """Locate the marker_single executable."""
    candidate = shutil.which("marker_single")
    if candidate:
        return candidate

    # Look relative to the current Python interpreter (conda env on Windows)
    python_dir = Path(sys.executable).parent
    for relative in (python_dir, python_dir / "Scripts"):
        for name in ("marker_single", "marker_single.exe"):
            exe = relative / name
            if exe.is_file():
                return str(exe)

    return None


ALLOWED_READ_STATUSES = {"read", "mastered"}
MARKER_PAGE_SEPARATOR_RE = re.compile(r"^\{(\d+)\}-+\s*$")


class PdfConversionError(RuntimeError):
    pass


class PdfToMarkdownConverterUnavailable(RuntimeError):
    pass


NO_WRITE_CONVERSION_FLAGS = {
    "db_write_performed": False,
    "core_db_write_performed": False,
    "llm_called": False,
    "external_llm_called": False,
    "mechanism_generated": False,
    "final_hypothesis_created": False,
    "vector_store_write_performed": False,
    "ocr_or_marker_performed": False,
    "ocr_executed": False,
    "marker_executed": False,
}


@dataclass(frozen=True)
class PdfImportResult:
    backend: str
    converted_md_path: Path
    layout_json_path: Path
    import_result: ImportResult
    fallback_reason: str | None = None


class BasePdfConverter(ABC):
    backend_name = "base"

    @abstractmethod
    def convert_pdf_to_markdown(
        self,
        pdf_path: str | Path,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> Path:
        raise NotImplementedError

    @abstractmethod
    def convert_pdf_to_layout_json(self, pdf_path: str | Path) -> Path:
        raise NotImplementedError


class MarkerPdfConverter(BasePdfConverter):
    backend_name = "marker"

    def convert_pdf_to_markdown(
        self,
        pdf_path: str | Path,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> Path:
        pdf = Path(pdf_path)
        self._ensure_marker_available()
        converted_md_path = _converted_md_path(pdf)
        converted_md_path.parent.mkdir(parents=True, exist_ok=True)

        marker_single = _find_marker_single()
        if marker_single is None:
            raise PdfConversionError("marker-pdf is installed but marker_single was not found on PATH.")

        output_dir = converted_md_path.parent / f"{pdf.stem}.marker_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            marker_single,
            str(pdf),
            "--output_dir",
            str(output_dir),
            "--disable_ocr",
            "--disable_image_extraction",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
        except subprocess.TimeoutExpired:
            raise PdfConversionError("Marker conversion timed out (likely downloading models). Falling back to mock converter.")
        if completed.returncode != 0:
            raise PdfConversionError("Marker conversion failed. Falling back to mock converter.")

        markdown_candidates = sorted(output_dir.rglob("*.md"))
        if not markdown_candidates:
            raise PdfConversionError("Marker did not produce a Markdown file.")

        marker_text = markdown_candidates[0].read_text(encoding="utf-8", errors="replace")
        converted_md_path.write_text(
            postprocess_marker_markdown(
                marker_text,
                pdf,
                start_page=start_page,
                end_page=end_page,
            ),
            encoding="utf-8",
        )
        return converted_md_path

    def convert_pdf_to_layout_json(self, pdf_path: str | Path) -> Path:
        pdf = Path(pdf_path)
        layout_path = _layout_json_path(pdf)
        layout_path.parent.mkdir(parents=True, exist_ok=True)

        marker_output_dir = _converted_md_path(pdf).parent / f"{pdf.stem}.marker_output"
        json_candidates = sorted(marker_output_dir.rglob("*.json")) if marker_output_dir.exists() else []
        if json_candidates:
            try:
                marker_json = json.loads(json_candidates[0].read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                marker_json = {"source": "marker", "raw_json_parseable": False}
            layout_path.write_text(
                json.dumps(_compact_layout_json(pdf, marker_json), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return layout_path

        _write_mock_layout_json(layout_path, pdf, backend="marker")
        return layout_path

    def _ensure_marker_available(self) -> None:
        if importlib.util.find_spec("marker") is None and _find_marker_single() is None:
            raise PdfConversionError("marker-pdf is not installed or marker_single is unavailable.")


class MockPdfConverter(BasePdfConverter):
    backend_name = "mock"

    def convert_pdf_to_markdown(
        self,
        pdf_path: str | Path,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> Path:
        pdf = Path(pdf_path)
        converted_md_path = _converted_md_path(pdf)
        converted_md_path.parent.mkdir(parents=True, exist_ok=True)
        safe_pdf_path = _display_path(pdf)
        title = pdf.stem.replace("_", " ").replace("-", " ").title()
        mock_marker_text = "\n".join(
            [
                "{0}------------------------------------------------",
                "",
                f"# {title}",
                "",
                "This is mock converted text for Phase 3 PDF import validation.",
                "It is not real PDF content.",
                "",
                "## Mock Method",
                "",
                "The mock converter creates short placeholder evidence for testing page mapping.",
                "",
                "{1}------------------------------------------------",
                "",
                "## Mock Result",
                "",
                "This second mock page verifies that chunks keep PDF page metadata.",
                "",
            ]
        )
        converted_md_path.write_text(
            postprocess_marker_markdown(
                mock_marker_text,
                pdf,
                start_page=start_page,
                end_page=end_page,
            ),
            encoding="utf-8",
        )
        return converted_md_path

    def convert_pdf_to_layout_json(self, pdf_path: str | Path) -> Path:
        pdf = Path(pdf_path)
        layout_path = _layout_json_path(pdf)
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        _write_mock_layout_json(layout_path, pdf, backend=self.backend_name)
        return layout_path


def import_pdf(
    pdf_path: str | Path,
    document_type: str,
    read_status: str,
    start_page: int | None = None,
    end_page: int | None = None,
    fallback_to_mock: bool = True,
) -> PdfImportResult:
    if read_status not in ALLOWED_READ_STATUSES:
        raise ValueError("read_status must be read or mastered before importing into the core library.")

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf}")

    converter: BasePdfConverter = MarkerPdfConverter()
    fallback_reason: str | None = None
    try:
        converted_md_path = converter.convert_pdf_to_markdown(pdf, start_page=start_page, end_page=end_page)
        layout_json_path = converter.convert_pdf_to_layout_json(pdf)
    except PdfConversionError as exc:
        if not fallback_to_mock:
            raise
        fallback_reason = str(exc)
        converter = MockPdfConverter()
        converted_md_path = converter.convert_pdf_to_markdown(pdf, start_page=start_page, end_page=end_page)
        layout_json_path = converter.convert_pdf_to_layout_json(pdf)

    result = import_markdown_file(
        converted_md_path,
        document_type=document_type,
        content_layer="converted_source",
        read_status=read_status,
        pdf_path=_display_path(pdf),
    )
    return PdfImportResult(
        backend=converter.backend_name,
        converted_md_path=converted_md_path,
        layout_json_path=layout_json_path,
        import_result=result,
        fallback_reason=fallback_reason,
    )


def convert_pdf_to_markdown_text_layer(
    pdf_path: str | Path,
    *,
    title: str | None = None,
    zotero_item_key: str | None = None,
    zotero_attachment_key: str | None = None,
    output_root: str | Path | None = None,
    extractor: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Create converted Markdown for one PDF without importing it.

    This endpoint-facing helper intentionally avoids Marker, OCR, PyMuPDF, DB
    writes, vector writes, and LLM calls. It writes only the converted Markdown
    file after basic identity verification succeeds.
    """
    pdf = Path(pdf_path).expanduser()
    if not pdf.is_file():
        return _pdf_to_md_blocked(
            "pdf_not_found",
            f"PDF file not found: {pdf}",
            pdf_path=pdf,
            title=title,
        )
    if pdf.suffix.lower() != ".pdf":
        return _pdf_to_md_blocked(
            "not_a_pdf",
            f"Expected a .pdf file, got: {pdf}",
            pdf_path=pdf,
            title=title,
        )

    converted_md_path = _converted_md_text_layer_path(pdf, title=title, output_root=output_root)
    if "test_minimal" in converted_md_path.name.lower():
        return _pdf_to_md_blocked(
            "converted_md_identity_mismatch",
            "Refusing to generate test_minimal.auto.md for a production import source.",
            pdf_path=pdf,
            title=title,
            converted_md_path=converted_md_path,
            identity_match=False,
        )

    try:
        extracted = extractor(pdf) if extractor else _extract_text_layer_pages(pdf)
        pages, backend = _coerce_text_layer_extraction(extracted)
    except PdfToMarkdownConverterUnavailable as exc:
        return _pdf_to_md_blocked(
            "pdf_to_md_converter_unavailable",
            str(exc),
            pdf_path=pdf,
            title=title,
            converted_md_path=converted_md_path,
            original_error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive API boundary
        return _pdf_to_md_blocked(
            "pdf_to_md_conversion_failed",
            f"PDF text-layer conversion failed: {exc}",
            pdf_path=pdf,
            title=title,
            converted_md_path=converted_md_path,
            original_error=repr(exc),
        )

    text_for_identity = "\n".join(text for _, text in pages if text.strip())
    if not text_for_identity.strip():
        return _pdf_to_md_blocked(
            "pdf_text_layer_empty",
            "No usable text layer was extracted. OCR/Marker was not run; generate Markdown with an explicit OCR/Marker workflow if needed.",
            pdf_path=pdf,
            title=title,
            converted_md_path=converted_md_path,
            conversion_backend=backend,
        )

    identity = _verify_converted_md_identity(
        extracted_text=text_for_identity,
        pdf_path=pdf,
        title=title,
    )
    if not identity["identity_match"]:
        return _pdf_to_md_blocked(
            "converted_md_identity_mismatch",
            "Markdown identity verification failed for the current PDF/title.",
            pdf_path=pdf,
            title=title,
            converted_md_path=converted_md_path,
            conversion_backend=backend,
            identity_match=False,
            identity=identity,
        )

    markdown_text = _format_text_layer_markdown(
        pages,
        pdf_path=pdf,
        title=title,
        zotero_item_key=zotero_item_key,
        zotero_attachment_key=zotero_attachment_key,
        conversion_backend=backend,
    )
    converted_md_path.parent.mkdir(parents=True, exist_ok=True)
    converted_md_path.write_text(markdown_text, encoding="utf-8")

    return {
        "status": "OK",
        "converted_md_path": _display_path(converted_md_path),
        "identity_match": True,
        "identity": identity,
        "title": title or _title_from_pdf_stem(pdf),
        "char_count": len(markdown_text),
        "page_count": len(pages),
        "conversion_backend": backend,
        "markdown_preview": markdown_text[:4000],
        **NO_WRITE_CONVERSION_FLAGS,
    }


def preview_pdf_text_layer_sample(
    pdf_path: str | Path,
    *,
    title: str | None = None,
    max_pages: int = 4,
    max_chars: int = 4000,
    extractor: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Read a small PDF text-layer sample without writing files or importing.

    This is intentionally separate from Markdown generation: it never writes a
    converted_md file, never invokes OCR/Marker, and never writes the core DB.
    """
    pdf = Path(pdf_path).expanduser()
    if not pdf.is_file():
        return _pdf_to_md_blocked(
            "pdf_not_found",
            f"PDF file not found: {pdf}",
            pdf_path=pdf,
            title=title,
        )
    if pdf.suffix.lower() != ".pdf":
        return _pdf_to_md_blocked(
            "not_a_pdf",
            f"Expected a .pdf file, got: {pdf}",
            pdf_path=pdf,
            title=title,
        )

    try:
        extracted = extractor(pdf) if extractor else _extract_text_layer_pages(pdf, max_pages=max_pages)
        pages, backend = _coerce_text_layer_extraction(extracted)
        page_count = extracted.get("page_count") if isinstance(extracted, dict) else None
    except PdfToMarkdownConverterUnavailable as exc:
        return _pdf_to_md_blocked(
            "pdf_text_layer_preview_unavailable",
            str(exc),
            pdf_path=pdf,
            title=title,
            original_error=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive API boundary
        return _pdf_to_md_blocked(
            "pdf_text_layer_preview_failed",
            f"PDF text-layer preview failed: {exc}",
            pdf_path=pdf,
            title=title,
            original_error=repr(exc),
        )

    sampled_pages = pages[:max(1, int(max_pages))]
    text_blocks: list[str] = []
    pages_with_text = 0
    for page_number, page_text in sampled_pages:
        if not page_text.strip():
            continue
        pages_with_text += 1
        text_blocks.append(f"<!-- PDF_PAGE: {page_number} -->\n\n{page_text.strip()}")
    text_sample = "\n\n".join(text_blocks).strip()
    if len(text_sample) > max_chars:
        text_sample = text_sample[: max_chars - 3].rstrip() + "..."

    if not text_sample:
        return {
            "status": "BLOCKED",
            "error": "pdf_text_layer_empty",
            "message": "未能从抽样页读取可用文本层。本步骤未运行 OCR/Marker；如需继续，请生成 Markdown 或使用显式 OCR/Marker 工作流。",
            "pdf_path": _display_path(pdf),
            "title": title,
            "page_count": page_count or len(pages),
            "sample_pages": [page_number for page_number, _text in sampled_pages],
            "sample_page_count": len(sampled_pages),
            "sample_pages_with_text": pages_with_text,
            "text_sample": "",
            "parser_backend": backend,
            "quality_summary": {
                "text_layer_coverage": 0.0,
                "scan_page_ratio": None,
                "sample_page_count": len(sampled_pages),
                "sample_pages_with_text": pages_with_text,
                "requires_ocr": None,
            },
            "next_action": "generate_markdown_or_run_explicit_ocr_marker_workflow",
            **NO_WRITE_CONVERSION_FLAGS,
        }

    text_layer_coverage = pages_with_text / (len(sampled_pages) or 1)
    return {
        "status": "OK",
        "pdf_path": _display_path(pdf),
        "title": title or _title_from_pdf_stem(pdf),
        "page_count": page_count or len(pages),
        "sample_pages": [page_number for page_number, _text in sampled_pages],
        "sample_page_count": len(sampled_pages),
        "sample_pages_with_text": pages_with_text,
        "sample_char_count": len(text_sample),
        "text_sample": text_sample,
        "parser_backend": backend,
        "quality_summary": {
            "text_layer_coverage": text_layer_coverage,
            "scan_page_ratio": 0.0 if text_layer_coverage >= 1.0 else None,
            "sample_page_count": len(sampled_pages),
            "sample_pages_with_text": pages_with_text,
            "requires_ocr": False if text_layer_coverage >= 1.0 else None,
        },
        "preview_only": True,
        "next_action": "review_preview_before_import",
        **NO_WRITE_CONVERSION_FLAGS,
    }


def postprocess_marker_markdown(
    text: str,
    pdf_path: str | Path,
    start_page: int | None = None,
    end_page: int | None = None,
) -> str:
    """Convert Marker page separators to NOTEBOOK_AI PDF_PAGE markers.

    Marker page separators observed in local output are zero-based, e.g.
    ``{0}------------------------------------------------`` for PDF page 1.
    The output markers are therefore one-based.
    """
    if start_page is not None and start_page < 1:
        raise ValueError("start_page must be >= 1.")
    if end_page is not None and end_page < 1:
        raise ValueError("end_page must be >= 1.")
    if start_page is not None and end_page is not None and start_page > end_page:
        raise ValueError("start_page must be <= end_page.")

    pages = _split_marker_pages(text)
    filtered_pages = [
        (page_number, page_text)
        for page_number, page_text in pages
        if (start_page is None or page_number >= start_page)
        and (end_page is None or page_number <= end_page)
        and page_text.strip()
    ]

    output_lines = [f"<!-- PDF_PATH: {_display_path(Path(pdf_path))} -->", ""]
    if not filtered_pages:
        return "\n".join(output_lines).rstrip() + "\n"

    for page_number, page_text in filtered_pages:
        output_lines.append(f"<!-- PDF_PAGE: {page_number} -->")
        output_lines.append("")
        output_lines.append(page_text.strip())
        output_lines.append("")

    return "\n".join(output_lines).rstrip() + "\n"


def _extract_text_layer_pages(pdf_path: Path, *, max_pages: int | None = None) -> dict[str, Any]:
    if importlib.util.find_spec("pypdfium2") is None:
        raise PdfToMarkdownConverterUnavailable(
            "No non-PyMuPDF text-layer converter is available. pypdfium2 is not importable; OCR/Marker was not run."
        )

    import pypdfium2

    document = pypdfium2.PdfDocument(str(pdf_path))
    pages: list[tuple[int, str]] = []
    try:
        page_count = len(document)
        limit = min(page_count, int(max_pages)) if max_pages is not None else page_count
        for index in range(limit):
            page = document[index]
            text_page = None
            try:
                text_page = page.get_textpage()
                text = text_page.get_text_range() or ""
                pages.append((index + 1, _clean_text_layer_page(text)))
            finally:
                close_text = getattr(text_page, "close", None)
                if callable(close_text):
                    close_text()
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
    finally:
        close_document = getattr(document, "close", None)
        if callable(close_document):
            close_document()
    return {"backend": "pypdfium2_text_layer", "page_count": page_count, "pages": pages}


def _coerce_text_layer_extraction(extracted: Any) -> tuple[list[tuple[int, str]], str]:
    backend = "text_layer"
    raw_pages: Any = extracted
    if isinstance(extracted, dict):
        backend = str(extracted.get("backend") or backend)
        raw_pages = extracted.get("pages") or []
    elif isinstance(extracted, tuple) and len(extracted) == 2:
        raw_pages, backend = extracted

    pages: list[tuple[int, str]] = []
    for index, page in enumerate(raw_pages or [], start=1):
        if isinstance(page, tuple) and len(page) == 2:
            page_number, text = page
        elif isinstance(page, dict):
            page_number = page.get("page") or page.get("page_number") or index
            text = page.get("text") or ""
        else:
            page_number, text = index, str(page or "")
        pages.append((int(page_number), _clean_text_layer_page(str(text))))
    return pages, str(backend)


def _format_text_layer_markdown(
    pages: list[tuple[int, str]],
    *,
    pdf_path: Path,
    title: str | None,
    zotero_item_key: str | None,
    zotero_attachment_key: str | None,
    conversion_backend: str,
) -> str:
    output_lines = [
        f"<!-- PDF_PATH: {_display_path(pdf_path)} -->",
        f"<!-- CONVERSION_BACKEND: {conversion_backend} -->",
    ]
    if zotero_item_key:
        output_lines.append(f"<!-- ZOTERO_ITEM_KEY: {zotero_item_key} -->")
    if zotero_attachment_key:
        output_lines.append(f"<!-- ZOTERO_ATTACHMENT_KEY: {zotero_attachment_key} -->")
    output_lines.extend(["", f"# {title or _title_from_pdf_stem(pdf_path)}", ""])

    for page_number, page_text in pages:
        if not page_text.strip():
            continue
        output_lines.append(f"<!-- PDF_PAGE: {page_number} -->")
        output_lines.append("")
        output_lines.append(page_text.strip())
        output_lines.append("")
    return "\n".join(output_lines).rstrip() + "\n"


def _verify_converted_md_identity(
    *,
    extracted_text: str,
    pdf_path: Path,
    title: str | None,
) -> dict[str, Any]:
    tokens = _identity_tokens(title or _title_from_pdf_stem(pdf_path))
    if not tokens:
        return {
            "identity_match": True,
            "matched_tokens": [],
            "required_match_count": 0,
            "candidate_tokens": [],
            "reason": "No title tokens were available; accepted by source PDF path binding.",
        }

    evidence = _identity_normalize(f"{pdf_path.stem} {extracted_text[:12000]}")
    matched = [token for token in tokens if token in evidence]
    required = min(2, len(tokens))
    identity_match = len(matched) >= required
    return {
        "identity_match": identity_match,
        "matched_tokens": matched,
        "required_match_count": required,
        "candidate_tokens": tokens,
        "reason": "" if identity_match else "Extracted PDF text/path did not match enough title tokens.",
    }


def _identity_tokens(value: str) -> list[str]:
    stopwords = {
        "using",
        "with",
        "from",
        "into",
        "that",
        "this",
        "there",
        "their",
        "paper",
        "real",
        "time",
    }
    tokens = []
    for token in re.findall(r"[a-z0-9]+", _identity_normalize(value)):
        if len(token) < 4 or token in stopwords:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:12]


def _identity_normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())


def _clean_text_layer_page(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(text or "").splitlines()]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    return "\n".join(collapsed).strip()


def _converted_md_text_layer_path(
    pdf_path: Path,
    *,
    title: str | None,
    output_root: str | Path | None,
) -> Path:
    target_dir = Path(output_root) if output_root else PROJECT_ROOT / "data" / "converted_md" / _pdf_kind(pdf_path)
    slug = _slug_for_converted_md(title or pdf_path.stem)
    return target_dir / f"{slug}.auto.md"


def _slug_for_converted_md(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:120] or "converted_pdf"


def _title_from_pdf_stem(pdf_path: Path) -> str:
    return pdf_path.stem.replace("_", " ").replace("-", " ").strip() or "Untitled PDF"


def _pdf_to_md_blocked(
    error: str,
    message: str,
    *,
    pdf_path: Path | None = None,
    title: str | None = None,
    converted_md_path: Path | None = None,
    conversion_backend: str | None = None,
    original_error: str | None = None,
    identity_match: bool | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "BLOCKED",
        "error": error,
        "message": message,
        "pdf_path": _display_path(pdf_path) if pdf_path else None,
        "title": title,
        "converted_md_path": _display_path(converted_md_path) if converted_md_path else None,
        "conversion_backend": conversion_backend,
        "identity_match": identity_match if identity_match is not None else False,
        "identity": identity or {},
        "original_error": original_error,
        "next_action": "use_existing_pdf_to_md_or_manual_markdown_workflow",
        **NO_WRITE_CONVERSION_FLAGS,
    }
    return payload


def _converted_md_path(pdf_path: Path) -> Path:
    kind = _pdf_kind(pdf_path)
    return PROJECT_ROOT / "data" / "converted_md" / kind / f"{pdf_path.stem}.auto.md"


def _layout_json_path(pdf_path: Path) -> Path:
    kind = _pdf_kind(pdf_path)
    return PROJECT_ROOT / "data" / "layout_json" / kind / f"{pdf_path.stem}.layout.json"


def _pdf_kind(pdf_path: Path) -> str:
    normalized = str(pdf_path).replace("\\", "/").lower()
    if "/books/" in normalized:
        return "books"
    return "papers"


def _split_marker_pages(text: str) -> list[tuple[int, str]]:
    pages: list[tuple[int, list[str]]] = []
    current_page = 1
    current_lines: list[str] = []
    saw_separator = False

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            pages.append((current_page, current_lines))
            current_lines = []

    for line in text.splitlines():
        if re.match(r"<!--\s*PDF_PATH:", line):
            continue
        pdf_page_match = re.match(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->", line)
        if pdf_page_match:
            flush()
            current_page = int(pdf_page_match.group(1))
            saw_separator = True
            continue

        marker_match = MARKER_PAGE_SEPARATOR_RE.match(line)
        if marker_match:
            flush()
            current_page = int(marker_match.group(1)) + 1
            saw_separator = True
            continue

        page_comment_match = re.match(r"<!--\s*Page\s+(\d+)\s*-->", line, flags=re.IGNORECASE)
        if page_comment_match:
            flush()
            current_page = int(page_comment_match.group(1))
            saw_separator = True
            continue

        current_lines.append(line)

    flush()
    if not saw_separator and not pages:
        return [(1, text.strip())]
    return [(page, "\n".join(lines).strip()) for page, lines in pages]


def _compact_layout_json(pdf_path: Path, marker_json: object) -> dict[str, object]:
    return {
        "source_pdf": _display_path(pdf_path),
        "backend": "marker",
        "page_locator_level": "page",
        "bbox_supported": False,
        "marker_json_type": type(marker_json).__name__,
    }


def _write_mock_layout_json(layout_path: Path, pdf_path: Path, backend: str) -> None:
    layout_path.write_text(
        json.dumps(
            {
                "source_pdf": _display_path(pdf_path),
                "backend": backend,
                "page_locator_level": "page",
                "bbox_supported": False,
                "pages": [
                    {"page": 1, "text_available": False},
                    {"page": 2, "text_available": False},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved)
