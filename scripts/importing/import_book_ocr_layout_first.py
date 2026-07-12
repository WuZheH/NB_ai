from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services.marker_structure_adapter import load_marker_structure_hints
from app.services.ocr_layout_chunker import (
    build_hybrid_candidates,
    chunk_ocr_layout_lines,
    count_filtered_lines,
    filtered_line_diagnostics,
    prepare_ocr_chunk_lines,
)
from app.services.ocr_layout_service import DEFAULT_MODEL_CACHE_ROOT, run_surya_ocr_page


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    report = run_import_ocr_layout_first(args)
    output = json.dumps(report, ensure_ascii=False, indent=2) if args.json else _text_report(report)
    print(output)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run or apply OCR/layout-first book page import.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--pdf-path")
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--title")
    parser.add_argument("--page-start", type=int, required=True)
    parser.add_argument("--page-end", type=int, required=True)
    parser.add_argument("--chapter-id", type=int)
    parser.add_argument("--max-pages-per-batch", type=int, default=1)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--model-cache-root", default=str(DEFAULT_MODEL_CACHE_ROOT))
    parser.add_argument("--chunk-size-target", type=int, default=700)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--write-candidates", action="store_true")
    parser.add_argument("--allow-review-candidates", action="store_true")
    parser.add_argument("--correct-candidates", action="store_true")
    parser.add_argument("--candidate-id", type=int, action="append", default=[])
    parser.add_argument("--compare-existing", action="store_true")
    parser.add_argument("--hybrid-marker-structure", action="store_true")
    parser.add_argument("--marker-artifact")
    parser.add_argument("--allow-large-range", action="store_true")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run_import_ocr_layout_first(args: argparse.Namespace) -> dict[str, Any]:
    apply_requested = bool(getattr(args, "apply", False))
    write_candidates = bool(getattr(args, "write_candidates", False))
    compare_existing = bool(getattr(args, "compare_existing", False))
    correct_candidates = bool(getattr(args, "correct_candidates", False))
    hybrid_marker_structure = bool(getattr(args, "hybrid_marker_structure", False))
    allow_review_candidates = bool(getattr(args, "allow_review_candidates", False))
    if args.page_end < args.page_start:
        raise ValueError("--page-end must be >= --page-start")
    page_count = args.page_end - args.page_start + 1
    if page_count > 5 and not args.allow_large_range:
        raise ValueError("page range over 5 pages requires --allow-large-range")
    if hybrid_marker_structure and apply_requested:
        raise ValueError("--hybrid-marker-structure is dry-run compare only; --apply is not permitted")
    if correct_candidates:
        return run_candidate_corrections(
            db_path=Path(args.db_path),
            document_id=args.document_id,
            page_start=args.page_start,
            page_end=args.page_end,
            candidate_ids=list(getattr(args, "candidate_id", []) or []),
            apply=apply_requested,
        )
    if apply_requested and not args.document_id:
        raise ValueError("--apply requires --document-id; OCR-first candidate apply is intentionally scoped to an existing book document")
    if apply_requested and not write_candidates:
        raise ValueError("--apply must be paired with --write-candidates; this script does not write OCR-first data into knowledge_chunks")

    db_path = Path(args.db_path)
    document = _load_document(db_path, document_id=args.document_id, title=args.title)
    pdf_path = Path(args.pdf_path or document.get("pdf_path") or "")
    if not pdf_path and not args.force_ocr:
        raise ValueError("--pdf-path or --document-id with pdf_path is required")
    if pdf_path and not pdf_path.is_absolute():
        pdf_path = (PROJECT_ROOT / pdf_path).resolve()

    layouts = _load_or_run_page_layouts(
        db_path=db_path,
        document_id=document.get("id"),
        pdf_path=pdf_path,
        page_start=args.page_start,
        page_end=args.page_end,
        force_ocr=args.force_ocr,
        device=args.device,
        model_cache_root=args.model_cache_root,
    )
    all_lines = [line for layout in layouts for line in layout["lines"]]
    page_width = layouts[0].get("page_width") if layouts else None
    page_height = layouts[0].get("page_height") if layouts else None
    chunks = chunk_ocr_layout_lines(
        all_lines,
        page_width=page_width,
        page_height=page_height,
        heading_path=f"{document.get('title') or args.title or 'OCR Layout First'} / OCR Layout First",
        section_title=None,
        pdf_path=str(pdf_path) if pdf_path else None,
        chunk_size_target=args.chunk_size_target,
    )
    role_counts = count_filtered_lines(all_lines, page_width=page_width, page_height=page_height)
    filtered_lines = filtered_line_diagnostics(all_lines, page_width=page_width, page_height=page_height)
    quality_gate = evaluate_candidate_quality_gate(chunks)
    old_chunks = _load_old_chunks(db_path, document.get("id"), args.page_start, args.page_end)
    candidate_views: list[dict[str, Any]] = []
    marker_structure: dict[str, Any] | None = None
    hybrid_result: dict[str, Any] | None = None
    if hybrid_marker_structure:
        candidate_views = _attach_surya_chunk_roles(
            _load_candidate_views(db_path, document.get("id"), args.page_start, args.page_end),
            chunks,
        )
        if not candidate_views:
            candidate_views = _generated_candidate_views(chunks)
        marker_structure = load_marker_structure_hints(
            page_start=args.page_start,
            page_end=args.page_end,
            artifact_path=getattr(args, "marker_artifact", None),
        )
        hybrid_result = build_hybrid_candidates(candidate_views, marker_structure)
    report: dict[str, Any] = {
        "status": "DRY_RUN" if not apply_requested else "APPLIED",
        "mode": "ocr_layout_first",
        "document_id": document.get("id"),
        "title": document.get("title") or args.title,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "page_start": args.page_start,
        "page_end": args.page_end,
        "pages": [layout["pdf_page"] for layout in layouts],
        "ocr_reused_from_db": all(layout.get("source") == "existing_db" for layout in layouts),
        "ocr_pages_run": [layout["pdf_page"] for layout in layouts if layout.get("source") == "surya_ocr_run"],
        "ocr_lines_count": len(all_lines),
        "role_counts": role_counts,
        "filtered_header_footer_count": sum(role_counts.get(role, 0) for role in ("header", "page_number", "footer")),
        "filtered_lines": filtered_lines,
        "proposed_chunks_count": len(chunks),
        "sample_chunks": [
            _chunk_preview(chunk, quality_gate["candidates_by_index"].get(chunk.chunk_index))
            for chunk in chunks[:8]
        ],
        "candidate_quality_gate": {
            key: value for key, value in quality_gate.items() if key != "candidates_by_index"
        },
        "candidates_clean_count": quality_gate["candidates_clean_count"],
        "candidates_needs_correction_count": quality_gate["candidates_needs_correction_count"],
        "candidates_needs_manual_review_count": quality_gate["candidates_needs_manual_review_count"],
        "candidates_blocked_from_apply_count": quality_gate["candidates_blocked_from_apply_count"],
        "apply_allowed": quality_gate["apply_allowed"],
        "allow_review_candidates_requested": allow_review_candidates,
        "old_chunks": [_public_old_chunk(chunk) for chunk in old_chunks],
        "no_database_writes_performed": not apply_requested,
        "no_lancedb_writes_performed": True,
        "apply_requires_explicit_apply": True,
        "write_candidates_requested": write_candidates,
        "compare_existing_requested": compare_existing,
        "hybrid_marker_structure_requested": hybrid_marker_structure,
        "model_cache_policy": _model_cache_policy_report(args.model_cache_root, marker_structure),
    }
    if hybrid_result is not None:
        report["marker_structure"] = marker_structure
        report["surya_only_candidates"] = [_public_candidate_view(candidate, "chunk_text") for candidate in candidate_views]
        report["corrected_candidates"] = [_public_candidate_view(candidate, "corrected_text") for candidate in candidate_views]
        report["hybrid_candidates"] = [_public_candidate_view(candidate, "chunk_text") for candidate in hybrid_result["candidates"]]
        report["hybrid_fusion"] = {key: value for key, value in hybrid_result.items() if key != "candidates"}
    if compare_existing:
        report["quality_compare"] = (
            compare_hybrid_quality(old_chunks, candidate_views, hybrid_result)
            if hybrid_result is not None
            else compare_existing_quality(old_chunks, chunks)
        )
    if apply_requested:
        if not quality_gate["apply_allowed"] and not allow_review_candidates:
            raise ValueError(
                "write-candidates --apply blocked by candidate quality gate; "
                "review dry-run diagnostics or use --allow-review-candidates to persist pending_review rows"
            )
        report["apply_result"] = _apply_candidate_chunks(
            db_path=db_path,
            document_id=int(document["id"]),
            chunks=chunks,
            chapter_id=args.chapter_id,
            replaces_chunk_ids=[chunk["chunk_id"] for chunk in old_chunks],
            review_status="pending" if quality_gate["apply_allowed"] else "pending_review",
        )
        report["no_database_writes_performed"] = False
    return report


def _load_or_run_page_layouts(
    *,
    db_path: Path,
    document_id: int | None,
    pdf_path: Path,
    page_start: int,
    page_end: int,
    force_ocr: bool,
    device: str,
    model_cache_root: str,
) -> list[dict[str, Any]]:
    layouts: list[dict[str, Any]] = []
    for page in range(page_start, page_end + 1):
        existing = [] if force_ocr or not document_id else _load_existing_ocr_lines(db_path, document_id, page)
        if existing:
            layouts.append(
                {
                    "pdf_page": page,
                    "source": "existing_db",
                    "lines": existing,
                    "spans": [],
                    "page_width": _infer_page_width(existing),
                    "page_height": _infer_page_height(existing),
                    "extracted_text": "\n".join(str(line.get("text") or "") for line in existing),
                }
            )
            continue
        layout = run_surya_ocr_page(
            pdf_path,
            page,
            device=device,
            model_cache_root=model_cache_root,
            return_words=True,
            allow_download=False,
        )
        lines = [line.to_dict() for line in layout.lines]
        spans = [span.to_dict() for span in layout.spans]
        layouts.append(
            {
                "pdf_page": page,
                "source": "surya_ocr_run",
                "lines": lines,
                "spans": spans,
                "page_width": layout.page_width,
                "page_height": layout.page_height,
                "extracted_text": layout.extracted_text,
            }
        )
    return layouts


def _apply_candidate_chunks(
    *,
    db_path: Path,
    document_id: int,
    chunks: list[Any],
    chapter_id: int | None,
    replaces_chunk_ids: list[int],
    review_status: str = "pending",
) -> dict[str, Any]:
    backup_path = _backup_sqlite(db_path)
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    candidate_ids: list[int] = []
    if chunks:
        page_start = min(int(chunk.pdf_page_start or 0) for chunk in chunks)
        page_end = max(int(chunk.pdf_page_end or 0) for chunk in chunks)
    else:
        page_start = 0
        page_end = 0
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        create_ocr_first_candidate_schema(connection)
        superseded_count = connection.execute(
            """
            UPDATE ocr_first_chunk_candidates
            SET review_status = 'superseded', notes = COALESCE(notes || '; ', '') || ?
            WHERE document_id = ?
              AND source_backend = 'surya_ocr'
              AND review_status IN ('pending', 'pending_review')
              AND pdf_page_start <= ?
              AND pdf_page_end >= ?
            """,
            (f"superseded by OCR-first candidate apply at {now}", document_id, page_end, page_start),
        ).rowcount
        replaces_json = json.dumps(replaces_chunk_ids, ensure_ascii=False)
        for chunk in chunks:
            cursor = connection.execute(
                """
                INSERT INTO ocr_first_chunk_candidates (
                    document_id, pdf_page_start, pdf_page_end, chapter_id, candidate_index,
                    chunk_text, search_text, display_text, source_line_ids_json, source_line_keys_json,
                    source_backend, confidence_summary_json, created_at, review_status,
                    replaces_chunk_ids_json, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    document_id,
                    chunk.pdf_page_start,
                    chunk.pdf_page_end,
                    chapter_id,
                    chunk.chunk_index,
                    chunk.chunk_text,
                    chunk.search_text,
                    chunk.display_text,
                    json.dumps(chunk.source_line_ids, ensure_ascii=False),
                    json.dumps(chunk.source_line_keys, ensure_ascii=False),
                    "surya_ocr",
                    json.dumps(chunk.confidence_summary, ensure_ascii=False, sort_keys=True),
                    now,
                    review_status,
                    replaces_json,
                ),
            )
            candidate_ids.append(int(cursor.lastrowid))
        connection.commit()
    return {
        "backup_path": str(backup_path),
        "candidate_table": "ocr_first_chunk_candidates",
        "inserted_candidates": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "superseded_pending_candidates": int(superseded_count or 0),
        "review_status": review_status,
        "knowledge_chunks_written": False,
        "lancedb_writes_performed": False,
    }


def create_ocr_first_candidate_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_first_chunk_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            pdf_page_start INTEGER NOT NULL,
            pdf_page_end INTEGER NOT NULL,
            chapter_id INTEGER,
            candidate_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            search_text TEXT,
            display_text TEXT,
            source_line_ids_json TEXT NOT NULL,
            source_line_keys_json TEXT NOT NULL,
            source_backend TEXT NOT NULL,
            confidence_summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending',
            replaces_chunk_ids_json TEXT,
            notes TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ocr_first_chunk_candidates_scope
        ON ocr_first_chunk_candidates(document_id, pdf_page_start, pdf_page_end, source_backend, review_status)
        """
    )


def create_ocr_first_candidate_correction_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_first_candidate_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            corrected_text TEXT NOT NULL,
            corrected_search_text TEXT,
            correction_rules_json TEXT NOT NULL,
            correction_diff_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ocr_first_candidate_corrections_candidate
        ON ocr_first_candidate_corrections(candidate_id, review_status)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ocr_first_candidate_corrections_document
        ON ocr_first_candidate_corrections(document_id, review_status)
        """
    )


def run_candidate_corrections(
    *,
    db_path: Path,
    document_id: int | None,
    page_start: int,
    page_end: int,
    candidate_ids: list[int] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    candidates = _load_pending_ocr_first_candidates(
        db_path,
        document_id=document_id,
        page_start=page_start,
        page_end=page_end,
        candidate_ids=candidate_ids or [],
    )
    results = [_candidate_correction_report(candidate) for candidate in candidates]
    apply_result: dict[str, Any] | None = None
    if apply:
        apply_result = _apply_candidate_corrections(db_path, results)
    return {
        "status": "APPLIED" if apply else "DRY_RUN",
        "mode": "ocr_first_candidate_correction",
        "document_id": document_id,
        "page_start": page_start,
        "page_end": page_end,
        "candidate_ids_requested": candidate_ids or [],
        "candidates_checked": len(results),
        "candidates_changed": sum(1 for result in results if result["changed"]),
        "correction_table": "ocr_first_candidate_corrections",
        "correction_results": results,
        "quality_compare": _candidate_correction_quality_compare(results),
        "apply_result": apply_result,
        "no_database_writes_performed": not apply,
        "no_lancedb_writes_performed": True,
        "knowledge_chunks_written": False,
        "lancedb_writes_performed": False,
        "ocr_rerun_performed": False,
    }


def _load_pending_ocr_first_candidates(
    db_path: Path,
    *,
    document_id: int | None,
    page_start: int,
    page_end: int,
    candidate_ids: list[int],
) -> list[dict[str, Any]]:
    where = ["review_status = 'pending'"]
    params: list[Any] = []
    if candidate_ids:
        placeholders = ", ".join("?" for _ in candidate_ids)
        where.append(f"id IN ({placeholders})")
        params.extend(candidate_ids)
    else:
        if document_id is None:
            raise ValueError("--correct-candidates requires --document-id unless --candidate-id is supplied")
        where.extend(
            [
                "document_id = ?",
                "pdf_page_start <= ?",
                "pdf_page_end >= ?",
            ]
        )
        params.extend([document_id, page_end, page_start])
    sql = f"""
        SELECT id, document_id, pdf_page_start, pdf_page_end, chunk_text, search_text, review_status
        FROM ocr_first_chunk_candidates
        WHERE {' AND '.join(where)}
        ORDER BY pdf_page_start, candidate_index, id
    """
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError("ocr_first_chunk_candidates table not found; apply OCR-first candidates before correction") from exc
    return [dict(row) for row in rows]


def _candidate_correction_report(candidate: dict[str, Any]) -> dict[str, Any]:
    before_text = str(candidate.get("chunk_text") or "")
    corrected = correct_ocr_candidate_text(before_text)
    corrected_search = _normalize_corrected_search_text(corrected["corrected_text"])
    return {
        "candidate_id": int(candidate["id"]),
        "document_id": int(candidate["document_id"]),
        "pdf_page_start": candidate.get("pdf_page_start"),
        "pdf_page_end": candidate.get("pdf_page_end"),
        "before_text": before_text,
        "after_text": corrected["corrected_text"],
        "corrected_search_text": corrected_search,
        "changed": corrected["changed"],
        "rules_applied": corrected["rules_applied"],
        "diff_summary": corrected["diff_summary"],
        "correction_diff": corrected["correction_diff"],
        "risky_rules_skipped": corrected["risky_rules_skipped"],
        "quality_before": correction_quality_metrics(before_text),
        "quality_after": correction_quality_metrics(corrected["corrected_text"]),
    }


def _apply_candidate_corrections(db_path: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    inserted_ids: list[int] = []
    reused_ids: list[int] = []
    superseded_count = 0
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        create_ocr_first_candidate_correction_schema(connection)
        for result in results:
            if not result["changed"]:
                continue
            rules_json = json.dumps(result["rules_applied"], ensure_ascii=False, sort_keys=True)
            diff_json = json.dumps(result["correction_diff"], ensure_ascii=False, sort_keys=True)
            existing = connection.execute(
                """
                SELECT id, corrected_text, corrected_search_text, correction_rules_json, correction_diff_json
                FROM ocr_first_candidate_corrections
                WHERE candidate_id = ? AND review_status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (result["candidate_id"],),
            ).fetchone()
            if existing and _pending_correction_matches(existing, result, rules_json, diff_json):
                reused_ids.append(int(existing["id"]))
                continue
            superseded_count += connection.execute(
                """
                UPDATE ocr_first_candidate_corrections
                SET review_status = 'superseded'
                WHERE candidate_id = ? AND review_status = 'pending'
                """,
                (result["candidate_id"],),
            ).rowcount
            cursor = connection.execute(
                """
                INSERT INTO ocr_first_candidate_corrections (
                    candidate_id, document_id, corrected_text, corrected_search_text,
                    correction_rules_json, correction_diff_json, created_at, review_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    result["candidate_id"],
                    result["document_id"],
                    result["after_text"],
                    result["corrected_search_text"],
                    rules_json,
                    diff_json,
                    now,
                ),
            )
            inserted_ids.append(int(cursor.lastrowid))
        connection.commit()
    return {
        "correction_table": "ocr_first_candidate_corrections",
        "inserted_corrections": len(inserted_ids),
        "inserted_correction_ids": inserted_ids,
        "reused_pending_corrections": len(reused_ids),
        "reused_correction_ids": reused_ids,
        "superseded_pending_corrections": int(superseded_count or 0),
        "candidate_rows_modified": False,
        "knowledge_chunks_written": False,
        "lancedb_writes_performed": False,
        "ocr_rerun_performed": False,
    }


def _pending_correction_matches(
    existing: sqlite3.Row,
    result: dict[str, Any],
    rules_json: str,
    diff_json: str,
) -> bool:
    return (
        existing["corrected_text"] == result["after_text"]
        and existing["corrected_search_text"] == result["corrected_search_text"]
        and existing["correction_rules_json"] == rules_json
        and existing["correction_diff_json"] == diff_json
    )


def correct_ocr_candidate_text(text: str) -> dict[str, Any]:
    value = str(text or "")
    rules: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    risky_skipped: list[dict[str, str]] = []

    value = _replace_literal(value, "\\delta", "δ", "math_delta_latex_residue", rules, replacements)
    value = _replace_regex(value, r"ð\s*\(", "δ(", "math_eth_open_paren_to_delta", rules, replacements)
    value = _replace_literal(value, "ð", "δ", "math_eth_to_delta", rules, replacements)
    value = _replace_literal(value, "-\\infty", "-∞", "math_negative_infty_latex", rules, replacements)
    if _has_math_context(value):
        value = _replace_literal(value, "一∞", "-∞", "math_cjk_one_infty_to_negative_infty", rules, replacements)
        value = _replace_regex(value, r"基\s*-\s*∞", "-∞", "math_ji_negative_infty_to_negative_infty", rules, replacements)
    elif "一∞" in value or "基-∞" in value:
        risky_skipped.append(
            {
                "rule": "math_negative_infty_context_required",
                "reason": "Skipped negative infinity repair because no math context was detected.",
            }
        )

    value = _replace_regex(value, r"举似[地恤]", "类似地", "known_ocr_phrase_similarly", rules, replacements)
    value = _replace_regex(value, r"引踵(?=\s*\d)", "引理", "lemma_number_context_yinzhong", rules, replacements)
    value = _cleanup_html_residue(value, rules, replacements)
    value = re.sub(r"[ \t]{2,}", " ", value).strip()

    if "结点。" in value:
        risky_skipped.append(
            {
                "rule": "forbidden_global_node_punctuation_replacement",
                "reason": "Skipped global replacement for 结点。 because it needs explicit sentence context.",
            }
        )

    return {
        "corrected_text": value,
        "changed": value != str(text or ""),
        "rules_applied": rules,
        "risky_rules_skipped": risky_skipped,
        "diff_summary": {
            "changed": value != str(text or ""),
            "replacement_count": sum(int(item["count"]) for item in replacements),
            "rules_count": len(rules),
            "before_length": len(str(text or "")),
            "after_length": len(value),
        },
        "correction_diff": {
            "before": str(text or ""),
            "after": value,
            "replacements": replacements,
        },
    }


def correction_quality_metrics(text: str) -> dict[str, Any]:
    base = detect_quality_issues(text)
    known_samples = _known_ocr_error_occurrences(text)
    suspicious_symbols = re_findall(r"ð|�|\\delta|\\infty|一∞|基\s*-\s*∞", str(text or ""))
    return {
        "suspicious_symbol_count": len(suspicious_symbols),
        "known_ocr_error_count": len(known_samples),
        "known_ocr_error_samples": sorted(set(known_samples)),
        "html_tag_count": base["html_tag_count"],
        "math_noise_count": base["math_noise_count"],
        "repeated_token_count": base["repeated_token_count"],
        "broken_sentence_count": base["broken_sentence_count"],
    }


def _candidate_correction_quality_compare(results: list[dict[str, Any]]) -> dict[str, Any]:
    before = _sum_correction_metrics(result["quality_before"] for result in results)
    after = _sum_correction_metrics(result["quality_after"] for result in results)
    return {"raw_candidates": before, "corrected_candidates": after}


def _sum_correction_metrics(items: Any) -> dict[str, Any]:
    totals = {
        "suspicious_symbol_count": 0,
        "known_ocr_error_count": 0,
        "html_tag_count": 0,
        "math_noise_count": 0,
        "repeated_token_count": 0,
        "broken_sentence_count": 0,
        "known_ocr_error_samples": [],
    }
    seen: set[str] = set()
    for item in items:
        for key in (
            "suspicious_symbol_count",
            "known_ocr_error_count",
            "html_tag_count",
            "math_noise_count",
            "repeated_token_count",
            "broken_sentence_count",
        ):
            totals[key] += int(item.get(key) or 0)
        for sample in item.get("known_ocr_error_samples") or []:
            if sample not in seen:
                totals["known_ocr_error_samples"].append(sample)
                seen.add(sample)
    return totals


def _known_ocr_error_occurrences(text: str) -> list[str]:
    value = str(text or "")
    patterns = ["引踵", "ð", "一∞", "基-∞", "举似恤", "举似地"]
    occurrences: list[str] = []
    for pattern in patterns:
        occurrences.extend([pattern] * value.count(pattern))
    return occurrences


def _replace_literal(
    value: str,
    before: str,
    after: str,
    rule: str,
    rules: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> str:
    count = value.count(before)
    if count == 0:
        return value
    rules.append({"rule": rule, "count": count, "before": before, "after": after})
    replacements.append({"rule": rule, "before": before, "after": after, "count": count})
    return value.replace(before, after)


def _replace_regex(
    value: str,
    pattern: str,
    after: str,
    rule: str,
    rules: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> str:
    matches = list(re.finditer(pattern, value))
    if not matches:
        return value
    samples = sorted({match.group(0) for match in matches})
    rules.append({"rule": rule, "count": len(matches), "pattern": pattern, "after": after, "samples": samples})
    replacements.append({"rule": rule, "before": samples, "after": after, "count": len(matches)})
    return re.sub(pattern, after, value)


def _cleanup_html_residue(
    value: str,
    rules: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> str:
    html_entities = {"&nbsp;": " ", "&amp;": "&"}
    for before, after in html_entities.items():
        value = _replace_literal(value, before, after, "html_entity_cleanup", rules, replacements)
    pattern = r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*)?>|&lt;/?[A-Za-z][^&]*&gt;"
    matches = list(re.finditer(pattern, value))
    if not matches:
        return value
    samples = sorted({match.group(0) for match in matches})
    rules.append({"rule": "html_tag_cleanup", "count": len(matches), "pattern": pattern, "samples": samples})
    replacements.append({"rule": "html_tag_cleanup", "before": samples, "after": "", "count": len(matches)})
    return re.sub(pattern, "", value)


def _has_math_context(text: str) -> bool:
    value = str(text or "")
    return bool(
        re.search(r"δ\s*\(|[A-Za-z]_\w|[A-Za-z]\([^)]*\)|\\infty|∞|≤|≥|<|>|最短路径|路径|权重|松弛|上界|估计|初始化|负值|负权", value)
    )


def _normalize_corrected_search_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _load_document(db_path: Path, *, document_id: int | None, title: str | None) -> dict[str, Any]:
    if not document_id and not title:
        return {"id": None, "title": title, "pdf_path": None, "document_type": "book"}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if document_id:
            row = connection.execute(
                "SELECT id, title, pdf_path, document_type FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id, title, pdf_path, document_type FROM documents WHERE title = ? ORDER BY id DESC LIMIT 1",
                (title,),
            ).fetchone()
    if not row:
        raise ValueError("document not found")
    if str(row["document_type"] or "") != "book":
        raise ValueError("ocr_layout_first import is only valid for document_type=book")
    return dict(row)


def _load_existing_ocr_lines(db_path: Path, document_id: int, pdf_page: int) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT id, document_id, pdf_page, block_id, line_index, text, normalized_text,
                       bbox_json, confidence, source_backend
                FROM pdf_page_layout_lines
                WHERE document_id = ? AND pdf_page = ? AND source_backend = 'surya_ocr'
                ORDER BY line_index
                """,
                (document_id, pdf_page),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "document_id": int(row["document_id"]),
                "pdf_page": int(row["pdf_page"]),
                "block_id": row["block_id"],
                "block_index": 0,
                "line_index": int(row["line_index"]),
                "text": row["text"],
                "normalized_text": row["normalized_text"],
                "bbox": json.loads(row["bbox_json"] or "{}"),
                "confidence": row["confidence"],
                "source_backend": row["source_backend"],
            }
        )
    return result


def _load_old_chunks(db_path: Path, document_id: int | None, page_start: int, page_end: int) -> list[dict[str, Any]]:
    if not document_id:
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT id, pdf_page_start, pdf_page_end, chunk_text
                FROM knowledge_chunks
                WHERE document_id = ?
                  AND COALESCE(pdf_page_start, pdf_page_end, 0) <= ?
                  AND COALESCE(pdf_page_end, pdf_page_start, 0) >= ?
                ORDER BY chunk_index, id
                """,
                (document_id, page_end, page_start),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {
            "chunk_id": int(row["id"]),
            "pdf_page_start": row["pdf_page_start"],
            "pdf_page_end": row["pdf_page_end"],
            "chunk_text_preview": _preview(row["chunk_text"], 260),
            "_quality_text": str(row["chunk_text"] or ""),
        }
        for row in rows
    ]


def _load_candidate_views(db_path: Path, document_id: int | None, page_start: int, page_end: int) -> list[dict[str, Any]]:
    if document_id is None:
        return []
    base_sql = """
        SELECT c.id, c.candidate_index, c.pdf_page_start, c.pdf_page_end, c.chunk_text,
               c.source_line_ids_json, c.source_line_keys_json,
               {corrected_column}
        FROM ocr_first_chunk_candidates c
        {correction_join}
        WHERE c.document_id = ? AND c.review_status = 'pending'
          AND c.pdf_page_start <= ? AND c.pdf_page_end >= ?
        ORDER BY c.candidate_index, c.id
    """
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                base_sql.format(
                    corrected_column="COALESCE(r.corrected_text, c.chunk_text) AS corrected_text",
                    correction_join="""
                        LEFT JOIN ocr_first_candidate_corrections r
                          ON r.id = (
                              SELECT MAX(rc.id) FROM ocr_first_candidate_corrections rc
                              WHERE rc.candidate_id = c.id AND rc.review_status = 'pending'
                          )
                    """,
                ),
                (document_id, page_end, page_start),
            ).fetchall()
        except sqlite3.OperationalError:
            try:
                rows = connection.execute(
                    base_sql.format(
                        corrected_column="c.chunk_text AS corrected_text",
                        correction_join="",
                    ),
                    (document_id, page_end, page_start),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
    return [
        {
            "candidate_id": int(row["id"]),
            "candidate_index": int(row["candidate_index"]),
            "pdf_page_start": row["pdf_page_start"],
            "pdf_page_end": row["pdf_page_end"],
            "chunk_text": str(row["chunk_text"] or ""),
            "corrected_text": str(row["corrected_text"] or row["chunk_text"] or ""),
            "source_line_ids": json.loads(row["source_line_ids_json"] or "[]"),
            "source_line_keys": json.loads(row["source_line_keys_json"] or "[]"),
            "role": "body",
            "section_title": None,
        }
        for row in rows
    ]


def _generated_candidate_views(chunks: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": None,
            "candidate_index": chunk.chunk_index,
            "pdf_page_start": chunk.pdf_page_start,
            "pdf_page_end": chunk.pdf_page_end,
            "chunk_text": chunk.chunk_text,
            "corrected_text": chunk.chunk_text,
            "source_line_ids": list(chunk.source_line_ids),
            "source_line_keys": list(chunk.source_line_keys),
            "role": chunk.role,
            "section_title": chunk.section_title,
        }
        for chunk in chunks
    ]


def evaluate_candidate_quality_gate(chunks: list[Any]) -> dict[str, Any]:
    reports = [_candidate_quality_diagnostic(chunk) for chunk in chunks]
    counts = {
        status: sum(1 for item in reports if item["quality_status"] == status)
        for status in ("clean", "needs_correction", "needs_manual_review", "blocked_from_apply")
    }
    return {
        "candidates": reports,
        "candidates_by_index": {item["candidate_index"]: item for item in reports},
        "candidates_clean_count": counts["clean"],
        "candidates_needs_correction_count": counts["needs_correction"],
        "candidates_needs_manual_review_count": counts["needs_manual_review"],
        "candidates_blocked_from_apply_count": counts["blocked_from_apply"],
        "apply_allowed": counts["clean"] == len(reports),
        "apply_blocked_reasons": sorted(
            {
                reason
                for item in reports
                if item["quality_status"] != "clean"
                for reason in item["blocked_reasons"]
            }
        ),
        "safe_correction_suggestions": [
            {"candidate_index": item["candidate_index"], **suggestion}
            for item in reports
            for suggestion in item["correction_suggestions"]["safe"]
        ],
        "risky_correction_suggestions": [
            {"candidate_index": item["candidate_index"], **suggestion}
            for item in reports
            for suggestion in item["correction_suggestions"]["risky"]
        ],
    }


def _candidate_quality_diagnostic(chunk: Any) -> dict[str, Any]:
    text = str(chunk.chunk_text or "")
    issues = detect_quality_issues(text)
    safe, risky = _candidate_correction_suggestions(text)
    reasons: list[str] = []
    manual = False
    blocked = False
    if issues["page_number_noise_count"] > 0:
        reasons.append("header_or_page_number_noise_in_candidate_text")
        blocked = True
    if re.search(r"p\s*=\s*⟨?\s*12(?:\s*[,，]\s*12){2,}", text):
        reasons.append("damaged_formula_repeated_numeric_tokens")
        manual = True
    minimum = (chunk.confidence_summary or {}).get("min")
    if minimum is not None and float(minimum) < 0.65:
        reasons.append("low_confidence_body_line_below_0.65")
        manual = True
    if issues["unknown_ocr_error_samples"]:
        reasons.append("unknown_ocr_error_samples_present")
    if safe:
        reasons.append("safe_correction_suggestions_present")
    if risky:
        reasons.append("risky_correction_suggestions_present")
        manual = True
    if blocked:
        status = "blocked_from_apply"
    elif manual:
        status = "needs_manual_review"
    elif safe or issues["unknown_ocr_error_samples"]:
        status = "needs_correction"
    else:
        status = "clean"
    return {
        "candidate_index": chunk.chunk_index,
        "source_line_ids": list(chunk.source_line_ids),
        "confidence_summary": dict(chunk.confidence_summary or {}),
        "quality_status": status,
        "blocked_reasons": reasons,
        "correction_suggestions": {"safe": safe, "risky": risky},
        "detected_issues": issues,
    }


def _candidate_correction_suggestions(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    safe: list[dict[str, Any]] = []
    risky: list[dict[str, Any]] = []
    if "Diikstra" in text:
        safe.append({"before": "Diikstra", "after": "Dijkstra", "reason": "known_algorithm_name_typo"})
    if re.search(r"包含色权重的边", text):
        safe.append({"before": "包含色权重的边", "after": "包含负权重的边", "reason": "negative_edge_context"})
    if "邀請点和終結点" in text:
        risky.append({"before": "邀請点和終結点", "suggested": "起始点和终结点", "reason": "requires_source_image_review"})
    if re.search(r"源结点\s*5", text):
        risky.append({"before": "源结点 5", "suggested": "源结点 s", "reason": "variable_glyph_requires_context"})
    if re.search(r"p\s*=\s*⟨?\s*12(?:\s*[,，]\s*12){2,}", text):
        risky.append({"before": "p=⟨12, 12, 12...", "suggested": None, "reason": "formula_damage_requires_manual_transcription"})
    return safe, risky


def _attach_surya_chunk_roles(candidate_views: list[dict[str, Any]], chunks: list[Any]) -> list[dict[str, Any]]:
    chunks_by_sources = {tuple(chunk.source_line_ids): chunk for chunk in chunks}
    for candidate in candidate_views:
        matched = chunks_by_sources.get(tuple(candidate.get("source_line_ids") or []))
        if matched is not None:
            candidate["role"] = matched.role
    return candidate_views


def _model_cache_policy_report(model_cache_root: str | Path, marker_structure: dict[str, Any] | None) -> dict[str, Any]:
    root = Path(model_cache_root)
    return {
        "model_cache_root": str(root),
        "surya_datalab_model_cache": str(root / "datalab" / "models"),
        "hf_home": str(root / "huggingface"),
        "hf_hub_cache": str(root / "huggingface" / "hub"),
        "transformers_cache": str(root / "huggingface" / "hub"),
        "torch_home": str(root / "torch"),
        "xdg_cache_home": str(root),
        "marker_cache_used": bool((marker_structure or {}).get("marker_cache_used")),
        "marker_cache_mode": (marker_structure or {}).get("marker_cache_mode", "not_used"),
    }


def _public_candidate_view(candidate: dict[str, Any], text_key: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_index": candidate.get("candidate_index"),
        "pdf_page_start": candidate.get("pdf_page_start"),
        "pdf_page_end": candidate.get("pdf_page_end"),
        "text": _preview(candidate.get(text_key), 420),
        "source_line_ids": list(candidate.get("source_line_ids") or []),
        "source_line_keys": list(candidate.get("source_line_keys") or []),
        "section_title": candidate.get("section_title"),
    }


def compare_hybrid_quality(
    old_chunks: list[dict[str, Any]],
    candidate_views: list[dict[str, Any]],
    hybrid_result: dict[str, Any],
) -> dict[str, Any]:
    old_entries = [
        {"id": chunk["chunk_id"], "text": str(chunk.get("_quality_text") or ""), "source_line_ids": []}
        for chunk in old_chunks
    ]
    surya_entries = [
        {"id": candidate.get("candidate_id"), "text": candidate.get("chunk_text", ""), **candidate}
        for candidate in candidate_views
    ]
    corrected_entries = [
        {"id": candidate.get("candidate_id"), "text": candidate.get("corrected_text", ""), **candidate}
        for candidate in candidate_views
    ]
    hybrid_entries = [
        {"id": candidate.get("candidate_id"), "text": candidate.get("chunk_text", ""), **candidate}
        for candidate in hybrid_result["candidates"]
    ]
    return {
        "old_chunks": _quality_lane(old_entries, located=False),
        "surya_only_candidates": _quality_lane(surya_entries),
        "corrected_candidates": _quality_lane(corrected_entries),
        "hybrid_candidates": _quality_lane(
            hybrid_entries,
            marker_conflict_count=hybrid_result["marker_conflict_count"],
            marker_rejected_repetition_count=hybrid_result["marker_rejected_repetition_count"],
        ),
    }


def _quality_lane(
    entries: list[dict[str, Any]],
    *,
    located: bool = True,
    marker_conflict_count: int = 0,
    marker_rejected_repetition_count: int = 0,
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for entry in entries:
        text = str(entry.get("text") or "")
        reports.append(
            {
                "id": entry.get("id"),
                "text_sample": _preview(text, 260),
                "source_line_ids": list(entry.get("source_line_ids") or []),
                "metrics": _hybrid_quality_metrics(text, entry.get("section_title")),
            }
        )
    totals = _sum_hybrid_metrics(report["metrics"] for report in reports)
    anchored = sum(1 for entry in entries if entry.get("source_line_ids"))
    totals["source_line_coverage"] = round(anchored / len(entries), 4) if located and entries else (0.0 if located else None)
    totals["marker_conflict_count"] = marker_conflict_count
    totals["marker_rejected_repetition_count"] = marker_rejected_repetition_count
    return {"items": reports, "metrics": totals}


def _hybrid_quality_metrics(text: str, section_title: str | None) -> dict[str, Any]:
    issues = detect_quality_issues(text)
    corrected = correction_quality_metrics(text)
    return {
        "html_tag_count": issues["html_tag_count"],
        "math_noise_count": issues["math_noise_count"],
        "repeated_token_count": issues["repeated_token_count"],
        "page_number_noise_count": issues["page_number_noise_count"],
        "broken_sentence_count": issues["broken_sentence_count"],
        "known_ocr_error_count": corrected["known_ocr_error_count"],
        "suspicious_symbol_count": corrected["suspicious_symbol_count"],
        "heading_quality_score": 1 if section_title or re.search(r"(?m)^#{1,6}\s+\S+", text) else 0,
        "formula_preservation_score": len(re_findall(r"δ\s*\(|∞|\\delta|\\infty|\$\$", text)),
    }


def _sum_hybrid_metrics(items: Any) -> dict[str, Any]:
    keys = (
        "html_tag_count",
        "math_noise_count",
        "repeated_token_count",
        "page_number_noise_count",
        "broken_sentence_count",
        "known_ocr_error_count",
        "suspicious_symbol_count",
        "heading_quality_score",
        "formula_preservation_score",
    )
    totals = {key: 0 for key in keys}
    for item in items:
        for key in keys:
            totals[key] += int(item.get(key) or 0)
    return totals


def compare_existing_quality(old_chunks: list[dict[str, Any]], chunks: list[Any]) -> dict[str, Any]:
    old_reports = []
    new_reports = []
    for chunk in old_chunks:
        text = str(chunk.get("_quality_text") or chunk.get("chunk_text_preview") or "")
        old_reports.append(
            {
                "chunk_id": chunk["chunk_id"],
                "text_sample": _preview(text, 260),
                "detected_issues": detect_quality_issues(text),
            }
        )
    for chunk in chunks:
        text = str(chunk.chunk_text or "")
        new_reports.append(
            {
                "candidate_index": chunk.chunk_index,
                "text": _preview(text, 420),
                "source_line_ids": chunk.source_line_ids,
                "detected_issues": detect_quality_issues(text),
            }
        )
    old_totals = _sum_quality_issues(item["detected_issues"] for item in old_reports)
    new_totals = _sum_quality_issues(item["detected_issues"] for item in new_reports)
    return {
        "old_chunks": old_reports,
        "new_candidates": new_reports,
        "quality_deltas": {
            "html_tag_count": {"old": old_totals["html_tag_count"], "new": new_totals["html_tag_count"]},
            "math_noise_count": {"old": old_totals["math_noise_count"], "new": new_totals["math_noise_count"]},
            "repeated_token_count": {"old": old_totals["repeated_token_count"], "new": new_totals["repeated_token_count"]},
            "page_number_noise_count": {"old": old_totals["page_number_noise_count"], "new": new_totals["page_number_noise_count"]},
            "broken_sentence_count": {"old": old_totals["broken_sentence_count"], "new": new_totals["broken_sentence_count"]},
            "unknown_ocr_error_samples": {
                "old": old_totals["unknown_ocr_error_samples"],
                "new": new_totals["unknown_ocr_error_samples"],
            },
        },
    }


def detect_quality_issues(text: str) -> dict[str, Any]:
    value = str(text or "")
    html_tag_matches = re_findall(r"</?[A-Za-z][^>]*>|&lt;/?[A-Za-z][^&]*&gt;|&amp;|&nbsp;", value)
    math_noise_matches = re_findall(r"\\(?:overset|triangle|rho|delta|infty|sum|frac|left|right)|&<br/>|<br/>|CV", value)
    repeated_token_matches = re_findall(r"([A-Za-z]\([^)]{1,12}\)|\b[\w]{1,12}\b)(?:\s*[·,，]\s*\1){2,}", value)
    page_number_matches = re_findall(r"(?:^|\s)(?:[1-9]\d{2,3})(?:\s|$)", value)
    broken_sentence_matches = re_findall(r"[。；，、,]\s*[。；，、,]|[A-Za-z]、|。 《|。。|无定。|最处", value)
    unknown_samples = _unknown_ocr_error_samples(value)
    return {
        "html_tag": bool(html_tag_matches),
        "html_tag_count": len(html_tag_matches),
        "math_noise": bool(math_noise_matches),
        "math_noise_count": len(math_noise_matches),
        "repeated_token": bool(repeated_token_matches),
        "repeated_token_count": len(repeated_token_matches),
        "page_number_noise": bool(page_number_matches),
        "page_number_noise_count": len(page_number_matches),
        "broken_sentence": bool(broken_sentence_matches),
        "broken_sentence_count": len(broken_sentence_matches),
        "unknown_ocr_error_samples": unknown_samples,
    }


def _sum_quality_issues(items: Any) -> dict[str, Any]:
    totals = {
        "html_tag_count": 0,
        "math_noise_count": 0,
        "repeated_token_count": 0,
        "page_number_noise_count": 0,
        "broken_sentence_count": 0,
        "unknown_ocr_error_samples": [],
    }
    seen_samples: set[str] = set()
    for item in items:
        for key in ("html_tag_count", "math_noise_count", "repeated_token_count", "page_number_noise_count", "broken_sentence_count"):
            totals[key] += int(item.get(key) or 0)
        for sample in item.get("unknown_ocr_error_samples") or []:
            if sample not in seen_samples:
                totals["unknown_ocr_error_samples"].append(sample)
                seen_samples.add(sample)
    return totals


def _unknown_ocr_error_samples(text: str) -> list[str]:
    suspicious = [
        "引踵",
        "ð",
        "结点。",
        "一∞",
        "基-∞",
        "举似恤",
        "划结点",
        "最处",
        "无定",
        "CV",
        "于路径",
    ]
    return [sample for sample in suspicious if sample in text]


def re_findall(pattern: str, text: str) -> list[str]:
    return [match.group(0) for match in re.finditer(pattern, text)]


def _public_old_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in chunk.items() if not key.startswith("_")}


def _chunk_preview(chunk: Any, quality: dict[str, Any] | None = None) -> dict[str, Any]:
    output = {
        "chunk_index": chunk.chunk_index,
        "pdf_page_start": chunk.pdf_page_start,
        "pdf_page_end": chunk.pdf_page_end,
        "source_line_ids": chunk.source_line_ids,
        "source_line_keys": chunk.source_line_keys,
        "source_line_start": chunk.source_line_start,
        "source_line_end": chunk.source_line_end,
        "confidence_summary": chunk.confidence_summary,
        "chunk_text": _preview(chunk.chunk_text, 420),
        "search_text": _preview(chunk.search_text, 220),
    }
    if quality:
        output.update(
            {
                "quality_status": quality["quality_status"],
                "blocked_reasons": quality["blocked_reasons"],
                "correction_suggestions": quality["correction_suggestions"],
            }
        )
    return output


def _backup_sqlite(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"research_memory_before_fix5l_ocr_first_candidates_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _infer_page_width(lines: list[dict[str, Any]]) -> float | None:
    values = [float(line.get("bbox", {}).get("x1", 0.0)) for line in lines if line.get("bbox")]
    return max(values) if values else None


def _infer_page_height(lines: list[dict[str, Any]]) -> float | None:
    values = [float(line.get("bbox", {}).get("y1", 0.0)) for line in lines if line.get("bbox")]
    return max(values) if values else None


def _preview(text: Any, limit: int = 180) -> str:
    value = str(text or "").replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _text_report(report: dict[str, Any]) -> str:
    lines = [
        f"status={report['status']}",
        f"mode={report['mode']}",
        f"document_id={report.get('document_id')}",
        f"pages={report['pages']}",
        f"ocr_lines_count={report['ocr_lines_count']}",
        f"filtered_header_footer_count={report['filtered_header_footer_count']}",
        f"proposed_chunks_count={report['proposed_chunks_count']}",
        f"no_database_writes_performed={report['no_database_writes_performed']}",
    ]
    for chunk in report["sample_chunks"][:3]:
        lines.append(f"chunk[{chunk['chunk_index']}] lines={chunk['source_line_keys']} text={chunk['chunk_text']}")
    if report.get("apply_result"):
        lines.append(f"apply_result={report['apply_result']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
