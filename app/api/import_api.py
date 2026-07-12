from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from app.api.schemas import (
    AiSuggestionsUploadRequest,
    BookCommitConfirmationRequest,
    CommitConfirmationRequest,
    ImportDuplicateCheckRequest,
    ImportPreviewNoteRequest,
    ImportPreviewRequest,
    PdfTextLayerPreviewRequest,
    PdfToMarkdownConvertRequest,
    ReviewedObjectsUploadRequest,
)
from app.services import (
    commit_book_service,
    commit_paper_service,
    import_duplicate_check_service,
    import_preview_service,
    object_tag_suggestion_package_service,
    pdf_conversion_service,
)
from app.services.import_preview_service import ImportPreviewError
from app.services.pdf_backend_service import PdfBackendUnavailableError


router = APIRouter(prefix="/api/v1/imports")

PAPER_COMMIT_CONFIRMATION_CONTEXTS = {
    "commit_paper_after_preview",
    "import_whole_paper_after_preview",
}
BOOK_COMMIT_CONFIRMATION_CONTEXTS = {"import_full_book_after_preview"}


@router.post("/preview")
def create_import_preview(request: ImportPreviewRequest) -> dict[str, Any]:
    try:
        return import_preview_service.create_import_preview(request.model_dump())
    except PdfBackendUnavailableError as exc:
        source_path = request.pdf_path or request.converted_md_path
        return JSONResponse(status_code=503, content=exc.to_response(source_path=source_path))
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/preview-only")
def create_import_preview_only(request: ImportPreviewRequest) -> dict[str, Any]:
    try:
        return import_preview_service.create_import_preview(request.model_dump())
    except PdfBackendUnavailableError as exc:
        source_path = request.pdf_path or request.converted_md_path
        return JSONResponse(status_code=503, content=exc.to_response(source_path=source_path))
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/text-layer-preview")
def preview_pdf_text_layer(request: PdfTextLayerPreviewRequest) -> Any:
    payload = pdf_conversion_service.preview_pdf_text_layer_sample(
        request.pdf_path,
        title=request.title,
        max_pages=request.max_pages,
        max_chars=request.max_chars,
    )
    if payload.get("status") == "BLOCKED":
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.post("/duplicate-check")
def check_import_duplicate(request: ImportDuplicateCheckRequest) -> dict[str, Any]:
    return import_duplicate_check_service.check_duplicate_import(request.model_dump())


@router.post("/convert-pdf-to-markdown")
def convert_pdf_to_markdown(request: PdfToMarkdownConvertRequest) -> Any:
    payload = pdf_conversion_service.convert_pdf_to_markdown_text_layer(
        request.pdf_path,
        title=request.title,
        zotero_item_key=request.zotero_item_key,
        zotero_attachment_key=request.zotero_attachment_key,
    )
    if payload.get("status") == "BLOCKED":
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/{import_job_id}")
def get_import_preview(import_job_id: str) -> dict[str, Any]:
    try:
        preview = import_preview_service.get_import_preview(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        package_status = object_tag_suggestion_package_service.get_suggestion_package_status(import_job_id)
    except ImportPreviewError:
        package_status = {}
    return {**preview, **package_status}


@router.post("/{import_job_id}/notes")
def append_import_preview_note(import_job_id: str, request: ImportPreviewNoteRequest) -> dict[str, Any]:
    try:
        return import_preview_service.append_import_note(import_job_id, request.model_dump())
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{import_job_id}/commit")
def commit_import_preview(import_job_id: str) -> dict[str, Any]:
    try:
        return import_preview_service.commit_import_preview(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{import_job_id}/ai-suggestions")
def upload_ai_suggestions(import_job_id: str, request: AiSuggestionsUploadRequest) -> dict[str, Any]:
    try:
        return object_tag_suggestion_package_service.upload_ai_suggestions(
            import_job_id, request.model_dump()
        )
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{import_job_id}/ai-suggestions")
def get_ai_suggestions(import_job_id: str) -> dict[str, Any]:
    try:
        return object_tag_suggestion_package_service.get_ai_suggestions(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{import_job_id}/reviewed-objects")
def upload_reviewed_objects(import_job_id: str, request: ReviewedObjectsUploadRequest) -> dict[str, Any]:
    try:
        return object_tag_suggestion_package_service.upload_reviewed_objects(
            import_job_id, request.model_dump()
        )
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{import_job_id}/reviewed-objects")
def get_reviewed_objects(import_job_id: str) -> dict[str, Any]:
    try:
        return object_tag_suggestion_package_service.get_reviewed_objects(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{import_job_id}/commit-paper")
def commit_paper(
    import_job_id: str,
    request: CommitConfirmationRequest | None = Body(default=None),
) -> dict[str, Any]:
    confirmation = _commit_confirmation(
        request,
        PAPER_COMMIT_CONFIRMATION_CONTEXTS,
        strict_context=True,
        message="commit-paper only accepts paper confirmation context.",
    )
    if confirmation:
        return JSONResponse(status_code=400, content=confirmation)
    try:
        payload = commit_paper_service.commit_paper_from_staging(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
    }


@router.post("/{import_job_id}/commit-book")
def commit_book(
    import_job_id: str,
    request: BookCommitConfirmationRequest | None = Body(default=None),
) -> dict[str, Any]:
    confirmation = _commit_confirmation(
        request,
        BOOK_COMMIT_CONFIRMATION_CONTEXTS,
        strict_context=True,
        message="commit-book only accepts full-book confirmation context.",
    )
    if confirmation:
        return JSONResponse(status_code=400, content=confirmation)
    try:
        payload = commit_book_service.commit_book_from_staging(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
    }


@router.post("/{import_job_id}/commit-objects")
def commit_objects(
    import_job_id: str,
    request: CommitConfirmationRequest | None = Body(default=None),
) -> dict[str, Any]:
    from app.services import commit_objects_service
    confirmation = _commit_confirmation(request, "commit_objects_after_review")
    if confirmation:
        return confirmation
    try:
        payload = commit_objects_service.commit_objects_from_staging(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
    }


@router.post("/{import_job_id}/commit-reviewed-objects")
def commit_reviewed_objects(
    import_job_id: str,
    request: CommitConfirmationRequest | None = Body(default=None),
) -> dict[str, Any]:
    from app.services.commit_objects_service import commit_reviewed_objects_from_remap
    confirmation = _commit_confirmation(request, "commit_reviewed_objects_after_remap")
    if confirmation:
        return confirmation
    try:
        payload = commit_reviewed_objects_from_remap(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
    }


@router.post("/{import_job_id}/remap-reviewed-objects-preview")
def remap_reviewed_objects_preview(import_job_id: str) -> dict[str, Any]:
    from app.services.object_evidence_remap_service import remap_reviewed_objects_preview as _remap
    try:
        return _remap(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{import_job_id}/chatgpt-object-tag-input")
def generate_chatgpt_object_tag_input(import_job_id: str) -> dict[str, Any]:
    from app.services.chatgpt_bundle_service import generate_chatgpt_bundle
    try:
        return generate_chatgpt_bundle(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{import_job_id}/chatgpt-object-tag-input")
def get_chatgpt_object_tag_input(import_job_id: str) -> dict[str, Any]:
    from app.services.chatgpt_bundle_service import get_chatgpt_bundle_content
    try:
        return get_chatgpt_bundle_content(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{import_job_id}/source-trace-sections")
def get_source_trace_sections(import_job_id: str) -> dict[str, Any]:
    try:
        sections = import_preview_service.get_source_trace_sections(import_job_id)
    except ImportPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "import_job_id": import_job_id, "sections": sections}


def _commit_confirmation(
    request: CommitConfirmationRequest | None,
    expected_context: str | set[str],
    *,
    strict_context: bool = False,
    message: str | None = None,
) -> dict[str, Any] | None:
    expected_contexts = {expected_context} if isinstance(expected_context, str) else set(expected_context)
    expected_context_label = sorted(expected_contexts)[0] if expected_contexts else ""
    if request and request.confirm_write is True:
        received_context = request.confirmation_context
        if strict_context and received_context not in expected_contexts:
            return _blocked_commit_response(
                expected_context=expected_context_label,
                expected_contexts=sorted(expected_contexts),
                received_context=received_context,
                confirmation_received=True,
                message=message or "Invalid confirmation_context for this commit endpoint.",
            )
        return None
    return _blocked_commit_response(
        expected_context=expected_context_label,
        expected_contexts=sorted(expected_contexts),
        received_context=request.confirmation_context if request else None,
        confirmation_received=False,
        message="Direct commit endpoint requires confirm_write=true.",
    )


def _blocked_commit_response(
    *,
    expected_context: str,
    expected_contexts: list[str],
    received_context: str | None,
    confirmation_received: bool,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "confirmation_required": True,
        "confirmation_received": confirmation_received,
        "expected_confirmation_context": expected_context,
        "expected_confirmation_contexts": expected_contexts,
        "received_confirmation_context": received_context,
        "message": message,
        "db_write_performed": False,
        "core_db_write_performed": False,
        "vector_store_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "external_llm_called": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "seed_apply_performed": False,
        "ocr_or_marker_performed": False,
        "vector_index_write_performed": False,
        "lancedb_write_performed": False,
        "zotero_notes_write_performed": False,
        "object_candidates_generated": False,
    }
