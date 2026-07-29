from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.database import connect_immutable_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH
from app.domains.retrieval.result_contracts import NotebookFragment, PublicEvidence


MAX_COHERENT_TEXT_CHARS = 2_400
_SENTENCE_END = re.compile(r"[.!?。！？](?:[\"'’”)]*)?(?=\s|$)")


@dataclass(frozen=True)
class CoherentPdfEvidence:
    text: str
    pdf_page: int | None
    page_label: str | None
    heading: str | None
    section: str | None
    context_before: str | None
    context_after: str | None


def serialize_public_evidence(
    fragment: NotebookFragment,
    *,
    selection_rank: int | None = None,
    include_context: bool = True,
    db_path: Path | None = None,
) -> PublicEvidence:
    """Serialize one internal fragment through the only public evidence whitelist."""

    coherent: CoherentPdfEvidence | None = None
    if fragment.source_type == "pdf_chunk":
        coherent = build_coherent_pdf_evidence(
            fragment,
            db_path=db_path or DEFAULT_DB_PATH,
        )
    return PublicEvidence(
        fragment_id=fragment.fragment_id,
        source_type=fragment.source_type,
        document_id=fragment.document_id,
        document_title=fragment.document_title,
        document_type=fragment.document_type,
        pdf_page=coherent.pdf_page if coherent else fragment.pdf_page,
        page_label=coherent.page_label if coherent else fragment.page_label,
        heading=coherent.heading if coherent else fragment.heading,
        section=coherent.section if coherent else fragment.section,
        coherent_text=coherent.text if coherent else None,
        user_note=fragment.note_text if fragment.source_type != "pdf_chunk" else None,
        selected_source_text=(
            fragment.selected_text if fragment.source_type != "pdf_chunk" else None
        ),
        context_before=(
            (coherent.context_before if coherent else fragment.context_before)
            if include_context
            else None
        ),
        context_after=(
            (coherent.context_after if coherent else fragment.context_after)
            if include_context
            else None
        ),
        tags=list(fragment.tags),
        provenance=_public_provenance(fragment),
        open_target=fragment.open_target,
        selection_rank=selection_rank,
    )


def build_coherent_pdf_evidence(
    fragment: NotebookFragment,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    maximum_chars: int = MAX_COHERENT_TEXT_CHARS,
) -> CoherentPdfEvidence:
    fallback = CoherentPdfEvidence(
        text=_limit_complete(_clean_text(fragment.text), maximum_chars),
        pdf_page=fragment.pdf_page,
        page_label=fragment.page_label,
        heading=fragment.heading,
        section=fragment.section,
        context_before=_clean_optional(fragment.context_before),
        context_after=_clean_optional(fragment.context_after),
    )
    if fragment.chunk_id is None or fragment.document_id is None:
        return fallback
    try:
        with connect_immutable_readonly_sqlite(Path(db_path)) as connection:
            current = connection.execute(
                """
                SELECT id, document_id, chunk_index, heading_path, chunk_text,
                       overlap_before, overlap_after, pdf_page_start, pdf_page_end,
                       chapter_id
                FROM knowledge_chunks
                WHERE id = ? AND document_id = ?
                """,
                (fragment.chunk_id, fragment.document_id),
            ).fetchone()
            if current is None:
                return fallback
            previous = _neighbor(connection, current, before=True)
            following = _neighbor(connection, current, before=False)
    except (OSError, sqlite3.Error):
        return fallback

    prefix = str(current["overlap_before"] or "")
    completed_prefix = _completed_prefix(previous, prefix)
    text = completed_prefix + str(current["chunk_text"] or "")
    text = _trim_incomplete_start(text)
    context_before = _previous_context(previous, prefix)

    minimum_end = len(text)
    suffix = str(current["overlap_after"] or "")
    text = _join_words(text, suffix)
    remaining_following = ""
    following_start = len(text)
    if following is not None and not _has_sentence_end(text, minimum_end):
        following_text = str(following["chunk_text"] or "")
        remaining_following = _after_overlap(following_text, suffix)
        text = _join_words(text, remaining_following)
    text, consumed = _complete_end(text, minimum_end)
    context_after = _following_context(
        remaining_following,
        max(0, consumed - following_start),
    )
    text = _limit_complete(_clean_text(text), maximum_chars)

    page_values = [
        _int_or_none(current["pdf_page_start"]),
        _int_or_none(current["pdf_page_end"]),
    ]
    if prefix and previous is not None:
        page_values.extend(
            [_int_or_none(previous["pdf_page_start"]), _int_or_none(previous["pdf_page_end"])]
        )
    if remaining_following and following is not None:
        page_values.extend(
            [_int_or_none(following["pdf_page_start"]), _int_or_none(following["pdf_page_end"])]
        )
    pages = [value for value in page_values if value is not None]
    page_start = min(pages) if pages else fragment.pdf_page
    page_end = max(pages) if pages else fragment.pdf_page
    page_label = (
        str(page_start)
        if page_start is not None and page_end == page_start
        else f"{page_start}–{page_end}"
        if page_start is not None and page_end is not None
        else fragment.page_label
    )
    heading = _clean_optional(current["heading_path"]) or fragment.heading
    return CoherentPdfEvidence(
        text=text or fallback.text,
        pdf_page=page_start,
        page_label=page_label,
        heading=heading,
        section=heading or fragment.section,
        context_before=context_before,
        context_after=context_after,
    )


def _neighbor(
    connection: sqlite3.Connection,
    current: sqlite3.Row,
    *,
    before: bool,
) -> sqlite3.Row | None:
    operator = "<" if before else ">"
    direction = "DESC" if before else "ASC"
    row = connection.execute(
        f"""
        SELECT id, document_id, chunk_index, heading_path, chunk_text,
               overlap_before, overlap_after, pdf_page_start, pdf_page_end,
               chapter_id
        FROM knowledge_chunks
        WHERE document_id = ? AND chunk_index {operator} ?
        ORDER BY chunk_index {direction}, id {direction}
        LIMIT 1
        """,
        (current["document_id"], current["chunk_index"]),
    ).fetchone()
    if row is None or not _same_section(current, row):
        return None
    return row


def _same_section(current: sqlite3.Row, neighbor: sqlite3.Row) -> bool:
    current_chapter = _int_or_none(current["chapter_id"])
    neighbor_chapter = _int_or_none(neighbor["chapter_id"])
    if current_chapter is not None or neighbor_chapter is not None:
        return current_chapter == neighbor_chapter
    current_heading = _clean_optional(current["heading_path"])
    neighbor_heading = _clean_optional(neighbor["heading_path"])
    return not (current_heading and neighbor_heading) or current_heading == neighbor_heading


def _public_provenance(fragment: NotebookFragment) -> dict[str, Any]:
    source = {
        "pdf_chunk": "pdf",
        "zotero_annotation_comment": "zotero_annotation",
        "zotero_child_note": "zotero_note",
        "zotero_inspiration_note": "zotero_note",
    }[fragment.source_type]
    return {
        key: value
        for key, value in {
            "source": source,
            "document_title": fragment.document_title,
            "page": fragment.page_label or fragment.pdf_page,
            "zotero_item_key": fragment.zotero_item_key,
            "zotero_attachment_key": fragment.zotero_attachment_key,
            "annotation_key": fragment.zotero_annotation_key,
            "fragment_id": fragment.fragment_id,
        }.items()
        if value is not None
    }


def _join_words(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left[-1].isspace() or right[0].isspace():
        return left + right
    return left + " " + right


def _trim_incomplete_start(text: str) -> str:
    cleaned = text.lstrip()
    if not cleaned or not cleaned[0].islower():
        return cleaned
    match = _SENTENCE_END.search(cleaned)
    if match is None:
        return cleaned
    return cleaned[match.end() :].lstrip()


def _after_overlap(text: str, overlap: str) -> str:
    overlap = overlap.strip()
    if overlap:
        index = text.find(overlap)
        if index >= 0:
            return text[index + len(overlap) :]
    return text


def _has_sentence_end(text: str, minimum: int) -> bool:
    return _SENTENCE_END.search(text, min(minimum, len(text))) is not None


def _complete_end(text: str, minimum: int) -> tuple[str, int]:
    match = _SENTENCE_END.search(text, min(minimum, len(text)))
    if match is None:
        return text.rstrip(), len(text)
    return text[: match.end()].rstrip(), match.end()


def _previous_context(previous: sqlite3.Row | None, overlap: str) -> str | None:
    if previous is None:
        return None
    text = str(previous["chunk_text"] or "")
    if overlap:
        index = text.rfind(overlap)
        if index >= 0:
            text = text[:index]
    matches = list(_SENTENCE_END.finditer(text))
    if len(matches) < 2:
        return _clean_optional(text[-400:])
    return _clean_optional(text[matches[-2].end() : matches[-1].end()])


def _completed_prefix(previous: sqlite3.Row | None, overlap: str) -> str:
    if previous is None or not overlap:
        return overlap
    previous_text = str(previous["chunk_text"] or "")
    index = previous_text.rfind(overlap)
    if index < 0:
        return overlap
    starts = [match.end() for match in _SENTENCE_END.finditer(previous_text[:index])]
    start = starts[-1] if starts else 0
    # ``overlap_before`` is the authoritative hand-off boundary.  Some legacy
    # chunks retain additional text after that boundary (and the next chunk
    # begins with the continuation), so carrying the whole previous tail would
    # splice the continuation into the stale duplicate.
    return previous_text[start : index + len(overlap)].lstrip()


def _following_context(remaining: str, consumed: int) -> str | None:
    if not remaining:
        return None
    tail = remaining[min(consumed, len(remaining)) :].lstrip()
    match = _SENTENCE_END.search(tail)
    return _clean_optional(tail[: match.end()] if match else tail[:400])


def _limit_complete(text: str, maximum: int) -> str:
    if len(text) <= maximum:
        return text
    matches = [match for match in _SENTENCE_END.finditer(text[:maximum])]
    if matches:
        return text[: matches[-1].end()].rstrip()
    return text[:maximum].rstrip()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_optional(value: Any) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
