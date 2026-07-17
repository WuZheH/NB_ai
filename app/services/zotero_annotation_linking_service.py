from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from sqlalchemy import select

from app.core.paths import ZOTERO_LIBRARY_DB_PATH
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk


ZOTERO_SQLITE_PATH = ZOTERO_LIBRARY_DB_PATH
ZOTERO_OPEN_PDF_PREFIX = "zotero://open-pdf/library/items/"


@dataclass(frozen=True)
class EvidenceAnnotationSource:
    document_id: int
    document_title: str
    chunk_id: int
    chunk_text: str
    snippet: str
    pdf_page_start: int | None
    zotero_open_url: str | None


@dataclass(frozen=True)
class ZoteroAnnotation:
    zotero_attachment_key: str
    zotero_annotation_key: str
    annotation_text: str | None
    annotation_comment: str | None
    annotation_page: int | None
    annotation_position: dict[str, Any] | None
    annotation_color: str | None
    annotation_type: int | None


@dataclass(frozen=True)
class ZoteroAnnotationCandidate:
    candidate_status: str
    document_id: int
    chunk_id: int
    zotero_attachment_key: str
    zotero_annotation_key: str
    annotation_text: str | None
    annotation_comment: str | None
    annotation_page: int | None
    annotation_position: dict[str, Any] | None
    match_method: str
    confidence: str
    warnings: list[str] = field(default_factory=list)
    zotero_annotation_uri_candidate: str | None = None


@dataclass(frozen=True)
class ZoteroAnnotationCandidateResult:
    status: str
    implementation_status: str
    chunk_id: int
    document_id: int | None
    candidates: list[ZoteroAnnotationCandidate]
    message: str | None = None


def list_zotero_annotations_for_attachment(attachment_key: str) -> list[ZoteroAnnotation]:
    if not attachment_key:
        return []
    if not ZOTERO_SQLITE_PATH.exists() or not ZOTERO_SQLITE_PATH.is_file():
        return []
    uri = ZOTERO_SQLITE_PATH.as_posix()
    with sqlite3.connect(f"file:{uri}?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                annotation_items.key AS annotation_key,
                attachment_items.key AS attachment_key,
                itemAnnotations.type,
                itemAnnotations.text,
                itemAnnotations.comment,
                itemAnnotations.color,
                itemAnnotations.pageLabel,
                itemAnnotations.position
            FROM itemAnnotations
            JOIN items AS annotation_items
                ON annotation_items.itemID = itemAnnotations.itemID
            JOIN items AS attachment_items
                ON attachment_items.itemID = itemAnnotations.parentItemID
            WHERE attachment_items.key = ?
            ORDER BY itemAnnotations.sortIndex, annotation_items.key
            """,
            (attachment_key,),
        ).fetchall()
    return [
        ZoteroAnnotation(
            zotero_attachment_key=row["attachment_key"],
            zotero_annotation_key=row["annotation_key"],
            annotation_text=row["text"],
            annotation_comment=row["comment"],
            annotation_page=_annotation_page(row["pageLabel"], row["position"]),
            annotation_position=_parse_position(row["position"]),
            annotation_color=row["color"],
            annotation_type=row["type"],
        )
        for row in rows
    ]


def find_annotation_candidates_for_evidence(document_id: int, chunk_id: int) -> ZoteroAnnotationCandidateResult:
    source = _load_evidence_source(document_id, chunk_id)
    attachment_key = _attachment_key_from_open_pdf_uri(source.zotero_open_url)
    if not attachment_key:
        return ZoteroAnnotationCandidateResult(
            status="zotero_attachment_unavailable",
            implementation_status="connected",
            chunk_id=chunk_id,
            document_id=source.document_id,
            candidates=[],
            message="No Zotero PDF attachment key is available for this evidence.",
        )
    annotations = list_zotero_annotations_for_attachment(attachment_key)
    candidates = build_annotation_candidates(source, attachment_key, annotations)
    return ZoteroAnnotationCandidateResult(
        status="ok",
        implementation_status="connected",
        chunk_id=chunk_id,
        document_id=source.document_id,
        candidates=candidates,
    )


def build_annotation_candidates(
    source: EvidenceAnnotationSource,
    attachment_key: str,
    annotations: list[ZoteroAnnotation],
) -> list[ZoteroAnnotationCandidate]:
    scored = []
    for annotation in annotations:
        score, match_method, confidence = compute_annotation_match_score(
            source.chunk_text or source.snippet,
            annotation.annotation_text,
            source.pdf_page_start,
            annotation.annotation_page,
        )
        if match_method != "unknown":
            scored.append((score, annotation, match_method, confidence))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []
    top_score = scored[0][0]
    close = [item for item in scored if item[0] >= top_score - 0.05]
    selected = close if len(close) > 1 else [scored[0]]
    ambiguous = len(selected) > 1
    return [
        _candidate(
            source=source,
            attachment_key=attachment_key,
            annotation=annotation,
            match_method=match_method,
            confidence="low" if ambiguous else confidence,
            warnings=["ambiguous_multiple_annotation_candidates"] if ambiguous else [],
        )
        for _score, annotation, match_method, confidence in selected
    ]


def normalize_text_for_annotation_match(text: Any) -> str:
    normalized = str(text or "").casefold()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


def compute_annotation_match_score(
    chunk_snippet: Any,
    annotation_text: Any,
    chunk_page: int | None = None,
    annotation_page: int | None = None,
) -> tuple[float, str, str]:
    chunk_norm = normalize_text_for_annotation_match(chunk_snippet)
    annotation_norm = normalize_text_for_annotation_match(annotation_text)
    same_page = bool(chunk_page and annotation_page and int(chunk_page) == int(annotation_page))
    if annotation_norm and chunk_norm:
        if annotation_norm == chunk_norm or annotation_norm in chunk_norm:
            return (1.0 if same_page else 0.95, "text_exact" if annotation_norm == chunk_norm else "text_contains", "high")
        overlap = _token_overlap(chunk_norm, annotation_norm)
        if same_page and overlap >= 0.65:
            return (0.85, "normalized_overlap", "medium")
        if overlap >= 0.8:
            return (0.75, "normalized_overlap", "medium")
    if same_page:
        return (0.35, "page_only", "low")
    return (0.0, "unknown", "low")


def build_zotero_annotation_uri(attachment_key: str | None, page: int | None, annotation_key: str | None) -> str | None:
    if not attachment_key or not annotation_key:
        return None
    page_query = f"?page={page}" if page else ""
    separator = "&" if page_query else "?"
    return f"{ZOTERO_OPEN_PDF_PREFIX}{attachment_key}{page_query}{separator}annotation={annotation_key}"


def _load_evidence_source(document_id: int, chunk_id: int) -> EvidenceAnnotationSource:
    with SessionLocal() as session:
        chunk = session.get(KnowledgeChunk, chunk_id)
        if chunk is None or chunk.document_id != document_id:
            raise ValueError(f"Evidence chunk {chunk_id} not found for document {document_id}.")
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document {document_id} not found.")
        text = chunk.chunk_text or ""
        snippet = " ".join(text.split())[:220]
        return EvidenceAnnotationSource(
            document_id=document.id,
            document_title=document.title,
            chunk_id=chunk.id,
            chunk_text=text,
            snippet=snippet,
            pdf_page_start=chunk.pdf_page_start,
            zotero_open_url=chunk.zotero_open_url,
        )


def _candidate(
    source: EvidenceAnnotationSource,
    attachment_key: str,
    annotation: ZoteroAnnotation,
    match_method: str,
    confidence: str,
    warnings: list[str],
) -> ZoteroAnnotationCandidate:
    return ZoteroAnnotationCandidate(
        candidate_status="suggested",
        document_id=source.document_id,
        chunk_id=source.chunk_id,
        zotero_attachment_key=attachment_key,
        zotero_annotation_key=annotation.zotero_annotation_key,
        annotation_text=annotation.annotation_text,
        annotation_comment=annotation.annotation_comment,
        annotation_page=annotation.annotation_page,
        annotation_position=annotation.annotation_position,
        match_method=match_method,
        confidence=confidence,
        warnings=warnings,
        zotero_annotation_uri_candidate=build_zotero_annotation_uri(
            attachment_key,
            annotation.annotation_page or source.pdf_page_start,
            annotation.zotero_annotation_key,
        ),
    )


def _attachment_key_from_open_pdf_uri(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text.startswith(ZOTERO_OPEN_PDF_PREFIX):
        return None
    tail = text.removeprefix(ZOTERO_OPEN_PDF_PREFIX)
    key = tail.split("?", 1)[0].split("#", 1)[0].strip()
    return key or None


def _annotation_page(page_label: Any, position: Any) -> int | None:
    label_text = str(page_label or "").strip()
    if label_text.isdigit():
        return int(label_text)
    parsed = _parse_position(position)
    page_index = parsed.get("pageIndex") if parsed else None
    if isinstance(page_index, int):
        return page_index + 1
    return None


def _parse_position(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
