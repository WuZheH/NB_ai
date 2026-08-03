from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Document, ZoteroPdfSource
from app.services.book_import_service import (
    apply_prepared_book_import,
    extract_pdf_outline_chapter_candidates,
    prepare_book_import,
    resolve_pdf_path,
)
from app.services.pdf_backend_service import PdfBackendUnavailableError, load_fitz_backend
from app.services.pdf_parser_backends import DEFAULT_PDF_PARSER_BACKEND, MARKER_SURYA_PAGE_BLOCKS_BACKEND


DOCUMENT_TYPES = {"paper", "book", "thesis", "report", "other"}
OBJECT_IMPORT_MODES = {"full_document", "chaptered"}


class PdfImportClassificationError(ValueError):
    pass


@dataclass(frozen=True)
class ZoteroImportMetadata:
    item_type: str | None = None
    title: str | None = None
    publication_title: str | None = None
    book_title: str | None = None
    isbn: str | None = None
    doi: str | None = None
    pages: str | None = None
    zotero_pdf_source_id: int | None = None
    zotero_item_key: str | None = None


@dataclass(frozen=True)
class PdfProbe:
    pdf_path: Path
    title: str
    page_count: int
    outline_titles: list[str] = field(default_factory=list)
    outline_chapter_count: int = 0
    has_book_like_outline: bool = False
    has_paper_like_sections: bool = False


def classify_pdf_import(
    pdf_path: str | Path,
    *,
    source: str = "local",
    zotero_key: str | None = None,
    zotero_pdf_source_id: int | None = None,
    zotero_metadata: dict[str, Any] | None = None,
    allowed_root: str | Path | None = None,
) -> dict[str, Any]:
    pdf = _resolve_classification_path(pdf_path, allowed_root=allowed_root)
    metadata = _load_zotero_metadata(
        source=source,
        zotero_key=zotero_key,
        zotero_pdf_source_id=zotero_pdf_source_id,
        explicit=zotero_metadata,
    )
    probe = probe_pdf(pdf, allowed_root=allowed_root)
    duplicate = find_duplicate_pdf(
        pdf,
        metadata=metadata,
        title=probe.title,
        allowed_root=allowed_root,
    )
    result = classify_pdf_probe(probe, zotero_metadata=metadata)
    return {
        "status": "ok",
        "pdf_path": str(pdf),
        "title": metadata.title or probe.title,
        "document_type": result["document_type"],
        "object_import_mode": result["object_import_mode"],
        "confidence": result["confidence"],
        "reasons": result["reasons"],
        "signals": {
            "zotero_item_type": metadata.item_type,
            "zotero_publication_title": metadata.publication_title,
            "zotero_book_title": metadata.book_title,
            "zotero_has_isbn": bool(metadata.isbn),
            "zotero_has_doi": bool(metadata.doi),
            "page_count": probe.page_count,
            "outline_chapter_count": probe.outline_chapter_count,
            "has_book_like_outline": probe.has_book_like_outline,
            "has_paper_like_sections": probe.has_paper_like_sections,
        },
        "requires_user_confirmation": result["requires_user_confirmation"],
        "duplicate": duplicate is not None,
        "existing_document_id": duplicate.get("document_id") if duplicate else None,
        "existing_document_type": duplicate.get("document_type") if duplicate else None,
        "existing_object_import_mode": duplicate.get("object_import_mode") if duplicate else None,
        "duplicate_reason": duplicate.get("reason") if duplicate else None,
        "db_write_performed": False,
        "external_llm_called": False,
    }


def classify_pdf_probe(
    probe: PdfProbe,
    *,
    zotero_metadata: ZoteroImportMetadata | dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _coerce_metadata(zotero_metadata)
    reasons: list[str] = []
    document_type = "other"
    confidence = "low"

    if metadata.item_type:
        document_type, confidence = _document_type_from_zotero_item_type(metadata.item_type)
        reasons.append(f"zotero_item_type:{metadata.item_type}->{document_type}")
    elif metadata.isbn or metadata.book_title:
        document_type = "book"
        confidence = "medium"
        reasons.append("zotero_book_signal")
    elif _title_looks_like_thesis(metadata.title or probe.title):
        document_type = "thesis"
        confidence = "medium"
        reasons.append("title_thesis_signal")
    else:
        document_type = "paper" if probe.page_count <= 60 or probe.has_paper_like_sections else "other"
        confidence = "medium" if document_type == "paper" else "low"
        reasons.append("metadata_missing_type_fallback")

    object_import_mode = "full_document"
    requires_user_confirmation = True
    if metadata.item_type and document_type in {"book", "thesis", "report"}:
        object_import_mode = "chaptered"
        reasons.append(f"{document_type}_defaults_chaptered")
    if probe.page_count <= 60:
        if object_import_mode != "chaptered":
            object_import_mode = "full_document"
            reasons.append("page_count<=60->full_document")
        requires_user_confirmation = False if confidence in {"high", "medium"} else True
    elif 61 <= probe.page_count < 120:
        object_import_mode = "chaptered" if probe.has_book_like_outline else object_import_mode
        reasons.append("page_count_61_119_ambiguous")
        requires_user_confirmation = True
        confidence = "medium" if probe.has_book_like_outline else "low"
    else:
        object_import_mode = "chaptered"
        reasons.append("page_count>=120->chaptered")
        requires_user_confirmation = True
        confidence = "high" if confidence == "high" or probe.has_book_like_outline else "medium"

    if probe.outline_chapter_count >= 5:
        object_import_mode = "chaptered"
        confidence = "high"
        reasons.append("outline_chapter_count>=5->chaptered")
    elif probe.has_paper_like_sections and probe.page_count <= 60:
        object_import_mode = "full_document"
        reasons.append("paper_like_outline_small_pdf->full_document")

    if document_type not in DOCUMENT_TYPES:
        document_type = "other"
    return {
        "document_type": document_type,
        "object_import_mode": object_import_mode,
        "confidence": confidence,
        "reasons": reasons,
        "requires_user_confirmation": requires_user_confirmation,
    }


def apply_user_override(
    classification: dict[str, Any],
    *,
    document_type: str,
    object_import_mode: str,
) -> dict[str, Any]:
    if document_type not in DOCUMENT_TYPES:
        raise PdfImportClassificationError(f"unsupported document_type: {document_type}")
    if object_import_mode not in OBJECT_IMPORT_MODES:
        raise PdfImportClassificationError(f"unsupported object_import_mode: {object_import_mode}")
    updated = dict(classification)
    updated["document_type"] = document_type
    updated["object_import_mode"] = object_import_mode
    updated["requires_user_confirmation"] = False
    updated["user_override_applied"] = True
    updated.setdefault("reasons", []).append("user_override")
    return updated


def commit_pdf_import(request: dict[str, Any]) -> dict[str, Any]:
    document_type = str(request.get("document_type") or "")
    object_import_mode = str(request.get("object_import_mode") or "")
    if document_type not in DOCUMENT_TYPES:
        raise PdfImportClassificationError(f"unsupported document_type: {document_type}")
    if object_import_mode not in OBJECT_IMPORT_MODES:
        raise PdfImportClassificationError(f"unsupported object_import_mode: {object_import_mode}")
    pdf_path = request.get("pdf_path")
    if not pdf_path:
        raise PdfImportClassificationError("pdf_path is required")
    classification = classify_pdf_import(
        pdf_path,
        source=str(request.get("source") or "local"),
        zotero_key=request.get("zotero_key"),
        zotero_pdf_source_id=request.get("zotero_pdf_source_id"),
    )
    if classification["duplicate"]:
        raise PdfImportClassificationError("duplicate_pdf")
    if object_import_mode == "full_document":
        return {
            "status": "preview_required",
            "message": "full_document imports continue through the existing import preview and review flow.",
            "document_type": document_type,
            "object_import_mode": object_import_mode,
            "db_write_performed": False,
            "external_llm_called": False,
        }

    prepared = prepare_book_import(
        pdf_path,
        title=request.get("confirm_title") or classification.get("title"),
        backend=request.get("backend") or MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    )
    result = apply_prepared_book_import(prepared)
    if document_type != "book" and result.get("document_id"):
        with SessionLocal() as session:
            document = session.get(Document, int(result["document_id"]))
            if document is not None:
                document.document_type = document_type
                session.commit()
    result["document_type"] = document_type
    result["requested_document_type"] = document_type
    result["object_import_mode"] = "chaptered"
    result["external_llm_called"] = False
    return result


def probe_pdf(
    pdf_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
) -> PdfProbe:
    pdf = _resolve_classification_path(pdf_path, allowed_root=allowed_root)
    fitz = load_fitz_backend()
    with fitz.open(pdf) as document:
        page_count = len(document)
        metadata_title = str((document.metadata or {}).get("title") or "").strip()
        external_outline = document.get_toc(simple=True) if allowed_root is not None else None
    if external_outline is None:
        outline_candidates = extract_pdf_outline_chapter_candidates(pdf)
        outline_titles = [candidate.title for candidate in outline_candidates]
    else:
        outline_titles = [
            str(item[1]).strip()
            for item in external_outline
            if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[1]).strip()
        ]
    outline_chapter_count = len(outline_titles)
    return PdfProbe(
        pdf_path=pdf,
        title=metadata_title or pdf.stem,
        page_count=page_count,
        outline_titles=outline_titles,
        outline_chapter_count=outline_chapter_count,
        has_book_like_outline=outline_chapter_count >= 5,
        has_paper_like_sections=_has_paper_like_outline(outline_titles),
    )


def find_duplicate_pdf(
    pdf_path: str | Path,
    *,
    metadata: ZoteroImportMetadata | None = None,
    title: str | None = None,
    allowed_root: str | Path | None = None,
) -> dict[str, Any] | None:
    pdf = _resolve_classification_path(pdf_path, allowed_root=allowed_root)
    normalized_pdf = str(pdf)
    normalized_title = _normalize_title(title or metadata.title or "")
    with SessionLocal() as session:
        rows = session.execute(
            select(Document.id, Document.title, Document.document_type, Document.pdf_path, Document.object_import_mode)
        ).all()
    for row in rows:
        item = row._mapping
        if item["pdf_path"] and str(item["pdf_path"]) == normalized_pdf:
            return _duplicate_payload(item, "pdf_path")
        if normalized_title and _normalize_title(str(item["title"] or "")) == normalized_title:
            return _duplicate_payload(item, "title")
    return None


def _resolve_classification_path(
    pdf_path: str | Path,
    *,
    allowed_root: str | Path | None,
) -> Path:
    if allowed_root is None:
        return resolve_pdf_path(pdf_path)
    root = Path(allowed_root).resolve(strict=True)
    pdf = Path(pdf_path).resolve(strict=True)
    if (
        not root.is_dir()
        or not pdf.is_file()
        or pdf.suffix.lower() != ".pdf"
        or not pdf.is_relative_to(root)
    ):
        raise FileNotFoundError("PDF is outside the explicit import root")
    return pdf


def _duplicate_payload(item: Any, reason: str) -> dict[str, Any]:
    return {
        "document_id": int(item["id"]),
        "document_type": item["document_type"],
        "object_import_mode": item["object_import_mode"] or "full_document",
        "reason": reason,
    }


def _load_zotero_metadata(
    *,
    source: str,
    zotero_key: str | None,
    zotero_pdf_source_id: int | None,
    explicit: dict[str, Any] | None,
) -> ZoteroImportMetadata:
    if explicit:
        return _coerce_metadata(explicit)
    if source != "zotero" and not zotero_pdf_source_id and not zotero_key:
        return ZoteroImportMetadata()
    with SessionLocal() as session:
        row = None
        if zotero_pdf_source_id:
            row = session.get(ZoteroPdfSource, int(zotero_pdf_source_id))
        if row is None and zotero_key:
            row = session.scalar(select(ZoteroPdfSource).where(ZoteroPdfSource.zotero_item_key == zotero_key))
        if row is None:
            return ZoteroImportMetadata(item_type=None, zotero_item_key=zotero_key)
        return ZoteroImportMetadata(
            title=row.title,
            publication_title=row.publication_title,
            zotero_pdf_source_id=row.id,
            zotero_item_key=row.zotero_item_key,
        )


def _coerce_metadata(value: ZoteroImportMetadata | dict[str, Any] | None) -> ZoteroImportMetadata:
    if isinstance(value, ZoteroImportMetadata):
        return value
    value = value or {}
    return ZoteroImportMetadata(
        item_type=value.get("item_type") or value.get("itemType") or value.get("zotero_item_type"),
        title=value.get("title"),
        publication_title=value.get("publication_title") or value.get("publicationTitle"),
        book_title=value.get("book_title") or value.get("bookTitle"),
        isbn=value.get("isbn") or value.get("ISBN"),
        doi=value.get("doi") or value.get("DOI"),
        pages=value.get("pages"),
        zotero_item_key=value.get("zotero_item_key") or value.get("zotero_key"),
    )


def _document_type_from_zotero_item_type(item_type: str) -> tuple[str, str]:
    normalized = item_type.strip()
    if normalized in {"book", "bookSection"}:
        return "book", "high"
    if normalized in {"thesis"}:
        return "thesis", "high"
    if normalized in {"report"}:
        return "report", "high"
    if normalized in {"journalArticle", "conferencePaper", "preprint"}:
        return "paper", "high"
    return "other", "medium"


def _has_paper_like_outline(titles: list[str]) -> bool:
    normalized = " | ".join(title.lower() for title in titles[:12])
    if not normalized:
        return False
    section_hits = sum(
        1
        for term in ("abstract", "introduction", "related work", "method", "experiment", "conclusion", "references")
        if term in normalized
    )
    return section_hits >= 3


def _title_looks_like_thesis(title: str) -> bool:
    return bool(re.search(r"\b(thesis|dissertation)\b|学位论文|硕士|博士", title, flags=re.IGNORECASE))


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()


__all__ = [
    "DEFAULT_PDF_PARSER_BACKEND",
    "DOCUMENT_TYPES",
    "OBJECT_IMPORT_MODES",
    "PdfBackendUnavailableError",
    "PdfImportClassificationError",
    "PdfProbe",
    "ZoteroImportMetadata",
    "apply_user_override",
    "classify_pdf_import",
    "classify_pdf_probe",
    "commit_pdf_import",
    "find_duplicate_pdf",
    "load_fitz_backend",
    "probe_pdf",
]
