"""Read-only chapter-level PDF text-layer quality diagnosis.

This script deliberately does not invoke OCR, parser import jobs, databases, or
vector services. It emits diagnostic artifacts only in the requested output
directory.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz


HTML_TAG_RE = re.compile(r"<(?!UNK\b)[A-Za-z/][^>]*>", re.IGNORECASE)
UNKNOWN_TOKEN_RE = re.compile(r"<UNK>", re.IGNORECASE)
REPEATED_TOKEN_RE = re.compile(r"\b([A-Za-z]{1,20}|\d+(?:\.\d+)?)\b(?:\s+\1\b){2,}", re.IGNORECASE)
HEADER_RE = re.compile(r"^(?:\d+\s+)?Chapter\s+\d+\.\s+.+$", re.IGNORECASE)
SECTION_HEADER_RE = re.compile(r"^\d+(?:\.\d+)+\.\s+.+$")
PAGE_NUMBER_RE = re.compile(r"^(?:[ivxlcdm]+|\d+)$", re.IGNORECASE)
MATH_SIGNAL_RE = re.compile(r"[=+\-<>]|\u2211|\u220f|\u222b|\u2264|\u2265|\u03b8|\u03bc|\u03c3|\u211d")
FORMULA_CORRUPTION_RE = re.compile(r"(?:\\[A-Za-z]+|[A-Za-z]\^\{|_\{|<br\s*/?>|\ufffd)")
FIGURE_TABLE_RE = re.compile(r"^(?:Figure|Table)\s+\d+\.\d+:", re.IGNORECASE)


@dataclass
class PageQuality:
    page: int
    text_length: int
    image_count: int
    image_sizes: list[list[int]]
    has_text_layer: bool
    blank_page: bool
    likely_scanned: bool
    exact_search_match_count: int
    html_tag_count: int
    unknown_token_count: int
    repeated_token_count: int
    math_signal_count: int
    formula_corruption_count: int
    figure_table_caption_count: int
    header_footer_residue_count: int
    text_sample: str


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _chapter_from_outline(
    toc: list[list[Any]], chapter_number: int, page_count: int
) -> tuple[str, int, int, str, str]:
    pattern = re.compile(rf"^\s*{chapter_number}\s+\S", re.IGNORECASE)
    found_index = None
    for index, entry in enumerate(toc):
        level, title, _page = entry[:3]
        if int(level) == 1 and pattern.match(str(title)):
            found_index = index
            break
    if found_index is None:
        raise ValueError(f"Chapter {chapter_number} was not found in PDF outline")
    level, title, start = toc[found_index][:3]
    end = page_count
    for entry in toc[found_index + 1 :]:
        next_level, _next_title, next_page = entry[:3]
        if int(next_level) <= int(level):
            end = int(next_page) - 1
            break
    return str(title), int(start), int(end), "pdf_outline", "low"


def _strip_page_header_footer(text: str) -> tuple[str, list[str]]:
    lines = [line.rstrip() for line in text.splitlines()]
    removed: list[str] = []
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and PAGE_NUMBER_RE.match(lines[0].strip()):
        removed.append(lines.pop(0).strip())
    if lines and HEADER_RE.match(lines[0].strip()):
        removed.append(lines.pop(0).strip())
    elif (
        len(lines) >= 2
        and SECTION_HEADER_RE.match(lines[0].strip())
        and PAGE_NUMBER_RE.match(lines[1].strip())
    ):
        removed.extend([lines.pop(0).strip(), lines.pop(0).strip()])
    while lines and PAGE_NUMBER_RE.match(lines[-1].strip()):
        removed.append(lines.pop().strip())
    return "\n".join(lines).strip(), removed


def _page_quality(doc: fitz.Document, page_number: int) -> tuple[PageQuality, str]:
    page = doc.load_page(page_number - 1)
    raw_text = page.get_text("text")
    cleaned_text, removed = _strip_page_header_footer(raw_text)
    images = page.get_images(full=True)
    image_sizes = [[int(item[2]), int(item[3])] for item in images]
    has_text = bool(raw_text.strip())
    blank_page = (not has_text) and not images
    likely_scanned = (not has_text) and any(width * height >= 1_000_000 for width, height in image_sizes)
    searchable_lines = [line.strip() for line in raw_text.splitlines() if len(line.strip()) >= 4]
    search_term = searchable_lines[0] if searchable_lines else ""
    quality = PageQuality(
        page=page_number,
        text_length=len(raw_text),
        image_count=len(images),
        image_sizes=image_sizes,
        has_text_layer=has_text,
        blank_page=blank_page,
        likely_scanned=likely_scanned,
        exact_search_match_count=len(page.search_for(search_term)) if search_term else 0,
        html_tag_count=len(HTML_TAG_RE.findall(raw_text)),
        unknown_token_count=len(UNKNOWN_TOKEN_RE.findall(raw_text)),
        repeated_token_count=len(REPEATED_TOKEN_RE.findall(raw_text)),
        math_signal_count=len(MATH_SIGNAL_RE.findall(raw_text)),
        formula_corruption_count=len(FORMULA_CORRUPTION_RE.findall(raw_text)),
        figure_table_caption_count=sum(1 for line in raw_text.splitlines() if FIGURE_TABLE_RE.match(line.strip())),
        header_footer_residue_count=len(removed),
        text_sample=" ".join(cleaned_text.split())[:240],
    )
    return quality, cleaned_text


def _proposed_chunks(pages: list[tuple[int, str]], target_chars: int = 1200) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    start_page: int | None = None
    end_page: int | None = None
    for page_number, text in pages:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else []
        for paragraph in paragraphs:
            if current and sum(len(part) for part in current) + len(paragraph) > target_chars:
                body = "\n\n".join(current)
                chunks.append(
                    {"page_start": start_page, "page_end": end_page, "char_count": len(body), "sample": body[:180]}
                )
                current = []
                start_page = None
            if start_page is None:
                start_page = page_number
            end_page = page_number
            current.append(paragraph)
    if current:
        body = "\n\n".join(current)
        chunks.append({"page_start": start_page, "page_end": end_page, "char_count": len(body), "sample": body[:180]})
    return chunks


def diagnose(pdf_path: Path, chapter_number: int, output_dir: Path) -> dict[str, Any]:
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc(simple=True)
    title, chapter_start, chapter_end, detection_method, uncertainty = _chapter_from_outline(
        toc, chapter_number, doc.page_count
    )
    chapter_pages: list[PageQuality] = []
    cleaned_pages: list[tuple[int, str]] = []
    for page_number in range(chapter_start, chapter_end + 1):
        quality, cleaned = _page_quality(doc, page_number)
        chapter_pages.append(quality)
        cleaned_pages.append((page_number, cleaned))
    first_page_quality, _first_cleaned = _page_quality(doc, 1)
    chunks = _proposed_chunks(cleaned_pages)
    text_lengths = [page.text_length for page in chapter_pages]
    content_pages = [page for page in chapter_pages if not page.blank_page]
    text_layer_pages = sum(page.has_text_layer for page in content_pages)
    scanned_pages = sum(page.likely_scanned for page in chapter_pages)
    report = {
        "status": "READ_ONLY_DIAGNOSIS",
        "pdf_path": str(pdf_path),
        "pdf_metadata": doc.metadata,
        "total_pages": doc.page_count,
        "chapter": {
            "number": chapter_number,
            "title": title,
            "page_start": chapter_start,
            "page_end": chapter_end,
            "page_count": chapter_end - chapter_start + 1,
            "detection_method": detection_method,
            "uncertainty": uncertainty,
        },
        "path_selected": "pymupdf_text_layer_baseline",
        "marker_surya_executed": False,
        "ocr_executed": False,
        "source_quality": {
            "page_1": asdict(first_page_quality),
            "chapter_blank_page_count": len(chapter_pages) - len(content_pages),
            "chapter_text_layer_coverage": text_layer_pages / len(content_pages) if content_pages else 0,
            "chapter_scanned_page_ratio": scanned_pages / len(chapter_pages),
            "chapter_average_text_length": round(statistics.mean(text_lengths), 1),
            "chapter_min_text_length": min(text_lengths),
            "chapter_max_text_length": max(text_lengths),
            "born_digital": text_layer_pages == len(content_pages) and scanned_pages == 0,
            "ocr_needed": scanned_pages > 0 or text_layer_pages < len(content_pages),
            "exact_text_location_feasible": all(page.exact_search_match_count > 0 for page in content_pages),
        },
        "quality_metrics": {
            "html_tag_count": sum(page.html_tag_count for page in chapter_pages),
            "unknown_token_count": sum(page.unknown_token_count for page in chapter_pages),
            "repeated_token_count": sum(page.repeated_token_count for page in chapter_pages),
            "math_signal_count": sum(page.math_signal_count for page in chapter_pages),
            "formula_corruption_count": sum(page.formula_corruption_count for page in chapter_pages),
            "figure_table_caption_count": sum(page.figure_table_caption_count for page in chapter_pages),
            "page_header_footer_residue_count_raw": sum(page.header_footer_residue_count for page in chapter_pages),
        },
        "pages": [asdict(page) for page in chapter_pages],
        "chunk_readiness": {
            "proposed_chunk_count": len(chunks),
            "average_chunk_length": round(statistics.mean(chunk["char_count"] for chunk in chunks), 1) if chunks else 0,
            "page_mapping_available": True,
            "source_trace_feasible": True,
            "expected_preview_mode": "exact_text_location",
            "sample_chunks": chunks[:6],
        },
        "recommendation": {
            "suitable_for_normal_text_layer_import": text_layer_pages == len(content_pages) and scanned_pages == 0,
            "needs_ocr_first": scanned_pages > 0,
            "parser_route": "text-layer/page-aware normal import; retain page mapping and clean running headers",
            "notes": [
                "Raw PyMuPDF text preserves running page headers; normal import should filter them.",
                "Figure/table-heavy pages include extracted labels and values that require ordering checks during parser import.",
            ],
        },
        "writes": {
            "sqlite": False,
            "knowledge_chunks": False,
            "lancedb": False,
            "vector_store": False,
            "temporary_artifacts_only": True,
        },
    }
    source_path = output_dir / "chapter1_source_quality.json"
    sample_path = output_dir / "chapter1_extracted_sample.md"
    report_path = output_dir / "chapter1_quality_report.md"
    _write_text(source_path, json.dumps(report, ensure_ascii=False, indent=2))
    extracted = [f"# {title}", "", f"PDF pages: {chapter_start}-{chapter_end}", ""]
    for page_number, cleaned in cleaned_pages:
        extracted.extend([f"<!-- PDF_PAGE: {page_number} -->", "", cleaned, ""])
    _write_text(sample_path, "\n".join(extracted))
    markdown_report = [
        f"# Chapter {chapter_number} PDF quality diagnosis",
        "",
        f"- PDF: `{pdf_path}`",
        f"- Chapter: `{title}` (PDF pages {chapter_start}-{chapter_end})",
        f"- Detection: `{detection_method}`; uncertainty: `{uncertainty}`",
        f"- Parser route evaluated: `{report['path_selected']}`",
        f"- OCR executed: `{str(report['ocr_executed']).lower()}`",
        f"- Marker/Surya executed: `{str(report['marker_surya_executed']).lower()}`",
        "",
        "## Source quality",
        "",
        f"- Text layer coverage: `{report['source_quality']['chapter_text_layer_coverage']:.0%}`",
        f"- Scanned page ratio: `{report['source_quality']['chapter_scanned_page_ratio']:.0%}`",
        f"- Born digital: `{str(report['source_quality']['born_digital']).lower()}`",
        f"- OCR needed: `{str(report['source_quality']['ocr_needed']).lower()}`",
        f"- Exact text location feasible: `{str(report['source_quality']['exact_text_location_feasible']).lower()}`",
        "",
        "## Quality metrics",
        "",
    ]
    for key, value in report["quality_metrics"].items():
        markdown_report.append(f"- {key}: `{value}`")
    markdown_report.extend(
        [
            "",
            "## Readiness",
            "",
            f"- Proposed page-aware text chunks: `{len(chunks)}`",
            f"- Average chunk length: `{report['chunk_readiness']['average_chunk_length']}`",
            "- Expected locator: `exact_text_location`",
            "- Recommended route: normal text-layer import with running-header cleanup.",
        ]
    )
    _write_text(report_path, "\n".join(markdown_report) + "\n")
    report["artifacts"] = {
        "source_quality_json": str(source_path),
        "extracted_sample_md": str(sample_path),
        "quality_report_md": str(report_path),
    }
    return report


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-path", required=True, type=Path)
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = diagnose(args.pdf_path, args.chapter, args.output_dir)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Chapter {args.chapter} quality artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
