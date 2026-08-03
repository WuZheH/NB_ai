from __future__ import annotations

import json

from app.schemas.evidence_export import EvidenceExportOptions
from app.services.retrieval.evidence_loader import EvidenceRecord


def render_evidence_jsonl(
    records: list[EvidenceRecord],
    *,
    options: EvidenceExportOptions,
    match_reasons: dict[str, list[str]],
) -> str:
    rows = []
    for index, record in enumerate(records, start=1):
        rows.append(
            json.dumps(
                {
                    "evidence_id": f"E{index:03d}",
                    "selected_order": index,
                    "fragment_id": record.fragment_id,
                    "display_id": record.display_id,
                    "source_record_id": record.source_record_id,
                    "canonical_source_locator": record.canonical_source_locator,
                    "source_type": record.source_type,
                    "origin_kind": record.origin_kind,
                    "document_id": record.document_id,
                    "title": record.title,
                    "authors": record.authors,
                    "year": record.year,
                    "collections": record.collections,
                    "tags": record.tags,
                    "page_number": record.page_number,
                    "page_label": record.page_label,
                    "section": record.section,
                    "heading_path": record.heading_path,
                    "text": record.text,
                    "context_before": (
                        record.context_before if options.include_context_before else None
                    ),
                    "context_after": (
                        record.context_after if options.include_context_after else None
                    ),
                    "note_comment": (
                        record.note_comment if options.include_note_comment else None
                    ),
                    "match_reasons": (
                        match_reasons.get(record.fragment_id, [])
                        if options.include_match_reasons
                        else []
                    ),
                    "zotero_item_key": record.zotero_item_key,
                    "zotero_attachment_key": record.zotero_attachment_key,
                    "zotero_annotation_key": record.zotero_annotation_key,
                    "zotero_uri": record.zotero_uri,
                    "original_file_path": record.original_file_path,
                    "duplicate_count": record.duplicate_count,
                    "duplicate_fragment_ids": record.duplicate_fragment_ids,
                    "duplicate_source_types": record.duplicate_source_types,
                    "provenance": record.provenance if options.include_provenance else [],
                    "warnings": record.warnings if options.include_raw_warnings else [],
                    "content_hash": record.content_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(rows) + "\n"
