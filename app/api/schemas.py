from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.paths import MODEL_CACHE_ROOT


class ResearchSessionDryRunRequest(BaseModel):
    research_goal: str = Field(..., min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)


class ReviewQueueBuildDryRunRequest(BaseModel):
    research_session_output: dict[str, Any]


class ReviewDecisionApplyDryRunRequest(BaseModel):
    review_queue: dict[str, Any]
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class PatchPreflightRequest(BaseModel):
    patch_plan: dict[str, Any]


class SandboxRehearsalRequest(BaseModel):
    patch_plan: dict[str, Any]
    preflight_package: dict[str, Any]


class ImportPreviewRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    pdf_path: str | None = None
    converted_md_path: str | None = None
    markdown_path: str | None = None
    marker_output_path: str | None = None
    title_hint: str | None = None
    notes_payload: str | None = None
    zotero_pdf_source_id: int | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None


class ImportPreviewNoteRequest(BaseModel):
    raw_note: str | None = None
    user_judgement: str | None = None
    zotero_annotation: dict[str, Any] | None = None


class PdfToMarkdownConvertRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    title: str | None = None


class PdfTextLayerPreviewRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    title: str | None = None
    max_pages: int = Field(default=4, ge=1, le=8)
    max_chars: int = Field(default=4000, ge=500, le=12000)


class ImportDuplicateCheckRequest(BaseModel):
    pdf_path: str | None = None
    title: str | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None


class AiSuggestionsUploadRequest(BaseModel):
    schema_version: str = Field(..., min_length=1)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str | None = None


class ReviewedObjectsUploadRequest(BaseModel):
    schema_version: str = Field(..., min_length=1)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    reviewed_by: str | None = None


class BookChapterObjectBundleRequest(BaseModel):
    dry_run: bool = True


class BookChapterObjectsPreviewRequest(BaseModel):
    json_text: str = Field(..., min_length=1)


class NoteCorrectionReviewValidateRequest(BaseModel):
    json_text: str | None = Field(default=None, min_length=1)
    review_json: dict[str, Any] | None = None


class NoteCorrectionSectionReviewValidateRequest(BaseModel):
    json_text: str | None = Field(default=None, min_length=1)
    review_json: dict[str, Any] | None = None
    section_id: str = Field(..., min_length=1)


class NoteCorrectionBatchReviewValidateRequest(BaseModel):
    json_text: str | None = Field(default=None, min_length=1)
    review_json: dict[str, Any] | None = None
    batch_size: int = 15
    batch_index: int = 0


class NoteCorrectionReviewSaveRequest(BaseModel):
    json_text: str | None = Field(default=None, min_length=1)
    review_json: dict[str, Any] | None = None
    normalized_review_json: dict[str, Any] | None = None
    human_audit_items: list[dict[str, Any]] = Field(default_factory=list)
    merge_preview: dict[str, Any] | None = None
    source_package_hash: str | None = None
    review_mode: str = "full_chapter"
    scope_id: str | None = None
    batch_size: int | None = None
    batch_index: int | None = None
    parent_review_mode: str | None = None
    parent_scope_id: str | None = None
    selected_server_note_ids: list[str] = Field(default_factory=list)
    selected_note_ids: list[str] = Field(default_factory=list)
    canary_subscope: bool = False
    supersede_existing: bool = False
    confirm_write: bool = False
    confirmation_context: str | None = None


class ChapterZoteroNotesApplyRequest(BaseModel):
    confirm_write: bool = False
    confirmation_context: str | None = None
    document_id: int | None = None
    chapter_id: int | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    expected_would_insert_count: int | None = Field(default=None, ge=0)


class NoteClassificationReviewValidateRequest(BaseModel):
    json_text: str | None = Field(default=None, min_length=1)
    review_json: dict[str, Any] | None = None


class BookChapterObjectsCommitRequest(BaseModel):
    json_text: str = Field(..., min_length=1)
    confirm_chapter_id: int
    confirm_write: bool = False
    confirmation_context: str | None = None


class CommitConfirmationRequest(BaseModel):
    confirm_write: bool = False
    confirmation_context: str | None = None


class BookCommitConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_write: bool = False
    confirmation_context: str | None = None


class PdfImportClassifyRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    zotero_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_pdf_source_id: int | None = None
    source: str = Field(default="local", min_length=1)
    zotero_metadata: dict[str, Any] | None = None


class PdfImportCommitRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    document_type: str = Field(..., min_length=1)
    object_import_mode: str = Field(..., min_length=1)
    backend: str = Field(default="pymupdf_text", min_length=1)
    confirm_title: str | None = None
    confirm_page_count: int | None = None
    source: str = Field(default="local", min_length=1)
    zotero_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_pdf_source_id: int | None = None


class ChapteredPdfImportJobRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    document_type: str = Field(..., min_length=1)
    object_import_mode: str = Field(..., min_length=1)
    backend: str = Field(default="marker_surya_page_blocks", min_length=1)
    confirm_title: str | None = None
    confirm_page_count: int | None = None
    confirm_chapter_count: int | None = None
    import_granularity: str | None = None
    selected_chapter_indexes: list[int] | None = None
    source: str = Field(default="local", min_length=1)
    zotero_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_pdf_source_id: int | None = None


class ChapteredPdfImportPreviewRequest(BaseModel):
    pdf_path: str = Field(..., min_length=1)
    source: str = Field(default="local", min_length=1)
    zotero_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_pdf_source_id: int | None = None
    document_type: str = Field(default="book", min_length=1)
    object_import_mode: str = Field(default="chaptered", min_length=1)
    backend: str = Field(default="marker_surya_page_blocks", min_length=1)


class PdfImportPreviewGateRequest(BaseModel):
    pdf_path: str | None = None
    zotero_attachment_path: str | None = None
    document_id: int | None = None
    sample_strategy: str = Field(default="first_chapter_first_section_two_pages", min_length=1)
    max_pages: int = Field(default=2, ge=1, le=2)


class PdfRepairPreviewRequest(BaseModel):
    preview_token: str = Field(..., min_length=1)
    sample_pages: list[int] = Field(..., min_length=1, max_length=2)
    max_pages: int = Field(default=2, ge=1, le=2)
    device: str = Field(default="auto", min_length=1)
    model_cache_root: str = Field(default=str(MODEL_CACHE_ROOT), min_length=1)


class PdfRepairPlanRequest(BaseModel):
    repair_preview_result: dict[str, Any]


def safety_fields(**overrides: Any) -> dict[str, Any]:
    value = {
        "production_write_enabled": False,
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
        "final_hypothesis_created": False,
    }
    value.update(overrides)
    if "core_db_write_performed" not in overrides and "db_write_performed" in overrides:
        value["core_db_write_performed"] = bool(value["db_write_performed"])
    if "external_llm_called" not in overrides and "llm_called" in overrides:
        value["external_llm_called"] = bool(value["llm_called"])
    if "llm_called" not in overrides and "external_llm_called" in overrides:
        value["llm_called"] = bool(value["external_llm_called"])
    if "final_hypothesis_created" not in overrides and "mechanism_generated" in overrides:
        value["final_hypothesis_created"] = bool(value["mechanism_generated"])
    return value
