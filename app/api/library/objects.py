from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.get("/books/{document_id}/chapters/{chapter_id}/tri-source-object-package")
def get_book_chapter_tri_source_object_package(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_tri_source_object_package_preview(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/object-candidates/dry-run")
def get_book_chapter_object_candidates_dry_run(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_chapter_object_candidate_dry_run_package(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/object-candidates/review-workbench")
def get_book_chapter_object_candidates_review_workbench(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_object_candidate_human_review_workbench(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/relation-candidates/dry-run")
def get_book_chapter_relation_candidates_dry_run(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = chapter_review_pipeline_service.build_chapter_relation_candidate_dry_run_package(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/object-candidates/save-dry-run")
async def save_book_chapter_object_candidates_dry_run(
    document_id: int,
    chapter_id: int,
    request: Request,
) -> dict[str, Any]:
    try:
        body: Any = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        dry_run_package: Any = (
            body.get("dry_run_package")
            if "dry_run_package" in body
            else body.get("package")
            if "package" in body
            else body.get("object_candidate_dry_run_package")
            if "object_candidate_dry_run_package" in body
            else body.get("candidates")
            if "candidates" in body
            else body
        )
        confirm_write = bool(body.get("confirm_write"))
        confirmation_context = body.get("confirmation_context")
    else:
        dry_run_package = body
        confirm_write = False
        confirmation_context = None
    try:
        payload = chapter_review_pipeline_service.save_chapter_object_candidate_dry_run_drafts(
            document_id=document_id,
            chapter_id=chapter_id,
            dry_run_package=dry_run_package,
            confirm_write=confirm_write,
            confirmation_context=confirmation_context,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/object-candidates/validate-human-review")
async def validate_book_chapter_object_candidates_human_review(
    document_id: int,
    chapter_id: int,
    request: Request,
) -> dict[str, Any]:
    try:
        body: Any = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        review_payload: Any = (
            body.get("human_review")
            if "human_review" in body
            else body.get("review_payload")
            if "review_payload" in body
            else body.get("object_candidate_human_review")
            if "object_candidate_human_review" in body
            else body
        )
    else:
        review_payload = body
    try:
        payload = chapter_review_pipeline_service.validate_object_candidate_human_review_payload(
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


@router.post("/books/{document_id}/chapters/{chapter_id}/object-candidates/save-human-review")
async def save_book_chapter_object_candidates_human_review(
    document_id: int,
    chapter_id: int,
    request: Request,
) -> dict[str, Any]:
    try:
        body: Any = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        review_payload: Any = (
            body.get("human_review")
            if "human_review" in body
            else body.get("review_payload")
            if "review_payload" in body
            else body.get("object_candidate_human_review")
            if "object_candidate_human_review" in body
            else body
        )
        confirm_write = bool(body.get("confirm_write"))
        confirmation_context = body.get("confirmation_context")
    else:
        review_payload = body
        confirm_write = False
        confirmation_context = None
    try:
        payload = chapter_review_pipeline_service.save_object_candidate_human_review(
            document_id=document_id,
            chapter_id=chapter_id,
            review_payload=review_payload,
            confirm_write=confirm_write,
            confirmation_context=confirmation_context,
        )
    except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected",
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/object-bundle")
def generate_book_chapter_object_bundle(
    document_id: int,
    chapter_id: int,
    request: BookChapterObjectBundleRequest,
) -> dict[str, Any]:
    try:
        payload = book_object_import_service.generate_chapter_object_bundle(
            document_id,
            chapter_id,
            dry_run=request.dry_run,
        )
    except book_object_import_service.BookObjectImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/objects/preview")
def preview_book_chapter_objects(
    document_id: int,
    chapter_id: int,
    request: BookChapterObjectsPreviewRequest,
) -> dict[str, Any]:
    try:
        payload = book_object_import_service.preview_chapter_objects(
            document_id,
            chapter_id,
            request.json_text,
        )
    except book_object_import_service.BookObjectImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        **safety_fields(db_write_performed=False),
    }


@router.post("/books/{document_id}/chapters/{chapter_id}/objects/commit")
def commit_book_chapter_objects(
    document_id: int,
    chapter_id: int,
    request: BookChapterObjectsCommitRequest,
) -> dict[str, Any]:
    if request.confirm_write is not True:
        return {
            "status": "BLOCKED",
            "confirmation_required": True,
            "confirmation_received": False,
            "expected_confirmation_context": "commit_book_chapter_objects_after_review",
            "document_id": document_id,
            "chapter_id": chapter_id,
            **safety_fields(db_write_performed=False),
        }
    try:
        payload = book_object_import_service.commit_chapter_objects(
            document_id,
            chapter_id,
            request.json_text,
            confirm_chapter_id=request.confirm_chapter_id,
        )
    except book_object_import_service.BookObjectImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "confirmation_required": True,
        "confirmation_received": True,
        "confirmation_context": request.confirmation_context,
        **safety_fields(db_write_performed=True),
    }
