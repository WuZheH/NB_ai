from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import sqlite3
from typing import Any

from sqlalchemy import select

from app.core.paths import ZOTERO_LIBRARY_DB_PATH, ZOTERO_LIBRARY_DIR
from app.db.session import SessionLocal
from app.models import Document
from app.services.library_service import resolve_safe_pdf_path


ZOTERO_DATA_DIR = ZOTERO_LIBRARY_DIR
ZOTERO_SQLITE_PATH = ZOTERO_LIBRARY_DB_PATH
ZOTERO_OPEN_PDF_TEMPLATE = "zotero://open-pdf/library/items/{attachment_key}?page={page}"
ZOTERO_SELECT_URI_TEMPLATE = "zotero://select/library/items/{item_key}"


@dataclass(frozen=True)
class NotebookDocumentMetadata:
    document_id: int
    title: str
    pdf_path: str | None
    zotero_key: str | None
    source_path: str | None
    source_type: str | None
    document_type: str | None


@dataclass(frozen=True)
class ZoteroAttachmentMetadata:
    zotero_item_key: str | None
    zotero_attachment_key: str
    title: str | None
    attachment_path: str | None
    resolved_attachment_path: str | None


@dataclass(frozen=True)
class ZoteroLinkCandidate:
    candidate_status: str
    zotero_item_key: str | None
    zotero_attachment_key: str | None
    zotero_select_uri: str | None
    zotero_open_pdf_uri_template: str | None
    match_method: str
    confidence: str
    reason: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ZoteroLinkCandidateResult:
    status: str
    implementation_status: str
    document_id: int
    document_title: str | None
    candidates: list[ZoteroLinkCandidate]
    message: str | None = None


def suggest_zotero_link_candidates(document_id: int) -> ZoteroLinkCandidateResult:
    document = _load_document_metadata(document_id)
    attachments = discover_zotero_pdf_attachments()
    candidates = build_zotero_link_candidates(document, attachments)
    return ZoteroLinkCandidateResult(
        status="ok",
        implementation_status="connected",
        document_id=document.document_id,
        document_title=document.title,
        candidates=candidates,
    )


def safe_unavailable_result(document_id: int, message: str | None = None) -> ZoteroLinkCandidateResult:
    return ZoteroLinkCandidateResult(
        status="zotero_metadata_unavailable",
        implementation_status="connected",
        document_id=document_id,
        document_title=None,
        candidates=[],
        message=message or "Zotero metadata is unavailable in this read-only environment.",
    )


def build_zotero_link_candidates(
    document: NotebookDocumentMetadata,
    attachments: list[ZoteroAttachmentMetadata],
) -> list[ZoteroLinkCandidate]:
    matches: list[tuple[ZoteroAttachmentMetadata, str, str, str]] = []
    document_pdf = _safe_resolved_pdf_path(document.pdf_path)
    document_title = _normalize_title(document.title)

    if document_pdf:
        for attachment in attachments:
            if attachment.resolved_attachment_path and _normalize_path(attachment.resolved_attachment_path) == document_pdf:
                matches.append((attachment, "pdf_path_exact", "high", "Document PDF path matches Zotero attachment path exactly."))

    if not matches and document_title:
        title_matches = [
            attachment
            for attachment in attachments
            if _normalize_title(attachment.title) == document_title
        ]
        confidence = "high" if len(title_matches) == 1 else "medium"
        for attachment in title_matches:
            matches.append((attachment, "title_exact", confidence, "Normalized document title matches Zotero parent item title."))

    if not matches and document_title:
        for attachment in attachments:
            candidate_title = _normalize_title(attachment.title)
            if not candidate_title:
                continue
            ratio = SequenceMatcher(None, document_title, candidate_title).ratio()
            if ratio >= 0.9 or _token_coverage(document_title, candidate_title) >= 0.88:
                matches.append((attachment, "title_fuzzy", "low", "Conservative fuzzy title match; review required."))

    if len(matches) > 1:
        return [_candidate(attachment, method, "low", reason, ["ambiguous_multiple_matches"]) for attachment, method, _confidence, reason in matches]
    return [_candidate(attachment, method, confidence, reason, []) for attachment, method, confidence, reason in matches]


def discover_zotero_pdf_attachments(zotero_sqlite_path: Path = ZOTERO_SQLITE_PATH) -> list[ZoteroAttachmentMetadata]:
    if not zotero_sqlite_path.exists() or not zotero_sqlite_path.is_file():
        raise FileNotFoundError("Zotero sqlite database not found.")
    try:
        rows = _fetch_zotero_pdf_attachment_rows(zotero_sqlite_path, immutable=False)
    except sqlite3.OperationalError as exc:
        if "database is locked" not in str(exc).casefold():
            raise
        rows = _fetch_zotero_pdf_attachment_rows(zotero_sqlite_path, immutable=True)
    return [
        ZoteroAttachmentMetadata(
            zotero_item_key=row["parent_key"],
            zotero_attachment_key=row["attachment_key"],
            title=row["title"],
            attachment_path=row["attachment_path"],
            resolved_attachment_path=_resolve_zotero_attachment_path(row["attachment_key"], row["attachment_path"]),
        )
        for row in rows
        if row["attachment_key"]
    ]


def _fetch_zotero_pdf_attachment_rows(zotero_sqlite_path: Path, immutable: bool) -> list[sqlite3.Row]:
    with _connect_zotero_sqlite_readonly(zotero_sqlite_path, immutable=immutable) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                attachment_items.key AS attachment_key,
                parent_items.key AS parent_key,
                itemAttachments.path AS attachment_path,
                itemDataValues.value AS title
            FROM itemAttachments
            JOIN items AS attachment_items
                ON attachment_items.itemID = itemAttachments.itemID
            LEFT JOIN items AS parent_items
                ON parent_items.itemID = itemAttachments.parentItemID
            LEFT JOIN itemData
                ON itemData.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND itemData.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'title' LIMIT 1)
            LEFT JOIN itemDataValues
                ON itemDataValues.valueID = itemData.valueID
            WHERE lower(COALESCE(itemAttachments.contentType, '')) = 'application/pdf'
                OR lower(COALESCE(itemAttachments.path, '')) LIKE '%.pdf'
            ORDER BY attachment_items.key
            """
        ).fetchall()


def _connect_zotero_sqlite_readonly(zotero_sqlite_path: Path, immutable: bool = False):
    uri = zotero_sqlite_path.as_posix()
    immutable_part = "&immutable=1" if immutable else ""
    return sqlite3.connect(f"file:{uri}?mode=ro{immutable_part}", uri=True)


def build_zotero_open_pdf_uri_template(attachment_key: str | None) -> str | None:
    if not attachment_key:
        return None
    return ZOTERO_OPEN_PDF_TEMPLATE.format(attachment_key=attachment_key, page="{page}")


def build_zotero_select_uri(item_key: str | None) -> str | None:
    if not item_key:
        return None
    return ZOTERO_SELECT_URI_TEMPLATE.format(item_key=item_key)


def _load_document_metadata(document_id: int) -> NotebookDocumentMetadata:
    with SessionLocal() as session:
        document = session.scalars(select(Document).where(Document.id == document_id)).first()
        if document is None:
            raise ValueError(f"Document {document_id} not found.")
        return NotebookDocumentMetadata(
            document_id=document.id,
            title=document.title,
            pdf_path=document.pdf_path,
            zotero_key=document.zotero_key,
            source_path=document.source_path,
            source_type="zotero" if document.zotero_key else ("pdf" if document.pdf_path else None),
            document_type=document.document_type,
        )


def _candidate(
    attachment: ZoteroAttachmentMetadata,
    match_method: str,
    confidence: str,
    reason: str,
    warnings: list[str],
) -> ZoteroLinkCandidate:
    return ZoteroLinkCandidate(
        candidate_status="suggested",
        zotero_item_key=attachment.zotero_item_key,
        zotero_attachment_key=attachment.zotero_attachment_key,
        zotero_select_uri=build_zotero_select_uri(attachment.zotero_item_key),
        zotero_open_pdf_uri_template=build_zotero_open_pdf_uri_template(attachment.zotero_attachment_key),
        match_method=match_method,
        confidence=confidence,
        reason=reason,
        warnings=warnings,
    )


def _safe_resolved_pdf_path(pdf_path: str | None) -> str | None:
    resolved = resolve_safe_pdf_path(pdf_path)
    return _normalize_path(str(resolved)) if resolved else None


def _resolve_zotero_attachment_path(attachment_key: str, attachment_path: str | None) -> str | None:
    if not attachment_path:
        return None
    text = str(attachment_path)
    if text.startswith("storage:"):
        return str((ZOTERO_DATA_DIR / "storage" / attachment_key / text.removeprefix("storage:")).resolve(strict=False))
    if text.startswith("attachments:"):
        return str((ZOTERO_DATA_DIR / text.removeprefix("attachments:")).resolve(strict=False))
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate.resolve(strict=False))
    return None


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path).resolve(strict=False)).casefold()


def _normalize_title(title: Any) -> str:
    text = str(title or "").casefold()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _token_coverage(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
