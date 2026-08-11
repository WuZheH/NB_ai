from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.paths import PROJECT_ROOT
from app.db.session import SessionLocal
from app.models import KnowledgeChunk
from app.services.import_preview_service import (
    ImportPreviewError,
    _existing_job_dir,
    _read_json,
    _write_json,
    _relative,
    _safety_response,
)
from app.services.object_commit_identity_service import (
    freeze_object_commit_input,
    reviewed_input_fingerprint,
)

REVIEWED_FILE = "reviewed_object_tag_package.json"
REMAP_PREVIEW_FILE = "object_evidence_remap_preview.json"


def remap_reviewed_objects_preview(import_job_id: str) -> dict[str, Any]:
    """Preview evidence_refs → chunk_id mapping for reviewed objects.

    Reads reviewed_object_tag_package.json, maps evidence_refs against
    KnowledgeChunk rows for the committed document.  No DB writes.
    """
    job_dir = _existing_job_dir(import_job_id)

    reviewed_path = job_dir / REVIEWED_FILE
    commit_paper_path = job_dir / "commit_result.json"

    if not reviewed_path.is_file():
        raise ImportPreviewError("reviewed_object_tag_package.json not found.")
    if not commit_paper_path.is_file():
        raise ImportPreviewError("Paper not committed. Run commit-paper first.")

    commit_paper = _read_json(commit_paper_path)
    document_id = commit_paper.get("document_id")
    if document_id is None:
        raise ImportPreviewError("commit_result.json has no document_id.")

    reviewed = _read_json(reviewed_path)
    reviewed_job_id = reviewed.get("import_job_id")
    if reviewed_job_id is not None and str(reviewed_job_id) != import_job_id:
        raise ImportPreviewError(
            "object_remap_preview_job_mismatch: "
            "reviewed package 与当前 import job 不一致。"
        )
    reviewed_document_id = reviewed.get("document_id")
    if reviewed_document_id is not None:
        if isinstance(reviewed_document_id, bool) or not isinstance(
            reviewed_document_id, int
        ):
            raise ImportPreviewError(
                "object_remap_preview_document_mismatch: "
                "reviewed package document_id 无效。"
            )
        if reviewed_document_id != int(document_id):
            raise ImportPreviewError(
                "object_remap_preview_document_mismatch: "
                "reviewed package 与 committed document 不一致。"
            )
    all_objects = reviewed.get("objects") or []
    frozen_reviewed = freeze_object_commit_input(
        import_job_id=import_job_id,
        phase="commit_reviewed_objects",
        document_id=int(document_id),
        reviewed_objects=all_objects,
    )
    source_fingerprint = reviewed_input_fingerprint(frozen_reviewed)

    # Filter: only accepted / edited
    processable = [
        obj for obj in all_objects
        if str(obj.get("review_status") or "").strip().lower() in ("accepted", "edited")
    ]

    # Build chunk index (read-only from DB)
    chunk_index = _build_chunk_index(document_id)

    object_results: list[dict[str, Any]] = []
    summary = {"mapped": 0, "partial": 0, "failed": 0, "not_mapped": 0}

    for obj in all_objects:
        review_status = str(obj.get("review_status") or "").strip().lower()
        if review_status not in ("accepted", "edited"):
            # Just report skipped
            object_results.append({
                "object_key": obj.get("object_key"),
                "object_name": obj.get("object_name"),
                "review_status": review_status,
                "mapping_status": "skipped",
                "mapped_chunk_ids": [],
                "warnings": [{"warning": f"skipped (review_status={review_status})"}],
                "evidence_ref_results": [],
            })
            continue

        refs = obj.get("evidence_refs") or []
        ref_results, mapped_ids, mapping_status, warnings = _map_evidence_refs_enhanced(
            refs, chunk_index,
        )

        object_results.append({
            "object_key": obj.get("object_key"),
            "object_name": obj.get("object_name"),
            "review_status": review_status,
            "mapping_status": mapping_status,
            "mapped_chunk_ids": mapped_ids,
            "warnings": warnings,
            "evidence_ref_results": ref_results,
        })

        if mapping_status not in ("skipped",):
            summary[mapping_status] = summary.get(mapping_status, 0) + 1

    result = {
        "status": "ok",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "reviewed_input_fingerprint": source_fingerprint,
        "object_count": len(object_results),
        "processable_count": len(processable),
        "chunk_index_size": len(chunk_index),
        "summary": summary,
        "objects": object_results,
        "core_db_write_performed": False,
        "external_llm_called": False,
    }

    # Write to staging
    remap_path = job_dir / REMAP_PREVIEW_FILE
    _write_json(remap_path, result)

    result["remap_preview_path"] = _relative(remap_path)
    return result


# ---------------------------------------------------------------------------
# Enhanced per-ref mapping (replaces _map_evidence_refs for preview)
# ---------------------------------------------------------------------------

def _map_evidence_refs_enhanced(
    refs: list[dict[str, Any]],
    chunk_index: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int], str, list[dict[str, str]]]:
    """Map evidence_refs → chunk_ids with per-ref diagnostics.

    Returns (ref_results, mapped_ids, mapping_status, warnings).
    """
    if not refs:
        return [], [], "not_mapped", []

    ref_results: list[dict[str, Any]] = []
    mapped_ids: list[int] = []
    warnings: list[dict[str, str]] = []
    quote_matched = 0
    fallback_used = 0
    nearby_used = 0
    failed = 0

    for ref in refs:
        if not isinstance(ref, dict):
            continue

        pdf_page_raw = ref.get("pdf_page")
        pdf_page = int(pdf_page_raw) if pdf_page_raw is not None else None
        section_title = str(ref.get("section_title") or ref.get("section_id") or "").strip()
        quote = str(ref.get("quote_text_short") or "").strip()

        ref_result = {
            "section_title": section_title or ref.get("section_id", ""),
            "pdf_page": pdf_page,
            "quote_text_short": quote,
            "matched_chunk_id": None,
            "match_type": "none",
            "warning": None,
        }

        # --- Pass 1: page candidates ---
        page_candidates = _page_candidates(pdf_page, chunk_index, allow_nearby=False)
        page_label = "exact_page"

        if not page_candidates:
            # Try nearby page ±1
            page_candidates, nearby_warn = _page_candidates_nearby(pdf_page, chunk_index)
            if page_candidates:
                page_label = "nearby_page"
                ref_result["warning"] = nearby_warn
            else:
                # Page not found at all — search all chunks
                page_candidates = list(chunk_index.values())
                page_label = "all_chunks_fallback"

        if not page_candidates:
            ref_result["match_type"] = "none"
            ref_result["warning"] = "no_chunks_in_index"
            ref_results.append(ref_result)
            warnings.append({"ref": section_title or "unknown", "warning": "no_chunks_in_index"})
            failed += 1
            continue

        # --- Pass 2: narrow by section ---
        heading_candidates = page_candidates
        section_narrowed = False
        if section_title:
            narrowed = [
                c for c in page_candidates
                if _fuzzy_contains(c["heading_path"], section_title)
            ]
            if narrowed:
                heading_candidates = narrowed
                section_narrowed = True

        # --- Pass 3: quote match ---
        found = False
        for candidate in heading_candidates:
            if quote and _fuzzy_contains(candidate["chunk_text"], quote):
                ref_result["matched_chunk_id"] = candidate["chunk_id"]
                ref_result["match_type"] = "exact"
                mapped_ids.append(candidate["chunk_id"])
                quote_matched += 1
                found = True
                break

        if found:
            ref_results.append(ref_result)
            continue

        # --- Fallback: use first heading candidate only if section narrowed ---
        if heading_candidates and section_narrowed:
            ref_result["matched_chunk_id"] = heading_candidates[0]["chunk_id"]
            if page_label == "nearby_page":
                ref_result["match_type"] = "nearby_page"
                nearby_used += 1
            else:
                ref_result["match_type"] = "fallback"
                fallback_used += 1
            if not ref_result["warning"]:
                ref_result["warning"] = "quote_not_found_used_fallback"
            mapped_ids.append(heading_candidates[0]["chunk_id"])
            warnings.append({"ref": section_title or "unknown", "warning": "quote_not_found_used_fallback"})
        elif page_candidates and not section_narrowed:
            # Try quote across all page chunks
            found2 = False
            for candidate in page_candidates:
                if quote and _fuzzy_contains(candidate["chunk_text"], quote):
                    ref_result["matched_chunk_id"] = candidate["chunk_id"]
                    ref_result["match_type"] = "exact"
                    mapped_ids.append(candidate["chunk_id"])
                    quote_matched += 1
                    found2 = True
                    break
            if not found2:
                ref_result["match_type"] = "none"
                ref_result["warning"] = "page_candidate_not_found"
                failed += 1
                warnings.append({"ref": section_title or "unknown", "warning": "page_candidate_not_found"})
        else:
            ref_result["match_type"] = "none"
            ref_result["warning"] = "section_not_found" if section_title else "no_candidates"
            failed += 1
            warnings.append({"ref": section_title or "unknown", "warning": ref_result["warning"]})

        ref_results.append(ref_result)

    # --- Compute aggregate mapping_status ---
    total = len([r for r in refs if isinstance(r, dict)])
    if total == 0:
        mapping_status = "not_mapped"
    elif quote_matched == total:
        mapping_status = "mapped"
    elif quote_matched > 0 or fallback_used > 0 or nearby_used > 0:
        mapping_status = "partial"
    elif failed == total:
        mapping_status = "failed"
    else:
        mapping_status = "failed"

    return ref_results, mapped_ids, mapping_status, warnings


def _page_candidates(
    pdf_page: int | None,
    chunk_index: dict[int, dict[str, Any]],
    allow_nearby: bool = False,
) -> list[dict[str, Any]]:
    """Return chunks that match the given pdf_page."""
    if pdf_page is None:
        return []
    if allow_nearby:
        return [
            c for c in chunk_index.values()
            if _page_nearby(c, pdf_page)
        ]
    return [
        c for c in chunk_index.values()
        if _page_matches(c, pdf_page)
    ]


def _page_candidates_nearby(
    pdf_page: int | None,
    chunk_index: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Try nearby page ±1 fallback. Returns (candidates, warning_message)."""
    if pdf_page is None or pdf_page < 1:
        return [], None
    for offset in (1, -1):
        nearby = pdf_page + offset
        if nearby < 1:
            continue
        candidates = [
            c for c in chunk_index.values()
            if _page_matches(c, nearby)
        ]
        if candidates:
            return candidates, f"page_{pdf_page}_not_found_used_nearby_p{nearby}"
    return [], None


# ---------------------------------------------------------------------------
# Reused from commit_objects_service (read-only helpers)
# ---------------------------------------------------------------------------

def _build_chunk_index(document_id: int) -> dict[int, dict[str, Any]]:
    with SessionLocal() as session:
        chunks = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        ).all()
    index: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        index[chunk.id] = {
            "chunk_id": chunk.id,
            "pdf_page_start": chunk.pdf_page_start,
            "pdf_page_end": chunk.pdf_page_end,
            "heading_path": chunk.heading_path or "",
            "chunk_text": chunk.chunk_text or "",
        }
    return index


def _page_matches(chunk: dict, evidence_page: int) -> bool:
    start = chunk.get("pdf_page_start")
    end = chunk.get("pdf_page_end")
    if start is None:
        return False
    if end is None:
        return start == evidence_page
    return start <= evidence_page <= end


def _page_nearby(chunk: dict, evidence_page: int) -> bool:
    """Check if chunk page is within ±1 of evidence_page."""
    start = chunk.get("pdf_page_start")
    end = chunk.get("pdf_page_end")
    if start is None:
        return False
    if end is None:
        return abs(start - evidence_page) <= 1
    return abs(start - evidence_page) <= 1 or abs(end - evidence_page) <= 1


def _fuzzy_contains(haystack: str, needle: str) -> bool:
    h = _normalize_text(haystack)
    n = _normalize_text(needle)
    if not n:
        return False
    if n in h:
        return True
    if len(n) > 60 and n[:60] in h:
        return True
    words = [w for w in n.split() if len(w) > 2]
    if len(words) >= 3 and all(w in h for w in words):
        return True
    if len(n) > 40 and n[:40] in h:
        return True
    return False


def _normalize_text(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)
    t = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", t)
    t = t.strip('.,;:!?()[]{}""\'\' ')
    return t
