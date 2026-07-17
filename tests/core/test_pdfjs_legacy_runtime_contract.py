from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pdf_surfaces_use_legacy_pdfjs_for_electron_chromium_compatibility() -> None:
    for relative_path in (
        "frontend/src/PdfLocationPreview.jsx",
        "frontend/src/PdfCoverThumbnail.jsx",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'import("pdfjs-dist/legacy/build/pdf.mjs")' in source
        assert 'import("pdfjs-dist/legacy/build/pdf.worker.mjs?url")' in source
        assert 'import("pdfjs-dist")' not in source
        assert 'import("pdfjs-dist/build/pdf.worker.mjs?url")' not in source
