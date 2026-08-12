from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.post("/books/{document_id}/chapters/{chapter_id}/note-correction-review/save-canary-plan")
def plan_book_chapter_note_correction_review_save_canary(
    document_id: int,
    chapter_id: int,
    request: NoteCorrectionReviewSaveRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.normalized_review_json is not None:
        review_payload = request.normalized_review_json
    elif request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_review_pipeline_service.build_note_correction_review_save_canary_plan(
            document_id=document_id,
            chapter_id=chapter_id,
            review_payload=review_payload,
            confirmation_context=request.confirmation_context,
            review_mode=request.review_mode,
            scope_id=request.scope_id,
            batch_size=request.batch_size,
            batch_index=request.batch_index,
            parent_review_mode=request.parent_review_mode,
            parent_scope_id=request.parent_scope_id,
            selected_server_note_ids=request.selected_server_note_ids,
            selected_note_ids=request.selected_note_ids,
            human_audit_items=request.human_audit_items,
            merge_preview=request.merge_preview,
            source_package_hash=request.source_package_hash,
            supersede_existing=request.supersede_existing,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-correction-review/save")
def save_book_chapter_note_correction_review(
    document_id: int,
    chapter_id: int,
    request: NoteCorrectionReviewSaveRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.normalized_review_json is not None:
        review_payload = request.normalized_review_json
    elif request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_review_pipeline_service.save_chapter_note_correction_review(
            document_id=document_id,
            chapter_id=chapter_id,
            review_payload=review_payload,
            confirm_write=request.confirm_write,
            confirmation_context=request.confirmation_context,
            review_mode=request.review_mode,
            scope_id=request.scope_id,
            batch_size=request.batch_size,
            batch_index=request.batch_index,
            parent_review_mode=request.parent_review_mode,
            parent_scope_id=request.parent_scope_id,
            selected_server_note_ids=request.selected_server_note_ids,
            selected_note_ids=request.selected_note_ids,
            human_audit_items=request.human_audit_items,
            merge_preview=request.merge_preview,
            source_package_hash=request.source_package_hash,
            supersede_existing=request.supersede_existing,
            canary_subscope=request.canary_subscope,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-classification-package")
def get_book_chapter_note_classification_package(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_chapter_note_classification_package(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-classification/dry-run-package")
def get_book_chapter_note_classification_dry_run_package(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_chapter_note_classification_dry_run_package(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-classification/validate-manual-json")
async def validate_book_chapter_note_classification_manual_json(
    document_id: int,
    chapter_id: int,
    request: Request,
) -> dict[str, Any]:
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    try:
        body: Any = await request.json()
    except Exception:
        body = body_text
    try:
        payload = chapter_review_pipeline_service.validate_chapter_note_classification_manual_json(
            document_id=document_id,
            chapter_id=chapter_id,
            classification_payload=body,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-classification/save-manual-json")
async def save_book_chapter_note_classification_manual_json(
    document_id: int,
    chapter_id: int,
    request: Request,
) -> dict[str, Any]:
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    try:
        body: Any = await request.json()
    except Exception:
        body = body_text
    if isinstance(body, dict):
        classification_payload: Any = (
            body.get("classification_json")
            if "classification_json" in body
            else body.get("json_text")
            if "json_text" in body
            else body.get("review_json")
            if "review_json" in body
            else body
        )
        confirm_write = bool(body.get("confirm_write"))
        confirmation_context = body.get("confirmation_context")
    else:
        classification_payload = body
        confirm_write = False
        confirmation_context = None
    try:
        payload = chapter_review_pipeline_service.save_chapter_note_classification_manual_json(
            document_id=document_id,
            chapter_id=chapter_id,
            classification_payload=classification_payload,
            confirm_write=confirm_write,
            confirmation_context=confirmation_context,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-classification-review/validate")
def validate_book_chapter_note_classification_review(
    document_id: int,
    chapter_id: int,
    request: NoteClassificationReviewValidateRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_review_pipeline_service.validate_chapter_note_classification_review(
            document_id=document_id,
            chapter_id=chapter_id,
            review_payload=review_payload,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }
