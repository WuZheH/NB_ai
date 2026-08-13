from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.paths import DATA_DIR, PROJECT_ROOT
from app.db.session import SessionLocal
from app.domains.library.contracts import (
    METADATA_CHUNK_MARKERS,
    DocumentPdfSource,
    LibraryChunkPreview,
    LibraryDocumentDetail,
    LibraryEvidenceItem,
    LibraryGroupedSearchChunk,
    LibraryGroupedSearchDocument,
    LibraryHomeItem,
    LibraryLinkedChunkItem,
    LibraryNoteItem,
    LibraryNotePreview,
    LibraryRelatedNoteItem,
    LibraryRelationItem,
    LibrarySearchResult,
    ReadLibraryDocumentSummary,
    evidence_locator_contract,
    is_metadata_chunk,
    is_metadata_chunk_text,
    normalize_evidence_text,
)
from app.domains.library.documents import (
    SAFE_PDF_ROOTS,
    TEST_DATA_METADATA_MARKERS,
    TEST_DATA_PATH_MARKERS,
    TEST_DATA_PREFIXES,
    TEST_DATA_TITLE_MARKERS,
    _is_relative_to,
    _object_value,
    is_safe_pdf_path,
    is_test_library_record,
    resolve_safe_pdf_path,
)
from app.models import (
    BookChapter,
    ChunkTag,
    Document,
    KnowledgeChunk,
    KnowledgeRelation,
    KnowledgeTag,
    NoteEvidenceLink,
    NoteTag,
    PersonalNote,
)
from app.services.keyword_search_service import build_pdf_open_url
from app.services.search_helpers import load_chunk_tags, load_related_note_titles


READ_LIBRARY_DOCUMENT_TYPES = {"book", "chapter"}
READ_LIBRARY_STATUSES = {"read", "mastered"}
EVIDENCE_SNIPPET_CHARS = 160
TOP_HEADINGS_LIMIT = 10
RELATED_NOTES_LIMIT = 10
RELATED_RELATIONS_LIMIT = 10
LINKED_CHUNKS_LIMIT = 10
HOME_DOCUMENT_TYPES = {"book", "paper", "chapter", "experiment", "code", "meeting"}
HOME_NOTE_TYPES = {
    "paper_card",
    "chapter_card",
    "concept_card",
    "reading_note",
    "experiment_log",
    "code_reading",
    "meeting_note",
}
GROUPED_SEARCH_MODE = "hybrid_lexical_v1"
GROUPED_SEARCH_SNIPPET_CHARS = 220
QUERY_EXPANSIONS = {
    "创新点": ("novelty", "contribution", "method", "architecture", "design", "propose", "proposed"),
    "方法": ("method", "approach", "model"),
    "局限": ("limitation", "drawback", "failure", "problem"),
    "问题": ("problem", "limitation", "challenge"),
    "机制": ("mechanism", "module", "architecture", "design"),
    "消融": ("ablation",),
    "指标": ("metric", "psnr", "ssim", "fid"),
    "数据集": ("dataset", "benchmark", "div2k"),
    "超分": ("super-resolution", "image super-resolution", "sisr"),
    "超分辨率": ("super-resolution", "image super-resolution", "sisr"),
    "神经网络": (
        "neural network",
        "neural networks",
        "deep neural network",
        "deep neural networks",
        "network",
        "networks",
        "cnn",
        "convolutional neural network",
    ),
    "卷积核": ("convolution kernel", "kernel", "kernels", "convolution", "convolutional", "filter", "filters"),
    "卷积": ("convolution", "convolutional", "cnn"),
    "残差块": ("residual block", "residual blocks", "residual module", "residual modules", "residual learning"),
    "残差缩放": ("residual scaling",),
    "残差": ("residual", "residual learning", "residual block", "residual scaling"),
    "深度网络": ("deep network", "deep neural network", "very deep network"),
    "图像恢复": ("image restoration", "restoration"),
    "纹理": ("texture", "detail", "high-frequency", "texture recovery"),
    "扩散模型": ("diffusion model", "diffusion"),
    "人体动作": ("human motion", "motion generation"),
    "文本到动作": ("text-to-motion", "text driven motion", "text2motion"),
    "物理合理性": (
        "physical plausibility",
        "physics",
        "penetration",
        "floating",
        "foot sliding",
        "ground contact",
    ),
    "时序一致性": ("temporal consistency", "temporal coherence"),
    "edsr": (
        "enhanced deep residual",
        "residual scaling",
        "remove unnecessary modules",
        "single image super-resolution",
        "super-resolution",
        "div2k",
        "psnr",
        "ssim",
    ),
    "mdm": ("motion diffusion", "text-to-motion", "human motion diffusion"),
    "physdiff": (
        "physics-guided",
        "physical plausibility",
        "floating",
        "foot sliding",
        "ground penetration",
    ),
    "residual scaling": ("residual block", "stability", "deep network"),
    "foot sliding": ("physical plausibility", "ground contact"),
    "temporal consistency": ("temporal coherence", "time", "sequence"),
}


def get_library_home(
    item_type: str | None = None,
    document_type: str | None = None,
    research_direction: str | None = None,
    limit: int = 50,
) -> list[LibraryHomeItem]:
    safe_limit = max(1, limit)
    with SessionLocal() as session:
        items: list[LibraryHomeItem] = []
        if item_type in (None, "document"):
            items.extend(
                _load_home_document_items(
                    session=session,
                    document_type=document_type,
                    research_direction=research_direction,
                    limit=safe_limit,
                )
            )
        if item_type in (None, "note") and document_type is None:
            items.extend(
                _load_home_note_items(
                    session=session,
                    research_direction=research_direction,
                    limit=safe_limit,
                )
            )
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[:safe_limit]


def search_library(query: str, limit: int = 10, top_k: int | None = None) -> list[LibrarySearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty.")

    safe_limit = max(1, top_k if top_k is not None else limit)
    like_query = f"%{_escape_like(normalized_query)}%"
    with SessionLocal() as session:
        results: list[LibrarySearchResult] = []
        results.extend(_search_documents(session, like_query, normalized_query, safe_limit))
        results.extend(_search_chunks(session, like_query, normalized_query, safe_limit))
        results.extend(_search_notes(session, like_query, normalized_query, safe_limit))
        results.extend(_search_tags(session, like_query, normalized_query, safe_limit))
        results.extend(_search_relations(session, like_query, normalized_query, safe_limit))
        return results[:safe_limit]


def search_library_grouped(
    query: str,
    limit_documents: int = 5,
    limit_chunks_per_document: int = 5,
) -> list[LibraryGroupedSearchDocument]:
    normalized_query = normalize_grouped_search_query(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")

    safe_document_limit = max(1, min(limit_documents, 20))
    safe_chunk_limit = max(1, min(limit_chunks_per_document, 20))
    query_plan = _build_grouped_query_plan(normalized_query)

    with SessionLocal() as session:
        documents = session.scalars(
            select(Document)
            .where(Document.read_status.in_(READ_LIBRARY_STATUSES))
            .order_by(Document.updated_at.desc(), Document.id.desc())
        ).all()
        if not documents:
            return []

        document_by_id = {document.id: document for document in documents}
        document_ids = list(document_by_id)
        rows = session.execute(
            select(Document, KnowledgeChunk)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .where(KnowledgeChunk.document_id.in_(document_ids))
            .order_by(Document.id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()
        chunk_ids = [chunk.id for _, chunk in rows]
        chunk_tags = load_chunk_tags(session, chunk_ids)
        related_note_titles = load_related_note_titles(session, chunk_ids)
        document_tags = {document.id: _load_document_tag_labels(session, document.id) for document in documents}
        relation_text_by_chunk = _load_grouped_relation_text_by_chunk(session, chunk_ids)
        relation_reasons_by_chunk = {
            chunk_id: ["关系命中"] for chunk_id, text in relation_text_by_chunk.items() if text
        }

        document_context: dict[int, tuple[float, list[str]]] = {}
        for document in documents:
            tag_text = " ".join(document_tags.get(document.id, []))
            context_text = " ".join([document.title, document.research_direction or "", tag_text])
            document_context[document.id] = _score_grouped_text(
                context_text,
                query_plan,
                phrase_reason="标题命中",
                token_reason="标题命中",
                expanded_reason="扩展词命中",
            )

        chunks_by_document: dict[int, list[LibraryGroupedSearchChunk]] = {document_id: [] for document_id in document_ids}
        for document, chunk in rows:
            if is_metadata_chunk(chunk):
                continue
            tags = chunk_tags.get(chunk.id, [])
            relation_text = relation_text_by_chunk.get(chunk.id, "")
            text_score, text_reasons = _score_grouped_text(
                " ".join([chunk.heading_path, chunk.chunk_text]),
                query_plan,
                phrase_reason="正文命中",
                token_reason="正文命中",
                expanded_reason="扩展词命中",
                field="body",
            )
            tag_score, tag_reasons = _score_grouped_text(
                " ".join(tags),
                query_plan,
                phrase_reason="标签命中",
                token_reason="标签命中",
                expanded_reason="标签扩展词命中",
                weight_scale=0.75,
                field="tag",
            )
            relation_score, relation_reasons = _score_grouped_text(
                relation_text,
                query_plan,
                phrase_reason="关系命中",
                token_reason="关系命中",
                expanded_reason="关系扩展词命中",
                weight_scale=0.65,
                field="relation",
            )
            tag_score = min(tag_score, 1.0)
            relation_score = min(relation_score, 0.65)
            document_score, document_reasons = document_context.get(document.id, (0.0, []))
            section_metadata = _chunk_section_metadata(document, chunk)
            section_score, section_reasons = _section_relevance_adjustment(section_metadata, query_plan)
            raw_score = text_score + tag_score + relation_score + (document_score * 0.25) + section_score
            if raw_score < 0.8:
                continue

            reasons = _ordered_unique(
                text_reasons
                + tag_reasons
                + relation_reasons
                + section_reasons
                + relation_reasons_by_chunk.get(chunk.id, [])
                + document_reasons
            )
            relevance_score = _normalize_relevance_score(raw_score)
            locator_contract = evidence_locator_contract(
                chunk_text=chunk.chunk_text,
                pdf_page_start=chunk.pdf_page_start,
                pdf_path=chunk.pdf_path or document.pdf_path,
                is_metadata=False,
            )
            chunks_by_document[document.id].append(
                LibraryGroupedSearchChunk(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    heading_path=chunk.heading_path,
                    pdf_path=chunk.pdf_path or document.pdf_path,
                    pdf_page_start=chunk.pdf_page_start,
                    pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                    zotero_open_url=chunk.zotero_open_url,
                    section_path=section_metadata["section_path"],
                    section_label=section_metadata["section_label"],
                    location_label=section_metadata["location_label"],
                    heading_level=section_metadata["heading_level"],
                    snippet=_best_grouped_snippet(chunk.chunk_text, query_plan),
                    chunk_text=chunk.chunk_text,
                    is_metadata_chunk=bool(locator_contract["is_metadata_chunk"]),
                    is_locatable=bool(locator_contract["is_locatable"]),
                    locator_status=str(locator_contract["locator_status"]),
                    locator_reason=str(locator_contract["locator_reason"]),
                    relevance_score=relevance_score,
                    relevance_label=_relevance_label(relevance_score),
                    match_reasons=reasons,
                    tags=tags,
                    related_relations=_load_related_relations(
                        session=session,
                        document_ids=[document.id],
                        chunk_ids=[chunk.id],
                        note_ids=[],
                        limit=3,
                    ),
                )
            )

        grouped_results: list[LibraryGroupedSearchDocument] = []
        for document in documents:
            chunks = sorted(
                chunks_by_document.get(document.id, []),
                key=lambda item: (item.relevance_score, -item.chunk_id),
                reverse=True,
            )
            if not chunks:
                continue
            top_chunks = chunks[:safe_chunk_limit]
            context_score, context_reasons = document_context.get(document.id, (0.0, []))
            top_scores = [chunk.relevance_score for chunk in top_chunks[:3]]
            max_chunk_score = max(top_scores)
            average_top_score = sum(top_scores) / len(top_scores)
            document_relevance_score = min(
                1.0,
                round((max_chunk_score * 0.72) + (average_top_score * 0.18) + (_normalize_relevance_score(context_score) * 0.10), 3),
            )
            grouped_results.append(
                LibraryGroupedSearchDocument(
                    document_id=document.id,
                    document_title=document.title,
                    document_type=document.document_type,
                    document_relevance_score=document_relevance_score,
                    document_relevance_label=_relevance_label(document_relevance_score),
                    match_reasons=_ordered_unique(context_reasons + [reason for chunk in top_chunks for reason in chunk.match_reasons]),
                    top_chunks=top_chunks,
                )
            )

        grouped_results.sort(key=lambda item: (item.document_relevance_score, -item.document_id), reverse=True)
        return grouped_results[:safe_document_limit]


def list_read_books(limit: int = 100) -> list[ReadLibraryDocumentSummary]:
    with SessionLocal() as session:
        documents = session.scalars(
            select(Document)
            .where(
                Document.document_type.in_(READ_LIBRARY_DOCUMENT_TYPES),
                Document.read_status.in_(READ_LIBRARY_STATUSES),
            )
            .order_by(Document.title, Document.id)
            .limit(max(1, limit))
        ).all()
        document_ids = [document.id for document in documents]
        chunk_counts = _load_chunk_counts_by_document(session, document_ids)
        note_ids_by_document = _load_note_ids_by_document(session, document_ids)
        return [
            ReadLibraryDocumentSummary(
                document_id=document.id,
                title=document.title,
                document_type=document.document_type,
                read_status=document.read_status,
                research_direction=document.research_direction,
                pdf_path=document.pdf_path,
                zotero_key=document.zotero_key,
                chunk_count=chunk_counts.get(document.id, 0),
                note_count=len(note_ids_by_document.get(document.id, set())),
            )
            for document in documents
        ]


def show_library_document(document_id: int) -> LibraryDocumentDetail:
    with SessionLocal() as session:
        document = _get_read_library_document(session, document_id)
        chunk_counts = _load_chunk_counts_by_document(session, [document.id])
        note_ids_by_document = _load_note_ids_by_document(session, [document.id])
        chunk_ids = _load_chunk_ids_by_document(session, document.id)
        note_ids = sorted(note_ids_by_document.get(document.id, set()))
        return LibraryDocumentDetail(
            document_id=document.id,
            title=document.title,
            document_type=document.document_type,
            content_layer=document.content_layer,
            read_status=document.read_status,
            research_direction=document.research_direction,
            source_path=document.source_path,
            pdf_path=document.pdf_path,
            pdf_open_url=build_pdf_open_url(document.pdf_path, None),
            zotero_key=document.zotero_key,
            zotero_open_url=_first_chunk_zotero_open_url(session, chunk_ids),
            object_import_mode=document.object_import_mode,
            object_import_status=document.object_import_status,
            created_at=document.created_at,
            updated_at=document.updated_at,
            chunk_count=chunk_counts.get(document.id, 0),
            note_count=len(note_ids),
            tags=_load_document_tag_labels(session, document.id),
            top_headings=_load_top_headings(session, document.id, limit=TOP_HEADINGS_LIMIT),
            related_notes=_load_related_note_items(session, note_ids, limit=RELATED_NOTES_LIMIT),
            related_relations=_load_related_relations(
                session=session,
                document_ids=[document.id],
                chunk_ids=chunk_ids,
                note_ids=note_ids,
                limit=RELATED_RELATIONS_LIMIT,
            ),
        )


def get_document_pdf_source(document_id: int) -> DocumentPdfSource:
    with SessionLocal() as session:
        document = _get_read_library_document(session, document_id)
        return DocumentPdfSource(
            document_id=document.id,
            title=document.title,
            pdf_path=document.pdf_path,
            source_path=document.source_path,
        )


def resolve_document_pdf_path(document_id: int) -> Path | None:
    source = get_document_pdf_source(document_id)
    for candidate in (source.pdf_path, source.source_path):
        resolved = resolve_safe_pdf_path(candidate)
        if resolved is not None:
            return resolved
    return None


def show_library_notes(document_id: int) -> list[LibraryNoteItem]:
    with SessionLocal() as session:
        document = _get_read_library_document(session, document_id)
        note_ids = sorted(_load_note_ids_by_document(session, [document.id]).get(document.id, set()))
        if not note_ids:
            return []
        notes = session.scalars(
            select(PersonalNote).where(PersonalNote.id.in_(note_ids)).order_by(PersonalNote.id)
        ).all()
        note_tags = _load_note_tag_labels(session, note_ids)
        return [
            LibraryNoteItem(
                note_id=note.id,
                title=note.title,
                note_type=note.note_type,
                source_path=note.source_path,
                scope_type=note.scope_type,
                scope_path=note.scope_path,
                summary=note.summary,
                content_snippet=_snippet(note.content, EVIDENCE_SNIPPET_CHARS),
                note_tags=note_tags.get(note.id, []),
            )
            for note in notes
        ]


def show_library_evidence(document_id: int, limit: int = 20) -> list[LibraryEvidenceItem]:
    with SessionLocal() as session:
        document = _get_read_library_document(session, document_id)
        chunks = session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document.id)
            .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            .limit(max(1, limit) * 3)
        ).all()
        chunks = [chunk for chunk in chunks if not is_metadata_chunk(chunk)][: max(1, limit)]
        chunk_ids = [chunk.id for chunk in chunks]
        related_note_titles = load_related_note_titles(session, chunk_ids)
        chunk_tags = load_chunk_tags(session, chunk_ids)
        items: list[LibraryEvidenceItem] = []
        for chunk in chunks:
            pdf_path = chunk.pdf_path or document.pdf_path
            locator_contract = evidence_locator_contract(
                chunk_text=chunk.chunk_text,
                pdf_page_start=chunk.pdf_page_start,
                pdf_path=pdf_path,
                is_metadata=False,
            )
            items.append(
                LibraryEvidenceItem(
                    chunk_id=chunk.id,
                    heading_path=chunk.heading_path,
                    pdf_page_start=chunk.pdf_page_start,
                    pdf_page_end=chunk.pdf_page_end,
                    pdf_open_url=build_pdf_open_url(pdf_path, chunk.pdf_page_start),
                    snippet=_snippet(chunk.chunk_text, EVIDENCE_SNIPPET_CHARS),
                    chunk_text=chunk.chunk_text,
                    is_metadata_chunk=bool(locator_contract["is_metadata_chunk"]),
                    is_locatable=bool(locator_contract["is_locatable"]),
                    locator_status=str(locator_contract["locator_status"]),
                    locator_reason=str(locator_contract["locator_reason"]),
                    related_note_titles=related_note_titles.get(chunk.id, []),
                    chunk_tags=chunk_tags.get(chunk.id, []),
                )
            )
        return items


def show_library_note(note_id: int) -> LibraryNotePreview:
    with SessionLocal() as session:
        note = session.get(PersonalNote, note_id)
        if note is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        linked_chunks = _load_linked_chunk_items(session, note.id, limit=LINKED_CHUNKS_LIMIT)
        note_tags = _load_note_tag_labels(session, [note.id]).get(note.id, [])
        related_relations = _load_related_relations(
            session=session,
            document_ids=[note.document_id] if note.document_id is not None else [],
            chunk_ids=[chunk.chunk_id for chunk in linked_chunks],
            note_ids=[note.id],
            limit=RELATED_RELATIONS_LIMIT,
        )
        return LibraryNotePreview(
            note_id=note.id,
            title=note.title,
            note_type=note.note_type,
            summary=note.summary,
            source_path=note.source_path,
            document_id=note.document_id,
            scope_type=note.scope_type,
            scope_path=note.scope_path,
            snippet=_snippet(note.content, EVIDENCE_SNIPPET_CHARS),
            linked_chunks=linked_chunks,
            note_tags=note_tags,
            related_relations=related_relations,
        )


def show_library_chunk(chunk_id: int) -> LibraryChunkPreview:
    with SessionLocal() as session:
        chunk = session.get(KnowledgeChunk, chunk_id)
        if chunk is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")
        document = session.get(Document, chunk.document_id)
        if document is None:
            raise ValueError(f"chunk document does not exist: {chunk.document_id}")
        note_ids = [note.id for note in _load_notes_for_chunk(session, chunk.id)]
        pdf_path = chunk.pdf_path or document.pdf_path
        locator_contract = evidence_locator_contract(
            chunk_text=chunk.chunk_text,
            pdf_page_start=chunk.pdf_page_start,
            pdf_path=pdf_path,
            is_metadata=is_metadata_chunk(chunk),
        )
        return LibraryChunkPreview(
            chunk_id=chunk.id,
            document_id=document.id,
            document_title=document.title,
            document_type=document.document_type,
            heading_path=chunk.heading_path,
            snippet=_snippet(chunk.chunk_text, EVIDENCE_SNIPPET_CHARS),
            chunk_text=chunk.chunk_text,
            is_metadata_chunk=bool(locator_contract["is_metadata_chunk"]),
            is_locatable=bool(locator_contract["is_locatable"]),
            locator_status=str(locator_contract["locator_status"]),
            locator_reason=str(locator_contract["locator_reason"]),
            pdf_path=pdf_path,
            pdf_page_start=chunk.pdf_page_start,
            pdf_page_end=chunk.pdf_page_end,
            pdf_open_url=build_pdf_open_url(pdf_path, chunk.pdf_page_start),
            zotero_open_url=chunk.zotero_open_url,
            related_notes=_load_related_note_items(session, note_ids, limit=RELATED_NOTES_LIMIT),
            chunk_tags=load_chunk_tags(session, [chunk.id]).get(chunk.id, []),
            related_relations=_load_related_relations(
                session=session,
                document_ids=[document.id],
                chunk_ids=[chunk.id],
                note_ids=note_ids,
                limit=RELATED_RELATIONS_LIMIT,
            ),
        )


def _get_read_library_document(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError(f"document_id does not exist: {document_id}")
    if document.read_status not in READ_LIBRARY_STATUSES:
        raise ValueError(f"document_id is not read/mastered: {document_id}")
    return document


def _load_home_document_items(
    session: Session,
    document_type: str | None,
    research_direction: str | None,
    limit: int,
) -> list[LibraryHomeItem]:
    statement = select(Document).where(Document.read_status.in_(READ_LIBRARY_STATUSES))
    if document_type is not None:
        statement = statement.where(Document.document_type == document_type)
    if research_direction is not None:
        statement = statement.where(Document.research_direction == research_direction)
    documents = session.scalars(statement.order_by(Document.updated_at.desc(), Document.id.desc()).limit(limit)).all()
    document_ids = [document.id for document in documents]
    chunk_counts = _load_chunk_counts_by_document(session, document_ids)
    chapter_counts = _load_chapter_counts_by_document(session, document_ids)
    note_ids_by_document = _load_note_ids_by_document(session, document_ids)
    tag_counts = _load_tag_counts_by_document(session, document_ids)
    return [
        LibraryHomeItem(
            item_type="document",
            item_id=document.id,
            title=document.title,
            document_type=document.document_type,
            note_type=None,
            read_status=document.read_status,
            research_direction=document.research_direction,
            updated_at=document.updated_at,
            has_pdf=bool(document.pdf_path),
            has_zotero=bool(document.zotero_key),
            chunk_count=chunk_counts.get(document.id, 0),
            note_count=len(note_ids_by_document.get(document.id, set())),
            tag_count=tag_counts.get(document.id, 0),
            source_document_id=document.id,
            object_import_mode=document.object_import_mode,
            object_import_status=document.object_import_status,
            chapter_count=chapter_counts.get(document.id, 0),
            pdf_path=document.pdf_path,
            zotero_key=document.zotero_key,
        )
        for document in documents
    ]


def _load_home_note_items(
    session: Session,
    research_direction: str | None,
    limit: int,
) -> list[LibraryHomeItem]:
    note_ids = sorted(_load_core_note_ids(session))
    if not note_ids:
        return []
    statement = select(PersonalNote).where(PersonalNote.id.in_(note_ids), PersonalNote.note_type.in_(HOME_NOTE_TYPES))
    notes = session.scalars(statement.order_by(PersonalNote.updated_at.desc(), PersonalNote.id.desc()).limit(limit)).all()
    if not notes:
        return []
    note_ids = [note.id for note in notes]
    linked_chunks_by_note = _load_linked_chunk_ids_by_note(session, note_ids)
    tag_counts = _load_tag_counts_by_note(session, note_ids)
    document_map = _load_note_source_documents(session, note_ids)
    items: list[LibraryHomeItem] = []
    for note in notes:
        document = document_map.get(note.id)
        if research_direction is not None and (
            document is None or document.research_direction != research_direction
        ):
            continue
        linked_chunk_ids = linked_chunks_by_note.get(note.id, set())
        has_chunk_pdf = _note_has_linked_chunk_pdf(session, linked_chunk_ids)
        items.append(
            LibraryHomeItem(
                item_type="note",
                item_id=note.id,
                title=note.title,
                document_type=document.document_type if document else None,
                note_type=note.note_type,
                read_status=document.read_status if document else None,
                research_direction=document.research_direction if document else None,
                updated_at=note.updated_at,
                has_pdf=bool((document.pdf_path if document else None) or has_chunk_pdf),
                has_zotero=bool(document.zotero_key if document else None),
                chunk_count=len(linked_chunk_ids),
                note_count=0,
                tag_count=tag_counts.get(note.id, 0),
                source_document_id=note.document_id,
                object_import_mode=document.object_import_mode if document else None,
                object_import_status=document.object_import_status if document else None,
                chapter_count=0,
            )
        )
    return items


def _search_documents(
    session: Session,
    like_query: str,
    query: str,
    limit: int,
) -> list[LibrarySearchResult]:
    documents = session.scalars(
        select(Document)
        .where(
            Document.read_status.in_(READ_LIBRARY_STATUSES),
            or_(
                Document.title.like(like_query, escape="\\"),
                Document.research_direction.like(like_query, escape="\\"),
            ),
        )
        .order_by(Document.updated_at.desc(), Document.id.desc())
        .limit(limit)
    ).all()
    return [
        LibrarySearchResult(
            result_type="document_result",
            id=document.id,
            title=document.title,
            snippet=_best_snippet(" ".join([document.title, document.research_direction or ""]), query),
            document_id=document.id,
            document_title=document.title,
            document_type=document.document_type,
            note_type=None,
            heading_path=None,
            pdf_path=document.pdf_path,
            pdf_page_start=None,
            pdf_open_url=build_pdf_open_url(document.pdf_path, None),
            zotero_open_url=None,
            tags=_load_document_tag_labels(session, document.id),
            related_notes=[],
            related_relations=_load_related_relations(
                session=session,
                document_ids=[document.id],
                chunk_ids=_load_chunk_ids_by_document(session, document.id),
                note_ids=sorted(_load_note_ids_by_document(session, [document.id]).get(document.id, set())),
                limit=RELATED_RELATIONS_LIMIT,
            ),
            relation_summary=None,
        )
        for document in documents
    ]


def _search_chunks(
    session: Session,
    like_query: str,
    query: str,
    limit: int,
) -> list[LibrarySearchResult]:
    rows = session.execute(
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(
            Document.read_status.in_(READ_LIBRARY_STATUSES),
            or_(
                KnowledgeChunk.chunk_text.like(like_query, escape="\\"),
                KnowledgeChunk.heading_path.like(like_query, escape="\\"),
            ),
        )
        .order_by(Document.updated_at.desc(), KnowledgeChunk.id)
        .limit(limit)
    ).all()
    chunk_ids = [chunk.id for _, chunk in rows]
    related_note_titles = load_related_note_titles(session, chunk_ids)
    chunk_tags = load_chunk_tags(session, chunk_ids)
    results: list[LibrarySearchResult] = []
    for document, chunk in rows:
        if is_metadata_chunk(chunk):
            continue
        note_ids = [note.id for note in _load_notes_for_chunk(session, chunk.id)]
        results.append(
            LibrarySearchResult(
                result_type="chunk_result",
                id=chunk.id,
                title=document.title,
                snippet=_best_snippet(chunk.chunk_text, query),
                document_id=document.id,
                document_title=document.title,
                document_type=document.document_type,
                note_type=None,
                heading_path=chunk.heading_path,
                pdf_path=chunk.pdf_path or document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
                pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                zotero_open_url=chunk.zotero_open_url,
                tags=chunk_tags.get(chunk.id, []),
                related_notes=related_note_titles.get(chunk.id, [])[:RELATED_NOTES_LIMIT],
                related_relations=_load_related_relations(
                    session=session,
                    document_ids=[document.id],
                    chunk_ids=[chunk.id],
                    note_ids=note_ids,
                    limit=RELATED_RELATIONS_LIMIT,
                ),
                relation_summary=None,
            )
        )
    return results


def _search_notes(
    session: Session,
    like_query: str,
    query: str,
    limit: int,
) -> list[LibrarySearchResult]:
    note_ids = sorted(_load_core_note_ids(session))
    if not note_ids:
        return []
    notes = session.scalars(
        select(PersonalNote)
        .where(
            PersonalNote.id.in_(note_ids),
            or_(
                PersonalNote.title.like(like_query, escape="\\"),
                PersonalNote.summary.like(like_query, escape="\\"),
                PersonalNote.content.like(like_query, escape="\\"),
            ),
        )
        .order_by(PersonalNote.updated_at.desc(), PersonalNote.id.desc())
        .limit(limit)
    ).all()
    note_tags = _load_note_tag_labels(session, [note.id for note in notes])
    document_map = _load_note_source_documents(session, [note.id for note in notes])
    results: list[LibrarySearchResult] = []
    for note in notes:
        document = document_map.get(note.id)
        linked_chunks = _load_linked_chunk_items(session, note.id)
        first_chunk = linked_chunks[0] if linked_chunks else None
        results.append(
            LibrarySearchResult(
                result_type="note_result",
                id=note.id,
                title=note.title,
                snippet=_best_snippet(note.summary or note.content, query),
                document_id=document.id if document else note.document_id,
                document_title=document.title if document else None,
                document_type=document.document_type if document else None,
                note_type=note.note_type,
                heading_path=first_chunk.heading_path if first_chunk else note.scope_path,
                pdf_path=first_chunk.pdf_path if first_chunk else (document.pdf_path if document else None),
                pdf_page_start=first_chunk.pdf_page_start if first_chunk else None,
                pdf_open_url=first_chunk.pdf_open_url if first_chunk else build_pdf_open_url(
                    document.pdf_path if document else None,
                    None,
                ),
                zotero_open_url=None,
                tags=note_tags.get(note.id, []),
                related_notes=[],
                related_relations=_load_related_relations(
                    session=session,
                    document_ids=[document.id] if document else [],
                    chunk_ids=[chunk.chunk_id for chunk in linked_chunks],
                    note_ids=[note.id],
                    limit=RELATED_RELATIONS_LIMIT,
                ),
                relation_summary=None,
            )
        )
    return results


def _search_tags(
    session: Session,
    like_query: str,
    query: str,
    limit: int,
) -> list[LibrarySearchResult]:
    tag_ids = sorted(_load_core_tag_ids(session))
    if not tag_ids:
        return []
    tags = session.scalars(
        select(KnowledgeTag)
        .where(
            KnowledgeTag.id.in_(tag_ids),
            or_(
                KnowledgeTag.name.like(like_query, escape="\\"),
                KnowledgeTag.description.like(like_query, escape="\\"),
            ),
        )
        .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
        .limit(limit)
    ).all()
    return [
        LibrarySearchResult(
            result_type="tag_result",
            id=tag.id,
            title=tag.name,
            snippet=_best_snippet(tag.description or tag.name, query),
            document_id=None,
            document_title=None,
            document_type=None,
            note_type=None,
            heading_path=None,
            pdf_path=None,
            pdf_page_start=None,
            pdf_open_url=None,
            zotero_open_url=None,
            tags=[f"{tag.tag_type}:{tag.name}"],
            related_notes=[],
            related_relations=[],
            relation_summary=tag.description,
        )
        for tag in tags
    ]


def _search_relations(
    session: Session,
    like_query: str,
    query: str,
    limit: int,
) -> list[LibrarySearchResult]:
    relation_ids = sorted(_load_core_relation_ids(session))
    if not relation_ids:
        return []
    relations = session.scalars(
        select(KnowledgeRelation)
        .where(
            KnowledgeRelation.id.in_(relation_ids),
            KnowledgeRelation.description.like(like_query, escape="\\"),
        )
        .order_by(KnowledgeRelation.updated_at.desc(), KnowledgeRelation.id.desc())
        .limit(limit)
    ).all()
    results: list[LibrarySearchResult] = []
    for relation in relations:
        chunk = session.get(KnowledgeChunk, relation.evidence_chunk_id) if relation.evidence_chunk_id else None
        document = session.get(Document, chunk.document_id) if chunk else None
        results.append(
            LibrarySearchResult(
                result_type="relation_result",
                id=relation.id,
                title=f"{relation.source_type}:{relation.source_id} {relation.relation_type} {relation.target_type}:{relation.target_id}",
                snippet=_best_snippet(relation.description or relation.relation_type, query),
                document_id=document.id if document else None,
                document_title=document.title if document else None,
                document_type=document.document_type if document else None,
                note_type=None,
                heading_path=chunk.heading_path if chunk else None,
                pdf_path=(chunk.pdf_path or document.pdf_path) if chunk and document else None,
                pdf_page_start=chunk.pdf_page_start if chunk else None,
                pdf_open_url=build_pdf_open_url(
                    (chunk.pdf_path or document.pdf_path) if chunk and document else None,
                    chunk.pdf_page_start if chunk else None,
                ),
                zotero_open_url=chunk.zotero_open_url if chunk else None,
                tags=[],
                related_notes=[],
                related_relations=[_to_relation_item(session, relation)],
                relation_summary=relation.description,
            )
        )
    return results


def normalize_grouped_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _build_grouped_query_plan(query: str) -> dict[str, list[str] | str]:
    normalized = normalize_grouped_search_query(query)
    original_terms = _query_terms(normalized)
    expanded_terms: list[str] = []
    for trigger in _query_expansion_triggers(normalized):
        expanded_terms.extend(QUERY_EXPANSIONS[trigger])
    for term in list(original_terms):
        expansions = QUERY_EXPANSIONS.get(term)
        if expansions:
            expanded_terms.extend(expansions)
    return {
        "phrase": normalized,
        "original_terms": _ordered_unique(original_terms),
        "expanded_terms": _ordered_unique([normalize_grouped_search_query(term) for term in expanded_terms]),
    }


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower())
    terms.extend([marker for marker in _query_expansion_triggers(query) if _contains_cjk(marker)])
    if query and query not in terms:
        terms.append(query)
    return terms


def _query_expansion_triggers(query: str) -> list[str]:
    cjk_matches: list[str] = []
    non_cjk_matches: list[str] = []
    for trigger in QUERY_EXPANSIONS:
        normalized_trigger = normalize_grouped_search_query(trigger)
        if not normalized_trigger or normalized_trigger not in query:
            continue
        if _contains_cjk(normalized_trigger):
            cjk_matches.append(trigger)
        else:
            non_cjk_matches.append(trigger)

    selected_cjk: list[str] = []
    for trigger in sorted(cjk_matches, key=len, reverse=True):
        if any(trigger != selected and trigger in selected for selected in selected_cjk):
            continue
        selected_cjk.append(trigger)

    selected = set(selected_cjk + non_cjk_matches)
    return [trigger for trigger in QUERY_EXPANSIONS if trigger in selected]


def _score_grouped_text(
    text: str | None,
    query_plan: dict[str, list[str] | str],
    *,
    phrase_reason: str,
    token_reason: str,
    expanded_reason: str,
    weight_scale: float = 1.0,
    field: str = "generic",
) -> tuple[float, list[str]]:
    if not text:
        return 0.0, []
    haystack = normalize_grouped_search_query(text)
    if not haystack:
        return 0.0, []

    phrase = str(query_plan["phrase"])
    original_terms = list(query_plan["original_terms"])
    expanded_terms = list(query_plan["expanded_terms"])
    mechanism_query = _is_mechanism_query(query_plan)
    score = 0.0
    reasons: list[str] = []

    if phrase and phrase in haystack:
        score += 4.0 * weight_scale
        reasons.append(phrase_reason)
    for term in original_terms:
        if term and term != phrase and _contains_search_term(haystack, term):
            score += 1.2 * weight_scale
            reasons.append(token_reason)
    for term in expanded_terms:
        if term and _contains_search_term(haystack, term):
            score += _expanded_term_weight(term, field=field, mechanism_query=mechanism_query) * weight_scale
            reasons.append(_expanded_term_reason(expanded_reason, field=field, mechanism_query=mechanism_query, term=term))
    return score, _ordered_unique(reasons)


def _expanded_term_weight(term: str, *, field: str, mechanism_query: bool) -> float:
    phrase_like = " " in term or "-" in term
    if field == "body" and phrase_like:
        return 2.8 if mechanism_query else 2.2
    if field == "body":
        return 1.0
    if field == "tag":
        return 0.45 if phrase_like else 0.35
    if field == "relation":
        return 0.35 if phrase_like else 0.25
    return 0.75


def _expanded_term_reason(reason: str, *, field: str, mechanism_query: bool, term: str) -> str:
    if field == "body" and (" " in term or "-" in term):
        return "机制正文命中" if mechanism_query else "正文直接命中"
    return reason


def _is_mechanism_query(query_plan: dict[str, list[str] | str]) -> bool:
    terms = " ".join(
        [str(query_plan.get("phrase") or "")]
        + list(query_plan.get("original_terms") or [])
        + list(query_plan.get("expanded_terms") or [])
    ).lower()
    mechanism_terms = (
        "残差块",
        "mechanism",
        "module",
        "architecture",
        "method",
        "approach",
        "residual block",
        "residual module",
    )
    return any(term in terms for term in mechanism_terms)


def _contains_search_term(text: str, term: str) -> bool:
    if not term:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in term):
        return term in text
    if " " in term or "-" in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _section_relevance_adjustment(
    section_metadata: dict[str, object],
    query_plan: dict[str, list[str] | str],
) -> tuple[float, list[str]]:
    if not _is_mechanism_query(query_plan):
        return 0.0, []

    section_text = " ".join(str(part) for part in section_metadata.get("section_path") or [])
    section_text = " ".join([section_text, str(section_metadata.get("section_label") or "")]).lower()
    if not section_text:
        return 0.0, []

    method_terms = (
        "proposed methods",
        "proposed method",
        "method",
        "methods",
        "approach",
        "architecture",
        "training details",
        "residual blocks",
    )
    if any(term in section_text for term in method_terms):
        return 1.0, ["方法章节加权"]
    if "introduction" in section_text:
        return 0.25, ["引言章节轻微加权"]
    weak_terms = ("conclusion", "challenge", "benchmark", "result", "ntire")
    if any(term in section_text for term in weak_terms):
        return -0.55, []
    if "front matter" in section_text or "标题 / 摘要页" in section_text:
        return -0.15, []
    return 0.0, []


def _normalize_relevance_score(raw_score: float) -> float:
    return min(1.0, round(raw_score / 8.0, 3))


def _relevance_label(score: float) -> str:
    if score >= 0.72:
        return "高度相关"
    if score >= 0.38:
        return "中度相关"
    return "低相关"


def _best_grouped_snippet(text: str | None, query_plan: dict[str, list[str] | str]) -> str:
    if not text:
        return ""
    compact_text = " ".join(text.split())
    lower_text = compact_text.lower()
    candidates = [str(query_plan["phrase"])]
    candidates.extend(list(query_plan["original_terms"]))
    candidates.extend(list(query_plan["expanded_terms"]))
    for term in _ordered_unique([candidate for candidate in candidates if candidate]):
        index = lower_text.find(term.lower())
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(compact_text), start + GROUPED_SEARCH_SNIPPET_CHARS)
            snippet = compact_text[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(compact_text):
                snippet = snippet.rstrip() + "..."
            return _snippet(snippet, GROUPED_SEARCH_SNIPPET_CHARS)
    return _snippet(compact_text, GROUPED_SEARCH_SNIPPET_CHARS)


def _load_grouped_relation_text_by_chunk(session: Session, chunk_ids: list[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    relations = session.scalars(
        select(KnowledgeRelation)
        .where(KnowledgeRelation.evidence_chunk_id.in_(chunk_ids))
        .order_by(KnowledgeRelation.id)
    ).all()
    text_by_chunk: dict[int, list[str]] = {chunk_id: [] for chunk_id in chunk_ids}
    for relation in relations:
        if relation.evidence_chunk_id is None:
            continue
        item = _to_relation_item(session, relation)
        text_by_chunk.setdefault(relation.evidence_chunk_id, []).append(
            " ".join(
                [
                    item.source_label,
                    item.relation_type,
                    item.relation_label_zh,
                    item.target_label,
                    item.description or "",
                    item.raw_relation,
                ]
            )
        )
    return {chunk_id: " ".join(parts) for chunk_id, parts in text_by_chunk.items() if parts}


def _chunk_section_metadata(document: Document, chunk: KnowledgeChunk) -> dict[str, object]:
    page = chunk.pdf_page_start
    page_label = f"p.{page}" if page else "页码暂不可用"
    raw_heading = (chunk.heading_path or "").strip()
    section_path = _section_path_from_heading(raw_heading, document.title)

    if not section_path:
        return {
            "section_path": [],
            "section_label": f"{page_label} · 未识别章节",
            "location_label": _chunk_location_label(document.id, chunk.id, page),
            "heading_level": None,
        }

    if _is_front_matter_section(section_path, page):
        return {
            "section_path": ["Front matter"],
            "section_label": f"标题 / 摘要页 · {page_label}",
            "location_label": _chunk_location_label(document.id, chunk.id, page),
            "heading_level": 1,
        }

    return {
        "section_path": section_path,
        "section_label": f"{' > '.join(section_path)} · {page_label}",
        "location_label": _chunk_location_label(document.id, chunk.id, page),
        "heading_level": len(section_path),
    }


def _section_path_from_heading(heading_path: str, document_title: str) -> list[str]:
    if not heading_path:
        return []
    parts = [part.strip() for part in re.split(r"\s*/\s*|(?:\s+>\s+)", heading_path) if part.strip()]
    if not parts:
        return []
    removed_document_title = _same_heading(parts[0], document_title)
    if removed_document_title:
        parts = parts[1:]
    if not parts and removed_document_title:
        return ["Front matter"]
    if not parts and heading_path.strip():
        parts = [heading_path.strip()]
    return _normalize_numbered_section_path(parts)


def _normalize_numbered_section_path(parts: list[str]) -> list[str]:
    if len(parts) <= 1:
        return parts
    leaf = parts[-1]
    leaf_number = _heading_number_prefix(leaf)
    if not leaf_number:
        return parts
    if len(leaf_number) == 1:
        return [leaf]

    ancestors: list[str] = []
    for part in parts[:-1]:
        number = _heading_number_prefix(part)
        if not number:
            continue
        if len(number) < len(leaf_number) and leaf_number[: len(number)] == number:
            ancestors.append(part)
    return ancestors + [leaf]


def _heading_number_prefix(value: str) -> tuple[int, ...] | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s+", value)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _is_front_matter_section(section_path: list[str], page: int | None) -> bool:
    if page not in (None, 1):
        return False
    if not section_path:
        return True
    normalized = " ".join(section_path).strip().lower()
    front_matter_terms = ("front matter", "abstract", "title", "authors", "enhanced deep residual networks")
    return len(section_path) == 1 and any(term in normalized for term in front_matter_terms)


def _same_heading(value: str, other: str) -> bool:
    return re.sub(r"\W+", "", value).lower() == re.sub(r"\W+", "", other).lower()


def _chunk_location_label(document_id: int, chunk_id: int, page: int | None) -> str:
    suffix = f" · p.{page}" if page else ""
    return f"doc{document_id} · chunk {chunk_id}{suffix}"


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values


def _load_chunk_counts_by_document(session: Session, document_ids: list[int]) -> dict[int, int]:
    if not document_ids:
        return {}
    counts = {document_id: 0 for document_id in document_ids}
    rows = session.execute(
        select(KnowledgeChunk.document_id, func.count(KnowledgeChunk.id))
        .where(KnowledgeChunk.document_id.in_(document_ids))
        .group_by(KnowledgeChunk.document_id)
    )
    for document_id, count in rows:
        counts[document_id] = int(count)
    return counts


def _load_chapter_counts_by_document(session: Session, document_ids: list[int]) -> dict[int, int]:
    if not document_ids:
        return {}
    counts = {document_id: 0 for document_id in document_ids}
    rows = session.execute(
        select(BookChapter.document_id, func.count(BookChapter.id))
        .where(BookChapter.document_id.in_(document_ids))
        .group_by(BookChapter.document_id)
    )
    for document_id, count in rows:
        counts[int(document_id)] = int(count)
    return counts


def _load_note_ids_by_document(session: Session, document_ids: list[int]) -> dict[int, set[int]]:
    note_ids_by_document: dict[int, set[int]] = {document_id: set() for document_id in document_ids}
    if not document_ids:
        return note_ids_by_document

    direct_rows = session.execute(
        select(PersonalNote.document_id, PersonalNote.id).where(PersonalNote.document_id.in_(document_ids))
    )
    for document_id, note_id in direct_rows:
        if document_id is not None:
            note_ids_by_document.setdefault(document_id, set()).add(note_id)

    linked_rows = session.execute(
        select(KnowledgeChunk.document_id, NoteEvidenceLink.note_id)
        .join(NoteEvidenceLink, NoteEvidenceLink.chunk_id == KnowledgeChunk.id)
        .where(KnowledgeChunk.document_id.in_(document_ids))
    )
    for document_id, note_id in linked_rows:
        note_ids_by_document.setdefault(document_id, set()).add(note_id)
    return note_ids_by_document


def _load_document_tag_labels(session: Session, document_id: int) -> list[str]:
    labels: set[str] = set()
    chunk_rows = session.execute(
        select(KnowledgeTag.tag_type, KnowledgeTag.name)
        .join(ChunkTag, ChunkTag.tag_id == KnowledgeTag.id)
        .join(KnowledgeChunk, KnowledgeChunk.id == ChunkTag.chunk_id)
        .where(KnowledgeChunk.document_id == document_id)
    )
    for tag_type, name in chunk_rows:
        labels.add(f"{tag_type}:{name}")

    note_ids = sorted(_load_note_ids_by_document(session, [document_id]).get(document_id, set()))
    for note_labels in _load_note_tag_labels(session, note_ids).values():
        labels.update(note_labels)
    return sorted(labels)


def _load_note_tag_labels(session: Session, note_ids: list[int]) -> dict[int, list[str]]:
    if not note_ids:
        return {}
    labels_by_note: dict[int, list[str]] = {note_id: [] for note_id in note_ids}
    rows = session.execute(
        select(NoteTag.note_id, KnowledgeTag.tag_type, KnowledgeTag.name)
        .join(KnowledgeTag, KnowledgeTag.id == NoteTag.tag_id)
        .where(NoteTag.note_id.in_(note_ids))
        .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
    )
    for note_id, tag_type, name in rows:
        label = f"{tag_type}:{name}"
        if label not in labels_by_note[note_id]:
            labels_by_note[note_id].append(label)
    return labels_by_note


def _load_chunk_ids_by_document(session: Session, document_id: int) -> list[int]:
    return list(
        session.scalars(
            select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document_id).order_by(KnowledgeChunk.id)
        ).all()
    )


def _first_chunk_zotero_open_url(session: Session, chunk_ids: list[int]) -> str | None:
    if not chunk_ids:
        return None
    return session.scalar(
        select(KnowledgeChunk.zotero_open_url)
        .where(KnowledgeChunk.id.in_(chunk_ids), KnowledgeChunk.zotero_open_url.is_not(None))
        .order_by(KnowledgeChunk.id)
        .limit(1)
    )


def _load_top_headings(session: Session, document_id: int, limit: int = TOP_HEADINGS_LIMIT) -> list[str]:
    rows = session.scalars(
        select(KnowledgeChunk.heading_path)
        .where(KnowledgeChunk.document_id == document_id)
        .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
    ).all()
    headings: list[str] = []
    for heading in rows:
        if heading not in headings:
            headings.append(heading)
        if len(headings) >= limit:
            break
    return headings


def _load_related_note_items(
    session: Session,
    note_ids: list[int],
    limit: int = RELATED_NOTES_LIMIT,
) -> list[LibraryRelatedNoteItem]:
    if not note_ids:
        return []
    note_ids = note_ids[: max(1, limit)]
    notes = session.scalars(
        select(PersonalNote).where(PersonalNote.id.in_(note_ids)).order_by(PersonalNote.id)
        .limit(max(1, limit))
    ).all()
    return [
        LibraryRelatedNoteItem(
            note_id=note.id,
            title=note.title,
            note_type=note.note_type,
            summary=note.summary,
            snippet=_snippet(note.content, EVIDENCE_SNIPPET_CHARS),
        )
        for note in notes
    ]


def _load_related_relations(
    session: Session,
    document_ids: list[int],
    chunk_ids: list[int],
    note_ids: list[int],
    limit: int,
) -> list[LibraryRelationItem]:
    conditions = []
    if document_ids:
        conditions.append(
            (KnowledgeRelation.source_type == "document") & KnowledgeRelation.source_id.in_(document_ids)
        )
        conditions.append(
            (KnowledgeRelation.target_type == "document") & KnowledgeRelation.target_id.in_(document_ids)
        )
    if chunk_ids:
        conditions.append(KnowledgeRelation.evidence_chunk_id.in_(chunk_ids))
        conditions.append((KnowledgeRelation.source_type == "chunk") & KnowledgeRelation.source_id.in_(chunk_ids))
        conditions.append((KnowledgeRelation.target_type == "chunk") & KnowledgeRelation.target_id.in_(chunk_ids))
    if note_ids:
        conditions.append(KnowledgeRelation.note_id.in_(note_ids))
        conditions.append((KnowledgeRelation.source_type == "note") & KnowledgeRelation.source_id.in_(note_ids))
        conditions.append((KnowledgeRelation.target_type == "note") & KnowledgeRelation.target_id.in_(note_ids))
    if not conditions:
        return []

    relations = session.scalars(
        select(KnowledgeRelation)
        .where(or_(*conditions))
        .order_by(KnowledgeRelation.updated_at.desc(), KnowledgeRelation.id.desc())
        .limit(max(1, limit))
    ).all()
    return [_to_relation_item(session, relation) for relation in relations]


def _to_relation_item(session: Session, relation: KnowledgeRelation) -> LibraryRelationItem:
    evidence_pdf_page = _chunk_pdf_page(session, relation.evidence_chunk_id)
    return LibraryRelationItem(
        relation_id=relation.id,
        source_type=relation.source_type,
        source_id=relation.source_id,
        relation_type=relation.relation_type,
        target_type=relation.target_type,
        target_id=relation.target_id,
        evidence_chunk_id=relation.evidence_chunk_id,
        note_id=relation.note_id,
        description=relation.description,
        confidence=relation.confidence,
        source_label=_relation_entity_label(session, relation.source_type, relation.source_id),
        target_label=_relation_entity_label(session, relation.target_type, relation.target_id),
        relation_label_zh=_relation_label_zh(relation.relation_type),
        evidence_pdf_page=evidence_pdf_page,
        raw_relation=_raw_relation(relation.source_type, relation.source_id, relation.relation_type, relation.target_type, relation.target_id),
    )


RELATION_LABELS_ZH = {
    "measured_by": "使用评价指标",
    "evaluates_on": "评估于",
    "uses": "使用",
    "has_limitation": "存在局限",
    "addresses": "解决问题",
    "improves": "改进",
    "derived_from": "来源于",
    "related_to": "相关",
}


TAG_BACKED_ENTITY_TYPES = {
    "tag",
    "method",
    "dataset",
    "metric",
    "topic",
    "problem",
    "mechanism",
    "inspiration",
}


def _relation_label_zh(relation_type: str | None) -> str:
    return RELATION_LABELS_ZH.get(str(relation_type or ""), str(relation_type or "unknown"))


def _relation_entity_label(session: Session, entity_type: str | None, entity_id: int | None) -> str:
    fallback = _entity_fallback(entity_type, entity_id)
    if entity_id is None:
        return fallback
    normalized_type = str(entity_type or "").strip().lower()
    if normalized_type == "document":
        document = session.get(Document, entity_id)
        return document.title if document and document.title else fallback
    if normalized_type in TAG_BACKED_ENTITY_TYPES:
        tag = session.get(KnowledgeTag, entity_id)
        return tag.name if tag and tag.name else fallback
    if normalized_type == "chunk":
        return f"chunk {entity_id}"
    if normalized_type == "note":
        note = session.get(PersonalNote, entity_id)
        return note.title if note and note.title else fallback
    return fallback


def _chunk_pdf_page(session: Session, chunk_id: int | None) -> int | None:
    if chunk_id is None:
        return None
    chunk = session.get(KnowledgeChunk, chunk_id)
    return chunk.pdf_page_start if chunk else None


def _raw_relation(
    source_type: str | None,
    source_id: int | None,
    relation_type: str | None,
    target_type: str | None,
    target_id: int | None,
) -> str:
    return f"{_entity_fallback(source_type, source_id)} {relation_type or 'unknown'} {_entity_fallback(target_type, target_id)}"


def _entity_fallback(entity_type: str | None, entity_id: int | None) -> str:
    return f"{entity_type or 'unknown'}:{entity_id if entity_id is not None else 'unknown'}"


def _load_linked_chunk_items(
    session: Session,
    note_id: int,
    limit: int = LINKED_CHUNKS_LIMIT,
) -> list[LibraryLinkedChunkItem]:
    rows = session.execute(
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .join(NoteEvidenceLink, NoteEvidenceLink.chunk_id == KnowledgeChunk.id)
        .where(NoteEvidenceLink.note_id == note_id)
        .order_by(KnowledgeChunk.id)
        .limit(max(1, limit))
    ).all()
    return [
        LibraryLinkedChunkItem(
            chunk_id=chunk.id,
            document_id=document.id,
            document_title=document.title,
            heading_path=chunk.heading_path,
            pdf_path=chunk.pdf_path or document.pdf_path,
            pdf_page_start=chunk.pdf_page_start,
            pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
            snippet=_snippet(chunk.chunk_text, EVIDENCE_SNIPPET_CHARS),
        )
        for document, chunk in rows
    ]


def _load_notes_for_chunk(session: Session, chunk_id: int) -> list[PersonalNote]:
    return session.scalars(
        select(PersonalNote)
        .join(NoteEvidenceLink, NoteEvidenceLink.note_id == PersonalNote.id)
        .where(NoteEvidenceLink.chunk_id == chunk_id)
        .order_by(PersonalNote.id)
    ).all()


def _load_core_note_ids(session: Session) -> set[int]:
    note_ids: set[int] = set()
    direct_rows = session.execute(
        select(PersonalNote.id)
        .outerjoin(Document, Document.id == PersonalNote.document_id)
        .where(
            or_(
                PersonalNote.document_id.is_(None),
                Document.read_status.in_(READ_LIBRARY_STATUSES),
            )
        )
    )
    note_ids.update(note_id for (note_id,) in direct_rows)

    linked_rows = session.execute(
        select(NoteEvidenceLink.note_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == NoteEvidenceLink.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(Document.read_status.in_(READ_LIBRARY_STATUSES))
    )
    note_ids.update(note_id for (note_id,) in linked_rows)
    return note_ids


def _load_linked_chunk_ids_by_note(session: Session, note_ids: list[int]) -> dict[int, set[int]]:
    chunk_ids_by_note: dict[int, set[int]] = {note_id: set() for note_id in note_ids}
    if not note_ids:
        return chunk_ids_by_note
    rows = session.execute(
        select(NoteEvidenceLink.note_id, NoteEvidenceLink.chunk_id)
        .where(NoteEvidenceLink.note_id.in_(note_ids))
    )
    for note_id, chunk_id in rows:
        chunk_ids_by_note.setdefault(note_id, set()).add(chunk_id)
    return chunk_ids_by_note


def _load_tag_counts_by_document(session: Session, document_ids: list[int]) -> dict[int, int]:
    counts = {document_id: 0 for document_id in document_ids}
    for document_id in document_ids:
        counts[document_id] = len(_load_document_tag_labels(session, document_id))
    return counts


def _load_tag_counts_by_note(session: Session, note_ids: list[int]) -> dict[int, int]:
    tag_labels = _load_note_tag_labels(session, note_ids)
    return {note_id: len(labels) for note_id, labels in tag_labels.items()}


def _load_note_source_documents(session: Session, note_ids: list[int]) -> dict[int, Document]:
    if not note_ids:
        return {}
    document_by_note: dict[int, Document] = {}
    direct_rows = session.execute(
        select(PersonalNote.id, Document)
        .join(Document, Document.id == PersonalNote.document_id)
        .where(PersonalNote.id.in_(note_ids))
    ).all()
    for note_id, document in direct_rows:
        document_by_note[note_id] = document

    missing_note_ids = [note_id for note_id in note_ids if note_id not in document_by_note]
    if not missing_note_ids:
        return document_by_note

    linked_rows = session.execute(
        select(NoteEvidenceLink.note_id, Document)
        .join(KnowledgeChunk, KnowledgeChunk.id == NoteEvidenceLink.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(NoteEvidenceLink.note_id.in_(missing_note_ids))
        .order_by(KnowledgeChunk.id)
    ).all()
    for note_id, document in linked_rows:
        document_by_note.setdefault(note_id, document)
    return document_by_note


def _note_has_linked_chunk_pdf(session: Session, chunk_ids: set[int]) -> bool:
    if not chunk_ids:
        return False
    return session.scalar(
        select(KnowledgeChunk.id)
        .where(KnowledgeChunk.id.in_(chunk_ids), KnowledgeChunk.pdf_path.is_not(None))
        .limit(1)
    ) is not None


def _load_core_tag_ids(session: Session) -> set[int]:
    tag_ids: set[int] = set()
    chunk_rows = session.execute(
        select(ChunkTag.tag_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == ChunkTag.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(Document.read_status.in_(READ_LIBRARY_STATUSES))
    )
    tag_ids.update(tag_id for (tag_id,) in chunk_rows)

    note_ids = sorted(_load_core_note_ids(session))
    if note_ids:
        note_rows = session.execute(select(NoteTag.tag_id).where(NoteTag.note_id.in_(note_ids)))
        tag_ids.update(tag_id for (tag_id,) in note_rows)
    return tag_ids


def _load_core_relation_ids(session: Session) -> set[int]:
    document_ids = list(
        session.scalars(select(Document.id).where(Document.read_status.in_(READ_LIBRARY_STATUSES))).all()
    )
    chunk_ids = list(
        session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id.in_(document_ids))).all()
    ) if document_ids else []
    note_ids = sorted(_load_core_note_ids(session))
    relations = _load_related_relations(
        session=session,
        document_ids=document_ids,
        chunk_ids=chunk_ids,
        note_ids=note_ids,
        limit=1000,
    )
    return {relation.relation_id for relation in relations}


def _best_snippet(text: str | None, query: str) -> str:
    if not text:
        return ""
    compact_text = " ".join(text.split())
    lower_text = compact_text.lower()
    lower_query = query.lower()
    match_index = lower_text.find(lower_query)
    if match_index < 0:
        return _snippet(compact_text, EVIDENCE_SNIPPET_CHARS)
    start = max(0, match_index - 60)
    end = min(len(compact_text), start + EVIDENCE_SNIPPET_CHARS)
    snippet = compact_text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(compact_text):
        snippet = snippet.rstrip() + "..."
    return _snippet(snippet, EVIDENCE_SNIPPET_CHARS)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(text: str, max_chars: int) -> str:
    compact_text = " ".join(text.split())
    if len(compact_text) <= max_chars:
        return compact_text
    return compact_text[: max_chars - 3].rstrip() + "..."


