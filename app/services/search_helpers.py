from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChunkTag, KnowledgeTag, NoteEvidenceLink, PersonalNote


def load_related_note_titles(session: Session, chunk_ids: list[int]) -> dict[int, list[str]]:
    if not chunk_ids:
        return {}

    statement = (
        select(NoteEvidenceLink.chunk_id, PersonalNote.title)
        .join(PersonalNote, PersonalNote.id == NoteEvidenceLink.note_id)
        .where(NoteEvidenceLink.chunk_id.in_(chunk_ids))
        .order_by(PersonalNote.title)
    )
    titles_by_chunk: dict[int, list[str]] = {chunk_id: [] for chunk_id in chunk_ids}
    for chunk_id, title in session.execute(statement):
        if title not in titles_by_chunk[chunk_id]:
            titles_by_chunk[chunk_id].append(title)
    return titles_by_chunk


def load_chunk_tags(session: Session, chunk_ids: list[int]) -> dict[int, list[str]]:
    if not chunk_ids:
        return {}

    statement = (
        select(ChunkTag.chunk_id, KnowledgeTag.name, KnowledgeTag.tag_type)
        .join(KnowledgeTag, KnowledgeTag.id == ChunkTag.tag_id)
        .where(ChunkTag.chunk_id.in_(chunk_ids))
        .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
    )
    tags_by_chunk: dict[int, list[str]] = {chunk_id: [] for chunk_id in chunk_ids}
    for chunk_id, name, tag_type in session.execute(statement):
        label = f"{tag_type}:{name}"
        if label not in tags_by_chunk[chunk_id]:
            tags_by_chunk[chunk_id].append(label)
    return tags_by_chunk


def make_snippet(text: str, query: str, max_chars: int, *, match_tokens: bool = False) -> str:
    compact_text = " ".join(text.split())
    if len(compact_text) <= max_chars:
        return compact_text

    if match_tokens:
        for token in query.split():
            index = compact_text.lower().find(token.lower())
            if index >= 0:
                start = max(0, index - ((max_chars - len(token)) // 2))
                end = min(len(compact_text), start + max_chars)
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(compact_text) else ""
                return prefix + compact_text[start:end].strip() + suffix
        return compact_text[: max_chars - 3].rstrip() + "..."

    lower_text = compact_text.lower()
    lower_query = query.lower()
    match_index = lower_text.find(lower_query)
    if match_index < 0:
        return compact_text[: max_chars - 3].rstrip() + "..."

    context_before = max(0, (max_chars - len(query)) // 2)
    start = max(0, match_index - context_before)
    end = min(len(compact_text), start + max_chars)
    snippet = compact_text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(compact_text):
        snippet = snippet.rstrip() + "..."
    return snippet
