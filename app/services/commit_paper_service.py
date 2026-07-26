from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.paths import DATA_PROJECT_ROOT, DEFAULT_DB_PATH, OUTPUTS_DIR
from app.db.session import SessionLocal
from app.models import Document
from app.services import import_service
from app.services import zotero_source_cache_service
from app.services import zotero_note_alignment_hook_service
from app.services import zotero_native_annotation_import_service
from app.services.import_preview_service import (
    ImportPreviewError, _existing_job_dir, _read_json, _write_json,
)
from app.services.markdown_parser import parse_markdown
from app.services.vector_index_service import VECTOR_INDEX_DIR, rebuild_vector_index

COMMIT_BACKUP_ROOT = OUTPUTS_DIR / "phase18e_commitpaper_backup"
DB_PATH = DEFAULT_DB_PATH
COMMIT_MANIFEST_FILE = "commit_result.json"


def commit_paper_from_staging(import_job_id: str, *, rebuild_legacy_vector_index: bool = True) -> dict[str, Any]:
    job_dir = _existing_job_dir(import_job_id)

    paper_md_path = job_dir / "paper.md"
    manifest_path = job_dir / "import_manifest.json"
    source_trace_path = job_dir / "source_trace.json"
    commit_path = job_dir / COMMIT_MANIFEST_FILE

    if not paper_md_path.is_file():
        raise ImportPreviewError("paper.md not found in import staging.")
    if not manifest_path.is_file():
        raise ImportPreviewError("import_manifest.json not found.")
    if not source_trace_path.is_file():
        raise ImportPreviewError("source_trace.json not found.")

    # Idempotency check
    if commit_path.is_file():
        existing = _read_json(commit_path)
        return {
            "status": "already_committed",
            "import_job_id": import_job_id,
            "document_id": existing.get("document_id"),
            "committed_at": existing.get("committed_at"),
            "message": "This import job has already been committed.",
            "zotero_note_alignment_hook": existing.get("zotero_note_alignment_hook"),
            "zotero_native_notes_import": existing.get("zotero_native_notes_import"),
            "core_db_write_performed": False,
            "external_llm_called": False,
        }

    manifest = _read_json(manifest_path)
    source_trace = _read_json(source_trace_path)

    # Backup
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = COMMIT_BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_dir / "research_memory_pre_commit.db")
    vector_chunks = VECTOR_INDEX_DIR / "chunks.jsonl"
    vector_manifest = VECTOR_INDEX_DIR / "manifest.json"
    if vector_chunks.exists():
        shutil.copy2(vector_chunks, backup_dir / "chunks_pre_commit.jsonl")
    if vector_manifest.exists():
        shutil.copy2(vector_manifest, backup_dir / "manifest_pre_commit.json")

    # Parse paper.md
    paper_text = paper_md_path.read_text(encoding="utf-8")
    title = _extract_title(paper_text, manifest)
    pdf_path = _extract_pdf_path(source_trace)
    zotero_key = source_trace.get("zotero_item_key")

    parsed = parse_markdown(paper_text, source_path=str(paper_md_path))
    chunks = import_service.split_nodes(parsed.nodes)

    with SessionLocal() as session:
        document = Document(
            title=title,
            document_type="paper",
            content_layer="evidence",
            source_path=str(paper_md_path.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
            pdf_path=pdf_path,
            zotero_key=zotero_key,
            read_status="read",
        )
        session.add(document)
        session.flush()

        result = import_service._upsert_nodes_and_chunks(session, document, parsed.nodes, chunks)
        session.commit()
        document_id = document.id

    zotero_source_cache_service.record_document_source(document_id, source_trace)
    zotero_native_notes_import = _sync_zotero_native_notes_for_paper(
        document_id=document_id,
        source_trace=source_trace,
    )
    zotero_note_alignment_hook = _run_zotero_note_alignment_hook(
        document_id=document_id,
        source_trace=source_trace,
        source_path=str(paper_md_path.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
    )

    chunk_count_index = 0
    if rebuild_legacy_vector_index:
        try:
            vector_result = rebuild_vector_index()
            chunk_count_index = vector_result.chunk_count
        except Exception:
            vector_chunks.write_text("", encoding="utf-8")
            vector_manifest.write_text(json.dumps({
                "chunk_count": 0, "embedding_model": "none",
                "embedder_type": "reset_empty_index",
            }), encoding="utf-8")

    # Record commit
    committed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    commit_data = {
        "status": "committed",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "title": title,
        "markdown_node_count": result.nodes_created,
        "chunk_count": result.chunks_created,
        "vector_index_chunk_count": chunk_count_index,
        "committed_at": committed_at,
        "backup_dir": str(backup_dir.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
        "zotero_native_notes_import": zotero_native_notes_import,
        "zotero_note_alignment_hook": zotero_note_alignment_hook,
        **_safety_fields(),
    }
    _write_json(commit_path, commit_data)

    # Update import manifest
    manifest["status"] = "committed"
    manifest.setdefault("commit_info", {})
    manifest["commit_info"]["document_id"] = document_id
    manifest["commit_info"]["committed_at"] = committed_at
    _write_json(manifest_path, manifest)

    return {
        "status": "committed",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "title": title,
        "markdown_node_count": result.nodes_created,
        "chunk_count": result.chunks_created,
        "vector_index_chunk_count": chunk_count_index,
        "backup_dir": str(backup_dir.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
        "zotero_native_notes_import": zotero_native_notes_import,
        "zotero_note_alignment_hook": zotero_note_alignment_hook,
        "core_db_write_performed": True,
        "external_llm_called": False,
    }


def _extract_title(paper_text: str, manifest: dict[str, Any]) -> str:
    # Try manifest title_hint first
    hint = manifest.get("title_hint")
    if hint and str(hint).strip():
        return str(hint).strip()
    # Try YAML front matter
    if paper_text.startswith("---"):
        end = paper_text.find("---", 3)
        if end > 0:
            front = paper_text[3:end]
            for line in front.split("\n"):
                line = line.strip()
                if line.startswith("title:"):
                    title_val = line[len("title:"):].strip().strip('"').strip("'")
                    if title_val:
                        return title_val
    # Fallback: first # heading
    for line in paper_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return "Untitled import"


def _extract_pdf_path(source_trace: dict[str, Any]) -> str | None:
    raw = source_trace.get("source_pdf_path")
    if raw and str(raw).strip():
        return str(raw).strip()
    return None


def _safety_fields() -> dict[str, bool]:
    return {
        "core_db_write_performed": True,
        "external_llm_called": False,
        "final_hypothesis_created": False,
    }


def _run_zotero_note_alignment_hook(
    *,
    document_id: int,
    source_trace: dict[str, Any],
    source_path: str | None,
) -> dict[str, Any]:
    source_type = str(source_trace.get("source_type") or "").strip()
    attachment_key = _clean_optional_text(source_trace.get("zotero_attachment_key"))
    zotero_item_key = _clean_optional_text(source_trace.get("zotero_item_key"))
    if source_type != "zotero_pdf":
        return zotero_note_alignment_hook_service.skipped_import_time_alignment_hook_report(
            reason="source_trace_not_zotero_pdf",
            document_id=document_id,
            attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            source_path=source_path,
        )
    if not attachment_key:
        return zotero_note_alignment_hook_service.skipped_import_time_alignment_hook_report(
            reason="zotero_attachment_key_missing",
            document_id=document_id,
            attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            source_path=source_path,
        )
    try:
        return zotero_note_alignment_hook_service.run_import_time_alignment_hook_dry_run(
            DB_PATH,
            document_id=document_id,
            attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            source_path=source_path,
        )
    except Exception as exc:
        return {
            "status": "WARN",
            "mode": zotero_note_alignment_hook_service.SERVICE_MODE,
            "service_mode": zotero_note_alignment_hook_service.SERVICE_MODE,
            "document_id": document_id,
            "attachment_key": attachment_key,
            "zotero_item_key": zotero_item_key,
            "source_path": source_path,
            "batch_result": None,
            "blockers": ["alignment_hook_error"],
            "warnings": ["alignment_hook_error"],
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "hook_recommended_next_action": "inspect_alignment_hook_error",
            **zotero_note_alignment_hook_service.NO_WRITE_FLAGS,
        }


def _sync_zotero_native_notes_for_paper(
    *,
    document_id: int,
    source_trace: dict[str, Any],
) -> dict[str, Any]:
    source_type = str(source_trace.get("source_type") or "").strip()
    attachment_key = _clean_optional_text(source_trace.get("zotero_attachment_key"))
    zotero_item_key = _clean_optional_text(source_trace.get("zotero_item_key"))
    if source_type != "zotero_pdf" or not attachment_key:
        return zotero_native_annotation_import_service.sync_zotero_native_annotations_for_document_or_unit(
            DB_PATH,
            zotero_native_annotation_import_service.DEFAULT_ZOTERO_SNAPSHOT_PATH,
            document_id=document_id,
            zotero_attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            unit_type="whole_paper_unit",
            apply=False,
        )
    try:
        return zotero_native_annotation_import_service.sync_zotero_native_annotations_for_document_or_unit(
            DB_PATH,
            zotero_native_annotation_import_service.DEFAULT_ZOTERO_SNAPSHOT_PATH,
            document_id=document_id,
            zotero_attachment_key=attachment_key,
            zotero_item_key=zotero_item_key,
            unit_type="whole_paper_unit",
            apply=True,
        )
    except Exception as exc:
        return {
            "status": "WARN",
            "mode": zotero_native_annotation_import_service.MODE,
            "attempted": True,
            "apply": True,
            "document_id": document_id,
            "unit_type": "whole_paper_unit",
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


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
