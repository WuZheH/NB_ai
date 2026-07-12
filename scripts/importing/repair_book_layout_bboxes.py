from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import gc
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services.ocr_layout_service import DEFAULT_MODEL_CACHE_ROOT, run_surya_ocr_page
from app.services.chunk_splitter import TextChunk
from app.services.pdf_layout_service import align_chunks_to_layout_blocks, align_chunks_to_layout_lines, create_pdf_layout_schema, layout_schema_status
from app.services.pdf_layout_service import insert_ocr_page_layout, persist_layout_blocks_and_links
from app.services.pdf_layout_service import relink_chunks_to_ocr_lines
from app.services.pdf_parser_backends import MARKER_SURYA_PAGE_BLOCKS_BACKEND, parse_pdf_to_markdown


@dataclass(frozen=True)
class RepairPlan:
    db_path: str
    document_id: int
    title: str
    pdf_path: str | None
    page_start: int | None
    page_end: int | None
    max_pages_per_batch: int
    device: str
    dry_run: bool
    chunks_in_range: int
    existing_layout_blocks: int
    existing_layout_lines: int
    existing_chunk_layout_links: int
    existing_chunk_layout_line_links: int
    existing_text_layer_cache: int
    pages_in_range: int
    estimated_batches: int
    planned_parser_runs: int
    planned_ocr_runs: int
    planned_max_pages_per_batch: int
    parser_executed: bool
    ocr_parser_executed: bool
    ocr_enabled: bool
    estimate_ocr: bool
    ocr_backend: str | None
    apply_required_for_writes: bool
    no_database_writes_performed: bool
    planned_writes: dict[str, int | str]
    warnings: list[str]
    next_step: str | None = None


def build_repair_plan(
    *,
    db_path: str | Path,
    document_id: int,
    page_start: int | None = None,
    page_end: int | None = None,
    max_pages_per_batch: int = 1,
    device: str = "auto",
    dry_run: bool = True,
    estimate_parser: bool = False,
    ocr: bool = False,
    estimate_ocr: bool = False,
    ocr_backend: str = "surya",
    backend: str = MARKER_SURYA_PAGE_BLOCKS_BACKEND,
) -> RepairPlan:
    db = Path(db_path)
    uri = f"file:{db.as_posix()}?mode=ro" if dry_run else str(db)
    warnings: list[str] = []
    with sqlite3.connect(uri, uri=dry_run) as connection:
        document = connection.execute(
            "SELECT id, title, pdf_path, source_path FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if not document:
            raise ValueError(f"document_id={document_id} not found")

        bounds = connection.execute(
            """
            SELECT MIN(pdf_page_start), MAX(COALESCE(pdf_page_end, pdf_page_start))
            FROM knowledge_chunks
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
        inferred_start = int(bounds[0]) if bounds and bounds[0] is not None else None
        inferred_end = int(bounds[1]) if bounds and bounds[1] is not None else inferred_start
        resolved_start = page_start or inferred_start
        resolved_end = page_end or inferred_end
        if resolved_start is not None and resolved_end is not None and resolved_start > resolved_end:
            raise ValueError("page_start cannot be greater than page_end")

        chunk_count = _count_chunks(connection, document_id, resolved_start, resolved_end)
        schema_status = layout_schema_status(connection)
        block_schema_present = bool(schema_status["block_schema_present"])
        line_schema_present = bool(schema_status["line_schema_present"])
        text_cache_schema_present = bool(schema_status["text_cache_schema_present"])
        if block_schema_present:
            existing_blocks = _count_table(connection, "pdf_page_layout_blocks", document_id, resolved_start, resolved_end)
            existing_links = _count_table(connection, "chunk_layout_links", document_id, resolved_start, resolved_end)
        else:
            existing_blocks = 0
            existing_links = 0
        if line_schema_present:
            existing_lines = _count_table(connection, "pdf_page_layout_lines", document_id, resolved_start, resolved_end)
            existing_line_links = _count_table(connection, "chunk_layout_line_links", document_id, resolved_start, resolved_end)
        else:
            existing_lines = 0
            existing_line_links = 0
        if text_cache_schema_present:
            existing_text_cache = _count_table(connection, "pdf_page_text_layer_cache", document_id, resolved_start, resolved_end)
        else:
            existing_text_cache = 0

        if not schema_status["layout_schema_present"]:
            warnings.append("layout_schema_missing")
        if not block_schema_present:
            warnings.append("block_schema_missing")
        if not line_schema_present:
            warnings.append("line_schema_missing")
        if not text_cache_schema_present:
            warnings.append("text_cache_schema_missing")

    total_pages = (
        max(0, int(resolved_end) - int(resolved_start) + 1)
        if resolved_start is not None and resolved_end is not None
        else 0
    )
    batch_size = max(1, int(max_pages_per_batch or 5))
    estimated_batches = (total_pages + batch_size - 1) // batch_size if total_pages else 0
    parser_executed = False
    ocr_parser_executed = False
    planned_writes: dict[str, int | str] = {
        "pdf_page_layout_blocks": "unknown_until_parse",
        "pdf_page_layout_lines": "unknown_until_parse",
        "pdf_page_layout_spans": "unknown_until_parse",
        "chunk_layout_links": "unknown_until_parse",
        "chunk_layout_line_links": "unknown_until_parse",
        "pdf_page_text_layer_cache": "unknown_until_parse",
    }
    if estimate_parser:
        estimate = _estimate_parser_outputs(
            db_path=db,
            document_id=document_id,
            pdf_path=str(document[2] or document[3] or ""),
            page_start=resolved_start,
            page_end=resolved_end,
            max_pages_per_batch=batch_size,
            backend=backend,
        )
        parser_executed = True
        planned_writes.update(
            {
                "pdf_page_layout_blocks": estimate["estimated_layout_blocks_count"],
                "pdf_page_layout_lines": estimate["estimated_layout_lines_count"],
                "pdf_page_layout_spans": estimate["estimated_layout_spans_count"],
                "chunk_layout_links": estimate["estimated_chunk_layout_links_count"],
                "chunk_layout_line_links": estimate["estimated_chunk_layout_line_links_count"],
                "pdf_page_text_layer_cache": estimate["estimated_text_layer_cache_count"],
            }
        )
    if estimate_ocr and "line_schema_missing" not in warnings:
        ocr_parser_executed = True
    next_step = None
    if "layout_schema_missing" in warnings:
        next_step = _schema_prepare_command(db)
    ocr_schema_ready = "line_schema_missing" not in warnings
    return RepairPlan(
        db_path=str(db),
        document_id=int(document[0]),
        title=str(document[1] or ""),
        pdf_path=str(document[2] or document[3] or "") or None,
        page_start=resolved_start,
        page_end=resolved_end,
        max_pages_per_batch=batch_size,
        device=device,
        dry_run=dry_run,
        chunks_in_range=chunk_count,
        existing_layout_blocks=existing_blocks,
        existing_layout_lines=existing_lines,
        existing_chunk_layout_links=existing_links,
        existing_chunk_layout_line_links=existing_line_links,
        existing_text_layer_cache=existing_text_cache,
        pages_in_range=total_pages,
        estimated_batches=estimated_batches,
        planned_parser_runs=estimated_batches,
        planned_ocr_runs=estimated_batches if (ocr or estimate_ocr) and ocr_schema_ready else 0,
        planned_max_pages_per_batch=batch_size,
        parser_executed=parser_executed,
        ocr_parser_executed=ocr_parser_executed,
        ocr_enabled=bool(ocr or estimate_ocr),
        estimate_ocr=bool(estimate_ocr),
        ocr_backend=ocr_backend if (ocr or estimate_ocr) else None,
        apply_required_for_writes=True,
        no_database_writes_performed=dry_run,
        planned_writes={
            **planned_writes,
            "planned_layout_blocks": planned_writes["pdf_page_layout_blocks"],
            "planned_layout_lines": planned_writes["pdf_page_layout_lines"],
            "planned_chunk_links": planned_writes["chunk_layout_links"],
            "planned_chunk_line_links": planned_writes["chunk_layout_line_links"],
        },
        warnings=warnings,
        next_step=next_step,
    )


def apply_repair_schema_only(*, db_path: str | Path) -> dict[str, Any]:
    db = Path(db_path)
    backup_path = _backup_sqlite(db)
    with sqlite3.connect(db) as connection:
        create_pdf_layout_schema(connection)
        connection.commit()
    return {
        "status": "schema_ready",
        "backup_path": str(backup_path),
        "message": "layout bbox extraction is batch/parser work; this apply step only prepares schema safely",
    }


def apply_layout_bbox_repair(
    *,
    db_path: str | Path,
    document_id: int,
    page_start: int | None = None,
    page_end: int | None = None,
    max_pages_per_batch: int = 1,
    device: str = "auto",
    backend: str = MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    resume: bool = False,
) -> dict[str, Any]:
    plan = build_repair_plan(
        db_path=db_path,
        document_id=document_id,
        page_start=page_start,
        page_end=page_end,
        max_pages_per_batch=max_pages_per_batch,
        device=device,
        dry_run=False,
    )
    if plan.page_start is None or plan.page_end is None:
        raise ValueError("page range could not be inferred")
    if not plan.pdf_path or not Path(plan.pdf_path).exists():
        raise FileNotFoundError(plan.pdf_path or "missing pdf_path")

    backup_path = _backup_sqlite(Path(db_path))
    inserted_blocks = 0
    inserted_links = 0
    skipped_batches = 0
    batch_reports: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        create_pdf_layout_schema(connection)
        for batch_start in range(plan.page_start, plan.page_end + 1, plan.max_pages_per_batch):
            batch_end = min(plan.page_end, batch_start + plan.max_pages_per_batch - 1)
            if resume and _count_table(connection, "pdf_page_layout_blocks", document_id, batch_start, batch_end) > 0:
                skipped_batches += 1
                continue
            chunk_ids, chunks = _load_chunks_for_range(connection, document_id, batch_start, batch_end)
            parse_result = parse_pdf_to_markdown(
                plan.pdf_path,
                backend=backend,
                page_start=batch_start,
                page_end=batch_end,
            )
            result = persist_layout_blocks_and_links(
                connection,
                document_id=document_id,
                chunks=chunks,
                chunk_ids=chunk_ids,
                layout_blocks=parse_result.layout_blocks,
                layout_lines=parse_result.layout_lines,
                layout_spans=parse_result.layout_spans,
            )
            connection.commit()
            inserted_blocks += int(result["inserted_layout_blocks"])
            inserted_links += int(result["inserted_chunk_layout_links"])
            batch_reports.append(
                {
                    "page_start": batch_start,
                    "page_end": batch_end,
                    "chunks": len(chunks),
                    **result,
                }
            )
            del parse_result
            gc.collect()
            _maybe_empty_cuda_cache(device)
    return {
        "status": "applied",
        "backup_path": str(backup_path),
        "inserted_layout_blocks": inserted_blocks,
        "inserted_chunk_layout_links": inserted_links,
        "skipped_batches": skipped_batches,
        "batches": batch_reports,
    }


def estimate_ocr_layout_repair(
    *,
    db_path: str | Path,
    document_id: int,
    page_start: int,
    page_end: int,
    device: str = "cpu",
    model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT,
) -> dict[str, Any]:
    db = Path(db_path)
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        document = connection.execute(
            "SELECT id, title, pdf_path, source_path FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if not document:
            raise ValueError(f"document_id={document_id} not found")
        pdf_path = str(document[2] or document[3] or "")
    if not pdf_path:
        raise ValueError("document pdf_path is required for --estimate-ocr")
    if not Path(pdf_path).exists():
        return {
            "dry_run": True,
            "no_database_writes_performed": True,
            "pages_estimated": 0,
            "estimated_lines": 0,
            "estimated_words": 0,
            "line_bbox_available": False,
            "word_bbox_available": False,
            "pages": [],
            "errors": [{"pdf_page": page_start, "error": f"pdf_path not found: {pdf_path}"}],
        }
    pages: list[dict[str, Any]] = []
    total_lines = 0
    total_spans = 0
    errors: list[dict[str, Any]] = []
    for page in range(page_start, page_end + 1):
        layout = run_surya_ocr_page(
            pdf_path,
            page,
            device=device,
            model_cache_root=model_cache_root,
            return_words=True,
            allow_download=False,
        )
        if layout.error:
            errors.append({"pdf_page": page, "error": layout.error, "traceback_tail": layout.traceback_tail})
        total_lines += len(layout.lines)
        total_spans += len(layout.spans)
        pages.append(
            {
                "pdf_page": page,
                "line_count": len(layout.lines),
                "word_count": len(layout.spans),
                "page_text_layer_length": layout.page_text_layer_length,
                "gpu_used": layout.gpu_used,
                "error": layout.error,
                "first_5_lines": [
                    {
                        "text": line.text,
                        "bbox": line.bbox,
                        "confidence": line.confidence,
                    }
                    for line in layout.lines[:5]
                ],
            }
        )
    return {
        "dry_run": True,
        "no_database_writes_performed": True,
        "pages_estimated": len(pages),
        "estimated_lines": total_lines,
        "estimated_words": total_spans,
        "line_bbox_available": total_lines > 0,
        "word_bbox_available": total_spans > 0,
        "pages": pages,
        "errors": errors,
    }


def apply_ocr_layout_repair(
    *,
    db_path: str | Path,
    document_id: int,
    page_start: int | None,
    page_end: int | None,
    max_pages_per_batch: int = 1,
    device: str = "cpu",
    model_cache_root: str | Path = DEFAULT_MODEL_CACHE_ROOT,
    resume: bool = False,
) -> dict[str, Any]:
    if page_start is None or page_end is None:
        raise ValueError("--ocr --apply requires explicit --page-start and --page-end")
    if page_end < page_start:
        raise ValueError("page_end must be >= page_start")
    plan = build_repair_plan(
        db_path=db_path,
        document_id=document_id,
        page_start=page_start,
        page_end=page_end,
        max_pages_per_batch=max_pages_per_batch,
        device=device,
        dry_run=False,
        ocr=True,
        ocr_backend="surya",
    )
    if not plan.pdf_path or not Path(plan.pdf_path).exists():
        raise FileNotFoundError(plan.pdf_path or "missing pdf_path")
    backup_path = _backup_sqlite(Path(db_path))
    totals = {
        "pages_processed": 0,
        "lines_written": 0,
        "spans_written": 0,
        "text_cache_written": 0,
        "chunk_line_links_written": 0,
        "chunks_linked": 0,
        "chunks_unlinked": 0,
    }
    batches: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        create_pdf_layout_schema(connection)
        for page in range(page_start, page_end + 1):
            if resume and _count_table(connection, "pdf_page_layout_lines", document_id, page, page) > 0:
                continue
            chunk_ids, chunks = _load_chunks_for_range(connection, document_id, page, page)
            layout = run_surya_ocr_page(
                plan.pdf_path,
                page,
                device=device,
                model_cache_root=model_cache_root,
                return_words=True,
                allow_download=False,
            )
            if layout.error:
                connection.rollback()
                raise RuntimeError(f"OCR failed for page {page}: {layout.error}")
            result = insert_ocr_page_layout(
                connection,
                document_id=document_id,
                ocr_page_layout=layout,
                chunks=chunks,
                chunk_ids=chunk_ids,
            )
            connection.commit()
            totals["pages_processed"] += 1
            for key in (
                "lines_written",
                "spans_written",
                "text_cache_written",
                "chunk_line_links_written",
                "chunks_linked",
                "chunks_unlinked",
            ):
                totals[key] += int(result.get(key, 0))
            batches.append({"pdf_page": page, "chunks": len(chunks), **result})
            gc.collect()
            _maybe_empty_cuda_cache(device)
    return {"status": "applied", "backup_path": str(backup_path), **totals, "batches": batches}


def _count_chunks(connection: sqlite3.Connection, document_id: int, page_start: int | None, page_end: int | None) -> int:
    if page_start is None or page_end is None:
        row = connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM knowledge_chunks
            WHERE document_id = ?
              AND COALESCE(pdf_page_start, pdf_page_end, 0) <= ?
              AND COALESCE(pdf_page_end, pdf_page_start, 0) >= ?
            """,
            (document_id, page_end, page_start),
        ).fetchone()
    return int(row[0] or 0)


def _load_chunks_for_range(
    connection: sqlite3.Connection,
    document_id: int,
    page_start: int,
    page_end: int,
) -> tuple[list[int], list[TextChunk]]:
    rows = connection.execute(
        """
        SELECT
            id, chunk_index, heading_path, chunk_text, char_count, token_count,
            overlap_before, overlap_after, pdf_page_start, pdf_page_end, pdf_path
        FROM knowledge_chunks
        WHERE document_id = ?
          AND COALESCE(pdf_page_start, pdf_page_end, 0) <= ?
          AND COALESCE(pdf_page_end, pdf_page_start, 0) >= ?
        ORDER BY chunk_index, id
        """,
        (document_id, page_end, page_start),
    ).fetchall()
    chunk_ids: list[int] = []
    chunks: list[TextChunk] = []
    for row in rows:
        chunk_ids.append(int(row[0]))
        chunks.append(
            TextChunk(
                node_order_index=int(row[1] or 0),
                chunk_index=int(row[1] or 0),
                heading_path=str(row[2] or ""),
                chunk_text=str(row[3] or ""),
                char_count=int(row[4] or len(str(row[3] or ""))),
                token_count=row[5],
                overlap_before=row[6],
                overlap_after=row[7],
                pdf_page_start=row[8],
                pdf_page_end=row[9],
                pdf_path=row[10],
            )
        )
    return chunk_ids, chunks


def _estimate_parser_outputs(
    *,
    db_path: Path,
    document_id: int,
    pdf_path: str,
    page_start: int | None,
    page_end: int | None,
    max_pages_per_batch: int,
    backend: str,
) -> dict[str, int]:
    if page_start is None or page_end is None:
        raise ValueError("page range is required for --estimate-parser")
    if page_end < page_start:
        raise ValueError("page_end must be >= page_start")
    if page_end - page_start + 1 > max_pages_per_batch * 20:
        raise ValueError("--estimate-parser page range is too large for a dry-run estimate")
    if not pdf_path:
        raise ValueError("document pdf_path is required for --estimate-parser")
    estimated_blocks = 0
    estimated_lines = 0
    estimated_spans = 0
    estimated_links = 0
    estimated_line_links = 0
    estimated_text_cache = 0
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        for batch_start in range(page_start, page_end + 1, max_pages_per_batch):
            batch_end = min(page_end, batch_start + max_pages_per_batch - 1)
            chunk_ids, chunks = _load_chunks_for_range(connection, document_id, batch_start, batch_end)
            parse_result = parse_pdf_to_markdown(
                pdf_path,
                backend=backend,
                page_start=batch_start,
                page_end=batch_end,
            )
            persisted_blocks = [
                {
                    "id": index + 1,
                    "document_id": document_id,
                    "pdf_page": block.pdf_page,
                    "block_index": block.block_index,
                    "text": block.text,
                    "normalized_text": block.normalized_text,
                    "bbox": block.bbox,
                    "page_width": block.page_width,
                    "page_height": block.page_height,
                }
                for index, block in enumerate(parse_result.layout_blocks)
            ]
            persisted_lines = [
                {
                    "id": index + 1,
                    "document_id": document_id,
                    "pdf_page": line.pdf_page,
                    "block_index": line.block_index,
                    "line_index": line.line_index,
                    "text": line.text,
                    "normalized_text": line.normalized_text,
                    "bbox": line.bbox,
                }
                for index, line in enumerate(parse_result.layout_lines)
            ]
            links = align_chunks_to_layout_blocks(
                chunks=chunks,
                chunk_ids=chunk_ids,
                document_id=document_id,
                persisted_blocks=persisted_blocks,
            )
            line_links = align_chunks_to_layout_lines(
                chunks=chunks,
                chunk_ids=chunk_ids,
                document_id=document_id,
                persisted_lines=persisted_lines,
            )
            estimated_blocks += len(parse_result.layout_blocks)
            estimated_lines += len(parse_result.layout_lines)
            estimated_spans += len(parse_result.layout_spans)
            estimated_links += len(links)
            estimated_line_links += len(line_links)
            estimated_text_cache += max(0, batch_end - batch_start + 1)
    return {
        "estimated_layout_blocks_count": estimated_blocks,
        "estimated_layout_lines_count": estimated_lines,
        "estimated_layout_spans_count": estimated_spans,
        "estimated_chunk_layout_links_count": estimated_links,
        "estimated_chunk_layout_line_links_count": estimated_line_links,
        "estimated_text_layer_cache_count": estimated_text_cache,
    }


def _count_table(
    connection: sqlite3.Connection,
    table_name: str,
    document_id: int,
    page_start: int | None,
    page_end: int | None,
) -> int:
    if page_start is None or page_end is None:
        row = connection.execute(f"SELECT COUNT(*) FROM {table_name} WHERE document_id = ?", (document_id,)).fetchone()
    else:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE document_id = ? AND pdf_page BETWEEN ? AND ?",
            (document_id, page_start, page_end),
        ).fetchone()
    return int(row[0] or 0)


def _backup_sqlite(db_path: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"research_memory_before_layout_bbox_repair_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _maybe_empty_cuda_cache(device: str) -> None:
    if device == "cpu":
        return
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run or prepare book layout bbox repair.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--page-start", type=int)
    parser.add_argument("--page-end", type=int)
    parser.add_argument("--max-pages-per-batch", type=int, default=1)
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="auto")
    parser.add_argument("--backend", default=MARKER_SURYA_PAGE_BLOCKS_BACKEND)
    parser.add_argument("--estimate-parser", action="store_true", help="Parse only the requested page range to estimate layout blocks; never writes DB.")
    parser.add_argument("--ocr", action="store_true", help="Plan OCR/text-layer repair work. This does not write DB without --apply.")
    parser.add_argument("--estimate-ocr", action="store_true", help="Plan a small OCR estimate; dry-run only and no database writes.")
    parser.add_argument("--ocr-backend", choices=["surya"], default="surya")
    parser.add_argument("--relink-ocr-lines", action="store_true", help="Relink existing OCR lines to chunks. Dry-run by default; never reruns OCR.")
    parser.add_argument("--chunk-id", action="append", type=int, default=[], help="Limit OCR relink to a chunk id. Can be repeated.")
    parser.add_argument("--explain-alignment", action="store_true", help="Include per-line role and score diagnostics for OCR relink.")
    parser.add_argument("--allow-multi-page", action="store_true", help="Allow OCR relink page ranges larger than one page.")
    parser.add_argument("--model-cache-root", default=str(DEFAULT_MODEL_CACHE_ROOT))
    parser.add_argument("--resume", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Plan only. This is the default.")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.relink_ocr_lines:
        if args.page_start is None or args.page_end is None:
            raise ValueError("--relink-ocr-lines requires explicit --page-start and --page-end")
        if args.page_end > args.page_start and not args.allow_multi_page:
            raise ValueError("--relink-ocr-lines is limited to one page unless --allow-multi-page is set")
        payload = {
            "ocr_line_relink_result": relink_chunks_to_ocr_lines(
                db_path=args.db_path,
                document_id=args.document_id,
                page_start=args.page_start,
                page_end=args.page_end,
                chunk_ids=args.chunk_id or None,
                dry_run=not args.apply,
                source_backend=f"{args.ocr_backend}_ocr" if args.ocr_backend == "surya" else args.ocr_backend,
                explain_alignment=args.explain_alignment,
                allow_multi_page=args.allow_multi_page,
            )
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            result = payload["ocr_line_relink_result"]
            print(f"document_id={result['document_id']} pages={result['page_start']}-{result['page_end']}")
            print(f"relink_ocr_lines=True dry_run={result['dry_run']}")
            print(f"source_backend={result['source_backend']}")
            print(f"no_database_writes_performed={result['no_database_writes_performed']}")
            for chunk in result["chunks"]:
                print(f"chunk_id={chunk['chunk_id']}")
                print(f"  current_linked_line_indexes={chunk['current_linked_line_indexes']}")
                print(f"  proposed_linked_line_indexes={chunk['proposed_linked_line_indexes']}")
                print(f"  removed_line_indexes={chunk['removed_line_indexes']}")
                print(f"  added_line_indexes={chunk['added_line_indexes']}")
                print(f"  proposed_line_roles={chunk['proposed_line_roles']}")
                print(f"  current_union_area_ratio={chunk['current_union_area_ratio']}")
                print(f"  proposed_union_area_ratio={chunk['proposed_union_area_ratio']}")
                if args.explain_alignment:
                    print(f"  selected_text={chunk.get('selected_text', [])}")
                    print("  alignment_diagnostics=" + json.dumps(chunk.get("alignment_diagnostics", []), ensure_ascii=False))
            if result.get("backup_path"):
                print(f"backup_path={result['backup_path']}")
        return 0
    if (args.estimate_ocr or (args.apply and args.ocr)) and (args.page_start is None or args.page_end is None):
        raise ValueError("--estimate-ocr and --ocr --apply require explicit --page-start and --page-end")
    plan = build_repair_plan(
        db_path=args.db_path,
        document_id=args.document_id,
        page_start=args.page_start,
        page_end=args.page_end,
        max_pages_per_batch=args.max_pages_per_batch,
        device=args.device,
        dry_run=not args.apply,
        estimate_parser=args.estimate_parser and not args.apply,
        ocr=args.ocr,
        estimate_ocr=args.estimate_ocr and not args.apply,
        ocr_backend=args.ocr_backend,
        backend=args.backend,
    )
    payload: dict[str, Any] = {"plan": asdict(plan), "resume": bool(args.resume)}
    if args.estimate_ocr and not args.apply and "line_schema_missing" not in plan.warnings:
        payload["ocr_estimate_result"] = estimate_ocr_layout_repair(
            db_path=args.db_path,
            document_id=args.document_id,
            page_start=args.page_start,
            page_end=args.page_end,
            device=args.device,
            model_cache_root=args.model_cache_root,
        )
    elif args.estimate_ocr and not args.apply:
        payload["ocr_estimate_result"] = {
            "dry_run": True,
            "no_database_writes_performed": True,
            "skipped": True,
            "reason": "line_schema_missing",
            "next_step": plan.next_step,
        }
    if args.apply:
        if args.ocr:
            payload["apply_result"] = apply_ocr_layout_repair(
                db_path=args.db_path,
                document_id=args.document_id,
                page_start=args.page_start,
                page_end=args.page_end,
                max_pages_per_batch=args.max_pages_per_batch,
                device=args.device,
                model_cache_root=args.model_cache_root,
                resume=args.resume,
            )
        else:
            payload["apply_result"] = apply_layout_bbox_repair(
                db_path=args.db_path,
                document_id=args.document_id,
                page_start=args.page_start,
                page_end=args.page_end,
                max_pages_per_batch=args.max_pages_per_batch,
                device=args.device,
                backend=args.backend,
                resume=args.resume,
            )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"document_id={plan.document_id} title={plan.title}")
        print(f"pages={plan.page_start}-{plan.page_end} chunks_in_range={plan.chunks_in_range}")
        print(f"estimated_batches={plan.estimated_batches} max_pages_per_batch={plan.max_pages_per_batch} device={plan.device}")
        print(f"existing_layout_blocks_count={plan.existing_layout_blocks}")
        print(f"existing_layout_lines_count={plan.existing_layout_lines}")
        print(f"existing_chunk_layout_links_count={plan.existing_chunk_layout_links}")
        print(f"existing_chunk_layout_line_links_count={plan.existing_chunk_layout_line_links}")
        print(f"existing_text_layer_cache_count={plan.existing_text_layer_cache}")
        print(f"pages_in_range={plan.pages_in_range}")
        print(f"planned_parser_runs={plan.planned_parser_runs}")
        print(f"planned_ocr_runs={plan.planned_ocr_runs}")
        print(f"planned_max_pages_per_batch={plan.planned_max_pages_per_batch}")
        print(f"parser_executed={plan.parser_executed}")
        print(f"ocr_parser_executed={plan.ocr_parser_executed}")
        print(f"ocr_enabled={plan.ocr_enabled}")
        print(f"estimate_ocr={plan.estimate_ocr}")
        print(f"ocr_backend={plan.ocr_backend}")
        print(f"apply_required_for_writes={plan.apply_required_for_writes}")
        print(f"no_database_writes_performed={plan.no_database_writes_performed}")
        print(f"dry_run={plan.dry_run} planned_writes={plan.planned_writes}")
        if args.apply:
            print(f"apply_result={payload['apply_result']}")
        if "ocr_estimate_result" in payload:
            estimate = payload["ocr_estimate_result"]
            print(f"ocr_estimated_lines={estimate['estimated_lines']}")
            print(f"ocr_estimated_words={estimate['estimated_words']}")
            print(f"ocr_line_bbox_available={estimate['line_bbox_available']}")
            print(f"ocr_word_bbox_available={estimate['word_bbox_available']}")
            print(f"ocr_sample_pages={estimate['pages']}")
        if plan.warnings:
            print("warnings=" + ", ".join(plan.warnings))
        if plan.next_step:
            print(f"next_step={plan.next_step}")
    return 0


def _schema_prepare_command(db_path: Path) -> str:
    return f"python scripts/prepare_layout_schema.py --db-path {db_path} --apply"


if __name__ == "__main__":
    raise SystemExit(main())
