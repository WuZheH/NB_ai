from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR
from app.services import chat_local_note_import_service, commit_book_service, commit_paper_service
from app.services.retrieval import fts_index_service
from app.services import vector_store_service
from app.services.vector_store_service import MANIFEST_PATH


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_document_to_production(*, import_job_id: str, document_type: str, note_files: list[Path] | None = None, inbox_root: Path | None = None, expected_before_db_sha256: str | None = None) -> dict[str, Any]:
    before = _sha(Path(DEFAULT_DB_PATH))
    if expected_before_db_sha256 and before.lower() != expected_before_db_sha256.lower():
        raise RuntimeError("chat_import_production_revision_changed")
    status = fts_index_service.get_index_status()
    if status.get("status") != "ready":
        raise RuntimeError("chat_import_fts_not_ready")
    if document_type in {"book", "thesis", "report"}:
        result = commit_book_service.commit_book_from_staging(import_job_id)
    else:
        result = commit_paper_service.commit_paper_from_staging(import_job_id, rebuild_legacy_vector_index=False)
    document_id = int(result["document_id"])
    try:
        notes = chat_local_note_import_service.import_local_notes(db_path=DEFAULT_DB_PATH, document_id=document_id, note_files=note_files or [], inbox_root=inbox_root or Path("."))
        after = _sha(Path(DEFAULT_DB_PATH))
        fts = fts_index_service.upsert_document_retrieval_fts(document_id=document_id, index_path=FTS_DB_PATH, manifest_path=FTS_MANIFEST_PATH, research_db_path=DEFAULT_DB_PATH, allow_production=True, expected_before_db_sha256=before, expected_after_db_sha256=after)
        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            ids = [f"chunk:{document_id}:{int(row[0])}" for row in connection.execute("SELECT id FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index,id", (document_id,))]
        vectors = vector_store_service.sync_affected_passage_embeddings(ids, dry_run=False, apply=True, store_path=LANCEDB_DIR, manifest_path=MANIFEST_PATH)
        return {"status": "completed", "document_id": document_id, "title": result.get("title", ""), "document_type": document_type, "chunk_count": result.get("chunk_count", 0), "note_count": notes["note_count"], "evidence_link_count": notes["evidence_link_count"], "fts_status": fts.get("status"), "passage_vectors_upserted": vectors.get("upserted_count", 0), "full_rebuild_performed": False}
    except Exception:
        raise
