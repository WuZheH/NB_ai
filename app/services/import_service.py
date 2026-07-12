from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import PROJECT_ROOT
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk, MarkdownNode
from app.services.chunk_splitter import TextChunk, split_nodes
from app.services.markdown_parser import ParsedMarkdownNode, parse_markdown_file


@dataclass(frozen=True)
class ImportResult:
    document_id: int
    nodes_created: int
    nodes_updated: int
    chunks_created: int
    chunks_updated: int
    chunks_unchanged: int


def import_markdown_file(
    path: str | Path,
    document_type: str | None = None,
    content_layer: str | None = None,
    read_status: str | None = None,
    pdf_path: str | None = None,
) -> ImportResult:
    init_db()
    markdown_path = Path(path)
    source_path = _normalize_source_path(markdown_path)
    parsed = parse_markdown_file(markdown_path)
    effective_pdf_path = pdf_path or parsed.pdf_path
    chunks = split_nodes(parsed.nodes)

    with SessionLocal() as session:
        document = _get_or_create_document(
            session=session,
            title=parsed.title,
            source_path=source_path,
            pdf_path=effective_pdf_path,
            document_type=document_type,
            content_layer=content_layer,
            read_status=read_status,
        )
        result = _upsert_nodes_and_chunks(session, document, parsed.nodes, chunks)
        session.commit()
        return result


def list_documents() -> list[Document]:
    with SessionLocal() as session:
        return list(session.scalars(select(Document).order_by(Document.id)).all())


def list_chunks(document_id: int) -> list[KnowledgeChunk]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.node_id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            ).all()
        )


def _get_or_create_document(
    session: Session,
    title: str,
    source_path: str,
    pdf_path: str | None,
    document_type: str | None = None,
    content_layer: str | None = None,
    read_status: str | None = None,
) -> Document:
    document = session.scalar(select(Document).where(Document.source_path == source_path))
    effective_document_type = document_type or _guess_document_type(source_path)
    effective_content_layer = content_layer or ("converted_source" if pdf_path else "personal_note")
    effective_read_status = read_status or "read"
    if document is None:
        document = Document(
            title=title,
            document_type=effective_document_type,
            content_layer=effective_content_layer,
            source_path=source_path,
            pdf_path=pdf_path,
            read_status=effective_read_status,
        )
        session.add(document)
        session.flush()
        return document

    document.title = title
    document.document_type = effective_document_type
    document.content_layer = effective_content_layer
    document.pdf_path = pdf_path
    document.read_status = effective_read_status
    document.updated_at = datetime.utcnow()
    return document


def _upsert_nodes_and_chunks(
    session: Session,
    document: Document,
    parsed_nodes: list[ParsedMarkdownNode],
    chunks: list[TextChunk],
) -> ImportResult:
    nodes_created = 0
    nodes_updated = 0
    node_map: dict[int, MarkdownNode] = {}

    for parsed_node in parsed_nodes:
        node = session.scalar(
            select(MarkdownNode).where(
                MarkdownNode.document_id == document.id,
                MarkdownNode.order_index == parsed_node.order_index,
            )
        )
        if node is None:
            node = MarkdownNode(
                document_id=document.id,
                parent_id=None,
                heading_level=parsed_node.heading_level,
                heading_title=parsed_node.heading_title,
                heading_path=parsed_node.heading_path,
                order_index=parsed_node.order_index,
                raw_content=parsed_node.raw_content,
            )
            session.add(node)
            session.flush()
            nodes_created += 1
        else:
            node.heading_level = parsed_node.heading_level
            node.heading_title = parsed_node.heading_title
            node.heading_path = parsed_node.heading_path
            node.raw_content = parsed_node.raw_content
            node.updated_at = datetime.utcnow()
            nodes_updated += 1
        node_map[parsed_node.order_index] = node

    for parsed_node in parsed_nodes:
        node = node_map[parsed_node.order_index]
        if parsed_node.parent_order_index is not None:
            parent = node_map.get(parsed_node.parent_order_index)
            node.parent_id = parent.id if parent else None
        else:
            node.parent_id = None

    chunks_created = 0
    chunks_updated = 0
    chunks_unchanged = 0
    for text_chunk in chunks:
        node = node_map[text_chunk.node_order_index]
        content_hash = _chunk_hash(text_chunk.chunk_text)
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document.id,
                KnowledgeChunk.node_id == node.id,
                KnowledgeChunk.chunk_index == text_chunk.chunk_index,
            )
        )
        if chunk is None:
            session.add(_build_chunk(document.id, node.id, text_chunk, content_hash))
            chunks_created += 1
            continue

        if chunk.content_hash == content_hash:
            chunks_unchanged += 1
            continue

        _update_chunk(chunk, text_chunk, content_hash)
        chunks_updated += 1

    return ImportResult(
        document_id=document.id,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        chunks_created=chunks_created,
        chunks_updated=chunks_updated,
        chunks_unchanged=chunks_unchanged,
    )


def _build_chunk(document_id: int, node_id: int, text_chunk: TextChunk, content_hash: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        document_id=document_id,
        node_id=node_id,
        chunk_index=text_chunk.chunk_index,
        heading_path=text_chunk.heading_path,
        chunk_text=text_chunk.chunk_text,
        char_count=text_chunk.char_count,
        token_count=text_chunk.token_count,
        overlap_before=text_chunk.overlap_before,
        overlap_after=text_chunk.overlap_after,
        content_hash=content_hash,
        pdf_path=text_chunk.pdf_path,
        pdf_page_start=text_chunk.pdf_page_start,
        pdf_page_end=text_chunk.pdf_page_end,
    )


def _update_chunk(chunk: KnowledgeChunk, text_chunk: TextChunk, content_hash: str) -> None:
    chunk.heading_path = text_chunk.heading_path
    chunk.chunk_text = text_chunk.chunk_text
    chunk.char_count = text_chunk.char_count
    chunk.token_count = text_chunk.token_count
    chunk.overlap_before = text_chunk.overlap_before
    chunk.overlap_after = text_chunk.overlap_after
    chunk.content_hash = content_hash
    chunk.pdf_path = text_chunk.pdf_path
    chunk.pdf_page_start = text_chunk.pdf_page_start
    chunk.pdf_page_end = text_chunk.pdf_page_end
    chunk.updated_at = datetime.utcnow()


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _guess_document_type(source_path: str) -> str:
    name = Path(source_path).name.lower()
    if "chapter" in name or "book" in name:
        return "chapter"
    if "experiment" in name:
        return "experiment"
    if "meeting" in name:
        return "meeting"
    if "code" in name:
        return "code"
    return "paper"


def _normalize_source_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)
