"""Library document path validation and record-classification helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.paths import DATA_DIR, DATA_PROJECT_ROOT, ZOTERO_LIBRARY_DIR


SAFE_PDF_ROOTS = (
    DATA_DIR / "pdfs",
    DATA_DIR / "pdfs" / "papers",
    ZOTERO_LIBRARY_DIR,
)
TEST_DATA_TITLE_MARKERS = ("mock", "test minimal")
TEST_DATA_PREFIXES = ("test ",)
TEST_DATA_METADATA_MARKERS = ("mock", "fixture", "test_seed")
TEST_DATA_PATH_MARKERS = ("fixture", "mock", "test_minimal")


def resolve_safe_pdf_path(pdf_path: str | None) -> Path | None:
    if not pdf_path:
        return None
    candidate = Path(pdf_path)
    if not candidate.is_absolute():
        candidate = DATA_PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if resolved.suffix.lower() != ".pdf":
        return None
    within_project = _is_relative_to(resolved, DATA_PROJECT_ROOT)
    within_safe_roots = any(
        _is_relative_to(resolved, root.resolve(strict=False)) for root in SAFE_PDF_ROOTS
    )
    if not within_project and not within_safe_roots:
        return None
    return resolved


def is_safe_pdf_path(pdf_path: str | None) -> bool:
    return resolve_safe_pdf_path(pdf_path) is not None


def is_test_library_record(item: object) -> bool:
    title = str(_object_value(item, "title", "") or "").strip().lower()
    if any(marker in title for marker in TEST_DATA_TITLE_MARKERS):
        return True
    if any(title.startswith(prefix) for prefix in TEST_DATA_PREFIXES):
        return True

    for key in ("source_type", "document_type", "content_layer", "read_status"):
        value = str(_object_value(item, key, "") or "").strip().lower()
        if value in TEST_DATA_METADATA_MARKERS:
            return True

    for key in ("source_path", "pdf_path", "pdf_open_url"):
        value = str(_object_value(item, key, "") or "").replace("\\", "/").lower()
        if any(marker in value for marker in TEST_DATA_PATH_MARKERS):
            return True
    return False


def _object_value(item: object, key: str, default: object = None) -> object:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


_CORE_SERVICE_EXPORTS = {
    "get_document_pdf_source",
    "resolve_document_pdf_path",
    "show_library_document",
}


def __getattr__(name: str) -> Any:
    if name not in _CORE_SERVICE_EXPORTS:
        raise AttributeError(name)
    from app.services import library_core_service

    return getattr(library_core_service, name)


__all__ = [
    "get_document_pdf_source",
    "is_safe_pdf_path",
    "is_test_library_record",
    "resolve_document_pdf_path",
    "resolve_safe_pdf_path",
    "show_library_document",
]
