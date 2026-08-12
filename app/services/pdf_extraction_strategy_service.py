from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from app.core.paths import CONVERTED_MD_DIR
from app.services.pdf_backend_service import load_fitz_backend
from app.services.pdf_parser_backends import check_marker_surya_available


NATIVE_TEXT = "native_text"
HIGH_QUALITY_MARKDOWN = "high_quality_pdf_to_markdown"
MIN_NATIVE_SCORE = 70.0
MIN_MARKDOWN_SCORE = 65.0
_SHA_MARKER = re.compile(
    r"<!--\s*SOURCE_PDF_SHA256:\s*([0-9a-fA-F]{64})\s*-->"
)
_PDF_PAGE_MARKER = re.compile(
    r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->"
)


class PdfExtractionStrategyError(RuntimeError):
    pass


def build_pdf_extraction_plan(
    pdf_path: str | Path,
    *,
    pdf_sha256: str | None = None,
    converted_root: str | Path = CONVERTED_MD_DIR,
    page_extractor: Callable[[Path], tuple[int, list[tuple[int, str]]]] | None = None,
    converter_probe: Callable[[], dict[str, Any]] = check_marker_surya_available,
) -> dict[str, Any]:
    pdf = Path(pdf_path).resolve(strict=False)
    if not pdf.is_file():
        raise PdfExtractionStrategyError(f"pdf_not_found: {pdf}")
    source_sha = (pdf_sha256 or _sha256_file(pdf)).casefold()
    extractor = page_extractor or _extract_sample_pages
    extraction_error = None
    try:
        estimated_pages, pages = extractor(pdf)
        quality = assess_text_quality(pages, estimated_pages=estimated_pages)
    except Exception as exc:
        estimated_pages, pages = 0, []
        extraction_error = type(exc).__name__
        quality = assess_text_quality([], estimated_pages=0)
        quality["reasons"] = [
            f"native_text_quality_evaluation_failed:{extraction_error}"
        ]
    reused = find_sha_bound_markdown(converted_root, source_sha)
    reused_quality: dict[str, Any] | None = None
    reused_character_count: int | None = None
    reused_page_count: int | None = None
    if reused is not None:
        try:
            reused_markdown = reused.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise PdfExtractionStrategyError(
                "verified_converted_markdown_unreadable"
            ) from exc
        reused_quality = validate_markdown_for_import(
            reused_markdown,
            expected_pdf_sha256=source_sha,
        )
        reused_pages = _markdown_pages(reused_markdown)
        reused_page_count = len(reused_pages)
        reused_character_count = sum(
            len(text.strip())
            for _page_number, text in reused_pages
        )

    converter = converter_probe()
    converter_ready = bool(
        converter.get("marker_importable")
        and converter.get("surya_importable")
        and converter.get("pdftext_importable")
        and converter.get("required_model_files_present")
    )

    if reused is not None:
        strategy = HIGH_QUALITY_MARKDOWN
        status = "reused_sha_verified"
        ready = True
        converted_path = str(reused)
        converted_sha = _sha256_file(reused)
        quality = reused_quality or quality
        estimated_pages = max(
            int(estimated_pages),
            int(reused_page_count or 0),
        )
    elif quality["score"] >= MIN_NATIVE_SCORE and not quality["hard_failure"]:
        strategy = NATIVE_TEXT
        status = "not_required"
        ready = True
        converted_path = None
        converted_sha = None
    else:
        strategy = HIGH_QUALITY_MARKDOWN
        converted_path = None
        converted_sha = None
        ready = converter_ready
        status = "conversion_required" if converter_ready else "converter_unavailable"

    estimated_characters = (
        int(reused_character_count)
        if reused_character_count is not None
        else int(
            quality["mean_characters_per_sampled_page"]
            * max(estimated_pages, 1)
        )
    )
    warnings = []
    blockers: list[dict[str, Any]] = []
    if extraction_error:
        warnings.append("native_text_quality_evaluation_failed")
    if strategy == HIGH_QUALITY_MARKDOWN and not ready:
        warnings.append("high_quality_pdf_to_markdown_unavailable")
        blockers.append(
            {
                "code": "required_extraction_models_missing",
                "extractor_strategy": HIGH_QUALITY_MARKDOWN,
                "missing_components": [
                    Path(str(value)).name
                    for value in converter.get("missing_model_files") or []
                    if value
                ],
            }
        )
    if strategy == HIGH_QUALITY_MARKDOWN and reused is None:
        warnings.append("converted_markdown_not_found_for_pdf_sha256")
    return {
        "extractor_strategy": strategy,
        "text_quality_score": quality["score"],
        "quality_reasons": quality["reasons"],
        "text_quality_metrics": quality["metrics"],
        "converted_markdown_status": status,
        "converted_markdown_path": converted_path,
        "converted_markdown_pdf_sha256": source_sha if reused else None,
        "converted_markdown_sha256": converted_sha,
        "converted_markdown_page_markers": (
            int(reused_page_count)
            if reused_page_count is not None
            else None
        ),
        "converted_markdown_characters": (
            int(reused_character_count)
            if reused_character_count is not None
            else None
        ),
        "estimated_pages": int(estimated_pages),
        "estimated_chunks": (
            max(1, math.ceil(estimated_characters / 1800))
            if ready
            else 0
        ),
        "extraction_ready": ready,
        "blockers": blockers,
        "high_quality_converter": "marker_surya_page_blocks",
        "high_quality_converter_available": converter_ready,
        "high_quality_converter_missing_model_files": list(
            converter.get("missing_model_files") or []
        ),
        "warnings": warnings,
    }


def assess_text_quality(
    pages: Iterable[tuple[int, str]], *, estimated_pages: int | None = None
) -> dict[str, Any]:
    sampled = [(int(number), str(text or "")) for number, text in pages]
    page_count = len(sampled)
    texts = [text for _number, text in sampled]
    nonempty = [text for text in texts if text.strip()]
    characters = sum(len(text.strip()) for text in texts)
    mean_chars = characters / max(page_count, 1)
    empty_ratio = 1.0 - (len(nonempty) / max(page_count, 1))
    joined = "\n".join(texts)
    replacement_ratio = joined.count("\ufffd") / max(len(joined), 1)
    nonprintable_ratio = sum(
        1 for char in joined if not char.isprintable() and char not in "\n\r\t"
    ) / max(len(joined), 1)
    lines = [line.strip() for text in texts for line in text.splitlines() if line.strip()]
    single_character_line_ratio = sum(len(line) == 1 for line in lines) / max(
        len(lines), 1
    )
    very_short_line_ratio = sum(len(line) <= 3 for line in lines) / max(len(lines), 1)
    abnormal_space_ratio = len(re.findall(r" {3,}", joined)) / max(len(lines), 1)
    repeated_line_ratio = _repeated_line_ratio(texts)
    broken_word_ratio = len(re.findall(r"\b[A-Za-z]\s+(?:[A-Za-z]\s+){3,}[A-Za-z]\b", joined)) / max(
        len(lines), 1
    )
    heading_count = sum(
        bool(re.match(r"^(?:chapter\s+\d+|\d+(?:\.\d+)*\s+\S|#{1,6}\s+\S)", line, re.I))
        for line in lines
    )

    deductions = (
        min(55.0, empty_ratio * 65.0)
        + min(35.0, max(0.0, 120.0 - mean_chars) / 120.0 * 35.0)
        + min(25.0, replacement_ratio * 2500.0)
        + min(20.0, nonprintable_ratio * 2000.0)
        + min(18.0, single_character_line_ratio * 80.0)
        + min(12.0, very_short_line_ratio * 25.0)
        + min(12.0, abnormal_space_ratio * 3.0)
        + min(18.0, repeated_line_ratio * 45.0)
        + min(20.0, broken_word_ratio * 50.0)
    )
    score = round(max(0.0, 100.0 - deductions), 1)
    reasons: list[str] = []
    if empty_ratio >= 0.4:
        reasons.append(f"high_empty_page_ratio:{empty_ratio:.3f}")
    if mean_chars < 120:
        reasons.append(f"low_characters_per_page:{mean_chars:.1f}")
    if replacement_ratio >= 0.002:
        reasons.append(f"unicode_replacement_ratio:{replacement_ratio:.4f}")
    if nonprintable_ratio >= 0.002:
        reasons.append(f"nonprintable_character_ratio:{nonprintable_ratio:.4f}")
    if single_character_line_ratio >= 0.08:
        reasons.append(f"single_character_line_ratio:{single_character_line_ratio:.3f}")
    if very_short_line_ratio >= 0.25:
        reasons.append(f"very_short_line_ratio:{very_short_line_ratio:.3f}")
    if abnormal_space_ratio >= 0.5:
        reasons.append(f"abnormal_spacing:{abnormal_space_ratio:.3f}")
    if repeated_line_ratio >= 0.2:
        reasons.append(f"repeated_header_footer_ratio:{repeated_line_ratio:.3f}")
    if broken_word_ratio >= 0.08:
        reasons.append(f"split_word_ratio:{broken_word_ratio:.3f}")
    if not reasons:
        reasons.append("native_text_layer_quality_acceptable")

    return {
        "score": score,
        "hard_failure": not nonempty or empty_ratio >= 0.8,
        "reasons": reasons,
        "mean_characters_per_sampled_page": mean_chars,
        "metrics": {
            "sampled_pages": page_count,
            "estimated_pages": estimated_pages,
            "empty_page_ratio": round(empty_ratio, 4),
            "mean_characters_per_page": round(mean_chars, 1),
            "replacement_character_ratio": round(replacement_ratio, 6),
            "nonprintable_character_ratio": round(nonprintable_ratio, 6),
            "single_character_line_ratio": round(single_character_line_ratio, 4),
            "very_short_line_ratio": round(very_short_line_ratio, 4),
            "abnormal_space_ratio": round(abnormal_space_ratio, 4),
            "repeated_line_ratio": round(repeated_line_ratio, 4),
            "split_word_ratio": round(broken_word_ratio, 4),
            "heading_count": heading_count,
        },
    }


def validate_markdown_for_import(
    markdown_text: str, *, expected_pdf_sha256: str
) -> dict[str, Any]:
    match = _SHA_MARKER.search(markdown_text[:4000])
    if match is None or match.group(1).casefold() != expected_pdf_sha256.casefold():
        raise PdfExtractionStrategyError("converted_markdown_pdf_sha256_mismatch")
    pages = _markdown_pages(markdown_text)
    estimated_pages = max(
        (page_number for page_number, _text in pages),
        default=len(pages),
    )
    quality = assess_text_quality(
        pages,
        estimated_pages=estimated_pages,
    )
    if quality["score"] < MIN_MARKDOWN_SCORE or quality["hard_failure"]:
        raise PdfExtractionStrategyError("converted_markdown_quality_below_threshold")
    return quality


def find_sha_bound_markdown(root: str | Path, pdf_sha256: str) -> Path | None:
    directory = Path(root)
    if not directory.is_dir():
        return None
    expected = pdf_sha256.casefold()
    for candidate in sorted(directory.rglob("*.md")):
        try:
            prefix = candidate.read_text(encoding="utf-8")[:4000]
        except (OSError, UnicodeError):
            continue
        match = _SHA_MARKER.search(prefix)
        if match and match.group(1).casefold() == expected:
            return candidate.resolve(strict=False)
    return None


def extraction_plan_fingerprint(plan: dict[str, Any]) -> str:
    stable = "|".join(
        [
            str(plan.get("extractor_strategy") or ""),
            str(plan.get("text_quality_score") or ""),
            str(plan.get("converted_markdown_status") or ""),
            str(plan.get("converted_markdown_pdf_sha256") or ""),
            str(plan.get("converted_markdown_sha256") or ""),
            str(plan.get("extraction_ready") or ""),
            str(plan.get("estimated_chunks") or 0),
            str(plan.get("chapter_count") or 0),
            str(
                plan.get("page_marker_count")
                or plan.get("converted_markdown_page_markers")
                or 0
            ),
            ",".join(
                str(item.get("code") or "")
                for item in plan.get("blockers") or []
                if isinstance(item, dict)
            ),
        ]
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _extract_sample_pages(pdf: Path) -> tuple[int, list[tuple[int, str]]]:
    fitz = load_fitz_backend()
    document = fitz.open(pdf)
    try:
        count = int(document.page_count)
        indexes = _sample_indexes(count)
        return count, [
            (index + 1, str(document.load_page(index).get_text("text") or ""))
            for index in indexes
        ]
    finally:
        document.close()


def _sample_indexes(page_count: int, maximum: int = 12) -> list[int]:
    if page_count <= maximum:
        return list(range(page_count))
    indexes = {0, 1, 2, page_count - 1}
    for step in range(maximum):
        indexes.add(round(step * (page_count - 1) / max(maximum - 1, 1)))
    return sorted(indexes)[:maximum]


def _repeated_line_ratio(texts: list[str]) -> float:
    if len(texts) < 2:
        return 0.0
    page_lines = [
        {re.sub(r"\d+", "#", line.strip().casefold()) for line in text.splitlines() if len(line.strip()) >= 4}
        for text in texts
    ]
    occurrences: dict[str, int] = {}
    for lines in page_lines:
        for line in lines:
            occurrences[line] = occurrences.get(line, 0) + 1
    repeated = {line for line, count in occurrences.items() if count >= max(2, len(texts) // 2)}
    total_lines = sum(len(lines) for lines in page_lines)
    return sum(len(lines.intersection(repeated)) for lines in page_lines) / max(total_lines, 1)


def _markdown_pages(markdown_text: str) -> list[tuple[int, str]]:
    matches = list(_PDF_PAGE_MARKER.finditer(markdown_text))
    if not matches:
        return [
            (
                1,
                _strip_markdown_metadata(markdown_text),
            )
        ]

    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(markdown_text)
        )
        pages.append(
            (
                int(match.group(1)),
                _strip_markdown_metadata(
                    markdown_text[start:end]
                ),
            )
        )
    return pages


def _strip_markdown_metadata(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
