from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services import vector_store_service
from app.services.ocr_layout_chunker import chunk_ocr_layout_lines
from scripts.promote_ocr_first_candidates import (
    build_old_candidate_mapping,
    build_proposed_db_writes,
    build_vector_sync_plan,
    inspect_schema,
    load_old_chunks,
    open_read_only_connection,
    quality_metrics,
    recommend_strategy,
)


READINESS_ALREADY_PROMOTED = "already_promoted"
READINESS_READY = "ready_for_promote_dryrun"
READINESS_NEEDS_OCR = "needs_ocr_layout"
READINESS_NEEDS_CANDIDATE = "needs_candidate_generation"
READINESS_NEEDS_CORRECTION = "needs_correction"
READINESS_NEEDS_REVIEW = "needs_manual_review"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    report = run_chapter_promote_dry_run(
        db_path=Path(args.db_path),
        document_id=args.document_id,
        chapter_title=args.chapter_title,
        chapter_id=args.chapter_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _text_report(report))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan read-only OCR-first promote batches for one book chapter.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--chapter-title", required=True)
    parser.add_argument("--chapter-id", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run_chapter_promote_dry_run(
    *,
    db_path: Path,
    document_id: int,
    chapter_title: str,
    chapter_id: int | None = None,
    dry_run: bool,
    vector_status_loader: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not dry_run:
        raise ValueError("chapter OCR-first planning is dry-run only; apply is not supported")
    with open_read_only_connection(db_path) as connection:
        document = _load_document(connection, document_id)
        chapter = detect_chapter_range(
            connection,
            document_id=document_id,
            chapter_title=chapter_title,
            chapter_id=chapter_id,
        )
        schema = inspect_schema(connection)
        pages = [
            build_page_readiness(
                connection,
                db_path=db_path,
                document_id=document_id,
                chapter=chapter,
                pdf_page=page,
                schema=schema,
            )
            for page in range(int(chapter["pdf_page_start"]), int(chapter["pdf_page_end"]) + 1)
        ]
    promoted_source_ids = [
        source_id
        for page in pages
        if page["readiness_status"] == READINESS_ALREADY_PROMOTED
        for source_id in page["proposed_vector_source_ids"]
    ]
    vector_status = _read_vector_status(
        db_path=db_path,
        source_ids=promoted_source_ids,
        loader=vector_status_loader,
    )
    for page in pages:
        page["vector_status"] = [
            item for item in vector_status.get("items", []) if item["source_id"] in page["proposed_vector_source_ids"]
        ] if page["readiness_status"] == READINESS_ALREADY_PROMOTED else []
    batch_plan = build_batch_plan(document_id=document_id, chapter=chapter, pages=pages)
    return {
        "status": "DRY_RUN",
        "mode": "chapter_ocr_first_promote_plan",
        "document": document,
        "chapter": chapter,
        "read_only_sqlite_connection": True,
        "no_database_writes_performed": True,
        "knowledge_chunks_written": False,
        "lancedb_writes_performed": False,
        "ocr_run_performed": False,
        "pdf_import_performed": False,
        "llm_calls_performed": False,
        "whole_chapter_apply_allowed": False,
        "page_readiness": pages,
        "readiness_summary": _readiness_summary(pages),
        "quality_summary": _chapter_quality_summary(pages),
        "already_promoted_vector_check": vector_status,
        "batch_plan": batch_plan,
        "blockers": _chapter_blockers(pages),
    }


def detect_chapter_range(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chapter_title: str,
    chapter_id: int | None = None,
) -> dict[str, Any]:
    requested = _normalize_title(chapter_title)
    if chapter_id is not None:
        rows = connection.execute(
            "SELECT id, chapter_index, title, pdf_page_start, pdf_page_end FROM book_chapters WHERE document_id = ? AND id = ?",
            (document_id, chapter_id),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT id, chapter_index, title, pdf_page_start, pdf_page_end FROM book_chapters WHERE document_id = ? ORDER BY chapter_index",
            (document_id,),
        ).fetchall()
        rows = [row for row in rows if requested in _normalize_title(str(row["title"] or ""))]
    if len(rows) != 1:
        raise ValueError(f"expected one matching chapter, found {len(rows)} for {chapter_title!r}")
    row = rows[0]
    chunk_summary = connection.execute(
        """
        SELECT MIN(id) AS min_chunk_id, MAX(id) AS max_chunk_id, COUNT(*) AS chunk_count,
               MIN(pdf_page_start) AS min_page, MAX(pdf_page_end) AS max_page
        FROM knowledge_chunks WHERE document_id = ? AND chapter_id = ?
        """,
        (document_id, int(row["id"])),
    ).fetchone()
    page_start = row["pdf_page_start"] if row["pdf_page_start"] is not None else chunk_summary["min_page"]
    page_end = row["pdf_page_end"] if row["pdf_page_end"] is not None else chunk_summary["max_page"]
    if page_start is None or page_end is None:
        raise ValueError("chapter has no PDF page range in book_chapters or knowledge_chunks")
    markdown_matches = [
        dict(match)
        for match in connection.execute(
            "SELECT id, heading_level, heading_title FROM markdown_nodes WHERE document_id = ? ORDER BY order_index",
            (document_id,),
        ).fetchall()
        if requested in _normalize_title(str(match["heading_title"] or ""))
    ]
    return {
        "chapter_id": int(row["id"]),
        "chapter_index": int(row["chapter_index"]),
        "title": str(row["title"]),
        "pdf_page_start": int(page_start),
        "pdf_page_end": int(page_end),
        "page_count": int(page_end) - int(page_start) + 1,
        "chunk_id_start": int(chunk_summary["min_chunk_id"]) if chunk_summary["min_chunk_id"] is not None else None,
        "chunk_id_end": int(chunk_summary["max_chunk_id"]) if chunk_summary["max_chunk_id"] is not None else None,
        "chunk_count": int(chunk_summary["chunk_count"] or 0),
        "range_source": "book_chapters_with_knowledge_chunks_confirmation",
        "markdown_heading_matches": markdown_matches,
        "range_uncertainty": None,
    }


def build_page_readiness(
    connection: sqlite3.Connection,
    *,
    db_path: Path,
    document_id: int,
    chapter: dict[str, Any],
    pdf_page: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    canonical_chunks = load_old_chunks(
        connection,
        document_id=document_id,
        page_start=pdf_page,
        page_end=pdf_page,
    )
    canonical_chunks = [item for item in canonical_chunks if item.get("chapter_id") == chapter["chapter_id"]]
    promoted_chunk_ids = _promoted_chunk_ids(connection, document_id=document_id, pdf_page=pdf_page)
    already_promoted_ids = sorted(set(promoted_chunk_ids).intersection(item["chunk_id"] for item in canonical_chunks))
    ocr_lines = _load_ocr_lines(connection, document_id=document_id, pdf_page=pdf_page)
    persisted_candidates = _load_candidate_views(connection, document_id=document_id, pdf_page=pdf_page)
    corrections_count = sum(1 for item in persisted_candidates if item["correction_id"] is not None)
    proposed_candidates = []
    candidate_source = "persisted"
    if ocr_lines and not persisted_candidates:
        proposed_candidates = _generate_candidate_views(
            ocr_lines,
            document_id=document_id,
            chapter=chapter,
            pdf_page=pdf_page,
        )
        candidate_source = "generated_in_memory"
    candidates = persisted_candidates or proposed_candidates
    baseline_chunks = (
        _load_snapshot_baseline_chunks(connection, document_id=document_id, pdf_page=pdf_page, canonical_chunks=canonical_chunks)
        if len(already_promoted_ids) == len(canonical_chunks) and canonical_chunks
        else canonical_chunks
    )
    mapping = build_old_candidate_mapping(baseline_chunks, candidates) if candidates else _empty_mapping(canonical_chunks)
    proposed_writes = (
        build_proposed_db_writes(
            schema=schema,
            recommendation=recommend_strategy(schema),
            mapping=mapping,
            old_chunks=baseline_chunks,
            candidates=candidates,
            promote_run_id=f"dry_run_chapter{chapter['chapter_id']}_page{pdf_page}",
        )
        if candidates and mapping["mappings"]
        else {"planned_snapshot_rows": 0, "operations": [], "performed": False}
    )
    vector_plan = build_vector_sync_plan(document_id=document_id, mapping=mapping)
    readiness, blockers = _classify_readiness(
        canonical_chunks=canonical_chunks,
        already_promoted_ids=already_promoted_ids,
        ocr_lines=ocr_lines,
        persisted_candidates=persisted_candidates,
        corrections_count=corrections_count,
        mapping=mapping,
    )
    old_metrics = _metric_totals(baseline_chunks, "old_issues")
    candidate_metrics = _metric_totals(candidates, "quality_metrics")
    return {
        "pdf_page": pdf_page,
        "old_chunks_count": len(canonical_chunks),
        "old_chunk_ids": [item["chunk_id"] for item in canonical_chunks],
        "has_ocr_layout_lines": bool(ocr_lines),
        "ocr_lines_count": len(ocr_lines),
        "has_ocr_first_candidates": bool(persisted_candidates),
        "candidates_count": len(persisted_candidates),
        "has_corrections": corrections_count > 0,
        "corrections_count": corrections_count,
        "generated_candidates_count": len(proposed_candidates),
        "candidate_source": candidate_source if candidates else None,
        "already_promoted_chunks_count": len(already_promoted_ids),
        "already_promoted_chunk_ids": already_promoted_ids,
        "vector_status": [],
        "readiness_status": readiness,
        "blockers": blockers,
        "mapping_dry_run": {
            **mapping,
            "promote_planned": readiness not in {READINESS_ALREADY_PROMOTED, READINESS_NEEDS_OCR},
            "proposed_snapshot_rows": 0 if readiness == READINESS_ALREADY_PROMOTED else proposed_writes["planned_snapshot_rows"],
            "proposed_vector_source_ids": vector_plan["stale_after_apply_source_ids"],
        },
        "proposed_snapshot_rows": 0 if readiness == READINESS_ALREADY_PROMOTED else proposed_writes["planned_snapshot_rows"],
        "proposed_vector_source_ids": vector_plan["stale_after_apply_source_ids"],
        "quality_compare": {
            "old": old_metrics,
            "candidate": candidate_metrics if candidates else None,
            "source_line_coverage": _source_line_coverage(candidates, ocr_lines),
            "expected_improvement_score": _improvement_score(old_metrics, candidate_metrics) if candidates else None,
            "baseline": "promote_snapshot" if readiness == READINESS_ALREADY_PROMOTED else "current_knowledge_chunks",
        },
        "sensitive_page_flags": _sensitive_page_flags(baseline_chunks, candidates),
    }


def build_batch_plan(*, document_id: int, chapter: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    already = [page for page in pages if page["readiness_status"] == READINESS_ALREADY_PROMOTED]
    clean = [
        page for page in pages
        if page["readiness_status"] == READINESS_READY
        and all(item["confidence"] == "high" for item in page["mapping_dry_run"]["mappings"])
    ][:3]
    correction = [
        page for page in pages
        if page["readiness_status"] in {READINESS_NEEDS_CORRECTION, READINESS_NEEDS_CANDIDATE}
    ]
    held = [
        page for page in pages
        if page["readiness_status"] in {READINESS_NEEDS_OCR, READINESS_NEEDS_REVIEW}
        or (page["readiness_status"] == READINESS_READY and page not in clean)
    ]
    return [
        _batch_item("Batch 0", "already_promoted", already, blocker=None, command=None),
        _batch_item(
            "Batch 1",
            "safest_next_pages",
            clean,
            blocker=None if clean else "no unpromoted page currently has persisted, corrected, high-confidence candidates",
            command=_promote_dry_run_command(document_id, clean[0]["pdf_page"]) if clean else None,
        ),
        _batch_item(
            "Batch 2",
            "prepare_candidates_or_corrections",
            correction,
            blocker="candidate persistence/correction review required before promote" if correction else "no candidate-ready page awaiting correction",
            command=_prepare_command(document_id, correction[0]) if correction else None,
        ),
        _batch_item(
            "Hold",
            "needs_ocr_or_manual_review",
            held,
            blocker="OCR layout rows or mapping review required before any page-level promote" if held else None,
            command=_prepare_command(document_id, held[0]) if held else None,
        ),
    ]


def _load_document(connection: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, title, document_type, content_layer, read_status, object_import_mode FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"document not found: {document_id}")
    return dict(row)


def _load_ocr_lines(connection: sqlite3.Connection, *, document_id: int, pdf_page: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, pdf_page, line_index, text, bbox_json, confidence
        FROM pdf_page_layout_lines
        WHERE document_id = ? AND pdf_page = ? AND source_backend = 'surya_ocr'
        ORDER BY line_index, id
        """,
        (document_id, pdf_page),
    ).fetchall()
    output = []
    for row in rows:
        try:
            bbox = json.loads(row["bbox_json"] or "{}")
        except json.JSONDecodeError:
            bbox = {}
        output.append(
            {
                "id": int(row["id"]),
                "pdf_page": int(row["pdf_page"]),
                "line_index": int(row["line_index"]),
                "text": str(row["text"] or ""),
                "bbox": bbox,
                "confidence": row["confidence"],
            }
        )
    return output


def _load_candidate_views(connection: sqlite3.Connection, *, document_id: int, pdf_page: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT c.id, c.document_id, c.pdf_page_start, c.pdf_page_end, c.chapter_id, c.candidate_index,
               c.chunk_text, c.source_line_ids_json, c.source_line_keys_json, c.review_status,
               r.id AS correction_id, r.corrected_text
        FROM ocr_first_chunk_candidates c
        LEFT JOIN ocr_first_candidate_corrections r ON r.id = (
            SELECT MAX(rc.id) FROM ocr_first_candidate_corrections rc
            WHERE rc.candidate_id = c.id AND rc.review_status IN ('pending', 'approved')
        )
        WHERE c.document_id = ? AND c.pdf_page_start <= ? AND c.pdf_page_end >= ?
          AND c.review_status IN ('pending', 'promoted')
        ORDER BY c.candidate_index, c.id
        """,
        (document_id, pdf_page, pdf_page),
    ).fetchall()
    line_ids = {line["id"] for line in _load_ocr_lines(connection, document_id=document_id, pdf_page=pdf_page)}
    candidates = []
    for row in rows:
        source_ids = [int(value) for value in json.loads(row["source_line_ids_json"] or "[]")]
        selected = str(row["corrected_text"] or row["chunk_text"] or "")
        candidates.append(
            {
                "candidate_id": int(row["id"]),
                "document_id": int(row["document_id"]),
                "candidate_index": int(row["candidate_index"]),
                "pdf_page_start": int(row["pdf_page_start"]),
                "pdf_page_end": int(row["pdf_page_end"]),
                "chapter_id": row["chapter_id"],
                "corrected_text": selected,
                "corrected_text_summary": _preview(selected),
                "source_line_ids": source_ids,
                "source_line_keys": json.loads(row["source_line_keys_json"] or "[]"),
                "review_status": str(row["review_status"]),
                "correction_id": int(row["correction_id"]) if row["correction_id"] is not None else None,
                "text_source": "correction" if row["correction_id"] is not None else "raw_candidate",
                "quality_metrics": quality_metrics(selected),
                "location_eligible": bool(source_ids) and not (set(source_ids) - line_ids),
            }
        )
    return candidates


def _generate_candidate_views(
    lines: list[dict[str, Any]],
    *,
    document_id: int,
    chapter: dict[str, Any],
    pdf_page: int,
) -> list[dict[str, Any]]:
    max_x = max((float(line["bbox"].get("x1", 0)) for line in lines), default=0.0)
    max_y = max((float(line["bbox"].get("y1", 0)) for line in lines), default=0.0)
    chunks = chunk_ocr_layout_lines(
        lines,
        page_width=max_x or None,
        page_height=max_y or None,
        heading_path=chapter["title"],
    )
    return [
        {
            "candidate_id": -(pdf_page * 1000 + index + 1),
            "document_id": document_id,
            "candidate_index": index,
            "pdf_page_start": pdf_page,
            "pdf_page_end": pdf_page,
            "chapter_id": chapter["chapter_id"],
            "corrected_text": chunk.chunk_text,
            "corrected_text_summary": _preview(chunk.chunk_text),
            "source_line_ids": list(chunk.source_line_ids),
            "source_line_keys": list(chunk.source_line_keys),
            "review_status": "proposed_in_memory",
            "correction_id": None,
            "text_source": "generated_raw_candidate",
            "quality_metrics": quality_metrics(chunk.chunk_text),
            "location_eligible": bool(chunk.source_line_ids),
        }
        for index, chunk in enumerate(chunks)
    ]


def _promoted_chunk_ids(connection: sqlite3.Connection, *, document_id: int, pdf_page: int) -> list[int]:
    return [
        int(row["chunk_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT chunk_id FROM chunk_layout_line_links
            WHERE document_id = ? AND pdf_page = ? AND match_method = 'ocr_layout_first_source_lines'
            ORDER BY chunk_id
            """,
            (document_id, pdf_page),
        ).fetchall()
    ]


def _load_snapshot_baseline_chunks(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    pdf_page: int,
    canonical_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    run = connection.execute(
        """
        SELECT promote_run_id FROM ocr_first_promote_snapshots
        WHERE document_id = ? AND page_start = ? AND page_end = ?
        GROUP BY promote_run_id ORDER BY MAX(created_at) DESC LIMIT 1
        """,
        (document_id, pdf_page, pdf_page),
    ).fetchone()
    if run is None:
        return canonical_chunks
    canonical_by_id = {item["chunk_id"]: item for item in canonical_chunks}
    rows = connection.execute(
        """
        SELECT chunk_id, old_chunk_text, old_content_hash, old_char_count, old_token_count,
               old_updated_at, old_line_links_json
        FROM ocr_first_promote_snapshots WHERE promote_run_id = ? ORDER BY chunk_id
        """,
        (run["promote_run_id"],),
    ).fetchall()
    if len(rows) != len(canonical_chunks):
        return canonical_chunks
    return [
        {
            **canonical_by_id[int(row["chunk_id"])],
            "old_chunk_text": str(row["old_chunk_text"]),
            "old_chunk_text_summary": _preview(str(row["old_chunk_text"])),
            "old_content_hash": row["old_content_hash"],
            "old_char_count": row["old_char_count"],
            "old_token_count": row["old_token_count"],
            "old_updated_at": row["old_updated_at"],
            "old_issues": quality_metrics(str(row["old_chunk_text"])),
            "existing_layout_line_links": json.loads(row["old_line_links_json"] or "{}"),
        }
        for row in rows
    ]


def _classify_readiness(
    *,
    canonical_chunks: list[dict[str, Any]],
    already_promoted_ids: list[int],
    ocr_lines: list[dict[str, Any]],
    persisted_candidates: list[dict[str, Any]],
    corrections_count: int,
    mapping: dict[str, Any],
) -> tuple[str, list[str]]:
    if canonical_chunks and len(already_promoted_ids) == len(canonical_chunks):
        return READINESS_ALREADY_PROMOTED, []
    if not ocr_lines:
        return READINESS_NEEDS_OCR, ["no persisted Surya OCR layout lines; OCR is not run by this planner"]
    if not persisted_candidates:
        return READINESS_NEEDS_CANDIDATE, ["proposed candidates are in-memory only; candidate persistence and correction review are required"]
    if corrections_count < len(persisted_candidates):
        return READINESS_NEEDS_CORRECTION, ["not every persisted candidate has a correction row"]
    if (
        mapping["unmapped_old_chunks"]
        or mapping["unmapped_candidates"]
        or any(item["confidence"] in {"medium", "low"} for item in mapping["mappings"])
    ):
        return READINESS_NEEDS_REVIEW, list(mapping["mapping_risks"]) or ["mapping is not fully high confidence"]
    return READINESS_READY, []


def _read_vector_status(
    *,
    db_path: Path,
    source_ids: list[str],
    loader: Callable[[list[str]], dict[str, Any]] | None,
) -> dict[str, Any]:
    if not source_ids:
        return {"performed": False, "items": [], "reason": "no already-promoted pages"}
    if loader is not None:
        return loader(source_ids)
    if db_path.resolve() != Path(DEFAULT_DB_PATH).resolve():
        return {"performed": False, "items": [], "reason": "non-default test database; vector status not queried"}
    result = vector_store_service.sync_affected_passage_embeddings(source_ids, dry_run=True, apply=False)
    return {
        "performed": True,
        "scope": result["scope"],
        "full_rebuild_allowed": result["full_rebuild_allowed"],
        "delete_orphans_allowed": result["delete_orphans_allowed"],
        "lancedb_writes_performed": result["lancedb_writes_performed"],
        "items": result["items"],
    }


def _empty_mapping(old_chunks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mapping_strategy": "not_automatically_mappable",
        "old_chunk_count": len(old_chunks),
        "candidate_count": 0,
        "location_eligible_candidate_count": 0,
        "mappings": [],
        "unmapped_old_chunks": [item["chunk_id"] for item in old_chunks],
        "unmapped_candidates": [],
        "mapping_risks": ["no OCR-first candidates available for mapping"],
    }


def _metric_totals(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    metrics = (
        "html_tag_count",
        "math_noise_count",
        "repeated_token_count",
        "page_number_noise_count",
        "broken_sentence_count",
        "known_ocr_error_count",
        "suspicious_symbol_count",
    )
    return {metric: sum(int(item.get(key, {}).get(metric, 0)) for item in items) for metric in metrics}


def _improvement_score(old: dict[str, int], candidate: dict[str, int]) -> int:
    return sum(old.values()) - sum(candidate.values())


def _source_line_coverage(candidates: list[dict[str, Any]], lines: list[dict[str, Any]]) -> float | None:
    if not lines or not candidates:
        return None
    covered = {line_id for candidate in candidates for line_id in candidate.get("source_line_ids", [])}
    return round(len(covered) / len(lines), 4)


def _sensitive_page_flags(old_chunks: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[str]:
    text = " ".join(item.get("old_chunk_text", "") for item in old_chunks) + " " + " ".join(
        item.get("corrected_text", "") for item in candidates
    )
    flags = []
    if re.search(r"[δρ∞]|\\(?:rho|delta|infty)|\bw\(", text):
        flags.append("formula_dense")
    if "图 " in text or "图2" in text or "图 2" in text:
        flags.append("figure_caption")
    if any(item.get("pdf_page_start") != item.get("pdf_page_end") for item in old_chunks):
        flags.append("cross_page_chunk")
    return flags


def _readiness_summary(pages: list[dict[str, Any]]) -> dict[str, int]:
    statuses = (
        READINESS_ALREADY_PROMOTED,
        READINESS_READY,
        READINESS_NEEDS_OCR,
        READINESS_NEEDS_CANDIDATE,
        READINESS_NEEDS_CORRECTION,
        READINESS_NEEDS_REVIEW,
    )
    return {status: sum(1 for page in pages if page["readiness_status"] == status) for status in statuses}


def _chapter_quality_summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [page for page in pages if page["quality_compare"]["candidate"] is not None]
    return {
        "pages_with_candidate_comparison": [page["pdf_page"] for page in comparable],
        "pages_without_candidate_comparison": [
            page["pdf_page"] for page in pages if page["quality_compare"]["candidate"] is None
        ],
        "old": _sum_page_metrics(comparable, "old"),
        "candidate": _sum_page_metrics(comparable, "candidate"),
        "expected_improvement_score": sum(
            int(page["quality_compare"]["expected_improvement_score"] or 0) for page in comparable
        ),
    }


def _sum_page_metrics(pages: list[dict[str, Any]], side: str) -> dict[str, int]:
    if not pages:
        return {}
    return {
        metric: sum(int(page["quality_compare"][side].get(metric, 0)) for page in pages)
        for metric in pages[0]["quality_compare"][side]
    }


def _chapter_blockers(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"pdf_page": page["pdf_page"], "readiness_status": page["readiness_status"], "blockers": page["blockers"]}
        for page in pages
        if page["blockers"]
    ]


def _batch_item(
    label: str,
    purpose: str,
    pages: list[dict[str, Any]],
    *,
    blocker: str | None,
    command: str | None,
) -> dict[str, Any]:
    return {
        "batch": label,
        "purpose": purpose,
        "pages": [page["pdf_page"] for page in pages],
        "chunk_ids": [chunk_id for page in pages for chunk_id in page["old_chunk_ids"]],
        "blocker": blocker,
        "next_command": command,
        "apply_allowed_in_this_run": False,
    }


def _prepare_command(document_id: int, page: dict[str, Any]) -> str:
    pdf_page = page["pdf_page"]
    if page["readiness_status"] == READINESS_NEEDS_OCR:
        return (
            f"scripts/import_book_ocr_layout_first.py --document-id {document_id} "
            f"--page-start {pdf_page} --page-end {pdf_page} --write-candidates --json  # future OCR estimate dry-run only"
        )
    return (
        f"scripts/import_book_ocr_layout_first.py --document-id {document_id} "
        f"--page-start {pdf_page} --page-end {pdf_page} --write-candidates --json  # candidate dry-run only"
    )


def _promote_dry_run_command(document_id: int, pdf_page: int) -> str:
    return (
        f"scripts/promote_ocr_first_candidates.py --document-id {document_id} "
        f"--page-start {pdf_page} --page-end {pdf_page} --dry-run --json"
    )


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _preview(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _text_report(report: dict[str, Any]) -> str:
    chapter = report["chapter"]
    summary = report["readiness_summary"]
    return (
        f"DRY_RUN chapter={chapter['title']} pages={chapter['pdf_page_start']}-{chapter['pdf_page_end']} "
        f"chunks={chapter['chunk_count']} already_promoted={summary[READINESS_ALREADY_PROMOTED]} "
        f"needs_ocr_layout={summary[READINESS_NEEDS_OCR]}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
