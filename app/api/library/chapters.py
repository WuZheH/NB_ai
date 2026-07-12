from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.get("/books/{document_id}")
def get_book_detail(document_id: int) -> dict[str, Any]:
    try:
        payload = book_chapter_service.build_book_detail_payload(document_id)
    except book_chapter_service.BookDocumentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except book_chapter_service.NotBookDocument as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except book_chapter_service.BookChapterSchemaUnavailable as exc:
        return {
            "status": "schema_unavailable",
            "implementation_status": "contract_ready",
            "document_id": document_id,
            "message": str(exc),
            **safety_fields(),
        }
    return {
        "status": "ok",
        "implementation_status": "contract_ready",
        **payload,
        **safety_fields(),
    }


@router.get("/books/{document_id}/object-import/next")
def get_book_next_object_import_chapter(document_id: int) -> dict[str, Any]:
    try:
        payload = book_chapter_service.get_next_object_import_chapter(document_id)
    except book_chapter_service.BookChapterSchemaUnavailable as exc:
        return {
            "status": "schema_unavailable",
            "implementation_status": "contract_ready",
            "document_id": document_id,
            "message": str(exc),
            **safety_fields(),
        }
    return {
        "implementation_status": "contract_ready",
        "document_id": document_id,
        **payload,
        **safety_fields(),
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/zotero-notes/dry-run")
def dry_run_book_chapter_zotero_notes(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_zotero_notes_dry_run_service.build_chapter_zotero_notes_dry_run(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_zotero_notes_dry_run_service.ChapterZoteroNotesDryRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
        "object_candidates_generated": False,
        "mechanism_generated": False,
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/zotero-notes/apply")
def apply_book_chapter_zotero_notes(
    document_id: int,
    chapter_id: int,
    request: ChapterZoteroNotesApplyRequest,
) -> dict[str, Any]:
    blocked_safety = {
        **safety_fields(db_write_performed=False),
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }
    base = {
        "mode": "chapter_zotero_notes_apply",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "confirmation_required": True,
        "expected_confirmation_context": CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT,
        "received_confirmation_context": request.confirmation_context,
    }
    if request.confirm_write is not True:
        return {
            **base,
            "status": "BLOCKED",
            "confirmation_received": False,
            "apply_blocked_reason": "confirm_write_required",
            **blocked_safety,
        }
    if request.confirmation_context != CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT:
        return {
            **base,
            "status": "BLOCKED",
            "confirmation_received": False,
            "apply_blocked_reason": "confirmation_context_invalid",
            **blocked_safety,
        }
    if request.document_id != document_id or request.chapter_id != chapter_id:
        return {
            **base,
            "status": "BLOCKED",
            "confirmation_received": True,
            "confirmation_context": request.confirmation_context,
            "apply_blocked_reason": "route_body_scope_mismatch",
            "received_document_id": request.document_id,
            "received_chapter_id": request.chapter_id,
            **blocked_safety,
        }

    try:
        dry_run = chapter_zotero_notes_dry_run_service.build_chapter_zotero_notes_dry_run(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_zotero_notes_dry_run_service.ChapterZoteroNotesDryRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    expected_key_errors = []
    if request.zotero_item_key != dry_run.get("zotero_item_key"):
        expected_key_errors.append("zotero_item_key_mismatch")
    if request.zotero_attachment_key != dry_run.get("zotero_attachment_key"):
        expected_key_errors.append("zotero_attachment_key_mismatch")
    if expected_key_errors:
        return {
            **base,
            "status": "BLOCKED",
            "confirmation_received": True,
            "confirmation_context": request.confirmation_context,
            "apply_blocked_reason": "zotero_identity_mismatch",
            "identity_errors": expected_key_errors,
            "expected_zotero_item_key": dry_run.get("zotero_item_key"),
            "expected_zotero_attachment_key": dry_run.get("zotero_attachment_key"),
            "received_zotero_item_key": request.zotero_item_key,
            "received_zotero_attachment_key": request.zotero_attachment_key,
            "would_insert_count": dry_run.get("would_insert_count", 0),
            "would_skip_existing_count": dry_run.get("would_skip_existing_count", 0),
            **blocked_safety,
        }

    if (
        request.expected_would_insert_count is not None
        and int(request.expected_would_insert_count) != int(dry_run.get("would_insert_count") or 0)
    ):
        return {
            **base,
            "status": "BLOCKED",
            "confirmation_received": True,
            "confirmation_context": request.confirmation_context,
            "apply_blocked_reason": "expected_would_insert_count_mismatch",
            "expected_would_insert_count": request.expected_would_insert_count,
            "actual_would_insert_count": dry_run.get("would_insert_count", 0),
            **blocked_safety,
        }

    if int(dry_run.get("would_insert_count") or 0) == 0 and int(dry_run.get("would_skip_existing_count") or 0) > 0:
        return {
            **dry_run,
            **base,
            "status": "ALREADY_IMPORTED",
            "dry_run": False,
            "apply_requested": False,
            "confirmation_received": True,
            "confirmation_context": request.confirmation_context,
            "import_skipped_reason": "already_existing_in_notebook_ai",
            "inserted_count": 0,
            "skipped_existing_count": dry_run.get("would_skip_existing_count", 0),
            "blocked_count": dry_run.get("would_block_count", 0),
            "only_table_write_scope": "none",
            **blocked_safety,
        }

    try:
        payload = chapter_zotero_notes_dry_run_service.apply_chapter_zotero_notes(
            document_id=document_id,
            chapter_id=chapter_id,
            expected_would_insert_count=request.expected_would_insert_count,
        )
    except chapter_zotero_notes_dry_run_service.ChapterZoteroNotesDryRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    db_write = bool(payload.get("db_write_performed"))
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
        "received_confirmation_context": request.confirmation_context,
        "expected_confirmation_context": CHAPTER_ZOTERO_NOTES_IMPORT_CONTEXT,
        **safety_fields(db_write_performed=db_write),
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-correction-review-plan")
def get_book_chapter_note_correction_review_plan(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_note_correction_prompt_service.build_chapter_note_correction_review_plan(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-correction-sections")
def get_book_chapter_note_correction_sections(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_note_correction_prompt_service.build_chapter_note_correction_sections(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-correction-package")
def get_book_chapter_note_correction_package(
    document_id: int,
    chapter_id: int,
    mode: str = Query(default="full_chapter"),
    section_id: str | None = None,
    batch_size: int = Query(default=15, ge=1, le=50),
    batch_index: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        package = chapter_note_correction_prompt_service.build_chapter_note_correction_scoped_package(
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=mode,
            section_id=section_id,
            batch_size=batch_size,
            batch_index=batch_index,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return chapter_note_correction_prompt_service.build_chapter_note_correction_package_preview_response(
        package,
        document_id=document_id,
        chapter_id=chapter_id,
    )


@router.post("/books/{document_id}/chapters/{chapter_id}/note-correction-review/validate")
def validate_book_chapter_note_correction_review(
    document_id: int,
    chapter_id: int,
    request: NoteCorrectionReviewValidateRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_note_correction_prompt_service.validate_chapter_note_correction_review(
            document_id=document_id,
            chapter_id=chapter_id,
            review_payload=review_payload,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safety = chapter_note_correction_prompt_service.note_correction_dry_run_safety_flags()
    return {
        **payload,
        "implementation_status": "connected",
        "safety_flags": {**payload.get("safety_flags", {}), **safety},
        **safety,
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-correction-review/validate-section")
def validate_book_chapter_note_correction_section_review(
    document_id: int,
    chapter_id: int,
    request: NoteCorrectionSectionReviewValidateRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_note_correction_prompt_service.validate_chapter_note_correction_section_review(
            document_id=document_id,
            chapter_id=chapter_id,
            section_id=request.section_id,
            review_payload=review_payload,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/note-correction-review/validate-batch")
def validate_book_chapter_note_correction_batch_review(
    document_id: int,
    chapter_id: int,
    request: NoteCorrectionBatchReviewValidateRequest,
) -> dict[str, Any]:
    review_payload: str | dict[str, Any]
    if request.review_json is not None:
        review_payload = request.review_json
    else:
        review_payload = request.json_text or ""
    try:
        payload = chapter_note_correction_prompt_service.validate_chapter_note_correction_batch_review(
            document_id=document_id,
            chapter_id=chapter_id,
            batch_size=request.batch_size,
            batch_index=request.batch_index,
            review_payload=review_payload,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-correction-review/save-readiness")
def get_book_chapter_note_correction_review_save_readiness(document_id: int, chapter_id: int) -> dict[str, Any]:
    preflight = chapter_review_pipeline_service.build_note_correction_review_production_canary_preflight(
        document_id=document_id,
        chapter_id=chapter_id,
    )
    return {
        **chapter_review_pipeline_service.build_note_correction_review_save_readiness(),
        "production_canary_preflight": preflight,
        "implementation_status": "connected",
        "document_id": document_id,
        "chapter_id": chapter_id,
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/note-correction-review/saved-state")
def get_book_chapter_note_correction_review_saved_state(
    document_id: int,
    chapter_id: int,
    review_mode: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        payload = chapter_workspace_state_service.build_chapter_saved_review_state(
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=review_mode,
            scope_id=scope_id,
        )
    except chapter_workspace_state_service.ChapterWorkspaceStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
        "document_id": document_id,
        "chapter_id": chapter_id,
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/workspace-state")
def get_book_chapter_workspace_state(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_workspace_state_service.build_chapter_workspace_state(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_workspace_state_service.ChapterWorkspaceStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/workspace-search")
def search_book_chapter_workspace(
    document_id: int,
    chapter_id: int,
    q: str = Query(default=""),
    limit: int = Query(default=6, ge=1, le=20),
) -> dict[str, Any]:
    try:
        payload = chapter_workspace_search_service.build_chapter_workspace_search(
            document_id=document_id,
            chapter_id=chapter_id,
            query=q,
            limit_per_layer=limit,
        )
    except chapter_workspace_search_service.ChapterWorkspaceSearchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }
