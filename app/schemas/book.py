from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BookChapterRead(BaseModel):
    chapter_id: int
    chapter_index: int
    title: str
    heading_path: str | None = None
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    object_import_status: str
    object_bundle_job_id: str | None = None
    object_committed_at: datetime | None = None
    object_count: int = 0
    evidence_count: int = 0


class BookChapterProgress(BaseModel):
    total_count: int
    completed_count: int
    committed_count: int
    skipped_count: int
    not_started_count: int
    bundle_generated_count: int
    json_pasted_count: int
    done: bool
    next_chapter: BookChapterRead | None = None


class BookDetailRead(BaseModel):
    document_id: int
    title: str
    document_type: str
    object_import_mode: str | None = None
    object_import_status: str | None = None
    object_import_progress: BookChapterProgress
    chapters: list[BookChapterRead]


class BookNextObjectImportChapterRead(BaseModel):
    status: str
    done: bool
    reason: str
    chapter: BookChapterRead | None = None
