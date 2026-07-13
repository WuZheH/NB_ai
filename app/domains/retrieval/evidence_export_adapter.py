from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from app.domains.retrieval.fragment_repository import get_notebook_fragments
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
    return {
        "fragment_id": fragment.fragment_id,
        "source_type": fragment.source_type,
        "document_id": fragment.document_id,
        "document_title": fragment.document_title,
        "document_type": fragment.document_type,
        "chunk_id": fragment.chunk_id,
        "pdf_page": fragment.pdf_page,
        "page_label": fragment.page_label,
        "selection_rank": selection_rank,
        "final_rank": selection_rank,
        "reranker_score": None,
        "pdf_text": fragment.text if fragment.source_type == "pdf_chunk" else None,
        "user_note": fragment.note_text,
        "selected_source_text": fragment.selected_text,
        "context_before": fragment.context_before if include_context_before else None,
        "context_after": fragment.context_after if include_context_after else None,
        "tags": fragment.tags,
        "provenance": fragment.provenance if include_provenance else [],
        "open_target": fragment.open_target.model_dump(mode="json"),
        "content_hash": fragment.content_hash,
    }


def _markdown(records: list[dict[str, Any]], *, query: str | None) -> str:
    lines = ["# NOTEBOOK_AI Evidence Export", ""]
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
                f"Final rank: {record['final_rank']} (selection order)",
                "Reranker score: unavailable (scores are not persisted by evidence export)",
                "",
            ]
        )
        if record["source_type"] == "pdf_chunk":
            lines.extend(["### PDF text", "", record.get("pdf_text") or "", ""])
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
        lines.extend(
            [
                "### Provenance",
                "",
                "```json",
                json.dumps(record.get("provenance") or [], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())
