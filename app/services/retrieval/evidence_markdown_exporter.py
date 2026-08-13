from __future__ import annotations

import json
from typing import Any

from app.schemas.evidence_export import EvidenceExportOptions
from app.services.retrieval.evidence_loader import EvidenceRecord


def render_evidence_markdown(
    records: list[EvidenceRecord],
    *,
    query: str | None,
    retrieval_mode: str | None,
    exported_at: str,
    source_index_hash: str,
    source_manifest_hash: str,
    export_fingerprint: str,
    options: EvidenceExportOptions,
    match_reasons: dict[str, list[str]],
) -> str:
    lines = [
        "---",
        f"query: {_inline(query)}",
        f"retrieval_mode: {_inline(retrieval_mode)}",
        f"exported_at: {_inline(exported_at)}",
        f"evidence_count: {len(records)}",
        f"source_index_hash: {_inline(source_index_hash)}",
        f"source_manifest_hash: {_inline(source_manifest_hash)}",
        f"export_fingerprint: {_inline(export_fingerprint)}",
        "---",
        "",
    ]
    previous_document_key: str | None = None
    for index, record in enumerate(records, start=1):
        if options.group_by_document:
            document_key = _document_key(record)
            if document_key != previous_document_key:
                lines.extend(
                    [
                        f"## Document Group: {_plain(record.title or document_key)}",
                        "",
                    ]
                )
                previous_document_key = document_key
        evidence_id = f"E{index:03d}"
        reasons = match_reasons.get(record.fragment_id, []) if options.include_match_reasons else []
        lines.extend(
            [
                f"# Evidence {evidence_id}",
                "",
                f"- Evidence ID: {_inline(evidence_id)}",
                f"- Fragment ID: {_inline(record.fragment_id)}",
                f"- Display ID: {_inline(record.display_id)}",
                f"- Title: {_inline(record.title)}",
                f"- Authors: {_inline(record.authors)}",
                f"- Year: {_inline(record.year)}",
                f"- Source Type: {_inline(record.source_type)}",
                f"- Origin Kind: {_inline(record.origin_kind)}",
                f"- Document ID: {_inline(record.document_id)}",
                f"- Page Number: {_inline(record.page_number)}",
                f"- Page Label: {_inline(record.page_label)}",
                f"- Section: {_inline(record.section)}",
                f"- Zotero Item Key: {_inline(record.zotero_item_key)}",
                f"- Attachment Key: {_inline(record.zotero_attachment_key)}",
                f"- Annotation Key: {_inline(record.zotero_annotation_key)}",
                f"- Collections: {_inline(record.collections)}",
                f"- Tags: {_inline(record.tags)}",
                f"- Match Reasons: {_inline(reasons)}",
                f"- Duplicate Count: {record.duplicate_count}",
                f"- Original File: {_inline(record.original_file_path)}",
                f"- Zotero Link: {_inline(record.zotero_uri)}",
                "",
                "## Original Text",
                "",
                _blockquote(record.text),
                "",
            ]
        )
        if options.include_context_before:
            lines.extend(["## Context Before", "", _plain(record.context_before), ""])
        if options.include_context_after:
            lines.extend(["## Context After", "", _plain(record.context_after), ""])
        if options.include_note_comment:
            lines.extend(["## My Note or Comment", "", _plain(record.note_comment), ""])
        if options.include_provenance:
            lines.extend(
                [
                    "## Provenance",
                    "",
                    f"- Source record: {_inline(record.source_record_id)}",
                    f"- Canonical source locator: {_inline(record.canonical_source_locator)}",
                    f"- Duplicate member IDs: {_inline(record.duplicate_fragment_ids)}",
                ]
            )
            for entry in record.provenance:
                lines.append(f"- Provenance row: `{json.dumps(entry, ensure_ascii=False, sort_keys=True)}`")
            lines.append("")
        if options.include_raw_warnings:
            lines.extend([f"- Raw warnings: {_inline(record.warnings)}", ""])
        lines.extend(["---", ""])
    return "\n".join(lines).rstrip() + "\n"


def _inline(value: Any) -> str:
    if value is None:
        return "null"
    return f"`{json.dumps(value, ensure_ascii=False, sort_keys=True)}`"


def _plain(value: str | None) -> str:
    return value if value is not None else "null"


def _blockquote(value: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())


def _document_key(record: EvidenceRecord) -> str:
    if record.document_id is not None:
        return f"document:{record.document_id}"
    if record.zotero_attachment_key:
        return f"attachment:{record.zotero_attachment_key}"
    return f"fragment:{record.fragment_id}"
