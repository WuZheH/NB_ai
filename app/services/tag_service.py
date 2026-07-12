from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import ChunkTag, KnowledgeChunk, KnowledgeTag, NoteTag, PersonalNote


ALLOWED_TAG_TYPES = {
    "task",
    "method",
    "dataset",
    "metric",
    "problem",
    "limitation",
    "model",
    "experiment",
    "concept",
    "paper",
    "code",
    "note",
    "idea",
}


@dataclass(frozen=True)
class TagResult:
    tag_id: int
    name: str
    tag_type: str
    description: str | None
    created: bool
    updated: bool


@dataclass(frozen=True)
class TagListItem:
    tag_id: int
    name: str
    tag_type: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TagBindingResult:
    binding_id: int
    created: bool
    owner_id: int
    tag_id: int
    tag_name: str
    tag_type: str


@dataclass(frozen=True)
class TaggedChunkItem:
    chunk_id: int
    document_title: str
    heading_path: str
    pdf_path: str | None
    pdf_page_start: int | None


@dataclass(frozen=True)
class TaggedNoteItem:
    note_id: int
    note_title: str
    note_type: str
    source_path: str | None


def create_tag(name: str, tag_type: str, description: str | None = None) -> TagResult:
    init_db()
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("tag name must not be empty.")
    _validate_tag_type(tag_type)

    with SessionLocal() as session:
        tag = session.scalar(
            select(KnowledgeTag).where(
                KnowledgeTag.name == clean_name,
                KnowledgeTag.tag_type == tag_type,
            )
        )
        if tag is None:
            tag = KnowledgeTag(name=clean_name, tag_type=tag_type, description=description)
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return TagResult(tag.id, tag.name, tag.tag_type, tag.description, created=True, updated=False)

        if description is not None and description != tag.description:
            tag.description = description
            tag.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(tag)
            return TagResult(tag.id, tag.name, tag.tag_type, tag.description, created=False, updated=True)

        return TagResult(tag.id, tag.name, tag.tag_type, tag.description, created=False, updated=False)


def list_tags(tag_type: str | None = None, limit: int = 50) -> list[TagListItem]:
    init_db()
    if tag_type is not None:
        _validate_tag_type(tag_type)
    statement = select(KnowledgeTag).order_by(KnowledgeTag.tag_type, KnowledgeTag.name).limit(max(1, limit))
    if tag_type is not None:
        statement = statement.where(KnowledgeTag.tag_type == tag_type)

    with SessionLocal() as session:
        return [
            TagListItem(
                tag_id=tag.id,
                name=tag.name,
                tag_type=tag.tag_type,
                description=tag.description,
                created_at=tag.created_at,
                updated_at=tag.updated_at,
            )
            for tag in session.scalars(statement).all()
        ]


def tag_chunk(chunk_id: int, tag_id: int) -> TagBindingResult:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeChunk, chunk_id) is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")
        tag = session.get(KnowledgeTag, tag_id)
        if tag is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")

        binding = session.scalar(
            select(ChunkTag).where(ChunkTag.chunk_id == chunk_id, ChunkTag.tag_id == tag_id)
        )
        if binding is None:
            binding = ChunkTag(chunk_id=chunk_id, tag_id=tag_id)
            session.add(binding)
            session.commit()
            session.refresh(binding)
            return TagBindingResult(binding.id, True, chunk_id, tag.id, tag.name, tag.tag_type)
        return TagBindingResult(binding.id, False, chunk_id, tag.id, tag.name, tag.tag_type)


def tag_note(note_id: int, tag_id: int) -> TagBindingResult:
    init_db()
    with SessionLocal() as session:
        if session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        tag = session.get(KnowledgeTag, tag_id)
        if tag is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")

        binding = session.scalar(select(NoteTag).where(NoteTag.note_id == note_id, NoteTag.tag_id == tag_id))
        if binding is None:
            binding = NoteTag(note_id=note_id, tag_id=tag_id)
            session.add(binding)
            session.commit()
            session.refresh(binding)
            return TagBindingResult(binding.id, True, note_id, tag.id, tag.name, tag.tag_type)
        return TagBindingResult(binding.id, False, note_id, tag.id, tag.name, tag.tag_type)


def list_chunk_tags(chunk_id: int) -> list[TagListItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeChunk, chunk_id) is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")
        rows = session.execute(
            select(KnowledgeTag)
            .join(ChunkTag, ChunkTag.tag_id == KnowledgeTag.id)
            .where(ChunkTag.chunk_id == chunk_id)
            .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
        ).scalars()
        return [_to_tag_list_item(tag) for tag in rows.all()]


def list_note_tags(note_id: int) -> list[TagListItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        rows = session.execute(
            select(KnowledgeTag)
            .join(NoteTag, NoteTag.tag_id == KnowledgeTag.id)
            .where(NoteTag.note_id == note_id)
            .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
        ).scalars()
        return [_to_tag_list_item(tag) for tag in rows.all()]


def list_tagged_chunks(tag_id: int) -> list[TaggedChunkItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeTag, tag_id) is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")
        rows = session.execute(
            select(KnowledgeChunk)
            .join(ChunkTag, ChunkTag.chunk_id == KnowledgeChunk.id)
            .where(ChunkTag.tag_id == tag_id)
            .order_by(KnowledgeChunk.id)
        ).scalars().all()
        return [
            TaggedChunkItem(
                chunk_id=chunk.id,
                document_title=chunk.document.title,
                heading_path=chunk.heading_path,
                pdf_path=chunk.pdf_path or chunk.document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
            )
            for chunk in rows
        ]


def list_tagged_notes(tag_id: int) -> list[TaggedNoteItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeTag, tag_id) is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")
        notes = session.execute(
            select(PersonalNote)
            .join(NoteTag, NoteTag.note_id == PersonalNote.id)
            .where(NoteTag.tag_id == tag_id)
            .order_by(PersonalNote.id)
        ).scalars().all()
        return [
            TaggedNoteItem(
                note_id=note.id,
                note_title=note.title,
                note_type=note.note_type,
                source_path=note.source_path,
            )
            for note in notes
        ]


def _to_tag_list_item(tag: KnowledgeTag) -> TagListItem:
    return TagListItem(
        tag_id=tag.id,
        name=tag.name,
        tag_type=tag.tag_type,
        description=tag.description,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
    )


def _validate_tag_type(tag_type: str) -> None:
    if tag_type not in ALLOWED_TAG_TYPES:
        raise ValueError(f"Invalid tag_type: {tag_type}. Allowed: {', '.join(sorted(ALLOWED_TAG_TYPES))}.")
