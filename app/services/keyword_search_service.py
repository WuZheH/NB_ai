from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk
from app.services.search_helpers import load_chunk_tags, load_related_note_titles, make_snippet


DEFAULT_SNIPPET_CHARS = 160
DEFAULT_LIMIT = 10
PDF_SERVICE_PREFIX_ENV = "NOTEBOOK_AI_PDF_SERVICE_PREFIX"


@dataclass(frozen=True)
class KeywordSearchResult:
    document_id: int
    document_title: str
    document_type: str
    content_layer: str
    heading_path: str
    chunk_id: int
    chunk_text_snippet: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    related_note_titles: list[str]
    chunk_tags: list[str]


def search_keywords(
    query: str,
    document_type: str | None = None,
    content_layer: str | None = None,
    research_direction: str | None = None,
    read_status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[KeywordSearchResult]:
    init_db()
    normalized_query = query.strip()
    if not normalized_query:
        return []

    safe_limit = max(1, limit)
    with SessionLocal() as session:
        rows = _query_chunks(
            session=session,
            query=normalized_query,
            document_type=document_type,
            content_layer=content_layer,
            research_direction=research_direction,
            read_status=read_status,
            limit=safe_limit,
        )
        chunk_ids = [chunk.id for _, chunk in rows]
        related_note_titles = load_related_note_titles(session, chunk_ids)
        chunk_tags = load_chunk_tags(session, chunk_ids)

        return [
            KeywordSearchResult(
                document_id=document.id,
                document_title=document.title,
                document_type=document.document_type,
                content_layer=document.content_layer,
                heading_path=chunk.heading_path,
                chunk_id=chunk.id,
                chunk_text_snippet=make_snippet(chunk.chunk_text, normalized_query, snippet_chars),
                pdf_path=chunk.pdf_path or document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
                pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                zotero_open_url=chunk.zotero_open_url,
                related_note_titles=related_note_titles.get(chunk.id, []),
                chunk_tags=chunk_tags.get(chunk.id, []),
            )
            for document, chunk in rows
        ]


def build_pdf_open_url(pdf_path: str | None, pdf_page_start: int | None) -> str | None:
    if not pdf_path:
        return None

    page_suffix = f"#page={pdf_page_start}" if pdf_page_start is not None else ""
    service_prefix = os.environ.get(PDF_SERVICE_PREFIX_ENV, "").strip().rstrip("/")
    if service_prefix:
        file_name = Path(pdf_path).name
        return f"{service_prefix}/pdfs/{file_name}{page_suffix}"

    if pdf_page_start is not None:
        return f"{pdf_path} (page {pdf_page_start})"
    return pdf_path


def _query_chunks(
    session: Session,
    query: str,
    document_type: str | None,
    content_layer: str | None,
    research_direction: str | None,
    read_status: str | None,
    limit: int,
) -> list[tuple[Document, KnowledgeChunk]]:
    like_query = f"%{_escape_like(query)}%"
    statement = (
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(
            or_(
                KnowledgeChunk.chunk_text.like(like_query, escape="\\"),
                KnowledgeChunk.heading_path.like(like_query, escape="\\"),
                Document.title.like(like_query, escape="\\"),
            )
        )
        .order_by(Document.id, KnowledgeChunk.id)
        .limit(limit)
    )

    if document_type:
        statement = statement.where(Document.document_type == document_type)
    if content_layer:
        statement = statement.where(Document.content_layer == content_layer)
    if research_direction:
        statement = statement.where(Document.research_direction == research_direction)
    if read_status:
        statement = statement.where(Document.read_status == read_status)

    return list(session.execute(statement).all())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
