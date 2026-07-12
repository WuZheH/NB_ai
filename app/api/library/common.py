from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api.schemas import (
    BookChapterObjectBundleRequest,
    BookChapterObjectsCommitRequest,
    BookChapterObjectsPreviewRequest,
    ChapterZoteroNotesApplyRequest,
    ChapteredPdfImportJobRequest,
    ChapteredPdfImportPreviewRequest,
    NoteClassificationReviewValidateRequest,
    NoteCorrectionBatchReviewValidateRequest,
    NoteCorrectionReviewValidateRequest,
    NoteCorrectionReviewSaveRequest,
    NoteCorrectionSectionReviewValidateRequest,
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
from app.schemas.manual_chatgpt_bridge import (
    MechanismSourcePackPastebackValidateRequest,
    MechanismSourcePackPromptExportRequest,
    WorkspaceSelectionSourcePackRequest,
)
from app.schemas.mechanism_draft_review import (
    MechanismDraftReviewActionPreviewRequest,
    MechanismDraftReviewPacketPreviewRequest,
)
from app.services import (
    book_chapter_service,
    chapter_note_correction_prompt_service,
    chapter_review_pipeline_service,
    chapter_workspace_search_service,
    chapter_workspace_state_service,
    chapter_zotero_notes_dry_run_service,
    book_object_import_service,
    high_quality_search_service,
    library_service,
    local_embedding_service,
    local_reranker_service,
    mechanism_draft_review_service,
    mechanism_prompt_export_service,
    object_candidate_service,
    object_semantic_search_service,
    pdf_chunk_locator_service,
    pdf_import_classifier_service,
    import_preview_gate_service,
    ocr_repair_plan_service,
    ocr_repair_preview_service,
    pdf_import_job_process_service,
    vector_store_service,
    vector_store_worker,
    workspace_selection_source_pack_service,
    zotero_annotation_linking_service,
    zotero_linking_service,
)
from app.services.pdf_backend_service import PdfBackendUnavailableError

CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT = "import_zotero_notes_to_notebook_ai"


__all__ = [name for name in globals() if not name.startswith("__")]
