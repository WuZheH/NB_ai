from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


def _pdf_backend_unavailable_response(
    exc: PdfBackendUnavailableError,
    *,
    source_path: str | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=503, content=exc.to_response(source_path=source_path))


@router.post("/import/pdf/classify")
def classify_pdf_import(request: PdfImportClassifyRequest) -> dict[str, Any]:
    try:
        payload = pdf_import_classifier_service.classify_pdf_import(
            request.pdf_path,
            source=request.source,
            zotero_key=request.zotero_key,
            zotero_pdf_source_id=request.zotero_pdf_source_id,
            zotero_metadata=request.zotero_metadata,
        )
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc, source_path=request.pdf_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
    }


@router.post("/import/pdf/commit")
def commit_pdf_import(request: PdfImportCommitRequest) -> dict[str, Any]:
    try:
        payload = pdf_import_classifier_service.commit_pdf_import(request.model_dump())
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc, source_path=request.pdf_path)
    except pdf_import_classifier_service.PdfImportClassificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"PDF parser backend unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"chaptered import failed: {exc}") from exc
    db_write = bool(payload.get("status") == "APPLIED" or payload.get("db_write_performed"))
    return {
        **payload,
        **safety_fields(db_write_performed=db_write),
    }


@router.post("/import/pdf/chaptered/preview")
def preview_chaptered_import(request: ChapteredPdfImportPreviewRequest) -> dict[str, Any]:
    import logging
    logger = logging.getLogger(__name__)

    from app.services.book_import_service import build_chaptered_preview_from_outline
    from app.services.pdf_import_classifier_service import classify_pdf_import

    try:
        classification = classify_pdf_import(
            request.pdf_path,
            source=request.source,
            zotero_key=request.zotero_key,
            zotero_pdf_source_id=request.zotero_pdf_source_id,
        )
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc, source_path=request.pdf_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("classification failed during chaptered preview")
        raise HTTPException(status_code=500, detail=f"classification failed: {exc}") from exc

    if classification.get("duplicate"):
        return {
            "status": "ok",
            "duplicate": True,
            "existing_document_id": classification.get("existing_document_id"),
            "existing_document_type": classification.get("existing_document_type"),
            "existing_object_import_mode": classification.get("existing_object_import_mode"),
            **safety_fields(db_write_performed=False),
        }

    try:
        preview = build_chaptered_preview_from_outline(
            request.pdf_path,
            title_hint=classification.get("title"),
        )
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc, source_path=request.pdf_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("outline preview failed")
        raise HTTPException(status_code=500, detail=f"outline preview failed: {exc}") from exc

    preview["duplicate"] = False
    return {
        **preview,
        **safety_fields(db_write_performed=False),
    }


@router.post("/import/pdf/preview-gate")
def preview_pdf_import_gate(request: PdfImportPreviewGateRequest) -> dict[str, Any]:
    source_path = request.pdf_path or request.zotero_attachment_path
    if not source_path:
        raise HTTPException(status_code=400, detail="pdf_path or zotero_attachment_path is required")
    try:
        import_preview_gate_service.validate_pdf_preview_path(source_path)
        payload = import_preview_gate_service.build_import_preview_gate(**request.model_dump())
        token = import_preview_gate_service.issue_pdf_preview_token(
            source_path,
            sample_pages=payload.get("sample_pages") or [],
        )
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc, source_path=str(source_path))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload.pop("pdf_path", None)
    return {
        **payload,
        "preview_token": token,
        "pdf_preview_url": f"/api/v1/library/import/pdf/preview-gate/file/{token}",
        **safety_fields(db_write_performed=False),
    }


@router.post("/import/pdf/repair-preview/start")
def start_pdf_repair_preview(request: PdfRepairPreviewRequest) -> dict[str, Any]:
    try:
        payload = ocr_repair_preview_service.build_ocr_repair_preview(**request.model_dump())
    except PdfBackendUnavailableError as exc:
        return _pdf_backend_unavailable_response(exc)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
    }


@router.post("/import/pdf/repair-preview/plan")
def draft_pdf_repair_plan(request: PdfRepairPlanRequest) -> dict[str, Any]:
    try:
        payload = ocr_repair_plan_service.build_ocr_repair_plan(request.repair_preview_result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
    }


@router.get("/import/pdf/preview-gate/file/{token}")
def preview_pdf_import_gate_file(token: str, request: Request) -> FileResponse:
    pdf_path = import_preview_gate_service.resolve_pdf_preview_token(token)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF preview token unavailable or expired")
    response = FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=_safe_pdf_filename(pdf_path),
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store, max-age=0"},
    )
    _apply_pdf_cors_headers(response, request)
    return response


@router.post("/import/pdf/chaptered/jobs")
def create_chaptered_import_job(request: ChapteredPdfImportJobRequest) -> dict[str, Any]:
    if request.object_import_mode != "chaptered":
        raise HTTPException(status_code=400, detail="Only chaptered imports are supported via job endpoint.")
    try:
        job = pdf_import_job_process_service.create_chaptered_import_job_process(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "job": job,
        **safety_fields(db_write_performed=False),
    }


@router.get("/import/pdf/jobs/{job_id}")
def get_import_job(job_id: str) -> dict[str, Any]:
    try:
        job = pdf_import_job_process_service.get_import_job_status(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db_write = bool(job.get("status") == "completed")
    return {
        "status": "ok",
        "job": job,
        **safety_fields(db_write_performed=db_write),
    }


@router.post("/import/pdf/jobs/{job_id}/cancel")
def cancel_import_job(job_id: str) -> dict[str, Any]:
    try:
        result = pdf_import_job_process_service.cancel_import_job_process(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "ok",
        "job": result,
        **result,
        **safety_fields(db_write_performed=False),
    }
