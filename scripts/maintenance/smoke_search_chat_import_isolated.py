from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any


def main() -> int:
    arguments = _parser().parse_args()
    root = Path(arguments.root).resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("isolated_root_must_be_empty")
    data_dir = root / "data"
    inbox = root / "inbox"
    runtime_dir = root / "runtime"
    logs_dir = root / "logs"
    for path in (data_dir, inbox, runtime_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)

    os.environ["SEARCH_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["SEARCH_LOG_DIR"] = str(logs_dir)
    os.environ["SEARCH_MACHINE_CONFIG_PATH"] = str(root / "missing-machine-config.json")
    os.environ["NOTEBOOK_AI_VECTOR_STORE_WORKER_ENABLED"] = "0"
    os.environ["NOTEBOOK_AI_VECTOR_STORE_AUTO_SYNC_ENABLED"] = "0"
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from app.core.paths import DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH
    from app.services import chat_pdf_production_import_service
    from app.services.library.document_deletion_service import DeletionRuntime
    from app.services.retrieval import fts_index_service
    from app.services.retrieval.source_registry import RetrievalSourceRegistry

    temp_db = data_dir / "db" / "research_memory.db"
    temp_fts = data_dir / "search_index" / "retrieval_fts_v1.db"
    temp_manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    temp_vector_store = data_dir / "vector_store" / "lancedb"
    temp_vector_manifest = data_dir / "vector_store" / "manifest.json"
    production_before = {"db": _sha(DEFAULT_DB_PATH), "fts": _sha(FTS_DB_PATH), "manifest": _sha(FTS_MANIFEST_PATH)}
    _clone_database_readonly(DEFAULT_DB_PATH, temp_db)
    with sqlite3.connect(temp_db) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"temp_db_integrity_failed:{integrity}")

    missing_zotero = temp_db.with_name(".b5b1-zotero-snapshot-absent.sqlite")
    missing_notes = temp_db.with_name(".b5b1-notes-absent")
    registry = RetrievalSourceRegistry(
        research_db_path=temp_db,
        zotero_snapshot_path=missing_zotero,
        notes_root=missing_notes,
    )
    # The production primitive validates targets against its configured data
    # root. Keep application paths unchanged and scope this isolated fixture
    # to the explicit TEMP data root instead of redefining SEARCH_DATA_DIR.
    fts_index_service.build_retrieval_fts(
        index_path=temp_fts, manifest_path=temp_manifest, registry=registry, target_root=data_dir
    )

    def temp_body_commit(import_job_id: str, document_type: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        title = "B6B2 Isolated TEMP Import"
        text = "Isolated chat import evidence for motion diffusion."
        with sqlite3.connect(temp_db) as connection:
            cursor = connection.execute(
                """INSERT INTO documents
                (title, document_type, content_layer, source_path, pdf_path,
                 read_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (title, document_type, "full", "isolated-temp", None, "unread", now, now),
            )
            document_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO knowledge_chunks
                (document_id, chunk_index, heading_path, chunk_text, char_count,
                 token_count, content_hash, created_at, updated_at)
                VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?)""",
                (document_id, "", text, len(text), len(text.split()), hashlib.sha256(text.encode()).hexdigest(), now, now),
            )
            connection.commit()
        return {"status": "committed", "document_id": document_id, "title": title, "chunk_count": 1}

    runtime = chat_pdf_production_import_service.ChatPdfImportRuntime(
        db_path=temp_db, data_dir=data_dir, fts_path=temp_fts,
        fts_manifest_path=temp_manifest, vector_store_path=temp_vector_store,
        vector_manifest_path=temp_vector_manifest,
        deletion_runtime=DeletionRuntime(
            db_path=temp_db, data_dir=data_dir, fts_path=temp_fts,
            fts_manifest_path=temp_manifest, vector_store_path=temp_vector_store,
            vector_manifest_path=temp_vector_manifest, archive_root=root / "rollback_archive",
        ), body_commit=temp_body_commit,
    )
    status_before = chat_pdf_production_import_service._fts_status(runtime)
    if status_before.get("status") != "ready" or status_before.get("ready") is not True:
        raise RuntimeError(json.dumps({"status": status_before, "manifest": str(temp_manifest), "db": str(temp_db)}))
    orchestrator_result = chat_pdf_production_import_service.import_document_to_production(
        import_job_id="isolated-temp", document_type="paper", note_files=[],
        inbox_root=inbox, allow_production=False, runtime=runtime,
    )
    document_id = int(orchestrator_result["document_id"])
    with sqlite3.connect(temp_db) as connection:
        document_count = int(connection.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,)).fetchone()[0])
        chunk_count = int(connection.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?", (document_id,)).fetchone()[0])
    final_status = chat_pdf_production_import_service._fts_status(runtime)
    production_after = {"db": _sha(DEFAULT_DB_PATH), "fts": _sha(FTS_DB_PATH), "manifest": _sha(FTS_MANIFEST_PATH)}
    result = {
        "status": "ok",
        "document_id": document_id,
        "document_count": document_count,
        "chunk_count": chunk_count,
        "temp_fts_status": final_status.get("status"),
        "temp_fts_ready": final_status.get("ready"),
        "full_rebuild_performed": orchestrator_result.get("full_rebuild_performed", False),
        "production_path_used": False,
        "production_db_unchanged": production_before["db"] == production_after["db"],
        "production_fts_unchanged": production_before["fts"] == production_after["fts"],
        "production_fts_manifest_unchanged": production_before["manifest"] == production_after["manifest"],
    }
    if (
        document_count != 1
        or chunk_count < 1
        or result["temp_fts_status"] != "ready"
        or result["temp_fts_ready"] is not True
        or result["full_rebuild_performed"] is not False
        or not result["production_db_unchanged"]
        or not result["production_fts_unchanged"]
        or not result["production_fts_manifest_unchanged"]
    ):
        raise RuntimeError("isolated_import_contract_failed")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _clone_database_readonly(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as target_connection:
            source_connection.backup(target_connection)
            target_connection.commit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
