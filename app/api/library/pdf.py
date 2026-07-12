from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.get("/documents/{document_id}/pdf", response_model=None)
def document_pdf(document_id: int, request: Request) -> Any:
    try:
        pdf_path = library_service.resolve_document_pdf_path(document_id)
    except Exception as exc:
        return _document_pdf_not_found_response(
            document_id,
            None,
            f"PDF source could not be resolved: {exc}",
            request,
        )

    if pdf_path is None or not pdf_path.exists() or not pdf_path.is_file():
        return _document_pdf_not_found_response(
            document_id,
            pdf_path,
            "PDF file was not found for this document.",
            request,
        )
    if pdf_path.suffix.lower() != ".pdf":
        return _document_pdf_not_found_response(
            document_id,
            pdf_path,
            "Resolved document source is not a PDF file.",
            request,
        )

    try:
        pdf_path.stat()
    except OSError as exc:
        return _document_pdf_not_found_response(
            document_id,
            pdf_path,
            f"PDF file is not readable: {exc}",
            request,
        )

    response = FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=_safe_pdf_filename(pdf_path),
        content_disposition_type="inline",
    )
    _apply_pdf_cors_headers(response, request)
    return response


def _document_pdf_not_found_response(
    document_id: int,
    resolved_path: Any,
    message: str,
    request: Request,
) -> JSONResponse:
    response = JSONResponse(
        status_code=404,
        content={
            "status": "not_found",
            "document_id": document_id,
            "resolved_path": str(resolved_path) if resolved_path is not None else None,
            "message": message,
            **safety_fields(),
        },
    )
    _apply_pdf_cors_headers(response, request)
    return response
