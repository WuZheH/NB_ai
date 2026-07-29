import hashlib

import pytest

from app.services import book_import_service, zotero_direction_b_import_service
from app.services import pdf_extraction_strategy_service as service
from app.services.pdf_parser_backends import (
    MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    PYMUPDF_BACKEND,
    PdfParseResult,
)


def _pdf(tmp_path, content=b"%PDF fixture"):
    path = tmp_path / "source.pdf"
    path.write_bytes(content)
    return path


def _available():
    return {
        "marker_importable": True,
        "surya_importable": True,
        "pdftext_importable": True,
        "required_model_files_present": True,
        "missing_model_files": [],
    }


def _unavailable():
    return {
        "marker_importable": True,
        "surya_importable": True,
        "pdftext_importable": True,
        "required_model_files_present": False,
        "missing_model_files": ["layout/model.safetensors"],
    }


def _good_pages(_path):
    paragraph = (
        "Chapter 1 Introduction\n"
        "This chapter develops a deterministic extraction strategy for ordinary "
        "text-layer PDF documents. The paragraph remains continuous, readable, "
        "and suitable for semantic chunking. Equation E = mc^2 is retained near "
        "the surrounding explanation.\n"
    )
    return 120, [(number, paragraph * 4) for number in range(1, 9)]


def _bad_pages(_path):
    return 200, [(number, "" if number < 7 else "A\nl\ng\no\nr\ni\nt\nh\nm") for number in range(1, 9)]


def test_high_quality_text_layer_routes_to_native(tmp_path):
    plan = service.build_pdf_extraction_plan(
        _pdf(tmp_path),
        page_extractor=_good_pages,
        converter_probe=_unavailable,
        converted_root=tmp_path / "converted",
    )
    assert plan["extractor_strategy"] == service.NATIVE_TEXT
    assert plan["extraction_ready"] is True
    assert plan["text_quality_score"] >= service.MIN_NATIVE_SCORE
    assert plan["estimated_pages"] == 120
    assert plan["estimated_chunks"] > 0


def test_low_quality_text_routes_to_available_high_quality_converter(tmp_path):
    plan = service.build_pdf_extraction_plan(
        _pdf(tmp_path),
        page_extractor=_bad_pages,
        converter_probe=_available,
        converted_root=tmp_path / "converted",
    )
    assert plan["extractor_strategy"] == service.HIGH_QUALITY_MARKDOWN
    assert plan["converted_markdown_status"] == "conversion_required"
    assert plan["extraction_ready"] is True
    assert len(plan["quality_reasons"]) >= 2


def test_low_quality_text_blocks_when_local_model_cache_is_incomplete(tmp_path):
    plan = service.build_pdf_extraction_plan(
        _pdf(tmp_path),
        page_extractor=_bad_pages,
        converter_probe=_unavailable,
        converted_root=tmp_path / "converted",
    )
    assert plan["extractor_strategy"] == service.HIGH_QUALITY_MARKDOWN
    assert plan["converted_markdown_status"] == "converter_unavailable"
    assert plan["extraction_ready"] is False
    assert plan["high_quality_converter_missing_model_files"]


def test_existing_markdown_is_reused_only_when_pdf_sha_is_exact(tmp_path):
    pdf = _pdf(tmp_path)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    converted = tmp_path / "converted"
    converted.mkdir()
    markdown = converted / "verified.md"
    markdown.write_text(
        f"<!-- SOURCE_PDF_SHA256: {sha} -->\n\n# Chapter 1\n\n"
        + "Readable paragraph with equation x = y + z and enough context. " * 20,
        encoding="utf-8",
    )
    plan = service.build_pdf_extraction_plan(
        pdf,
        page_extractor=_bad_pages,
        converter_probe=_unavailable,
        converted_root=converted,
    )
    assert plan["converted_markdown_status"] == "reused_sha_verified"
    assert plan["converted_markdown_path"] == str(markdown.resolve())
    assert plan["extraction_ready"] is True


def test_markdown_sha_mismatch_is_rejected(tmp_path):
    markdown = (
        "<!-- SOURCE_PDF_SHA256: "
        + ("0" * 64)
        + " -->\n\n# Chapter\n\nReadable paragraph. "
    )
    with pytest.raises(
        service.PdfExtractionStrategyError,
        match="converted_markdown_pdf_sha256_mismatch",
    ):
        service.validate_markdown_for_import(
            markdown, expected_pdf_sha256="1" * 64
        )


def test_low_quality_conversion_result_is_rejected(tmp_path):
    markdown = (
        f"<!-- SOURCE_PDF_SHA256: {'1' * 64} -->\n\n"
        "A\nl\ng\no\nr\ni\nt\nh\nm"
    )
    with pytest.raises(
        service.PdfExtractionStrategyError,
        match="converted_markdown_quality_below_threshold",
    ):
        service.validate_markdown_for_import(
            markdown, expected_pdf_sha256="1" * 64
        )


def test_quality_metrics_keep_heading_paragraph_and_formula_signal():
    text = (
        "Chapter 3 Dynamic Programming\n"
        "A normal paragraph remains on one line instead of being split into "
        "one-character fragments. The recurrence T(n) = T(n-1) + n is present.\n"
    ) * 8
    result = service.assess_text_quality([(1, text), (2, text.replace("3", "4"))])
    assert result["score"] >= service.MIN_NATIVE_SCORE
    assert result["metrics"]["heading_count"] >= 2
    assert result["metrics"]["single_character_line_ratio"] == 0


def test_repeated_header_footer_pollution_is_explainable():
    pages = [
        (
            number,
            "INTRODUCTION TO ALGORITHMS\n"
            + ("Substantive body paragraph with readable text. " * 12)
            + f"\nINTRODUCTION TO ALGORITHMS\n{number}",
        )
        for number in range(1, 9)
    ]
    result = service.assess_text_quality(pages)
    assert result["metrics"]["repeated_line_ratio"] > 0
    assert any(reason.startswith("repeated_header_footer_ratio") for reason in result["reasons"])


def test_plan_fingerprint_changes_when_strategy_changes(tmp_path):
    pdf = _pdf(tmp_path)
    native = service.build_pdf_extraction_plan(
        pdf,
        page_extractor=_good_pages,
        converter_probe=_unavailable,
        converted_root=tmp_path / "converted",
    )
    high_quality = service.build_pdf_extraction_plan(
        pdf,
        page_extractor=_bad_pages,
        converter_probe=_available,
        converted_root=tmp_path / "converted",
    )
    assert service.extraction_plan_fingerprint(native) != service.extraction_plan_fingerprint(high_quality)


def test_large_native_document_without_markdown_headings_is_chunked_by_page(
    tmp_path, monkeypatch
):
    pdf = _pdf(tmp_path)
    body = "Readable body text with formula f(x) = x^2. " * 40
    markdown = "\n\n".join(
        f"<!-- PDF_PAGE: {page} -->\n\nREPEATED BOOK HEADER\n{body}\n{page}"
        for page in range(1, 121)
    )
    monkeypatch.setattr(
        book_import_service,
        "parse_pdf_to_markdown",
        lambda *_args, **_kwargs: PdfParseResult(
            markdown_text=markdown,
            page_markers_present=True,
            page_count=120,
            parser_backend=PYMUPDF_BACKEND,
        ),
    )
    monkeypatch.setattr(
        book_import_service,
        "detect_book_chapters",
        lambda *_args, **_kwargs: {
            "chapters": [
                book_import_service.DetectedChapter(
                    chapter_index=1,
                    title="Chapter 1",
                    heading_path="Chapter 1",
                    pdf_page_start=1,
                    pdf_page_end=120,
                    detection_method="pdf_outline",
                    source="pdf_outline",
                    confidence=0.95,
                )
            ],
            "detection_method": "pdf_outline",
            "rejected_candidates_summary": {},
            "rejected_candidates": [],
        },
    )
    prepared = book_import_service.prepare_book_import(
        pdf,
        title="Large Book",
        backend=PYMUPDF_BACKEND,
    )
    assert prepared.chunks
    assert prepared.binding_rate == 1.0
    assert "plain_text_page_fallback_used" in prepared.warnings
    assert all("REPEATED BOOK HEADER" not in chunk.chunk_text for chunk in prepared.chunks)
    assert any("f(x) = x^2" in chunk.chunk_text for chunk in prepared.chunks)


def test_high_quality_conversion_is_quality_gated_before_database_apply(
    tmp_path, monkeypatch
):
    pdf = _pdf(tmp_path)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    calls = []
    markdown = (
        "# Chapter 1\n\n"
        + "A readable converted paragraph retains equation E = mc^2 and context. " * 30
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "parse_pdf_to_markdown",
        lambda *_args, **kwargs: calls.append(kwargs) or PdfParseResult(
            markdown_text=markdown,
            page_markers_present=False,
            page_count=1,
            parser_backend=service.HIGH_QUALITY_MARKDOWN,
        ),
    )
    monkeypatch.setattr(
        book_import_service,
        "prepare_book_import_from_markdown",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        book_import_service,
        "apply_prepared_book_import",
        lambda *_args, **_kwargs: {"document_id": 77, "inserted_chunks": 3},
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service.commit_book_service,
        "_record_document_source",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service.commit_book_service,
        "_record_document_zotero_key",
        lambda *_args, **_kwargs: None,
    )
    result = zotero_direction_b_import_service._default_selected_book_body_importer(
        preview={
            "zotero_item": {"title": "Converted Book", "library_id": 1},
            "selected_attachment": {"pdf_sha256": sha},
            "extractor_strategy": service.HIGH_QUALITY_MARKDOWN,
            "extraction_ready": True,
            "converted_markdown_status": "conversion_required",
        },
        db_path=tmp_path / "temp.db",
        pdf_path=pdf,
    )
    assert result["document_id"] == 77
    assert result["parser_backend"] == MARKER_SURYA_PAGE_BLOCKS_BACKEND
    assert calls == [{"backend": MARKER_SURYA_PAGE_BLOCKS_BACKEND}]
