from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import PROJECT_ROOT
from app.schemas.evidence_export import (
    EvidenceExportRequest,
    EvidenceExportResponse,
)
from app.services.retrieval.evidence_errors import EvidenceWorkflowError
from app.services.retrieval.evidence_jsonl_exporter import render_evidence_jsonl
from app.services.retrieval.evidence_loader import EvidenceRecord, load_evidence_records
from app.services.retrieval.evidence_markdown_exporter import render_evidence_markdown
from app.services.retrieval.export_fingerprint import build_export_fingerprint
from app.services.retrieval.match_reason import evaluate_match_signals
from app.services.retrieval.query_aliases import expand_curated_aliases
from app.services.retrieval.query_normalizer import normalize_query
from app.services.retrieval.ranking import score_candidate


MAX_EXPORT_ITEMS = 1000
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "outputs" / "retrieval_evidence_exports"


def export_evidence(
    request: EvidenceExportRequest | dict[str, Any],
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> dict[str, Any]:
    export_request = (
        request
        if isinstance(request, EvidenceExportRequest)
        else EvidenceExportRequest.model_validate(request)
    )
    fragment_ids, duplicate_count = _unique_ids(export_request.fragment_ids)
    if not fragment_ids:
        raise EvidenceWorkflowError(
            "empty_evidence_selection",
            "At least one fragment ID is required for evidence export.",
            status_code=400,
        )
    if len(fragment_ids) > MAX_EXPORT_ITEMS:
        raise EvidenceWorkflowError(
            "selection_limit_exceeded",
            f"Evidence export contains {len(fragment_ids)} fragments, exceeding the cap of {MAX_EXPORT_ITEMS}.",
            status_code=413,
            details={"available_count": len(fragment_ids), "max_items": MAX_EXPORT_ITEMS},
        )

    load_result = load_evidence_records(
        fragment_ids,
        index_path=index_path,
        manifest_path=manifest_path,
    )
    status = load_result.index_status
    query = _clean_optional(export_request.query)
    match_reasons = _build_match_reasons(
        load_result.records,
        query=query,
        retrieval_mode=export_request.retrieval_mode,
    )
    options_payload = export_request.options.model_dump(mode="json")
    fingerprint = build_export_fingerprint(
        load_result.records,
        source_index_hash=status["index_content_hash"],
        source_manifest_hash=status["manifest_sha256"],
        query=query,
        retrieval_mode=export_request.retrieval_mode,
        options=options_payload,
        export_format=export_request.format,
    )
    exported_at = datetime.now(timezone.utc).isoformat()
    if export_request.format == "markdown":
        content = render_evidence_markdown(
            load_result.records,
            query=query,
            retrieval_mode=export_request.retrieval_mode,
            exported_at=exported_at,
            source_index_hash=status["index_content_hash"],
            source_manifest_hash=status["manifest_sha256"],
            export_fingerprint=fingerprint,
            options=export_request.options,
            match_reasons=match_reasons,
        )
        extension = ".md"
        mime_type = "text/markdown"
    else:
        content = render_evidence_jsonl(
            load_result.records,
            options=export_request.options,
            match_reasons=match_reasons,
        )
        extension = ".jsonl"
        mime_type = "application/x-ndjson"

    warnings: list[str] = []
    if duplicate_count:
        warnings.append(f"duplicate_fragment_ids_removed:{duplicate_count}")
    if len(load_result.records) > 100 or len(content) > 200_000:
        warnings.append("large_evidence_export")
    base_filename = _base_filename(query, fingerprint, extension)
    output_path: str | None = None
    filename = base_filename
    if export_request.save_to_file:
        saved = _save_unique(content, base_filename=base_filename, output_dir=Path(output_dir))
        output_path = str(saved)
        filename = saved.name

    response = {
        "status": "OK",
        "format": export_request.format,
        "content": content,
        "filename": filename,
        "mime_type": mime_type,
        "evidence_count": len(load_result.records),
        "export_fingerprint": fingerprint,
        "source_index_hash": status["index_content_hash"],
        "source_manifest_hash": status["manifest_sha256"],
        "exported_at": exported_at,
        "warnings": warnings,
        "output_path": output_path,
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }
    return EvidenceExportResponse.model_validate(response).model_dump(mode="json")


def _build_match_reasons(
    records: list[EvidenceRecord],
    *,
    query: str | None,
    retrieval_mode: str | None,
) -> dict[str, list[str]]:
    if not query:
        return {record.fragment_id: [] for record in records}
    plan = normalize_query(query)
    alias_matches = (
        expand_curated_aliases(plan.normalized_query)
        if retrieval_mode == "coverage"
        else []
    )
    alias_terms = list(
        dict.fromkeys(
            term
            for match in alias_matches
            for term in match.expanded_terms
        )
    )
    result: dict[str, list[str]] = {}
    for record in records:
        candidate = {**record.match_fields, "_base_score": 0.0}
        signals = evaluate_match_signals(
            candidate,
            plan=plan,
            alias_terms=alias_terms,
        )
        result[record.fragment_id] = score_candidate(candidate, signals)["match_reasons"]
    return result


def _unique_ids(values: list[str]) -> tuple[list[str], int]:
    result: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for value in values:
        fragment_id = str(value).strip()
        if not fragment_id:
            continue
        if fragment_id in seen:
            duplicates += 1
            continue
        seen.add(fragment_id)
        result.append(fragment_id)
    return result, duplicates


def _base_filename(query: str | None, fingerprint: str, extension: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", query or "evidence").strip("-").lower()
    slug = (slug or "evidence")[:48]
    return f"retrieval_evidence_{slug}_{fingerprint[:12]}{extension}"


def _save_unique(content: str, *, base_filename: str, output_dir: Path) -> Path:
    resolved_root = PROJECT_ROOT.resolve(strict=True)
    resolved_dir = output_dir.resolve(strict=False)
    try:
        resolved_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise EvidenceWorkflowError(
            "invalid_evidence_export_path",
            "Evidence exports may only be written inside the project workspace.",
            status_code=400,
        ) from exc
    resolved_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(base_filename).stem
    suffix = Path(base_filename).suffix
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for attempt in range(100):
        counter = f"_{attempt}" if attempt else ""
        candidate = resolved_dir / f"{stem}_{timestamp}{counter}{suffix}"
        try:
            with candidate.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            return candidate
        except FileExistsError:
            continue
    raise EvidenceWorkflowError(
        "evidence_export_filename_exhausted",
        "Could not allocate a unique evidence export filename.",
        status_code=500,
    )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
