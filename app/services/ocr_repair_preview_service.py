from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import import_preview_gate_service
from app.services.ocr_layout_chunker import (
    FILTERED_LINE_ROLES,
    chunk_ocr_layout_lines,
    prepare_ocr_chunk_lines,
)
from app.services.ocr_layout_service import DEFAULT_MODEL_CACHE_ROOT, OcrPageLayout, run_surya_ocr_page
from app.services.pdf_backend_service import load_fitz_backend


MAX_REPAIR_PREVIEW_PAGES = 2
MIN_CANDIDATE_TEXT_CHARS = 12
QUALITY_STATUSES = frozenset({"clean", "safe_auto_correct", "needs_review", "blocked"})


def build_ocr_repair_preview(
    *,
    preview_token: str,
    sample_pages: list[int],
    max_pages: int = MAX_REPAIR_PREVIEW_PAGES,
    device: str = "auto",
    model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT,
) -> dict[str, Any]:
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("device must be one of auto, cpu, or cuda")
    cache_root = Path(model_cache_root).resolve(strict=False)
    if cache_root != DEFAULT_MODEL_CACHE_ROOT.resolve(strict=False):
        raise ValueError("repair preview model_cache_root must use the configured local model cache")
    pdf_path = import_preview_gate_service.resolve_pdf_preview_sample_pages(
        preview_token,
        sample_pages,
        max_pages,
    )
    effective_device = "cpu" if device == "auto" else device
    fitz = load_fitz_backend()
    with fitz.open(pdf_path) as document:
        if any(page > document.page_count for page in sample_pages):
            raise ValueError("repair preview sample page outside PDF page range")
        page_labels = {
            page: document.load_page(page - 1).get_label() or str(page)
            for page in sample_pages
        }

    pages = [
        _preview_page(
            pdf_path=pdf_path,
            physical_page=page,
            page_label=page_labels[page],
            device=effective_device,
            model_cache_root=cache_root,
        )
        for page in sample_pages
    ]
    statuses = [candidate["quality_status"] for page in pages for candidate in page["candidates"]]
    if "blocked" in statuses:
        recommendation = "replace_pdf_recommended"
    elif "needs_review" in statuses:
        recommendation = "manual_review_required"
    else:
        recommendation = "repair_viable"
    normal_text_layer_available = bool(pages) and all(page["page_text_layer_length"] > 0 for page in pages)
    warnings = (
        ["normal_text_layer_already_available_repair_not_recommended"]
        if normal_text_layer_available
        else []
    )
    return {
        "status": "OK",
        "job_mode": "repair_preview",
        "db_write_performed": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
        "vector_store_write_performed": False,
        "ocr_executed": True,
        "ocr_apply": False,
        "marker_executed": False,
        "external_llm_called": False,
        "ocr_scope": {
            "max_pages": max_pages,
            "pages": list(sample_pages),
            "full_book_ocr": False,
            "full_chapter_ocr": False,
        },
        "device_requested": device,
        "device_effective": effective_device,
        "pages": pages,
        "normal_text_layer_available": normal_text_layer_available,
        "warnings": warnings,
        "overall_recommendation": recommendation,
        "repair_plan": {
            "status": "preliminary",
            "scope": "sample_pages_only",
            "pages": list(sample_pages),
            "apply_enabled": False,
            "batch_apply_enabled": False,
            "canonical_promote_enabled": False,
        },
        "next_actions": ["continue_to_repair_plan", "replace_pdf", "cancel"],
    }


def _preview_page(
    *,
    pdf_path: Path,
    physical_page: int,
    page_label: str,
    device: str,
    model_cache_root: Path,
) -> dict[str, Any]:
    layout = _run_page_ocr(
        pdf_path=pdf_path,
        physical_page=physical_page,
        device=device,
        model_cache_root=model_cache_root,
    )
    raw_lines = [line.to_dict() for line in layout.lines]
    prepared = prepare_ocr_chunk_lines(
        raw_lines,
        page_width=layout.page_width,
        page_height=layout.page_height,
    )
    ocr_lines = [_public_line(line) for line in prepared]
    line_by_id = {line["line_id"]: line for line in ocr_lines}
    chunks = chunk_ocr_layout_lines(
        raw_lines,
        page_width=layout.page_width,
        page_height=layout.page_height,
        heading_path="OCR Repair Preview",
        chunk_size_target=700,
    )
    try:
        from scripts.importing.import_book_ocr_layout_first import (
            evaluate_candidate_quality_gate,
        )
        diagnostics = evaluate_candidate_quality_gate(chunks)
    except ImportError:
        diagnostics = {"candidates_by_index": {}}
    candidate_text_length = sum(len(chunk.chunk_text) for chunk in chunks)
    page_block_reasons: list[str] = []
    if layout.error:
        page_block_reasons.append("ocr_page_unavailable")
    if candidate_text_length < MIN_CANDIDATE_TEXT_CHARS:
        page_block_reasons.append("ocr_text_too_short")
    candidates = [
        _public_candidate(chunk, diagnostics["candidates_by_index"].get(chunk.chunk_index, {}), line_by_id, page_block_reasons)
        for chunk in chunks
    ]
    if not candidates:
        candidates = [_blocked_empty_candidate(page_block_reasons or ["ocr_text_too_short"])]
    summary = {
        f"{status}_count": sum(1 for candidate in candidates if candidate["quality_status"] == status)
        for status in QUALITY_STATUSES
    }
    summary["apply_allowed"] = False
    return {
        "physical_page": physical_page,
        "page_label": page_label,
        "page_width": layout.page_width,
        "page_height": layout.page_height,
        "page_text_layer_length": layout.page_text_layer_length,
        "ocr_lines": ocr_lines,
        "filtered_lines": [line for line in ocr_lines if line["role"] in FILTERED_LINE_ROLES],
        "candidates": candidates,
        "quality_summary": summary,
        "ocr_error": "ocr_preview_unavailable" if layout.error else None,
    }


def _run_page_ocr(
    *,
    pdf_path: Path,
    physical_page: int,
    device: str,
    model_cache_root: Path,
) -> OcrPageLayout:
    try:
        return run_surya_ocr_page(
            pdf_path,
            physical_page,
            device=device,
            model_cache_root=model_cache_root,
            return_words=True,
            allow_download=False,
        )
    except RuntimeError as exc:
        fitz = load_fitz_backend()
        with fitz.open(pdf_path) as document:
            page = document.load_page(physical_page - 1)
            return OcrPageLayout(
                pdf_page=physical_page,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
                image_width=0,
                image_height=0,
                page_text_layer_length=len(page.get_text("text") or ""),
                lines=[],
                spans=[],
                model_cache_root=str(model_cache_root),
                error=str(exc),
            )


def _public_line(line: Any) -> dict[str, Any]:
    return {
        "line_id": _preview_line_id(line.source_line_key),
        "text": line.display_text,
        "confidence": line.confidence,
        "bbox": dict(line.bbox),
        "role": line.role,
    }


def _public_candidate(
    chunk: Any,
    diagnostic: dict[str, Any],
    line_by_id: dict[str, dict[str, Any]],
    page_block_reasons: list[str],
) -> dict[str, Any]:
    source_line_ids = [_preview_line_id(key) for key in chunk.source_line_keys]
    safe = list((diagnostic.get("correction_suggestions") or {}).get("safe") or [])
    risky = list((diagnostic.get("correction_suggestions") or {}).get("risky") or [])
    reasons = list(diagnostic.get("blocked_reasons") or [])
    reasons.extend(reason for reason in page_block_reasons if reason not in reasons)
    status = _preview_quality_status(diagnostic.get("quality_status"), safe, risky)
    if page_block_reasons:
        status = "blocked"
    corrected_text = str(chunk.chunk_text or "")
    for suggestion in safe:
        corrected_text = corrected_text.replace(str(suggestion["before"]), str(suggestion["after"]))
    return {
        "candidate_index": chunk.chunk_index,
        "source_line_ids": source_line_ids,
        "candidate_text": chunk.chunk_text,
        "corrected_preview_text": corrected_text,
        "quality_status": status,
        "blocked_reasons": reasons,
        "safe_corrections": safe,
        "risky_corrections": risky,
        "bbox_union": _bbox_union([line_by_id[line_id]["bbox"] for line_id in source_line_ids if line_id in line_by_id]),
    }


def _blocked_empty_candidate(reasons: list[str]) -> dict[str, Any]:
    return {
        "candidate_index": 0,
        "source_line_ids": [],
        "candidate_text": "",
        "corrected_preview_text": "",
        "quality_status": "blocked",
        "blocked_reasons": reasons,
        "safe_corrections": [],
        "risky_corrections": [],
        "bbox_union": None,
    }


def _preview_quality_status(original: str | None, safe: list[dict[str, Any]], risky: list[dict[str, Any]]) -> str:
    if original == "blocked_from_apply":
        return "blocked"
    if original == "needs_manual_review" or risky:
        return "needs_review"
    if original == "needs_correction" and safe:
        return "safe_auto_correct"
    if original == "needs_correction":
        return "needs_review"
    return "clean"


def _preview_line_id(source_line_key: str) -> str:
    return f"preview:{source_line_key}"


def _bbox_union(rects: list[dict[str, float]]) -> dict[str, float] | None:
    if not rects:
        return None
    return {
        "x0": min(rect["x0"] for rect in rects),
        "y0": min(rect["y0"] for rect in rects),
        "x1": max(rect["x1"] for rect in rects),
        "y1": max(rect["y1"] for rect in rects),
    }


__all__ = ["MAX_REPAIR_PREVIEW_PAGES", "QUALITY_STATUSES", "build_ocr_repair_preview"]
