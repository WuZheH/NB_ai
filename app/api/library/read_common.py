from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api.schemas import (
    ChapteredPdfImportJobRequest,
    ChapteredPdfImportPreviewRequest,
    PdfImportClassifyRequest,
    PdfImportCommitRequest,
    PdfImportPreviewGateRequest,
    PdfRepairPlanRequest,
    PdfRepairPreviewRequest,
    safety_fields,
)
from app.api.library_presenters import (
    READ_STATUSES,
    _apply_pdf_cors_headers,
    _chunk_detail_item,
    _document_item,
    _evidence_preview_item,
    _grouped_search_document_item,
    _note_preview_item,
    _object_search_results,
    _pdf_location_item,
    _read_shelf_item,
    _related_note_item,
    _relation_item,
    _safe_pdf_filename,
    _search_result_item,
    _value,
    _zotero_annotation_candidate_item,
    _zotero_link_candidate_item,
)
from app.services import (
    book_chapter_service,
    high_quality_search_service,
    import_preview_gate_service,
    library_service,
    local_embedding_service,
    local_reranker_service,
    object_candidate_service,
    object_semantic_search_service,
    ocr_repair_plan_service,
    ocr_repair_preview_service,
    pdf_chunk_locator_service,
    pdf_import_classifier_service,
    pdf_import_job_process_service,
    vector_store_service,
    vector_store_worker,
    zotero_annotation_linking_service,
    zotero_linking_service,
)
from app.services.pdf_backend_service import PdfBackendUnavailableError


__all__ = [name for name in globals() if not name.startswith("__")]
