from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from app.domains.retrieval.fragment_repository import get_notebook_fragments
from app.domains.retrieval.public_evidence import serialize_public_evidence
from app.domains.retrieval.result_contracts import NotebookFragment


NotebookEvidenceFormat = Literal["markdown", "jsonl", "json"]
ADAPTER_VERSION = "notebook_evidence_export.v1"


def render_notebook_evidence(
    fragment_ids: list[str],
    *,
    format: NotebookEvidenceFormat,
    query: str | None,
    include_context_before: bool = True,
    include_context_after: bool = True,
    include_provenance: bool = True,
) -> dict[str, Any]:
    fragments = get_notebook_fragments(fragment_ids)
    records = [
        _record(
            fragment,
            selection_rank=index,
            include_context_before=include_context_before,
            include_context_after=include_context_after,
            include_provenance=include_provenance,
        )
        for index, fragment in enumerate(fragments, start=1)
    ]
    if format == "markdown":
        content = _markdown(records, query=query)
        extension = ".md"
        mime_type = "text/markdown"
    elif format == "jsonl":
        content = "\n".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records
        )
        if content:
            content += "\n"
        extension = ".jsonl"
        mime_type = "application/x-ndjson"
    elif format == "json":
        content = json.dumps(
            {
                "mode": "high_quality_notebook_search_v1",
                "query": query,
                "results": records,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        extension = ".json"
        mime_type = "application/json"
    else:  # pragma: no cover - Pydantic validates callers
        raise ValueError(f"unsupported evidence format: {format}")

    source_hash = hashlib.sha256(
        "\n".join(
            f"{fragment.fragment_id}|{fragment.content_hash}" for fragment in fragments
        ).encode("utf-8")
    ).hexdigest()
    manifest_hash = hashlib.sha256(ADAPTER_VERSION.encode("utf-8")).hexdigest()
    return {
        "content": content,
        "extension": extension,
        "mime_type": mime_type,
        "evidence_count": len(records),
        "source_index_hash": source_hash,
        "source_manifest_hash": manifest_hash,
    }


def _record(
    fragment: NotebookFragment,
    *,
    selection_rank: int,
    include_context_before: bool,
    include_context_after: bool,
    include_provenance: bool,
) -> dict[str, Any]:
    record = serialize_public_evidence(
        fragment,
        selection_rank=selection_rank,
        include_context=include_context_before or include_context_after,
    ).model_dump(mode="json")
    if not include_context_before:
        record["context_before"] = None
    if not include_context_after:
        record["context_after"] = None
    if not include_provenance:
        record["provenance"] = {}
    return record


def _markdown(records: list[dict[str, Any]], *, query: str | None) -> str:
    lines = ["# Search Evidence Export", ""]
    if query:
        lines.extend([f"Query: {_single_line(query)}", ""])
    for index, record in enumerate(records, start=1):
        lines.extend(
            [
                f"## Evidence {index}",
                "",
                f"Source type: {_single_line(record['source_type'])}",
                f"Document: {_single_line(record.get('document_title') or 'Unknown')}",
                f"Page: {_single_line(record.get('page_label') or record.get('pdf_page') or 'Unknown')}",
                f"Fragment ID: {_single_line(record['fragment_id'])}",
                f"Selection rank: {record['selection_rank']}",
                "",
            ]
        )
        if record["source_type"] == "pdf_chunk":
            lines.extend(["### PDF text", "", record.get("coherent_text") or "", ""])
        else:
            lines.extend(["### User note", "", record.get("user_note") or "(not available)", ""])
            lines.extend(
                [
                    "### Selected source text",
                    "",
                    record.get("selected_source_text") or "(not available for this note)",
                    "",
                ]
            )
        context = "\n\n".join(
            value
            for value in (record.get("context_before"), record.get("context_after"))
            if value
        )
        if context:
            lines.extend(["### Context", "", context, ""])
        provenance = record.get("provenance") or {}
        lines.extend(["### Source", ""])
        for label, key in (
            ("Source", "source"),
            ("Zotero item", "zotero_item_key"),
            ("Zotero attachment", "zotero_attachment_key"),
            ("Annotation", "annotation_key"),
        ):
            if provenance.get(key) is not None:
                lines.append(f"{label}: {_single_line(provenance[key])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())
