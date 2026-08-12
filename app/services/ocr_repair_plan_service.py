from __future__ import annotations

from typing import Any


QUALITY_STATUSES = frozenset({"clean", "safe_auto_correct", "needs_review", "blocked"})
REQUIRED_CONFIRMATIONS = [
    "confirm OCR layout apply for selected pages",
    "confirm candidate write",
    "confirm promote dry-run",
    "confirm vector sync",
]
FORBIDDEN_ACTIONS = [
    "full_book_ocr",
    "full_chapter_ocr",
    "direct_knowledge_chunks_write",
    "direct_lancedb_write",
]


def build_ocr_repair_plan(repair_preview_result: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only plan draft from a completed sample-level repair preview."""
    if not isinstance(repair_preview_result, dict):
        raise ValueError("repair_preview_result must be an object")
    if repair_preview_result.get("job_mode") != "repair_preview":
        raise ValueError("repair_preview_result must be a repair_preview result")

    pages = repair_preview_result.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) > 2:
        raise ValueError("repair plan draft requires one or two sampled pages")

    candidates = _collect_candidates(pages)
    summary = _quality_summary(pages, candidates)
    decision, reasons = _recommend_decision(
        summary,
        normal_text_layer_available=bool(repair_preview_result.get("normal_text_layer_available")),
    )
    sampled_pages = summary["sampled_physical_pages"]

    return {
        "status": "OK",
        "plan_mode": "repair_plan_draft",
        "plan_basis": "repair_preview_result_only",
        "apply_enabled": False,
        "batch_apply_executed": False,
        "canonical_promote_executed": False,
        "formal_import_started": False,
        "db_write_performed": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
        "vector_store_write_performed": False,
        "ocr_executed": False,
        "ocr_apply": False,
        "marker_executed": False,
        "external_llm_called": False,
        "recommended_decision": decision,
        "decision_reasons": reasons,
        "sample_quality_summary": {
            key: value for key, value in summary.items() if key != "sampled_physical_pages"
        },
        "next_batch_strategy": _next_batch_strategy(decision, sampled_pages),
        "risk_report": _risk_report(candidates, summary, decision),
        "estimated_runtime": {
            "next_single_page_batch": "approximately 1-3 minutes after explicit confirmation; device dependent",
            "full_scope": "not estimated in plan draft mode",
        },
        "required_confirmations_before_apply": list(REQUIRED_CONFIRMATIONS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
    }


def _collect_candidates(pages: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("repair preview pages must be objects")
        page_candidates = page.get("candidates") or []
        if not isinstance(page_candidates, list):
            raise ValueError("repair preview candidates must be a list")
        for candidate in page_candidates:
            if not isinstance(candidate, dict):
                raise ValueError("repair preview candidate must be an object")
            status = candidate.get("quality_status")
            if status not in QUALITY_STATUSES:
                raise ValueError(f"invalid repair preview quality_status: {status}")
            candidates.append(candidate)
    return candidates


def _quality_summary(pages: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [candidate["quality_status"] for candidate in candidates]
    sampled_pages: list[int] = []
    for page in pages:
        physical_page = page.get("physical_page")
        if isinstance(physical_page, int) and physical_page > 0:
            sampled_pages.append(physical_page)
    return {
        "pages_sampled": len(pages),
        "candidate_count": len(candidates),
        "clean_count": statuses.count("clean"),
        "safe_auto_correct_count": statuses.count("safe_auto_correct"),
        "needs_review_count": statuses.count("needs_review"),
        "blocked_count": statuses.count("blocked"),
        "sampled_physical_pages": sampled_pages,
    }


def _recommend_decision(
    summary: dict[str, Any],
    *,
    normal_text_layer_available: bool,
) -> tuple[str, list[str]]:
    if normal_text_layer_available:
        return "normal_import_preferred", ["sample_pages_already_have_text_layer"]

    candidate_count = summary["candidate_count"]
    blocked_count = summary["blocked_count"]
    needs_review_count = summary["needs_review_count"]
    accepted_count = summary["clean_count"] + summary["safe_auto_correct_count"]

    if blocked_count:
        return "replace_pdf", ["blocked_candidates_present"]
    if not candidate_count:
        return "manual_review_first", ["no_candidate_evidence_available"]
    if needs_review_count / candidate_count >= 0.5:
        return "manual_review_first", ["at_least_half_of_candidates_need_review"]
    if accepted_count == candidate_count:
        return "continue_repair", ["all_sample_candidates_are_clean_or_deterministically_correctable"]
    if needs_review_count:
        return "manual_review_first", ["sample_contains_candidates_needing_review"]
    return "continue_repair", ["sample_quality_supports_single_page_repair_planning"]


def _next_batch_strategy(decision: str, sampled_pages: list[int]) -> dict[str, Any]:
    if decision == "continue_repair" and sampled_pages:
        return {
            "max_pages_per_batch": 1,
            "recommended_first_batch_pages": sampled_pages[:1],
            "why": "Start with one sampled physical page and require confirmation before any later apply.",
        }
    reason = {
        "normal_import_preferred": "A text layer is already available; draft mode recommends the normal import route.",
        "replace_pdf": "Blocked sample candidates must be resolved or the PDF replaced before planning apply work.",
        "manual_review_first": "Review sample candidates before selecting any single-page repair batch.",
    }.get(decision, "Review the sample result before selecting a batch.")
    return {
        "max_pages_per_batch": 1,
        "recommended_first_batch_pages": [],
        "why": reason,
    }


def _risk_report(
    candidates: list[dict[str, Any]],
    summary: dict[str, Any],
    decision: str,
) -> dict[str, Any]:
    risk_markers: list[str] = []
    for candidate in candidates:
        risk_markers.extend(str(value).lower() for value in (candidate.get("blocked_reasons") or []))
        risk_markers.extend(str(value).lower() for value in (candidate.get("risky_corrections") or []))
    joined = " ".join(risk_markers)
    return {
        "formula_risk": "manual_formula_review_required" if "formula" in joined else "not_detected_in_sample",
        "low_confidence_risk": (
            "low_confidence_lines_require_review"
            if "confidence" in joined or summary["needs_review_count"] > 0
            else "not_detected_in_sample"
        ),
        "manual_review_required": decision != "continue_repair" or summary["needs_review_count"] > 0,
    }
