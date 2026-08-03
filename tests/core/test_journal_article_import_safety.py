from __future__ import annotations

import pytest

from app.services import book_import_service
from app.services.chunk_splitter import TextChunk
from app.services.pdf_parser_backends import PYMUPDF_BACKEND, PdfParseResult


REFERENCE_TITLE = "[7] CMU. Cmu graphics lab motion capture database. 2003."
SYNTHETIC_WARNING = "synthetic_full_text: no chapter heading candidates detected"


class _SafetyProbe(RuntimeError):
    pass


def _prepared_article_like_document(tmp_path):
    pdf = tmp_path / "article.pdf"
    pdf.write_bytes(b"%PDF article fixture")
    markdown = "\n\n".join(
        f"<!-- PDF_PAGE: {page} -->\nReadable article body on page {page}."
        for page in range(1, 11)
    )
    chapter = book_import_service.DetectedChapter(
        chapter_index=1,
        title="Generating Diverse and Natural 3D Human Motions From Text",
        heading_path="Generating Diverse and Natural 3D Human Motions From Text",
        pdf_page_start=1,
        pdf_page_end=10,
        detection_method="synthetic_full_text",
        source="synthetic",
        confidence=0.0,
    )
    chunk_text = (
        "Readable article body with enough content for semantic indexing."
    )
    chunk = TextChunk(
        node_order_index=0,
        chunk_index=0,
        heading_path=chapter.heading_path,
        chunk_text=chunk_text,
        char_count=len(chunk_text),
        token_count=None,
        overlap_before=None,
        overlap_after=None,
        pdf_page_start=1,
        pdf_page_end=10,
        pdf_path=str(pdf),
    )
    return book_import_service.PreparedBookImport(
        pdf_path=pdf,
        title=chapter.title,
        backend=PYMUPDF_BACKEND,
        parse_result=PdfParseResult(
            markdown_text=markdown,
            page_markers_present=True,
            page_count=10,
            parser_backend=PYMUPDF_BACKEND,
            block_stats={"page_marker_count": 10},
        ),
        markdown_text=markdown,
        detection_method="synthetic_full_text",
        chapters=[chapter],
        chunks=[chunk],
        chunk_chapter_indexes=[1],
        binding_rate=1.0,
        warnings=[SYNTHETIC_WARNING],
        rejected_candidates=[
            book_import_service.ChapterCandidate(
                title=REFERENCE_TITLE,
                source="section_header",
                page_start=10,
                confidence=0.2,
                suspicious_reason="reference_entry",
                rejected_reason="pseudocode",
            )
        ],
    )


def test_book_profile_keeps_chapter_structure_blockers(tmp_path):
    prepared = _prepared_article_like_document(tmp_path)

    result = book_import_service.evaluate_auto_apply_safety(
        prepared,
        db_path=tmp_path / "missing.db",
        document_type="book",
    )

    assert result["auto_apply_eligible"] is False
    assert "synthetic_full_text_not_apply_safe" in result["reasons"]
    assert "chapter_count_below_5" in result["reasons"]
    assert any(
        reason.startswith("suspicious_chapter_titles:")
        for reason in result["reasons"]
    )
    assert f"high_risk_warning:{SYNTHETIC_WARNING}" in result["reasons"]


def test_journal_article_profile_skips_only_book_structure_blockers(tmp_path):
    prepared = _prepared_article_like_document(tmp_path)

    result = book_import_service.evaluate_auto_apply_safety(
        prepared,
        db_path=tmp_path / "missing.db",
        document_type="journalArticle",
    )

    assert result["auto_apply_eligible"] is True
    assert result["reasons"] == []
    assert result["book_safety_decision"] == "allowed"
    assert result["book_safety_blockers"] == []
    assert result["chapter_title_quality"] == "not_applicable"
    assert all(chapter.title != REFERENCE_TITLE for chapter in prepared.chapters)
    assert prepared.rejected_candidates[0].title == REFERENCE_TITLE


def test_apply_forwards_document_type_to_safety(tmp_path, monkeypatch):
    prepared = _prepared_article_like_document(tmp_path)
    captured = {}

    def fake_safety(_prepared, *, db_path, document_type):
        captured["db_path"] = db_path
        captured["document_type"] = document_type
        raise _SafetyProbe

    monkeypatch.setattr(
        book_import_service,
        "evaluate_auto_apply_safety",
        fake_safety,
    )

    with pytest.raises(_SafetyProbe):
        book_import_service.apply_prepared_book_import(
            prepared,
            db_path=tmp_path / "temp.db",
            backup=False,
            document_type="journalArticle",
        )

    assert captured == {
        "db_path": tmp_path / "temp.db",
        "document_type": "journalArticle",
    }


def test_reference_heading_is_rejected_by_real_detection(
    tmp_path,
    monkeypatch,
):
    pdf = tmp_path / "article-with-reference-heading.pdf"
    pdf.write_bytes(b"%PDF article fixture")

    monkeypatch.setattr(
        book_import_service,
        "extract_pdf_outline_chapter_candidates",
        lambda _pdf_path: [],
    )

    pages = []
    for page in range(1, 11):
        body = f"Readable article body on page {page}."
        if page == 10:
            body += f"\n\n# {REFERENCE_TITLE}"
        pages.append(
            f"<!-- PDF_PAGE: {page} -->\n{body}"
        )

    prepared = (
        book_import_service
        .prepare_book_import_from_markdown(
            pdf,
            "\n\n".join(pages),
            title=(
                "Generating Diverse and Natural "
                "3D Human Motions From Text"
            ),
            backend=PYMUPDF_BACKEND,
        )
    )

    assert prepared.detection_method == "synthetic_full_text"
    assert all(
        chapter.title != REFERENCE_TITLE
        for chapter in prepared.chapters
    )

    rejected = [
        candidate
        for candidate in prepared.rejected_candidates
        if candidate.title == REFERENCE_TITLE
    ]

    assert len(rejected) == 1
    assert rejected[0].rejected_reason == "pseudocode"
