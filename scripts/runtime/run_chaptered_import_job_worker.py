from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.services.book_import_service import (
    MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    apply_prepared_book_import,
    evaluate_auto_apply_safety,
    prepare_book_import,
)
from app.services.pdf_import_classifier_service import (
    PdfImportClassificationError,
    classify_pdf_import,
)
from app.services.pdf_import_job_process_service import (
    STAGES,
    elapsed_seconds,
    read_status_file,
    utcnow,
    write_status_atomic,
)
from app.services.production_write_surface_guard import (
    require_proven_legacy_for_legacy_write_surface,
)
from app.services import zotero_native_annotation_import_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a chaptered PDF import job worker.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--payload-file", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--worker-log", required=True)
    args = parser.parse_args(argv)

    payload_file = Path(args.payload_file)
    status_file = Path(args.status_file)
    worker_log = Path(args.worker_log)
    worker_log.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _append_worker_log(worker_log, f"failed to read payload: {exc}\n")
        _fail_status(
            status_file,
            error=f"failed to read payload: {exc}",
            traceback_tail=traceback.format_exc(),
        )
        return 2

    try:
        run_worker(
            job_id=args.job_id,
            payload=payload,
            status_file=status_file,
            worker_log=worker_log,
        )
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        _append_worker_log(worker_log, tb)
        _fail_status(status_file, error=str(exc), traceback_tail=tb)
        return 1


def run_worker(
    *,
    job_id: str,
    payload: dict[str, Any],
    status_file: Path,
    worker_log: Path,
) -> None:
    _require_legacy_job_worker_surface()
    pdf_path = str(payload.get("pdf_path") or "")
    document_type = str(payload.get("document_type") or "book")
    backend = str(payload.get("backend") or MARKER_SURYA_PAGE_BLOCKS_BACKEND)
    confirm_title = payload.get("confirm_title") or None
    confirm_chapter_count = payload.get("confirm_chapter_count") or None
    import_granularity = payload.get("import_granularity") or "chapter"
    selected_chapter_indexes = payload.get("selected_chapter_indexes") or None
    initial_status = read_status_file(status_file)
    parser_device = str(payload.get("worker_device") or initial_status.get("worker_device") or initial_status.get("parser_device") or "unknown")
    device_selection_reason = str(
        payload.get("device_selection_reason")
        or initial_status.get("device_selection_reason")
        or (initial_status.get("runtime") or {}).get("reason")
        or ""
    )
    worker_gpu_name = payload.get("worker_gpu_name") or initial_status.get("worker_gpu_name") or (initial_status.get("runtime") or {}).get("cuda_device_name")

    _append_worker_log(
        worker_log,
        f"worker started\njob_id={job_id}\nworker_pid={_current_pid()}\npdf_path={pdf_path}\nbackend={backend}\nimport_granularity={import_granularity}\nparser_device={parser_device}\nworker_device={parser_device}\ndevice_selection_reason={device_selection_reason}\nworker_gpu_name={worker_gpu_name or ''}\n",
    )

    _set_stage(
        status_file,
        "classifying",
        "正在识别 PDF 文献类型与导入方式。",
        worker_pid=_current_pid(),
        extra={
            "worker_backend": backend,
            "worker_device": parser_device,
            "worker_gpu_name": worker_gpu_name,
            "device_selection_reason": device_selection_reason,
            "import_backend_device": parser_device,
            "parser_device": parser_device,
        },
    )

    classification = classify_pdf_import(
        pdf_path,
        source=str(payload.get("source") or "local"),
        zotero_key=payload.get("zotero_key"),
        zotero_pdf_source_id=payload.get("zotero_pdf_source_id"),
    )

    if classification.get("duplicate"):
        raise PdfImportClassificationError(
            f"duplicate_pdf: document_id={classification.get('existing_document_id')}"
        )

    title = confirm_title or classification.get("title") or "Untitled"
    page_count = classification.get("signals", {}).get("page_count") or payload.get("confirm_page_count") or 0

    _set_stage(
        status_file,
        "parsing_pdf",
        f"正在解析 PDF 与识别章节（{page_count} 页，后端 {backend}）。",
    )
    _append_worker_log(
        worker_log,
        f"enter prepare_book_import\npdf_path={pdf_path}\nbackend={backend}\nimport_granularity={import_granularity}\nparser_device={parser_device}\nworker_device={parser_device}\n",
    )

    def progress_callback(stage: str, progress_percent: int, message: str, extra: dict[str, Any] | None = None) -> None:
        _set_stage(
            status_file,
            stage,
            message,
            progress_percent=progress_percent,
            extra=extra,
        )

    prepared = prepare_book_import(
        pdf_path,
        title=title,
        backend=backend,
        max_chapters=confirm_chapter_count,
        selected_chapter_indexes=selected_chapter_indexes,
        import_granularity=import_granularity,
        job_progress_callback=progress_callback,
        job_id=job_id,
        parser_device=parser_device,
    )

    _set_stage(
        status_file,
        "detecting_chapters",
        f"已检测 {len(prepared.chapters)} 章，{prepared.estimated_chunk_count} 个证据片段。",
    )

    safety = evaluate_auto_apply_safety(prepared)
    _set_stage(
        status_file,
        "detecting_chapters",
        f"已检测 {len(prepared.chapters)} 章，{prepared.estimated_chunk_count} 个证据片段。",
        extra={
            "book_safety_decision": safety.get("book_safety_decision"),
            "book_safety_blockers": safety.get("book_safety_blockers", []),
            "book_safety_warnings": safety.get("book_safety_warnings", []),
            "detected_chapter_count": safety.get("detected_chapter_count", len(prepared.chapters)),
            "chapter_title_quality": safety.get("chapter_title_quality"),
        },
    )
    if not safety.get("auto_apply_eligible"):
        raise ValueError("book import is not safe to apply: " + "; ".join(safety.get("reasons") or []))

    _require_legacy_job_worker_surface()
    _set_stage(
        status_file,
        "writing_db",
        f"正在写入 {len(prepared.chapters)} 章到资料库。",
        cancel_allowed=False,
    )

    result = apply_prepared_book_import(prepared)

    if document_type != "book" and result.get("document_id"):
        _patch_document_type(int(result["document_id"]), document_type)

    result["document_type"] = document_type
    result["requested_document_type"] = document_type
    result["object_import_mode"] = "chaptered"
    result["import_granularity"] = import_granularity
    result["selected_chapter_indexes"] = selected_chapter_indexes or []
    result["outline_units_used"] = str(prepared.detection_method).startswith("pdf_outline")
    result["external_llm_called"] = False

    document_id = int(result.get("document_id") or 0) or None
    result["zotero_native_notes_import"] = _sync_zotero_native_notes_for_chapters(
        document_id=document_id,
        payload=payload,
        prepared=prepared,
    )

    _set_stage(status_file, "verifying", "正在校验导入结果。", cancel_allowed=False)
    _complete_status(status_file, result=result, document_id=document_id)
    _append_worker_log(worker_log, f"job {job_id} completed\n")


def _require_legacy_job_worker_surface() -> None:
    require_proven_legacy_for_legacy_write_surface(
        error_code="chaptered_import_job_versioned_frozen",
        message=(
            "后台 chaptered PDF import worker 在 versioned production 中已冻结；"
            "本次 worker 未执行任何 production mutation。"
        ),
        db_path=DEFAULT_DB_PATH,
        data_dir=DATA_DIR,
    )


def _set_stage(
    status_file: Path,
    stage: str,
    message: str,
    *,
    progress_percent: int | None = None,
    worker_pid: int | None = None,
    cancel_allowed: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    status = read_status_file(status_file)
    updates: dict[str, Any] = {
        "status": "running",
        "stage": stage,
        "progress_percent": progress_percent if progress_percent is not None else STAGES.get(stage, 0),
        "message": message,
        "worker_pid": worker_pid or status.get("worker_pid") or _current_pid(),
        "started_at": status.get("started_at") or utcnow(),
    }
    if cancel_allowed is not None:
        updates["cancel_allowed"] = cancel_allowed
    if extra:
        updates.update(extra)
    _write_merged_status(status_file, status, **updates)


def _complete_status(status_file: Path, *, result: dict[str, Any], document_id: int | None) -> None:
    status = read_status_file(status_file)
    _write_merged_status(
        status_file,
        status,
        status="completed",
        stage="completed",
        progress_percent=100,
        message=f"Import complete. document_id={document_id}",
        document_id=document_id,
        result=dict(result),
        error=None,
        traceback_tail=None,
        cancel_allowed=False,
    )


def _fail_status(status_file: Path, *, error: str, traceback_tail: str | None) -> None:
    try:
        status = read_status_file(status_file)
    except Exception:
        return
    tail = _traceback_tail(traceback_tail)
    _write_merged_status(
        status_file,
        status,
        status="failed",
        stage="failed",
        progress_percent=100,
        message=f"Import failed: {str(error)[:200]}",
        error=str(error),
        traceback_tail=tail,
        cancel_allowed=False,
    )


def _write_merged_status(status_file: Path, current: dict[str, Any], **updates: Any) -> None:
    now = utcnow()
    merged = dict(current)
    merged.update(updates)
    merged["updated_at"] = now
    merged["heartbeat_at"] = now
    merged["elapsed_seconds"] = elapsed_seconds(merged.get("created_at"), now)
    write_status_atomic(status_file, merged)


def _patch_document_type(document_id: int, document_type: str) -> None:
    db = DEFAULT_DB_PATH
    if not db.exists():
        return
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE documents SET document_type = ? WHERE id = ?",
            (document_type, document_id),
        )
        conn.commit()


def _sync_zotero_native_notes_for_chapters(
    *,
    document_id: int | None,
    payload: dict[str, Any],
    prepared: Any,
) -> dict[str, Any]:
    if not document_id:
        return {
            "status": "SKIPPED",
            "mode": zotero_native_annotation_import_service.MODE,
            "attempted": False,
            "apply": False,
            "reason": "document_id_missing",
            "imported_count": 0,
            "skipped_existing_count": 0,
            "blocked_count": 0,
            "warnings": ["document_id_missing"],
            "db_write_performed": False,
            **zotero_native_annotation_import_service.NO_WRITE_FLAGS,
        }
    attachment_key = payload.get("zotero_attachment_key")
    zotero_item_key = payload.get("zotero_key") or payload.get("zotero_item_key")
    chapters = [
        {
            "chapter_index": chapter.chapter_index,
            "title": chapter.title,
            "pdf_page_start": chapter.pdf_page_start,
            "pdf_page_end": chapter.pdf_page_end,
        }
        for chapter in prepared.chapters
    ]
    try:
        return zotero_native_annotation_import_service.sync_zotero_native_annotations_for_chapters(
            research_db_path=DEFAULT_DB_PATH,
            zotero_db_path=zotero_native_annotation_import_service.DEFAULT_ZOTERO_SNAPSHOT_PATH,
            document_id=document_id,
            zotero_attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            chapters=chapters,
            apply=True,
        )
    except Exception as exc:
        return {
            "status": "WARN",
            "mode": zotero_native_annotation_import_service.MODE,
            "attempted": bool(attachment_key or zotero_item_key),
            "apply": True,
            "document_id": document_id,
            "unit_type": "book_chapter",
            "zotero_attachment_key": attachment_key,
            "zotero_item_key": zotero_item_key,
            "imported_count": 0,
            "skipped_existing_count": 0,
            "blocked_count": 0,
            "warnings": ["zotero_native_annotation_import_error"],
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "db_write_performed": False,
            **zotero_native_annotation_import_service.NO_WRITE_FLAGS,
        }


def _traceback_tail(text: str | None) -> str | None:
    if not text:
        return None
    return "\n".join(str(text).splitlines()[-50:])


def _append_worker_log(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _current_pid() -> int:
    import os

    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
