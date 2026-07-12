from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import PROJECT_ROOT
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk, NoteEvidenceLink, PersonalNote
from app.services.keyword_search_service import build_pdf_open_url


ALLOWED_NOTE_TYPES = {
    "paper_card",
    "chapter_card",
    "concept_card",
    "reading_note",
    "experiment_log",
    "idea_note",
    "code_reading",
    "meeting_note",
}
ALLOWED_LINK_TYPES = {
    "supports",
    "explains",
    "contradicts",
    "extends",
    "related_to",
    "derived_from",
}
ALLOWED_EVIDENCE_ROLES = {
    "definition",
    "method_detail",
    "experiment_result",
    "metric",
    "limitation",
    "motivation",
    "claim_support",
}
SUMMARY_CHARS = 200
CONTENT_SNIPPET_CHARS = 300
QUOTE_TEXT_MAX_CHARS = 300


@dataclass(frozen=True)
class ImportedNoteResult:
    note_id: int
    created: bool
    updated: bool
    title: str
    note_type: str
    source_path: str | None


@dataclass(frozen=True)
class NoteListItem:
    note_id: int
    title: str
    note_type: str
    scope_path: str | None
    source_path: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NoteDetail:
    note_id: int
    title: str
    note_type: str
    source_path: str | None
    summary: str | None
    content_snippet: str


@dataclass(frozen=True)
class EvidenceLinkResult:
    link_id: int
    created: bool
    note_id: int
    chunk_id: int
    link_type: str
    evidence_role: str


@dataclass(frozen=True)
class NoteEvidenceItem:
    chunk_id: int
    document_title: str
    heading_path: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    link_type: str
    evidence_role: str
    quote_text_snippet: str | None


@dataclass(frozen=True)
class ChunkNoteItem:
    note_id: int
    note_title: str
    note_type: str
    link_type: str
    evidence_role: str


def import_note_md(
    path: str | Path,
    note_type: str,
    document_id: int | None = None,
    scope_type: str | None = None,
    scope_path: str | None = None,
) -> ImportedNoteResult:
    init_db()
    _validate_note_type(note_type)
    note_path = Path(path)
    text = note_path.read_text(encoding="utf-8")
    source_path = _normalize_source_path(note_path)
    title = _extract_title(text, note_path)
    content = _extract_content(text)
    summary = _make_summary(content)
    content_hash = _content_hash(content)

    with SessionLocal() as session:
        if document_id is not None and session.get(Document, document_id) is None:
            raise ValueError(f"document_id does not exist: {document_id}")

        note = _find_existing_note_by_source_path(session, source_path)
        if note is None:
            note = _find_existing_note(
                session=session,
                title=title,
                note_type=note_type,
                document_id=document_id,
                scope_type=scope_type,
                scope_path=scope_path,
            )
        if note is None:
            note = PersonalNote(
                document_id=document_id,
                note_type=note_type,
                scope_type=scope_type,
                scope_path=scope_path,
                source_path=source_path,
                content_hash=content_hash,
                title=title,
                content=content,
                summary=summary,
            )
            session.add(note)
            session.commit()
            session.refresh(note)
            return ImportedNoteResult(
                note_id=note.id,
                created=True,
                updated=False,
                title=note.title,
                note_type=note.note_type,
                source_path=note.source_path,
            )

        note.document_id = document_id
        note.note_type = note_type
        note.scope_type = scope_type
        note.scope_path = scope_path
        note.source_path = source_path
        note.content_hash = content_hash
        note.title = title
        note.content = content
        note.summary = summary
        note.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(note)
        return ImportedNoteResult(
            note_id=note.id,
            created=False,
            updated=True,
            title=note.title,
            note_type=note.note_type,
            source_path=note.source_path,
        )


def list_personal_notes(note_type: str | None = None, limit: int = 20) -> list[NoteListItem]:
    init_db()
    if note_type is not None:
        _validate_note_type(note_type)
    safe_limit = max(1, limit)
    with SessionLocal() as session:
        statement = select(PersonalNote).order_by(PersonalNote.id.desc()).limit(safe_limit)
        if note_type is not None:
            statement = statement.where(PersonalNote.note_type == note_type)
        notes = session.scalars(statement).all()
        return [
            NoteListItem(
                note_id=note.id,
                title=note.title,
                note_type=note.note_type,
                scope_path=note.scope_path,
                source_path=note.source_path,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
            for note in notes
        ]


def show_personal_note(note_id: int) -> NoteDetail:
    init_db()
    with SessionLocal() as session:
        note = session.get(PersonalNote, note_id)
        if note is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        return NoteDetail(
            note_id=note.id,
            title=note.title,
            note_type=note.note_type,
            source_path=note.source_path,
            summary=note.summary,
            content_snippet=_snippet(note.content, CONTENT_SNIPPET_CHARS),
        )


def link_note_to_chunk(
    note_id: int,
    chunk_id: int,
    link_type: str,
    evidence_role: str,
    quote_text: str | None = None,
    confidence: float | None = None,
) -> EvidenceLinkResult:
    init_db()
    _validate_link_type(link_type)
    _validate_evidence_role(evidence_role)
    if quote_text is not None and len(quote_text) > QUOTE_TEXT_MAX_CHARS:
        raise ValueError(f"quote_text must be {QUOTE_TEXT_MAX_CHARS} characters or fewer.")

    with SessionLocal() as session:
        if session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        if session.get(KnowledgeChunk, chunk_id) is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")

        existing = session.scalar(
            select(NoteEvidenceLink).where(
                NoteEvidenceLink.note_id == note_id,
                NoteEvidenceLink.chunk_id == chunk_id,
                NoteEvidenceLink.link_type == link_type,
                NoteEvidenceLink.evidence_role == evidence_role,
            )
        )
        if existing is not None:
            return EvidenceLinkResult(
                link_id=existing.id,
                created=False,
                note_id=note_id,
                chunk_id=chunk_id,
                link_type=link_type,
                evidence_role=evidence_role,
            )

        link = NoteEvidenceLink(
            note_id=note_id,
            chunk_id=chunk_id,
            link_type=link_type,
            evidence_role=evidence_role,
            quote_text=quote_text,
            confidence=confidence,
            created_by="manual",
        )
        session.add(link)
        session.commit()
        session.refresh(link)
        return EvidenceLinkResult(
            link_id=link.id,
            created=True,
            note_id=note_id,
            chunk_id=chunk_id,
            link_type=link_type,
            evidence_role=evidence_role,
        )


def list_note_evidence(note_id: int) -> list[NoteEvidenceItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(PersonalNote, note_id) is None:
            raise ValueError(f"note_id does not exist: {note_id}")
        rows = session.execute(
            select(NoteEvidenceLink, KnowledgeChunk, Document)
            .join(KnowledgeChunk, KnowledgeChunk.id == NoteEvidenceLink.chunk_id)
            .join(Document, Document.id == KnowledgeChunk.document_id)
            .where(NoteEvidenceLink.note_id == note_id)
            .order_by(NoteEvidenceLink.id)
        ).all()
        return [
            NoteEvidenceItem(
                chunk_id=chunk.id,
                document_title=document.title,
                heading_path=chunk.heading_path,
                pdf_path=chunk.pdf_path or document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
                pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                link_type=link.link_type,
                evidence_role=link.evidence_role or "",
                quote_text_snippet=_snippet(link.quote_text, 120) if link.quote_text else None,
            )
            for link, chunk, document in rows
        ]


def list_chunk_notes(chunk_id: int) -> list[ChunkNoteItem]:
    init_db()
    with SessionLocal() as session:
        if session.get(KnowledgeChunk, chunk_id) is None:
            raise ValueError(f"chunk_id does not exist: {chunk_id}")
        rows = session.execute(
            select(NoteEvidenceLink, PersonalNote)
            .join(PersonalNote, PersonalNote.id == NoteEvidenceLink.note_id)
            .where(NoteEvidenceLink.chunk_id == chunk_id)
            .order_by(NoteEvidenceLink.id)
        ).all()
        return [
            ChunkNoteItem(
                note_id=note.id,
                note_title=note.title,
                note_type=note.note_type,
                link_type=link.link_type,
                evidence_role=link.evidence_role or "",
            )
            for link, note in rows
        ]


def _find_existing_note_by_source_path(session: Session, source_path: str | None) -> PersonalNote | None:
    if not source_path:
        return None
    return session.scalar(select(PersonalNote).where(PersonalNote.source_path == source_path))


def _find_existing_note(
    session: Session,
    title: str,
    note_type: str,
    document_id: int | None,
    scope_type: str | None,
    scope_path: str | None,
) -> PersonalNote | None:
    return session.scalar(
        select(PersonalNote).where(
            PersonalNote.title == title,
            PersonalNote.note_type == note_type,
            PersonalNote.source_path.is_(None),
            PersonalNote.document_id.is_(document_id)
            if document_id is None
            else PersonalNote.document_id == document_id,
            PersonalNote.scope_type.is_(scope_type)
            if scope_type is None
            else PersonalNote.scope_type == scope_type,
            PersonalNote.scope_path.is_(scope_path)
            if scope_path is None
            else PersonalNote.scope_path == scope_path,
        )
    )


def _normalize_source_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return path.stem.replace("_", " ").replace("-", " ").strip()


def _extract_content(text: str) -> str:
    lines = text.splitlines()
    if lines and re.match(r"^#\s+.+?\s*$", lines[0]):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _make_summary(content: str) -> str | None:
    compact = " ".join(content.split())
    if not compact:
        return None
    return _snippet(compact, SUMMARY_CHARS)


def _snippet(text: str | None, max_chars: int) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _validate_note_type(note_type: str) -> None:
    if note_type not in ALLOWED_NOTE_TYPES:
        raise ValueError(f"Invalid note_type: {note_type}. Allowed: {', '.join(sorted(ALLOWED_NOTE_TYPES))}.")


def _validate_link_type(link_type: str) -> None:
    if link_type not in ALLOWED_LINK_TYPES:
        raise ValueError(f"Invalid link_type: {link_type}. Allowed: {', '.join(sorted(ALLOWED_LINK_TYPES))}.")


def _validate_evidence_role(evidence_role: str) -> None:
    if evidence_role not in ALLOWED_EVIDENCE_ROLES:
        raise ValueError(
            f"Invalid evidence_role: {evidence_role}. Allowed: {', '.join(sorted(ALLOWED_EVIDENCE_ROLES))}."
        )
