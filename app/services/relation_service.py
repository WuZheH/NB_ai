from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk, KnowledgeRelation, KnowledgeTag, PersonalNote
from app.services.keyword_search_service import build_pdf_open_url


ALLOWED_ENTITY_TYPES = {
    "document",
    "chunk",
    "note",
    "tag",
    "method",
    "concept",
    "experiment",
    "idea",
}
TAG_BACKED_ENTITY_TYPES = {"method", "concept", "experiment", "idea"}
ALLOWED_RELATION_TYPES = {
    "uses",
    "solves",
    "evaluates_on",
    "measured_by",
    "has_limitation",
    "supports",
    "contradicts",
    "explains",
    "extends",
    "derived_from",
    "related_to",
    "improves",
    "compares_with",
    "motivates",
}


@dataclass(frozen=True)
class RelationResult:
    relation_id: int
    created: bool
    source_type: str
    source_id: int
    relation_type: str
    target_type: str
    target_id: int
    evidence_chunk_id: int | None
    note_id: int | None
    confidence: float | None
    description: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    evidence_document_title: str | None
    evidence_heading_path: str | None
    evidence_pdf_path: str | None
    evidence_pdf_page_start: int | None
    evidence_pdf_open_url: str | None


def create_relation(
    source_type: str,
    source_id: int,
    relation_type: str,
    target_type: str,
    target_id: int,
    evidence_chunk_id: int | None = None,
    note_id: int | None = None,
    confidence: float | None = None,
    description: str | None = None,
) -> RelationResult:
    init_db()
    _validate_entity_type(source_type, "source_type")
    _validate_entity_type(target_type, "target_type")
    _validate_relation_type(relation_type)
    _validate_confidence(confidence)

    with SessionLocal() as session:
        _validate_entity_exists(session, source_type, source_id, "source")
        _validate_entity_exists(session, target_type, target_id, "target")
        if evidence_chunk_id is not None and session.get(KnowledgeChunk, evidence_chunk_id) is None:
            raise ValueError(f"evidence_chunk_id does not exist: {evidence_chunk_id}")
        if note_id is not None and session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")

        relation = _find_existing_relation(
            session=session,
            source_type=source_type,
            source_id=source_id,
            relation_type=relation_type,
            target_type=target_type,
            target_id=target_id,
            evidence_chunk_id=evidence_chunk_id,
            note_id=note_id,
        )
        if relation is None:
            relation = KnowledgeRelation(
                source_type=source_type,
                source_id=source_id,
                relation_type=relation_type,
                target_type=target_type,
                target_id=target_id,
                evidence_chunk_id=evidence_chunk_id,
                note_id=note_id,
                confidence=confidence,
                description=description,
                created_by="manual",
            )
            session.add(relation)
            session.commit()
            session.refresh(relation)
            return _to_relation_result(session, relation, created=True)

        changed = False
        if confidence is not None and confidence != relation.confidence:
            relation.confidence = confidence
            changed = True
        if description is not None and description != relation.description:
            relation.description = description
            changed = True
        if changed:
            relation.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(relation)
        return _to_relation_result(session, relation, created=False)


def list_relations(
    source_type: str | None = None,
    source_id: int | None = None,
    relation_type: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    limit: int = 50,
) -> list[RelationResult]:
    init_db()
    if source_type is not None:
        _validate_entity_type(source_type, "source_type")
    if target_type is not None:
        _validate_entity_type(target_type, "target_type")
    if relation_type is not None:
        _validate_relation_type(relation_type)

    statement = select(KnowledgeRelation).order_by(KnowledgeRelation.id.desc()).limit(max(1, limit))
    if source_type is not None:
        statement = statement.where(KnowledgeRelation.source_type == source_type)
    if source_id is not None:
        statement = statement.where(KnowledgeRelation.source_id == source_id)
    if relation_type is not None:
        statement = statement.where(KnowledgeRelation.relation_type == relation_type)
    if target_type is not None:
        statement = statement.where(KnowledgeRelation.target_type == target_type)
    if target_id is not None:
        statement = statement.where(KnowledgeRelation.target_id == target_id)

    with SessionLocal() as session:
        return [_to_relation_result(session, relation, created=False) for relation in session.scalars(statement).all()]


def show_relation(relation_id: int) -> RelationResult:
    init_db()
    with SessionLocal() as session:
        relation = session.get(KnowledgeRelation, relation_id)
        if relation is None:
            raise ValueError(f"relation_id does not exist: {relation_id}")
        return _to_relation_result(session, relation, created=False)


def list_relations_for_tag(tag_id: int) -> list[RelationResult]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeTag, tag_id) is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")
    return list_relations_for_entity("tag", tag_id)


def list_relations_for_note(note_id: int) -> list[RelationResult]:
    init_db()
    with SessionLocal() as session:
        if session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")
    return list_relations_for_entity("note", note_id)


def list_relations_for_chunk(chunk_id: int) -> list[RelationResult]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeChunk, chunk_id) is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")
    return list_relations_for_entity("chunk", chunk_id)


def list_relations_for_entity(entity_type: str, entity_id: int) -> list[RelationResult]:
    conditions = [
        (KnowledgeRelation.source_type == entity_type) & (KnowledgeRelation.source_id == entity_id),
        (KnowledgeRelation.target_type == entity_type) & (KnowledgeRelation.target_id == entity_id),
    ]
    if entity_type == "chunk":
        conditions.append(KnowledgeRelation.evidence_chunk_id == entity_id)
    if entity_type == "note":
        conditions.append(KnowledgeRelation.note_id == entity_id)

    with SessionLocal() as session:
        rows = session.scalars(
            select(KnowledgeRelation)
            .where(or_(*conditions))
            .order_by(KnowledgeRelation.id.desc())
        ).all()
        return [_to_relation_result(session, relation, created=False) for relation in rows]


def _find_existing_relation(
    session: Session,
    source_type: str,
    source_id: int,
    relation_type: str,
    target_type: str,
    target_id: int,
    evidence_chunk_id: int | None,
    note_id: int | None,
) -> KnowledgeRelation | None:
    return session.scalar(
        select(KnowledgeRelation).where(
            KnowledgeRelation.source_type == source_type,
            KnowledgeRelation.source_id == source_id,
            KnowledgeRelation.relation_type == relation_type,
            KnowledgeRelation.target_type == target_type,
            KnowledgeRelation.target_id == target_id,
            KnowledgeRelation.evidence_chunk_id.is_(evidence_chunk_id)
            if evidence_chunk_id is None
            else KnowledgeRelation.evidence_chunk_id == evidence_chunk_id,
            KnowledgeRelation.note_id.is_(note_id) if note_id is None else KnowledgeRelation.note_id == note_id,
        )
    )


def _to_relation_result(session: Session, relation: KnowledgeRelation, created: bool) -> RelationResult:
    document_title = None
    heading_path = None
    pdf_path = None
    pdf_page_start = None
    pdf_open_url = None
    if relation.evidence_chunk_id is not None:
        chunk = session.get(KnowledgeChunk, relation.evidence_chunk_id)
        if chunk is not None:
            document = session.get(Document, chunk.document_id)
            document_title = document.title if document else None
            heading_path = chunk.heading_path
            pdf_path = chunk.pdf_path or (document.pdf_path if document else None)
            pdf_page_start = chunk.pdf_page_start
            pdf_open_url = build_pdf_open_url(pdf_path, pdf_page_start)
    return RelationResult(
        relation_id=relation.id,
        created=created,
        source_type=relation.source_type,
        source_id=relation.source_id,
        relation_type=relation.relation_type,
        target_type=relation.target_type,
        target_id=relation.target_id,
        evidence_chunk_id=relation.evidence_chunk_id,
        note_id=relation.note_id,
        confidence=relation.confidence,
        description=relation.description,
        created_by=relation.created_by,
        created_at=relation.created_at,
        updated_at=relation.updated_at,
        evidence_document_title=document_title,
        evidence_heading_path=heading_path,
        evidence_pdf_path=pdf_path,
        evidence_pdf_page_start=pdf_page_start,
        evidence_pdf_open_url=pdf_open_url,
    )


def _validate_entity_exists(session: Session, entity_type: str, entity_id: int, label: str) -> None:
    if entity_type == "document":
        exists = session.get(Document, entity_id) is not None
    elif entity_type == "chunk":
        exists = session.get(KnowledgeChunk, entity_id) is not None
    elif entity_type == "note":
        exists = session.get(PersonalNote, entity_id) is not None
    elif entity_type == "tag":
        exists = session.get(KnowledgeTag, entity_id) is not None
    elif entity_type in TAG_BACKED_ENTITY_TYPES:
        tag = session.get(KnowledgeTag, entity_id)
        exists = tag is not None and tag.tag_type == entity_type
    else:
        exists = False

    if not exists:
        raise ValueError(f"{label} {entity_type}:{entity_id} does not exist.")


def _validate_entity_type(entity_type: str, field_name: str) -> None:
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(
            f"Invalid {field_name}: {entity_type}. Allowed: {', '.join(sorted(ALLOWED_ENTITY_TYPES))}."
        )


def _validate_relation_type(relation_type: str) -> None:
    if relation_type not in ALLOWED_RELATION_TYPES:
        raise ValueError(
            f"Invalid relation_type: {relation_type}. Allowed: {', '.join(sorted(ALLOWED_RELATION_TYPES))}."
        )


def _validate_confidence(confidence: float | None) -> None:
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1.")
