from __future__ import annotations

from typing import Any

from app.services import book_chapter_service, library_service


class WorkspaceReadError(LookupError):
    pass


READ_ONLY_SAFETY_FLAGS = {
    "db_write_performed": False,
    "core_db_write_performed": False,
    "llm_called": False,
    "external_llm_called": False,
    "relation_generated": False,
    "mechanism_generated": False,
    "zotero_write_performed": False,
    "vector_write_performed": False,
}


def build_workspace_state(*, document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        book = book_chapter_service.build_book_detail_payload(document_id)
    except (
        book_chapter_service.BookDocumentNotFound,
        book_chapter_service.NotBookDocument,
        book_chapter_service.BookChapterSchemaUnavailable,
    ) as exc:
        raise WorkspaceReadError(str(exc)) from exc

    chapter = next(
        (
            item
            for item in book.get("chapters") or []
            if int(item.get("chapter_id") or 0) == int(chapter_id)
        ),
        None,
    )
    if chapter is None:
        raise WorkspaceReadError(
            f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
        )

    try:
        document_detail = library_service.show_library_document(document_id)
    except Exception:
        document_detail = None

    evidence_count = _count(chapter, "evidence_count")
    note_count = _count(chapter, "note_count")
    user_note_count = _count(chapter, "user_note_count")
    evidence_only_count = _count(chapter, "evidence_only_count")
    pdf_path = _value(document_detail, "pdf_path") or _value(document_detail, "source_path")
    zotero_key = _value(document_detail, "zotero_key")
    safety_flags = dict(READ_ONLY_SAFETY_FLAGS)

    return {
        "status": "ok",
        "notebook_title": "Research Workspace",
        "document_title": book.get("title") or "",
        "chapter_title": chapter.get("title") or "",
        "source_count": 1,
        "document": {
            "document_id": int(book["document_id"]),
            "title": book.get("title") or "",
        },
        "current_chapter": {
            "chapter_id": int(chapter["chapter_id"]),
            "chapter_index": chapter.get("chapter_index"),
            "title": chapter.get("title") or "",
            "page_start": chapter.get("pdf_page_start"),
            "page_end": chapter.get("pdf_page_end"),
        },
        "source_ingestion_status": {
            "pdf_available": bool(pdf_path),
            "chunked": evidence_count > 0,
            "chunk_count": evidence_count,
            "zotero_source_available": bool(zotero_key),
        },
        "notes_import_status": {
            "status": "available" if note_count > 0 else "empty",
            "existing": note_count,
            "user_notes": user_note_count,
            "evidence_only": evidence_only_count,
        },
        "search_layer_availability": {
            "passages": "available" if evidence_count > 0 else "unavailable",
            "notes": "available" if note_count > 0 else "unavailable",
        },
        "safety_flags": safety_flags,
        **safety_flags,
    }


def _count(source: dict[str, Any], key: str) -> int:
    return max(0, int(source.get(key) or 0))


def _value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)
