from __future__ import annotations

from typing import Any

from app.schemas.retrieval_fragment import (
    RetrievalFragment,
    RetrievalOriginKind,
    RetrievalSourceType,
)
from app.services.retrieval.fragment_id import display_id_for, fragment_uuid
from app.services.retrieval.fragment_normalizer import (
    build_index_text,
    infer_language,
    normalize_text,
    sha256_text,
)
from app.services.retrieval.metadata_resolver import ResolvedSourceMetadata


_UNSET = object()


def make_fragment(
    *,
    source_type: RetrievalSourceType,
    origin_kind: RetrievalOriginKind,
    source_record_id: str,
    canonical_locator: str,
    text: str,
    adapter_version: str,
    metadata: ResolvedSourceMetadata | None = None,
    document_id: int | None | object = _UNSET,
    zotero_library_id: int | None | object = _UNSET,
    zotero_item_key: str | None | object = _UNSET,
    zotero_attachment_key: str | None | object = _UNSET,
    zotero_annotation_key: str | None = None,
    parent_fragment_id: str | None = None,
    source_group_id: str | None = None,
    duplicate_group_id: str | None = None,
    duplicate_candidate: bool = False,
    title: str | None | object = _UNSET,
    authors: list[str] | None = None,
    year: int | None | object = _UNSET,
    collections: list[str] | None = None,
    tags: list[str] | None = None,
    page_number: int | None = None,
    page_label: str | None = None,
    section: str | None = None,
    heading_path: list[str] | None = None,
    source_order: int | None = None,
    position: dict[str, Any] | None = None,
    bbox: dict[str, Any] | None = None,
    note_comment: str | None = None,
    context_before: str | None = None,
    context_after: str | None = None,
    context_status: str = "pending",
    context_method: str | None = None,
    original_file_path: str | None | object = _UNSET,
    zotero_uri: str | None | object = _UNSET,
    source_created_at: str | None = None,
    source_updated_at: str | None = None,
    provenance: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> RetrievalFragment:
    meta = metadata or ResolvedSourceMetadata()
    normalized_text = normalize_text(text)
    if not normalized_text:
        raise ValueError(f"{source_type} source record {source_record_id!r} has no text")
    fragment_id = fragment_uuid(canonical_locator)
    effective_document_id = meta.document_id if document_id is _UNSET else document_id
    effective_library_id = meta.zotero_library_id if zotero_library_id is _UNSET else zotero_library_id
    effective_item_key = meta.zotero_item_key if zotero_item_key is _UNSET else zotero_item_key
    effective_attachment_key = (
        meta.zotero_attachment_key if zotero_attachment_key is _UNSET else zotero_attachment_key
    )
    effective_title = meta.title if title is _UNSET else title
    effective_year = meta.year if year is _UNSET else year
    effective_file_path = meta.original_file_path if original_file_path is _UNSET else original_file_path
    effective_uri = meta.zotero_uri if zotero_uri is _UNSET else zotero_uri
    effective_authors = list(meta.authors) if authors is None else authors
    effective_collections = list(meta.collections) if collections is None else collections
    effective_tags = list(meta.tags) if tags is None else tags
    effective_heading_path = heading_path or []
    effective_warnings = list(
        dict.fromkeys([*meta.warnings, *(warnings or [])])
    )
    effective_provenance = [
        *meta.provenance,
        *(provenance or []),
    ]
    effective_raw_metadata = {
        "document_mapping_status": meta.mapping_status,
        "candidate_document_ids": list(meta.candidate_document_ids),
        **(raw_metadata or {}),
    }

    return RetrievalFragment(
        fragment_id=fragment_id,
        display_id=display_id_for(
            source_type,
            fragment_id=fragment_id,
            document_id=effective_document_id if isinstance(effective_document_id, int) else None,
            page_number=page_number,
            source_record_id=str(source_record_id),
        ),
        source_type=source_type,
        origin_kind=origin_kind,
        source_record_id=str(source_record_id),
        canonical_source_locator=canonical_locator,
        document_id=effective_document_id,
        zotero_library_id=effective_library_id,
        zotero_item_key=effective_item_key,
        zotero_attachment_key=effective_attachment_key,
        zotero_annotation_key=zotero_annotation_key,
        parent_fragment_id=parent_fragment_id,
        source_group_id=source_group_id,
        duplicate_group_id=duplicate_group_id,
        duplicate_candidate=duplicate_candidate,
        title=effective_title,
        authors=effective_authors,
        year=effective_year,
        collections=effective_collections,
        tags=effective_tags,
        page_number=page_number,
        page_label=page_label,
        section=section,
        heading_path=effective_heading_path,
        source_order=source_order,
        position=position,
        bbox=bbox,
        text=normalized_text,
        note_comment=normalize_text(note_comment) or None,
        context_before=normalize_text(context_before) or None,
        context_after=normalize_text(context_after) or None,
        context_status=context_status,
        context_method=context_method,
        original_file_path=effective_file_path,
        zotero_uri=effective_uri,
        language=infer_language(normalized_text),
        index_text=build_index_text(
            effective_title,
            effective_authors,
            effective_heading_path,
            effective_tags,
            normalized_text,
            note_comment,
        ),
        content_hash=sha256_text(normalized_text),
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        adapter_version=adapter_version,
        provenance=effective_provenance,
        warnings=effective_warnings,
        raw_metadata=effective_raw_metadata,
    )
