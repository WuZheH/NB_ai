from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import BookChapter, Document, KnowledgeChunk, ObjectCandidate
from app.services.book_import_contract import (
    BookChapterContract,
    calculate_book_object_import_progress,
    select_next_chapter,
)
from app.services.unit_note_object_processing_service import note_processing_summary


class BookChapterSchemaUnavailable(RuntimeError):
    pass


class BookDocumentNotFound(LookupError):
    pass


class NotBookDocument(ValueError):
    pass


def list_book_chapters(document_id: int, session: Session | None = None) -> list[dict[str, Any]]:
    if session is not None:
        return _list_book_chapters_with_session(session, document_id)
    with SessionLocal() as owned_session:
        return _list_book_chapters_with_session(owned_session, document_id)


def get_book_object_import_progress(document_id: int, session: Session | None = None) -> dict[str, Any]:
    chapters = list_book_chapters(document_id, session=session)
    contracts = [_chapter_contract(chapter) for chapter in chapters]
    progress = calculate_book_object_import_progress(contracts)
    progress["next_chapter"] = _chapter_payload(progress["next_chapter"]) if progress["next_chapter"] else None
    return progress


def get_next_object_import_chapter(document_id: int, session: Session | None = None) -> dict[str, Any]:
    chapters = list_book_chapters(document_id, session=session)
    next_chapter = select_next_chapter(_chapter_contract(chapter) for chapter in chapters)
    if next_chapter is None:
        return {
            "status": "ok",
            "done": True,
            "reason": "all_chapters_committed_or_skipped",
            "chapter": None,
        }
    return {
        "status": "ok",
        "done": False,
        "reason": "next_chapter_available",
        "chapter": _chapter_payload(next_chapter),
    }


def build_book_detail_payload(document_id: int, session: Session | None = None) -> dict[str, Any]:
    if session is not None:
        return _build_book_detail_payload_with_session(session, document_id)
    with SessionLocal() as owned_session:
        return _build_book_detail_payload_with_session(owned_session, document_id)


def _build_book_detail_payload_with_session(session: Session, document_id: int) -> dict[str, Any]:
    _ensure_book_schema(session)
    document_row = session.execute(
        select(
            Document.id,
            Document.title,
            Document.document_type,
            Document.object_import_mode,
            Document.object_import_status,
        ).where(Document.id == document_id)
    ).one_or_none()
    if document_row is None:
        raise BookDocumentNotFound(f"document not found: {document_id}")

    document = document_row._mapping
    if document["object_import_mode"] != "chaptered":
        raise NotBookDocument(f"document {document_id} is not a chaptered document")

    chapters = _list_book_chapters_with_session(session, document_id)
    return {
        "document_id": document["id"],
        "title": document["title"],
        "document_type": document["document_type"],
        "object_import_mode": document["object_import_mode"],
        "object_import_status": document["object_import_status"],
        "object_import_progress": get_book_object_import_progress(document_id, session=session),
        "chapters": chapters,
    }


def _list_book_chapters_with_session(session: Session, document_id: int) -> list[dict[str, Any]]:
    _ensure_book_schema(session)
    chapters = list(
        session.scalars(
            select(BookChapter)
            .where(BookChapter.document_id == document_id)
            .order_by(BookChapter.chapter_index, BookChapter.id)
        )
    )
    object_counts = _count_by_chapter(session, ObjectCandidate)
    evidence_counts = _count_by_chapter(session, KnowledgeChunk)
    note_counts = _count_notes_by_chapter_page_range(session, document_id, chapters)
    native_note_summaries = _note_role_summaries_by_chapter_page_range(
        session,
        document_id,
        chapters,
        source="zotero_native_annotation",
    )
    return [
        {
            "chapter_id": chapter.id,
            "chapter_index": chapter.chapter_index,
            "title": chapter.title,
            "heading_path": chapter.heading_path,
            "pdf_page_start": chapter.pdf_page_start,
            "pdf_page_end": chapter.pdf_page_end,
            "object_import_status": chapter.object_import_status,
            "object_bundle_job_id": chapter.object_bundle_job_id,
            "object_committed_at": chapter.object_committed_at,
            "object_count": object_counts.get(chapter.id, 0),
            "evidence_count": evidence_counts.get(chapter.id, 0),
            "note_count": note_counts.get(chapter.id, 0),
            "synced_note_count": native_note_summaries.get(chapter.id, {}).get("annotation_count", 0),
            **_chapter_note_summary(native_note_summaries.get(chapter.id)),
        }
        for chapter in chapters
    ]


def _count_by_chapter(session: Session, model: Any) -> dict[int, int]:
    inspector = inspect(session.bind)
    if not inspector.has_table(model.__tablename__):
        return {}
    column_names = {column["name"] for column in inspector.get_columns(model.__tablename__)}
    if "chapter_id" not in column_names:
        return {}
    rows = session.execute(
        select(model.chapter_id, func.count(model.id))
        .where(model.chapter_id.is_not(None))
        .group_by(model.chapter_id)
    )
    return {int(chapter_id): int(count) for chapter_id, count in rows if chapter_id is not None}


def _count_notes_by_chapter_page_range(
    session: Session,
    document_id: int,
    chapters: list[BookChapter],
    *,
    source: str | None = None,
) -> dict[int, int]:
    inspector = inspect(session.bind)
    if not inspector.has_table("zotero_inspiration_notes"):
        return {}
    column_names = {column["name"] for column in inspector.get_columns("zotero_inspiration_notes")}
    required = {"id", "matched_document_id", "pdf_page"}
    if not required.issubset(column_names):
        return {}

    counts: dict[int, int] = {}
    for chapter in chapters:
        if chapter.pdf_page_start is None or chapter.pdf_page_end is None:
            continue
        params: dict[str, Any] = {
            "document_id": document_id,
            "page_start": int(chapter.pdf_page_start),
            "page_end": int(chapter.pdf_page_end),
        }
        source_clause = ""
        if source and "source" in column_names:
            source_clause = " AND source = :source"
            params["source"] = source
        count = session.execute(
            text(
                """
                SELECT COUNT(id)
                FROM zotero_inspiration_notes
                WHERE matched_document_id = :document_id
                  AND pdf_page BETWEEN :page_start AND :page_end
                """
                + source_clause
            ),
            params,
        ).scalar_one()
        counts[int(chapter.id)] = int(count or 0)
    return counts


def _note_role_summaries_by_chapter_page_range(
    session: Session,
    document_id: int,
    chapters: list[BookChapter],
    *,
    source: str | None = None,
) -> dict[int, dict[str, int]]:
    inspector = inspect(session.bind)
    if not inspector.has_table("zotero_inspiration_notes"):
        return {}
    column_names = {column["name"] for column in inspector.get_columns("zotero_inspiration_notes")}
    required = {"id", "matched_document_id", "pdf_page"}
    if not required.issubset(column_names):
        return {}

    selected = [
        name
        for name in ["id", "source", "selected_text", "note_text", "alignment_warnings_json"]
        if name in column_names
    ]
    if "id" not in selected:
        selected.insert(0, "id")

    summaries: dict[int, dict[str, int]] = {}
    for chapter in chapters:
        if chapter.pdf_page_start is None or chapter.pdf_page_end is None:
            continue
        params: dict[str, Any] = {
            "document_id": document_id,
            "page_start": int(chapter.pdf_page_start),
            "page_end": int(chapter.pdf_page_end),
        }
        source_clause = ""
        if source and "source" in column_names:
            source_clause = " AND source = :source"
            params["source"] = source
        rows = session.execute(
            text(
                f"""
                SELECT {', '.join(selected)}
                FROM zotero_inspiration_notes
                WHERE matched_document_id = :document_id
                  AND pdf_page BETWEEN :page_start AND :page_end
                """
                + source_clause
            ),
            params,
        ).mappings().all()
        summaries[int(chapter.id)] = note_processing_summary([dict(row) for row in rows])
    return summaries


def _chapter_note_summary(summary: dict[str, int] | None) -> dict[str, int]:
    if summary is None:
        return {
            "annotation_count": 0,
            "user_note_count": 0,
            "evidence_only_count": 0,
            "correction_review_eligible_count": 0,
            "classification_review_eligible_count": 0,
        }
    return {
        "annotation_count": int(summary.get("annotation_count") or 0),
        "user_note_count": int(summary.get("user_note_count") or 0),
        "evidence_only_count": int(summary.get("evidence_only_count") or 0),
        "correction_review_eligible_count": int(summary.get("correction_review_eligible_count") or 0),
        "classification_review_eligible_count": int(summary.get("classification_review_eligible_count") or 0),
    }


def _ensure_book_schema(session: Session) -> None:
    try:
        inspector = inspect(session.bind)
        if not inspector.has_table("book_chapters"):
            raise BookChapterSchemaUnavailable("book_chapters table is missing")
    except OperationalError as exc:
        raise BookChapterSchemaUnavailable(str(exc)) from exc


def _chapter_contract(chapter: dict[str, Any]) -> BookChapterContract:
    return BookChapterContract(
        chapter_id=chapter["chapter_id"],
        chapter_index=chapter["chapter_index"],
        title=chapter["title"],
        object_import_status=chapter["object_import_status"],
        heading_path=chapter.get("heading_path"),
        pdf_page_start=chapter.get("pdf_page_start"),
        pdf_page_end=chapter.get("pdf_page_end"),
        object_count=int(chapter.get("object_count") or 0),
        evidence_count=int(chapter.get("evidence_count") or 0),
    )


def _chapter_payload(chapter: BookChapterContract | dict[str, Any]) -> dict[str, Any]:
    if isinstance(chapter, BookChapterContract):
        return chapter.to_dict()
    return dict(chapter)
