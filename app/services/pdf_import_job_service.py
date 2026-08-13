"""Compatibility facade for chaptered PDF import jobs.

New code should use app.services.pdf_import_job_process_service directly.
"""

from __future__ import annotations

from typing import Any

from app.services import pdf_import_job_process_service as _process_service
from app.services.book_import_service import (
    MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    apply_prepared_book_import,
    prepare_book_import,
)
from app.services.pdf_import_classifier_service import (
    PdfImportClassificationError,
    classify_pdf_import,
)
from app.services.pdf_import_job_process_service import (
    NON_CANCELABLE_STAGES,
    STAGES,
    cancel_import_job_process,
    create_chaptered_import_job_process,
    get_import_job_status,
)
from app.services.pdf_parser_backends import probe_runtime


def create_chaptered_import_job(payload: dict[str, Any]) -> dict[str, Any]:
    _process_service.probe_runtime = probe_runtime
    return create_chaptered_import_job_process(payload)


def get_import_job(job_id: str) -> dict[str, Any]:
    return get_import_job_status(job_id)


def cancel_import_job(job_id: str) -> dict[str, Any]:
    return cancel_import_job_process(job_id)


def _patch_document_type(document_id: int, document_type: str) -> None:
    """Compatibility hook for older tests; subprocess worker owns real patching."""
    return None


__all__ = [
    "MARKER_SURYA_PAGE_BLOCKS_BACKEND",
    "PdfImportClassificationError",
    "STAGES",
    "NON_CANCELABLE_STAGES",
    "apply_prepared_book_import",
    "cancel_import_job",
    "classify_pdf_import",
    "create_chaptered_import_job",
    "get_import_job",
    "prepare_book_import",
    "probe_runtime",
    "_patch_document_type",
]
