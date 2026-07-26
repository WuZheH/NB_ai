from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR
from app.services import chat_local_note_import_service, commit_book_service, commit_paper_service, vector_store_service
from app.services.library import document_deletion_service
from app.services.retrieval import fts_index_service, fts_status_service
from app.services.vector_store_service import MANIFEST_PATH


@dataclass(frozen=True)
class ChatPdfImportRuntime:
    db_path: Path
    data_dir: Path
    fts_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path
    deletion_runtime: document_deletion_service.DeletionRuntime
    body_commit: Callable[[str, str], dict[str, Any]]

    @classmethod
    def production(cls) -> "ChatPdfImportRuntime":
        def body(job_id: str, document_type: str) -> dict[str, Any]:
            if document_type in {"book", "thesis", "report"}:
                return commit_book_service.commit_book_from_staging(job_id)
            return commit_paper_service.commit_paper_from_staging(job_id, rebuild_legacy_vector_index=False)
        return cls(DEFAULT_DB_PATH, DATA_DIR, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR, MANIFEST_PATH,
                   document_deletion_service.DeletionRuntime(db_path=DEFAULT_DB_PATH, data_dir=DATA_DIR,
                       fts_path=FTS_DB_PATH, fts_manifest_path=FTS_MANIFEST_PATH,
                       vector_store_path=LANCEDB_DIR, vector_manifest_path=MANIFEST_PATH), body)


def _is_production_runtime(runtime: ChatPdfImportRuntime) -> bool:
    pairs = ((runtime.db_path, DEFAULT_DB_PATH), (runtime.data_dir, DATA_DIR), (runtime.fts_path, FTS_DB_PATH),
             (runtime.fts_manifest_path, FTS_MANIFEST_PATH), (runtime.vector_store_path, LANCEDB_DIR),
             (runtime.vector_manifest_path, MANIFEST_PATH))
    return all(Path(a).resolve(strict=False) == Path(b).resolve(strict=False) for a, b in pairs)


def _document_ids(db_path: Path) -> set[int]:
    with sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True) as c:
        return {int(row[0]) for row in c.execute("SELECT id FROM documents")}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fts_status(runtime: ChatPdfImportRuntime) -> dict[str, Any]:
    if _is_production_runtime(runtime):
        return fts_status_service.get_index_status(index_path=runtime.fts_path, manifest_path=runtime.fts_manifest_path, production_db_path=runtime.db_path)
    missing_zotero = runtime.db_path.with_name(".b5b1-zotero-snapshot-absent.sqlite")
    missing_notes = runtime.db_path.with_name(".b5b1-notes-absent")
    return fts_status_service.get_index_status(index_path=runtime.fts_path, manifest_path=runtime.fts_manifest_path, production_db_path=runtime.db_path, zotero_snapshot_path=missing_zotero, notes_root=missing_notes)


def _rollback_document(document_id: int, runtime: ChatPdfImportRuntime) -> dict[str, Any]:
    preview = document_deletion_service.create_deletion_preview(document_id, runtime=runtime.deletion_runtime)
    result = document_deletion_service.delete_document(document_id=document_id, preview_token=str(preview["preview_token"]), expected_document_revision=str(preview["document_revision"]), confirmation_text="删除", runtime=runtime.deletion_runtime)
    if result.get("status") != "completed":
        raise RuntimeError("chat_import_rollback_failed")
    return result


def import_document_to_production(*, import_job_id: str, document_type: str, note_files: list[Path] | None = None, inbox_root: Path | None = None, expected_before_db_sha256: str | None = None, allow_production: bool = False, runtime: ChatPdfImportRuntime | None = None) -> dict[str, Any]:
    actual = runtime or ChatPdfImportRuntime.production()
    production = _is_production_runtime(actual)
    if production and not allow_production:
        raise RuntimeError("chat_import_production_opt_in_required")
    if not production and allow_production:
        raise RuntimeError("chat_import_temp_runtime_rejects_production_opt_in")
    status = _fts_status(actual)
    if status.get("status") != "ready":
        raise RuntimeError("chat_import_fts_not_ready")
    before_ids = _document_ids(actual.db_path)
    before_sha = _sha(actual.db_path)
    if expected_before_db_sha256 and before_sha.lower() != expected_before_db_sha256.lower():
        raise RuntimeError("chat_import_production_revision_changed")
    document_id: int | None = None
    try:
        result = actual.body_commit(import_job_id, document_type)
        created = _document_ids(actual.db_path) - before_ids
        if len(created) != 1 or int(result.get("document_id") or 0) not in created:
            raise RuntimeError("chat_import_document_delta_invalid")
        document_id = next(iter(created))
    except Exception as exc:
        created = _document_ids(actual.db_path) - before_ids
        if len(created) == 1:
            try:
                _rollback_document(next(iter(created)), actual)
            except Exception as rollback_exc:
                raise RuntimeError("chat_import_rollback_failed") from rollback_exc
        elif len(created) > 1:
            raise RuntimeError("chat_import_rollback_ambiguous")
        raise exc
    try:
        notes = chat_local_note_import_service.import_local_notes(db_path=actual.db_path, document_id=document_id, note_files=note_files or [], inbox_root=inbox_root or Path("."))
        after_sha = _sha(actual.db_path)
        fts = fts_index_service.upsert_document_retrieval_fts(document_id=document_id, index_path=actual.fts_path, manifest_path=actual.fts_manifest_path, research_db_path=actual.db_path, allow_production=production, expected_before_db_sha256=before_sha if production else None, expected_after_db_sha256=after_sha if production else None)
        with sqlite3.connect(f"file:{actual.db_path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            ids = [f"chunk:{document_id}:{int(row[0])}" for row in connection.execute("SELECT id FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index,id", (document_id,))]
        vectors = vector_store_service.sync_affected_passage_embeddings(ids, dry_run=False, apply=True, source_db_path=None if production else actual.db_path, store_path=actual.vector_store_path, manifest_path=actual.vector_manifest_path)
        final_status = _fts_status(actual)
        with sqlite3.connect(f"file:{actual.db_path.resolve().as_posix()}?mode=ro", uri=True) as verify_connection:
            document_count = int(verify_connection.execute("SELECT COUNT(*) FROM documents WHERE id=?", (document_id,)).fetchone()[0])
            chunk_count = int(verify_connection.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (document_id,)).fetchone()[0])
        if (final_status.get("status") != "ready" or document_count != 1 or chunk_count <= 0
                or vectors.get("scope") != "affected_source_ids_only"
                or vectors.get("full_rebuild_allowed") is not False
                or vectors.get("delete_orphans_allowed") is not False):
            raise RuntimeError("chat_import_final_verify_failed")
        return {"status": "completed", "document_id": document_id, "title": result.get("title", ""), "document_type": document_type, "chunk_count": result.get("chunk_count", 0), "note_count": notes["note_count"], "evidence_link_count": notes["evidence_link_count"], "fts_status": final_status.get("status"), "passage_vectors_upserted": vectors.get("upserted_count", 0), "full_rebuild_performed": False}
    except Exception:
        try:
            _rollback_document(document_id, actual)
        except Exception as rollback_exc:
            raise RuntimeError("chat_import_rollback_failed") from rollback_exc
        raise
