from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services.ocr_layout_service import DEFAULT_MODEL_CACHE_ROOT
from scripts.promote_ocr_first_candidates import load_old_chunks, open_read_only_connection


FIX5V_DOCUMENT_ID = 3
FIX5V_SELECTED_PAGES = (389, 391, 392)
FIX5V_PROMOTED_PAGE = 390
MAX_PAGES_PER_DRY_RUN_BATCH = 3
CUDA_BENCHMARK_SECONDS = {390: 9.64, 391: 3.44, 606: 6.10}
METRIC_KEYS = (
    "html_tag_count",
    "math_noise_count",
    "repeated_token_count",
    "page_number_noise_count",
    "broken_sentence_count",
    "known_ocr_error_count",
    "suspicious_symbol_count",
)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    report = run_ocr_layout_batch_plan(
        db_path=Path(args.db_path),
        document_id=args.document_id,
        pages=args.pages,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _text_report(report))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan one small OCR layout preparation batch without executing OCR.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--pages", nargs="+", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run_ocr_layout_batch_plan(
    *,
    db_path: Path,
    document_id: int,
    pages: list[int] | tuple[int, ...],
    dry_run: bool,
    pdf_probe: Callable[[Path, list[int]], dict[int, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    selected_pages = _validate_scope(document_id=document_id, pages=pages, dry_run=dry_run)
    with open_read_only_connection(db_path) as connection:
        document = _load_document(connection, document_id=document_id)
        page_db_rows = {
            page: _load_page_db_diagnostics(connection, document_id=document_id, pdf_page=page)
            for page in selected_pages
        }
    pdf_path = _resolve_pdf_path(document)
    source_quality = (pdf_probe or probe_pdf_source_quality)(pdf_path, selected_pages)
    page_plans = [
        _build_page_plan(
            document_id=document_id,
            pdf_page=page,
            database=page_db_rows[page],
            pdf_quality=source_quality[page],
        )
        for page in selected_pages
    ]
    return {
        "status": "DRY_RUN",
        "mode": "ocr_layout_batch_prepare_plan",
        "document": document,
        "selected_pages": selected_pages,
        "excluded_already_promoted_page": FIX5V_PROMOTED_PAGE,
        "scope_guard": {
            "allowed_pages": list(FIX5V_SELECTED_PAGES),
            "max_pages_per_planning_batch": MAX_PAGES_PER_DRY_RUN_BATCH,
            "whole_chapter_ocr_allowed": False,
            "already_promoted_page_allowed": False,
        },
        "model_cache_root": str(DEFAULT_MODEL_CACHE_ROOT),
        "read_only_sqlite_connection": True,
        "no_database_writes_performed": True,
        "knowledge_chunks_written": False,
        "lancedb_writes_performed": False,
        "ocr_run_performed": False,
        "pdf_import_performed": False,
        "llm_calls_performed": False,
        "future_commands_executed": False,
        "pages": page_plans,
        "ocr_cost_estimate": _summarize_runtime(page_plans),
        "batch_recommendation": {
            "max_pages_per_batch": 1,
            "reason": "Persist and review Surya layout page-by-page before candidate correction or promote.",
            "apply_allowed_in_this_run": False,
        },
        "candidate_generation_plan": {
            "requires_persisted_ocr_layout_lines": True,
            "candidate_script_reuse_condition": "Verify ocr_reused_from_db=true and ocr_pages_run=[] before accepting candidate writes.",
            "pages": [
                {"pdf_page": page["pdf_page"], "pipeline": page["pipeline"]}
                for page in page_plans
            ],
        },
        "blockers": _blockers(page_plans),
    }


def _validate_scope(*, document_id: int, pages: list[int] | tuple[int, ...], dry_run: bool) -> list[int]:
    if not dry_run:
        raise ValueError("Fix5V OCR layout batch planning is dry-run only; OCR/apply is not supported")
    if document_id != FIX5V_DOCUMENT_ID:
        raise ValueError("Fix5V planning is limited to document_id=3")
    selected = sorted(set(int(page) for page in pages))
    if not selected:
        raise ValueError("--pages requires at least one page")
    if FIX5V_PROMOTED_PAGE in selected:
        raise ValueError("page 390 is already promoted and is excluded from Fix5V OCR preparation")
    if len(selected) > MAX_PAGES_PER_DRY_RUN_BATCH:
        raise ValueError("Fix5V refuses broad OCR planning; select no more than three pages")
    unsupported = sorted(set(selected) - set(FIX5V_SELECTED_PAGES))
    if unsupported:
        raise ValueError(f"Fix5V first batch is limited to pages {list(FIX5V_SELECTED_PAGES)}; received {unsupported}")
    return selected


def _load_document(connection: Any, *, document_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, title, pdf_path, source_path FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"document not found: {document_id}")
    return dict(row)


def _resolve_pdf_path(document: dict[str, Any]) -> Path:
    value = document.get("pdf_path") or document.get("source_path")
    if not value:
        raise ValueError("document does not define a PDF path")
    path = Path(str(value))
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _load_page_db_diagnostics(connection: Any, *, document_id: int, pdf_page: int) -> dict[str, Any]:
    chunks = load_old_chunks(connection, document_id=document_id, page_start=pdf_page, page_end=pdf_page)
    ocr_count = connection.execute(
        """
        SELECT COUNT(*) AS n FROM pdf_page_layout_lines
        WHERE document_id = ? AND pdf_page = ? AND source_backend = 'surya_ocr'
        """,
        (document_id, pdf_page),
    ).fetchone()["n"]
    totals = _metric_totals(chunks)
    ranked = sorted(chunks, key=lambda item: _issue_score(item["old_issues"]), reverse=True)
    return {
        "old_chunks": chunks,
        "old_chunk_ids": [chunk["chunk_id"] for chunk in chunks],
        "old_chunks_count": len(chunks),
        "old_quality_metrics": totals,
        "most_severe_old_chunks": [
            {
                "chunk_id": chunk["chunk_id"],
                "text_summary": chunk["old_chunk_text_summary"],
                "quality_metrics": chunk["old_issues"],
                "issue_score": _issue_score(chunk["old_issues"]),
            }
            for chunk in ranked[:2]
        ],
        "has_ocr_layout_lines": int(ocr_count) > 0,
        "ocr_layout_lines_count": int(ocr_count),
    }


def probe_pdf_source_quality(pdf_path: Path, pages: list[int]) -> dict[int, dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyMuPDF/fitz is required for read-only PDF source quality diagnostics") from exc
    diagnostics: dict[int, dict[str, Any]] = {}
    with fitz.open(str(pdf_path)) as document:
        for pdf_page in pages:
            if pdf_page < 1 or pdf_page > document.page_count:
                raise ValueError(f"PDF page outside source document: {pdf_page}")
            page = document[pdf_page - 1]
            text = page.get_text("text")
            images = []
            for image in page.get_images(full=True):
                xref, width, height = int(image[0]), int(image[2]), int(image[3])
                rects = page.get_image_rects(xref)
                rendered_rect = max(rects, key=lambda rect: rect.width * rect.height) if rects else page.rect
                images.append(
                    {
                        "xref": xref,
                        "width_px": width,
                        "height_px": height,
                        "rendered_width_points": round(float(rendered_rect.width), 2),
                        "rendered_height_points": round(float(rendered_rect.height), 2),
                        "estimated_dpi_x": round(width * 72.0 / max(float(rendered_rect.width), 1.0), 1),
                        "estimated_dpi_y": round(height * 72.0 / max(float(rendered_rect.height), 1.0), 1),
                        "page_area_coverage": round(
                            float(rendered_rect.width * rendered_rect.height) / max(float(page.rect.width * page.rect.height), 1.0),
                            4,
                        ),
                    }
                )
            primary = max(images, key=lambda item: item["width_px"] * item["height_px"]) if images else None
            scanned_image = len(text.strip()) == 0 and bool(primary) and primary["page_area_coverage"] >= 0.8
            diagnostics[pdf_page] = {
                "pdf_page": pdf_page,
                "page_text_length": len(text),
                "page_size_points": {
                    "width": round(float(page.rect.width), 2),
                    "height": round(float(page.rect.height), 2),
                },
                "image_count": len(images),
                "images": images,
                "primary_image": primary,
                "is_scanned_image_page": bool(scanned_image),
                "ocr_required": bool(scanned_image or len(text.strip()) == 0),
            }
    return diagnostics


def _build_page_plan(
    *,
    document_id: int,
    pdf_page: int,
    database: dict[str, Any],
    pdf_quality: dict[str, Any],
) -> dict[str, Any]:
    runtime = _runtime_estimate(pdf_page)
    ocr_ready = database["has_ocr_layout_lines"]
    pipeline = _pipeline(document_id=document_id, pdf_page=pdf_page)
    return {
        "pdf_page": pdf_page,
        **database,
        "pdf_source_quality": pdf_quality,
        "ocr_required": bool(pdf_quality["ocr_required"] and not ocr_ready),
        "expected_ocr_line_rows": "unknown_until_surya_ocr_layout_apply",
        "expected_ocr_span_rows": "unknown_until_surya_ocr_layout_apply",
        "expected_candidate_count": {
            "estimate": database["old_chunks_count"],
            "basis": "current old chunk count proxy; final count depends on Surya line grouping",
        },
        "expected_promote_complexity": _promote_complexity(database["old_quality_metrics"], database["old_chunks_count"]),
        "recommended_ocr_device": "cuda",
        "estimated_runtime_cuda_seconds": runtime,
        "max_pages_per_batch": 1,
        "future_ocr_layout_apply_command": _ocr_apply_command(document_id, pdf_page),
        "pipeline": pipeline,
        "readiness_after_this_plan": "needs_ocr_layout" if not ocr_ready else "ready_for_candidate_generation",
    }


def _runtime_estimate(pdf_page: int) -> dict[str, Any]:
    if pdf_page in CUDA_BENCHMARK_SECONDS:
        value = CUDA_BENCHMARK_SECONDS[pdf_page]
        return {
            "point": value,
            "low": value,
            "high": value,
            "basis": f"observed earlier CUDA benchmark for page {pdf_page}",
        }
    observations = list(CUDA_BENCHMARK_SECONDS.values())
    point = round(sum(observations) / len(observations), 2)
    return {
        "point": point,
        "low": min(observations),
        "high": max(observations),
        "basis": "benchmark envelope from pages 390, 391 and 606; no OCR executed for this estimate",
    }


def _summarize_runtime(pages: list[dict[str, Any]]) -> dict[str, Any]:
    runtimes = [page["estimated_runtime_cuda_seconds"] for page in pages]
    return {
        "device": "cuda",
        "point_seconds": round(sum(item["point"] for item in runtimes), 2),
        "low_seconds": round(sum(item["low"] for item in runtimes), 2),
        "high_seconds": round(sum(item["high"] for item in runtimes), 2),
        "benchmark_observations_seconds": CUDA_BENCHMARK_SECONDS,
        "actual_ocr_executed": False,
    }


def _pipeline(*, document_id: int, pdf_page: int) -> list[dict[str, Any]]:
    cache = str(DEFAULT_MODEL_CACHE_ROOT)
    return [
        {
            "step": 1,
            "name": "ocr_layout_apply",
            "prerequisite": "future explicit authorization for one page only",
            "command": _ocr_apply_command(document_id, pdf_page),
            "executed_in_this_run": False,
        },
        {
            "step": 2,
            "name": "candidate_write",
            "prerequisite": "OCR layout rows persisted; first verify import report shows ocr_reused_from_db=true and ocr_pages_run=[]",
            "command": (
                f'python scripts\\import_book_ocr_layout_first.py --document-id {document_id} '
                f"--page-start {pdf_page} --page-end {pdf_page} --chapter-id 24 --write-candidates --apply "
                f'--device cuda --model-cache-root "{cache}" --json'
            ),
            "executed_in_this_run": False,
        },
        {
            "step": 3,
            "name": "correction_review_and_apply",
            "prerequisite": "candidate quality review completed",
            "dry_run_command": (
                f"python scripts\\import_book_ocr_layout_first.py --document-id {document_id} "
                f"--page-start {pdf_page} --page-end {pdf_page} --correct-candidates --json"
            ),
            "apply_command": (
                f"python scripts\\import_book_ocr_layout_first.py --document-id {document_id} "
                f"--page-start {pdf_page} --page-end {pdf_page} --correct-candidates --apply --json"
            ),
            "executed_in_this_run": False,
        },
        {
            "step": 4,
            "name": "promote_dry_run",
            "prerequisite": "candidate corrections accepted and source_line_ids verified",
            "command": (
                f"python scripts\\promote_ocr_first_candidates.py --document-id {document_id} "
                f"--page-start {pdf_page} --page-end {pdf_page} --dry-run --explain-medium-mapping --json"
            ),
            "executed_in_this_run": False,
        },
        {
            "step": 5,
            "name": "promote_apply",
            "prerequisite": "page-specific safety guard expansion, snapshot/vector validation, and later explicit user authorization",
            "command": None,
            "apply_allowed_in_this_run": False,
            "executed_in_this_run": False,
        },
    ]


def _ocr_apply_command(document_id: int, pdf_page: int) -> str:
    return (
        f"python scripts\\repair_book_layout_bboxes.py --document-id {document_id} "
        f"--page-start {pdf_page} --page-end {pdf_page} --max-pages-per-batch 1 --ocr --apply "
        f'--ocr-backend surya --device cuda --model-cache-root "{DEFAULT_MODEL_CACHE_ROOT}"'
    )


def _metric_totals(chunks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(int(chunk["old_issues"].get(key, 0)) for chunk in chunks)
        for key in METRIC_KEYS
    }


def _issue_score(metrics: dict[str, int]) -> int:
    return sum(int(metrics.get(key, 0)) for key in METRIC_KEYS)


def _promote_complexity(metrics: dict[str, int], chunks_count: int) -> dict[str, str]:
    score = _issue_score(metrics)
    if chunks_count >= 5 or score >= 12:
        return {"level": "high", "reason": "many old chunks or dense existing quality defects require correction review"}
    if chunks_count >= 3 or score > 0:
        return {"level": "medium", "reason": "multiple old chunks require line-to-candidate boundary review"}
    return {"level": "low", "reason": "small page footprint with no measured old-text defect"}


def _blockers(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "pdf_page": page["pdf_page"],
            "blocker": "no persisted Surya OCR layout lines; OCR layout apply is a future authorized step",
        }
        for page in pages
        if not page["has_ocr_layout_lines"]
    ]


def _text_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
