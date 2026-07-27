from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid5

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalFragment,
)
from app.schemas.library_deletion import DeletionOptions
from app.services import vector_store_service
from app.services.library import book_archive_service, document_deletion_service
from app.services.library.local_mutation_security import reset_security_state_for_tests
from app.services.retrieval import fts_index_service
from app.services.retrieval.fts_status_service import connect_readonly_index


def _create_database(root: Path) -> tuple[Path, Path, Path]:
    data_dir = root / "data"
    db_path = data_dir / "db" / "research_memory.db"
    fts_path = data_dir / "search_index" / "retrieval_fts_v1.db"
    db_path.parent.mkdir(parents=True)
    fts_path.parent.mkdir(parents=True)
    converted = data_dir / "converted_md"
    pdfs = data_dir / "pdfs"
    converted.mkdir(parents=True)
    pdfs.mkdir(parents=True)
    markdown = converted / "book.md"
    markdown.write_text("# book\ncontent", encoding="utf-8")
    pdf = pdfs / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 isolated test")

    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            document_type TEXT NOT NULL,
            content_layer TEXT NOT NULL,
            source_path TEXT,
            pdf_path TEXT,
            zotero_key TEXT,
            read_status TEXT NOT NULL,
            research_direction TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            object_import_mode TEXT,
            object_import_status TEXT
        );
        CREATE TABLE markdown_nodes (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            parent_id INTEGER,
            heading_level INTEGER NOT NULL,
            heading_title TEXT NOT NULL,
            heading_path TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            raw_content TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            node_id INTEGER REFERENCES markdown_nodes(id),
            chunk_index INTEGER NOT NULL,
            heading_path TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            embedding_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE book_chapters (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            chapter_index INTEGER NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE object_candidates (
            id INTEGER PRIMARY KEY,
            document_id INTEGER,
            object_key TEXT NOT NULL,
            user_comment TEXT,
            source_package_path TEXT,
            source_import_manifest_path TEXT,
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            mapped_chunk_ids_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE personal_notes (
            id INTEGER PRIMARY KEY,
            document_id INTEGER REFERENCES documents(id),
            title TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE note_evidence_links (
            id INTEGER PRIMARY KEY,
            note_id INTEGER NOT NULL REFERENCES personal_notes(id),
            chunk_id INTEGER NOT NULL REFERENCES knowledge_chunks(id)
        );
        CREATE TABLE zotero_inspiration_notes (
            id INTEGER PRIMARY KEY,
            matched_document_id INTEGER,
            matched_chunk_id INTEGER,
            matched_chunk_ids_json TEXT,
            matched_object_ids_json TEXT NOT NULL DEFAULT '[]',
            match_status TEXT NOT NULL DEFAULT 'matched',
            evidence_alignment_status TEXT
        );
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY,
            evidence_chunk_id INTEGER REFERENCES knowledge_chunks(id)
        );
        CREATE TABLE inspiration_card_sources (
            id INTEGER PRIMARY KEY,
            source_doc_id INTEGER,
            source_chunk_id INTEGER
        );
        CREATE TABLE chunk_tags (
            id INTEGER PRIMARY KEY,
            chunk_id INTEGER NOT NULL REFERENCES knowledge_chunks(id)
        );
        """
    )
    now = "2026-07-23T00:00:00+00:00"
    connection.execute(
        "INSERT INTO documents VALUES (1, 'Safe Book', 'book', 'source', ?, ?, NULL, 'read', NULL, ?, ?, 'chaptered', 'ready')",
        (str(markdown), str(pdf), now, now),
    )
    connection.execute(
        "INSERT INTO documents VALUES (2, 'Other Book', 'book', 'source', NULL, NULL, NULL, 'read', NULL, ?, ?, NULL, NULL)",
        (now, now),
    )
    connection.execute("INSERT INTO markdown_nodes VALUES (11, 1, NULL, 1, 'H', 'H', 0, 'raw', ?, ?)", (now, now))
    connection.execute("INSERT INTO knowledge_chunks VALUES (101, 1, 11, 0, 'H', 'chunk', 'hash', 'chunk:1:101', ?, ?)", (now, now))
    connection.execute("INSERT INTO book_chapters VALUES (21, 1, 0, 'C')")
    connection.execute("INSERT INTO object_candidates VALUES (31, 1, 'exclusive-key', '', NULL, NULL, '[]', '[101]')")
    connection.execute("INSERT INTO personal_notes VALUES (41, 1, 'note', 'private')")
    connection.execute("INSERT INTO note_evidence_links VALUES (51, 41, 101)")
    connection.execute("INSERT INTO zotero_inspiration_notes VALUES (61, 1, 101, '[101]', '[31]', 'matched', 'aligned')")
    connection.commit()
    connection.close()

    fts = sqlite3.connect(fts_path)
    fts.execute("CREATE TABLE retrieval_fragments (row_id INTEGER PRIMARY KEY, document_id INTEGER, text TEXT)")
    fts.executemany("INSERT INTO retrieval_fragments VALUES (?, ?, ?)", [(1, 1, "book"), (2, 2, "other")])
    fts.commit()
    fts.close()
    return db_path, fts_path, data_dir


class _VectorHarness:
    def __init__(self) -> None:
        self.passages = {"chunk:1:101"}
        self.objects = {"object:exclusive-key"}
        self.cleanup_calls = 0

    def inspect(self, *, passage_source_ids, object_keys, store_path):
        object_ids = {f"object:{key}" for key in object_keys}
        return {
            "status": "ok",
            "read_only": True,
            "passage_vector_count": len(self.passages.intersection(passage_source_ids)),
            "object_vector_count": len(self.objects.intersection(object_ids)),
        }

    def cleanup(self, *, passage_source_ids, affected_object_keys, store_path, manifest_path):
        self.cleanup_calls += 1
        passage_before = len(self.passages.intersection(passage_source_ids))
        object_ids = {f"object:{key}" for key in affected_object_keys}
        object_before = len(self.objects.intersection(object_ids))
        self.passages.difference_update(passage_source_ids)
        self.objects.difference_update(object_ids)
        return {
            "status": "ok",
            "deleted_passage_vectors": passage_before,
            "deleted_object_vectors": object_before,
            "updated_shared_object_vectors": 0,
        }


def _runtime(root: Path, *, vector: _VectorHarness | None = None, cleanup_fts=None, cleanup_vectors=None):
    db_path, fts_path, data_dir = _create_database(root)
    harness = vector or _VectorHarness()

    def cleanup(*, document_id, index_path, manifest_path, production_db_path):
        connection = sqlite3.connect(index_path)
        connection.execute("DELETE FROM retrieval_fragments WHERE document_id = ?", (document_id,))
        count = connection.execute("SELECT COUNT(*) FROM retrieval_fragments").fetchone()[0]
        connection.commit()
        connection.close()
        Path(manifest_path).write_text(json.dumps({"fragment_count": count}), encoding="utf-8")
        return {"status": "ready", "fragment_count": count}

    return document_deletion_service.DeletionRuntime(
        db_path=db_path,
        data_dir=data_dir,
        fts_path=fts_path,
        fts_manifest_path=fts_path.with_suffix(".json"),
        vector_store_path=data_dir / "vector_store" / "lancedb",
        vector_manifest_path=data_dir / "vector_store" / "manifest.json",
        archive_root=root / "archives",
        cleanup_fts=cleanup_fts or cleanup,
        inspect_vectors=harness.inspect,
        cleanup_vectors=cleanup_vectors or harness.cleanup,
    ), harness


def _retrieval_fragment(document_id: int) -> RetrievalFragment:
    locator = f"test://document/{document_id}/fragment/1"
    text = f"isolated book {document_id}"
    return RetrievalFragment(
        fragment_id=str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, locator)),
        display_id=f"test-{document_id}",
        source_type="pdf_chunk",
        origin_kind="manual_import",
        source_record_id=f"chunk:{document_id}",
        canonical_source_locator=locator,
        document_id=document_id,
        title=f"Book {document_id}",
        text=text,
        context_status="not_requested",
        index_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        adapter_version="test.v1",
    )


def _sequential_runtime(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[document_deletion_service.DeletionRuntime, _VectorHarness]:
    runtime, harness = _runtime(root)
    now = "2026-07-24T00:00:00+00:00"
    connection = sqlite3.connect(runtime.db_path)
    for document_id in range(3, 6):
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, 'book', 'source', NULL, NULL, NULL, 'read', NULL, ?, ?, NULL, NULL)",
            (document_id, f"Book {document_id}", now, now),
        )
    connection.execute("UPDATE documents SET title='Book 1' WHERE id=1")
    connection.execute("UPDATE documents SET title='Book 2' WHERE id=2")
    connection.commit()
    connection.close()

    runtime.fts_path.unlink()
    fts_index_service._build_database(
        runtime.fts_path,
        [_retrieval_fragment(document_id) for document_id in range(1, 6)],
    )
    runtime.fts_manifest_path.write_text(
        json.dumps({"fragment_count": 5}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fts_index_service, "DATA_DIR", runtime.data_dir)
    return replace(runtime, cleanup_fts=None), harness


@pytest.fixture(autouse=True)
def _reset_tokens() -> None:
    document_deletion_service.reset_preview_tokens_for_tests()
    reset_security_state_for_tests()


def test_deletion_preview_is_read_only_and_redacts_paths(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    before = runtime.db_path.read_bytes()
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    assert runtime.db_path.read_bytes() == before
    assert not runtime.resolved_archive_root().exists()
    assert preview["whether_safe_to_delete"] is True
    assert preview["chunk_count"] == 1
    assert preview["personal_note_count"] == 1
    assert preview["zotero_note_count"] == 1
    assert preview["passage_vector_count"] == 1
    assert preview["pdf"]["basename"] == "book.pdf"
    serialized = json.dumps(preview, ensure_ascii=False)
    assert str(tmp_path) not in serialized


def test_empty_vector_identity_list_is_valid_zero_impact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "empty-vector-store"
    store.mkdir()
    fake_database = object()
    monkeypatch.setattr(
        vector_store_service,
        "_connect_existing_vector_store",
        lambda _path: fake_database,
    )
    monkeypatch.setattr(vector_store_service, "open_vector_store", lambda _path: fake_database)
    monkeypatch.setattr(vector_store_service, "_table_names", lambda _db: [])
    monkeypatch.setattr(vector_store_service, "collect_object_sources", lambda: [])
    monkeypatch.setattr(vector_store_service, "get_vector_manifest", lambda _path: {})
    monkeypatch.setattr(
        vector_store_service,
        "_updated_manifest",
        lambda **_kwargs: {},
    )
    impact = vector_store_service.inspect_document_vector_impact(
        passage_source_ids=[],
        object_keys=[],
        store_path=store,
    )
    cleanup = vector_store_service.cleanup_document_vectors(
        passage_source_ids=[],
        affected_object_keys=[],
        store_path=store,
        manifest_path=tmp_path / "manifest.json",
    )
    assert impact["passage_vector_count"] == 0
    assert impact["object_vector_count"] == 0
    assert cleanup["deleted_passage_vectors"] == 0
    assert cleanup["deleted_object_vectors"] == 0


def test_archive_is_lazy_reversible_and_excluded_from_active_status(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='library_archive_states'").fetchone() is None
    connection.close()
    archived = book_archive_service.archive_documents([1], db_path=runtime.db_path)
    assert archived["search_includes_archived"] is False
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT read_status FROM documents WHERE id=1").fetchone()[0] == "archived"
    assert connection.execute("SELECT previous_read_status FROM library_archive_states WHERE document_id=1").fetchone()[0] == "read"
    connection.close()
    assert [item.item_id for item in book_archive_service.list_archived_documents(db_path=runtime.db_path)] == [1]
    book_archive_service.restore_documents([1], db_path=runtime.db_path)
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT read_status FROM documents WHERE id=1").fetchone()[0] == "read"
    connection.close()


def test_delete_transaction_preserves_notes_pdf_and_creates_recovery_package(tmp_path: Path) -> None:
    runtime, harness = _runtime(tmp_path)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="Safe Book",
        deletion_options=DeletionOptions(),
        runtime=runtime,
    )
    assert result["status"] == "completed"
    assert harness.cleanup_calls == 1
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 0
    assert connection.execute("SELECT document_id FROM personal_notes WHERE id=41").fetchone()[0] is None
    zotero = connection.execute("SELECT matched_document_id, matched_chunk_id, matched_chunk_ids_json FROM zotero_inspiration_notes WHERE id=61").fetchone()
    assert zotero[0] is None and zotero[1] is None and json.loads(zotero[2]) == []
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    assert (runtime.data_dir / "pdfs" / "book.pdf").is_file()
    assert not (runtime.data_dir / "converted_md" / "book.md").exists()
    archive = runtime.resolved_archive_root() / result["audit_id"]
    assert (archive / "database_rows.json").is_file()
    assert (archive / "recovery_manifest.json").is_file()
    assert (archive / "deletion_report.json").is_file()
    assert result["orphan_scan"]["ok"] is True
    assert "chunk:1:101" not in harness.passages
    assert "object:exclusive-key" not in harness.objects


def test_five_sequential_deletions_complete_with_scoped_fts_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _harness = _sequential_runtime(tmp_path, monkeypatch)

    results = []
    for document_id in range(1, 6):
        preview = document_deletion_service.create_deletion_preview(
            document_id,
            runtime=runtime,
        )
        result = document_deletion_service.delete_document(
            document_id=document_id,
            preview_token=preview["preview_token"],
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
        results.append(result)

    assert [result["status"] for result in results] == ["completed"] * 5
    assert [result["fts"]["removed_fragment_rows"] for result in results] == [1] * 5
    assert all(result["orphan_scan"]["ok"] is True for result in results)
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    with sqlite3.connect(runtime.fts_path) as fts:
        assert fts.execute("SELECT COUNT(*) FROM retrieval_fragments").fetchone()[0] == 0
        assert fts.execute("SELECT COUNT(*) FROM retrieval_fts_unicode").fetchone()[0] == 0
        assert fts.execute("SELECT COUNT(*) FROM retrieval_fts_trigram").fetchone()[0] == 0
        assert fts.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list(runtime.resolved_archive_root().glob("delete-*"))) == 5


def test_scoped_fts_cleanup_is_idempotent_and_keeps_unrelated_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _harness = _sequential_runtime(tmp_path, monkeypatch)
    first = fts_index_service.cleanup_document_retrieval_fts(
        document_id=1,
        index_path=runtime.fts_path,
        manifest_path=runtime.fts_manifest_path,
        production_db_path=runtime.db_path,
    )
    second = fts_index_service.cleanup_document_retrieval_fts(
        document_id=1,
        index_path=runtime.fts_path,
        manifest_path=runtime.fts_manifest_path,
        production_db_path=runtime.db_path,
    )
    assert first["removed_fragment_rows"] == 1
    assert first["already_absent"] is False
    assert second["removed_fragment_rows"] == 0
    assert second["already_absent"] is True
    with sqlite3.connect(runtime.fts_path) as connection:
        assert connection.execute(
            "SELECT document_id FROM retrieval_fragments ORDER BY document_id"
        ).fetchall() == [(2,), (3,), (4,), (5,)]


def test_scoped_fts_cleanup_waits_for_coordinated_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _harness = _sequential_runtime(tmp_path, monkeypatch)
    reader = connect_readonly_index(runtime.fts_path)
    reader.execute("BEGIN")
    assert reader.execute("SELECT COUNT(*) FROM retrieval_fragments").fetchone()[0] == 5
    outcome: dict[str, object] = {}

    def cleanup() -> None:
        try:
            outcome["result"] = fts_index_service.cleanup_document_retrieval_fts(
                document_id=1,
                index_path=runtime.fts_path,
                manifest_path=runtime.fts_manifest_path,
                production_db_path=runtime.db_path,
            )
        except Exception as exc:  # pragma: no cover - asserted through outcome
            outcome["error"] = exc

    worker = threading.Thread(target=cleanup)
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive()
    reader.close()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["result"]["status"] == "ready"


def test_zotero_note_linked_only_by_object_id_is_preserved_and_detached(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute(
        "INSERT INTO zotero_inspiration_notes VALUES (62, NULL, NULL, '[]', '[31]', 'matched', 'aligned')"
    )
    connection.commit()
    connection.close()

    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    assert preview["zotero_note_count"] == 2
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "completed"
    connection = sqlite3.connect(runtime.db_path)
    row = connection.execute(
        "SELECT matched_object_ids_json, match_status FROM zotero_inspiration_notes WHERE id=62"
    ).fetchone()
    connection.close()
    assert json.loads(row[0]) == []
    assert row[1] == "detached_document_deleted"


def test_stale_revision_and_wrong_confirmation_do_not_delete(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("UPDATE documents SET updated_at='changed' WHERE id=1")
    connection.commit()
    connection.close()
    with pytest.raises(document_deletion_service.DeletionError, match="变化") as stale:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token=preview["preview_token"],
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
    assert stale.value.error_code == "deletion_document_revision_stale"
    fresh = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    with pytest.raises(document_deletion_service.DeletionError) as confirmation:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token=fresh["preview_token"],
            expected_document_revision=fresh["document_revision"],
            confirmation_text="wrong",
            runtime=runtime,
        )
    assert confirmation.value.error_code == "deletion_confirmation_invalid"
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 1


def test_write_lock_recheck_rejects_change_after_recovery_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _runtime(tmp_path)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    create_recovery_package = document_deletion_service._create_recovery_package

    def create_then_mutate(**kwargs):
        archive_dir = create_recovery_package(**kwargs)
        connection = sqlite3.connect(runtime.db_path)
        connection.execute("UPDATE knowledge_chunks SET content_hash='changed-after-preview' WHERE id=101")
        connection.commit()
        connection.close()
        return archive_dir

    monkeypatch.setattr(
        document_deletion_service,
        "_create_recovery_package",
        create_then_mutate,
    )
    with pytest.raises(document_deletion_service.DeletionError) as stale:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token=preview["preview_token"],
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
    assert stale.value.error_code == "deletion_preview_stale_after_write_lock"
    assert harness.cleanup_calls == 0
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 1


def test_shared_objects_are_reported_and_user_comments_block(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("INSERT INTO object_candidates VALUES (32, 2, 'exclusive-key', '', NULL, NULL, '[]', '[]')")
    connection.commit()
    connection.close()
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    assert preview["shared_object_count"] == 1
    assert "shared_objects_will_be_preserved" in preview["warnings"]
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("UPDATE object_candidates SET user_comment='keep me' WHERE id=31")
    connection.commit()
    connection.close()
    blocked = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    assert blocked["whether_safe_to_delete"] is False
    assert {item["code"] for item in blocked["deletion_blockers"]} >= {"object_user_comment_requires_manual_preservation"}


def test_database_failure_rolls_back_before_vector_or_file_cleanup(tmp_path: Path) -> None:
    runtime, harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("CREATE TRIGGER block_document_delete BEFORE DELETE ON documents BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    connection.commit()
    connection.close()
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    with pytest.raises(document_deletion_service.DeletionError) as error:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token=preview["preview_token"],
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
    assert error.value.error_code == "deletion_transaction_rolled_back"
    assert harness.cleanup_calls == 0
    assert (runtime.data_dir / "converted_md" / "book.md").is_file()
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 1


def test_recovery_package_failure_stops_before_database_or_vector_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _runtime(tmp_path)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)

    def fail_recovery(**_kwargs):
        raise document_deletion_service.DeletionError(
            "deletion_recovery_package_failed",
            "isolated recovery failure",
        )

    monkeypatch.setattr(document_deletion_service, "_create_recovery_package", fail_recovery)
    with pytest.raises(document_deletion_service.DeletionError) as failure:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token=preview["preview_token"],
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
    assert failure.value.error_code == "deletion_recovery_package_failed"
    assert harness.cleanup_calls == 0
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 1


def test_post_commit_vector_failure_is_cleanup_incomplete(tmp_path: Path) -> None:
    harness = _VectorHarness()

    def fail_vectors(**_kwargs):
        raise RuntimeError("isolated vector failure")

    runtime, _ = _runtime(tmp_path, vector=harness, cleanup_vectors=fail_vectors)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "cleanup_incomplete"
    assert result["error_code"] == "deletion_cleanup_incomplete"
    assert any(item["error_code"] == "vector_cleanup_failed" for item in result["remediation"])
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 0


def test_post_commit_file_failure_is_cleanup_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, harness = _runtime(tmp_path)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)

    def fail_files(_plan):
        raise OSError("isolated file cleanup failure")

    monkeypatch.setattr(document_deletion_service, "_cleanup_files", fail_files)
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "cleanup_incomplete"
    assert result["error_code"] == "deletion_cleanup_incomplete"
    assert any(item["error_code"] == "file_cleanup_failed" for item in result["remediation"])
    assert harness.cleanup_calls == 1
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 0


def test_local_mutation_security_blocks_public_origin_and_accepts_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app, client=("127.0.0.1", 50000))
    public = client.post(
        "/api/v1/library/management/mutation-session",
        headers={"host": "127.0.0.1:8000", "origin": "https://public.example"},
    )
    assert public.status_code == 403
    session = client.post(
        "/api/v1/library/management/mutation-session",
        headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:5173"},
    )
    assert session.status_code == 200
    token = session.json()["mutation_token"]
    monkeypatch.setattr(
        book_archive_service,
        "archive_documents",
        lambda ids: {"status": "ok", "document_ids": ids},
    )
    archived = client.post(
        "/api/v1/library/management/archive",
        headers={
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:5173",
            "x-search-mutation-token": token,
        },
        json={"document_ids": [1]},
    )
    assert archived.status_code == 200
    forwarded = client.post(
        "/api/v1/library/management/mutation-session",
        headers={
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:5173",
            "x-forwarded-for": "127.0.0.1",
        },
    )
    assert forwarded.status_code == 403
    assert forwarded.json()["detail"]["error_code"] == "library_mutation_forwarded_request_forbidden"
    no_renderer_origin = client.get(
        "/api/v1/library/documents/1/deletion-preview",
        headers={"host": "127.0.0.1:8000"},
    )
    assert no_renderer_origin.status_code == 403
    assert no_renderer_origin.json()["detail"]["error_code"] == "library_mutation_renderer_origin_required"


def test_batch_schema_rejects_more_than_five_documents() -> None:
    from pydantic import ValidationError
    from app.schemas.library_deletion import ArchiveDocumentsRequest

    with pytest.raises(ValidationError):
        ArchiveDocumentsRequest(document_ids=[1, 2, 3, 4, 5, 6])


def test_missing_document_and_invalid_preview_token_are_rejected(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    with pytest.raises(document_deletion_service.DeletionError) as missing:
        document_deletion_service.create_deletion_preview(999, runtime=runtime)
    assert missing.value.error_code == "deletion_document_not_found"
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    with pytest.raises(document_deletion_service.DeletionError) as token:
        document_deletion_service.delete_document(
            document_id=1,
            preview_token="x" * 40,
            expected_document_revision=preview["document_revision"],
            confirmation_text="删除",
            runtime=runtime,
        )
    assert token.value.error_code == "deletion_preview_token_invalid_or_expired"


def test_search_review_artifacts_are_recovered_and_deleted_after_confirmation(
    tmp_path: Path,
) -> None:
    runtime, _harness = _runtime(tmp_path)

    connection = sqlite3.connect(runtime.db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE note_correction_reviews (
            review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL
        );
        CREATE TABLE note_correction_review_items (
            id INTEGER PRIMARY KEY,
            review_id TEXT NOT NULL
                REFERENCES note_correction_reviews(review_id)
                ON DELETE NO ACTION,
            payload TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO note_correction_reviews VALUES ('review-1', 1)"
    )
    connection.execute(
        "INSERT INTO note_correction_review_items"
        "(review_id, payload) VALUES ('review-1', 'fixture')"
    )
    connection.commit()
    connection.close()

    preview = document_deletion_service.create_deletion_preview(
        1,
        runtime=runtime,
    )

    assert preview["whether_safe_to_delete"] is True
    assert preview["search_review_artifact_count"] == 2
    assert preview["deletion_blockers"] == []
    assert (
        "search_review_artifacts_will_be_deleted"
        in preview["warnings"]
    )
    assert preview["retention"]["external_pdf"] == "always_preserved"
    assert preview["retention"]["personal_notes"] == "preserve_and_detach"
    assert preview["retention"]["zotero_data"] == "preserve_and_detach"

    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )

    assert result["status"] == "completed"

    connection = sqlite3.connect(runtime.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM note_correction_review_items"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM note_correction_reviews"
        ).fetchone()[0] == 0
        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    finally:
        connection.close()

    recovery_dir = (
        runtime.resolved_archive_root()
        / result["audit_id"]
    )
    recovered = json.loads(
        (recovery_dir / "database_rows.json").read_text(
            encoding="utf-8"
        )
    )["tables"]

    assert len(recovered["note_correction_reviews"]) == 1
    assert len(recovered["note_correction_review_items"]) == 1
    assert (
        recovered["note_correction_reviews"][0]["review_id"]
        == "review-1"
    )


def test_managed_pdf_is_deleted_only_when_explicitly_previewed(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    options = DeletionOptions(delete_managed_pdf=True)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime, deletion_options=options)
    assert preview["retention"]["managed_pdf"] == "delete"
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        deletion_options=options,
        runtime=runtime,
    )
    assert result["status"] == "completed"
    assert not (runtime.data_dir / "pdfs" / "book.pdf").exists()


def test_external_pdf_is_always_preserved(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    external_pdf = tmp_path / "external-book.pdf"
    external_pdf.write_bytes(b"%PDF-1.4 isolated external test")
    connection = sqlite3.connect(runtime.db_path)
    connection.execute(
        "UPDATE documents SET pdf_path=?, source_path=? WHERE id=1",
        (str(external_pdf), str(external_pdf)),
    )
    connection.commit()
    connection.close()
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    assert preview["pdf"]["scope"] == "external"
    assert preview["retention"]["external_pdf"] == "always_preserved"
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "completed"
    assert external_pdf.is_file()


def test_shared_object_row_and_vector_are_preserved(tmp_path: Path) -> None:
    runtime, harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("INSERT INTO object_candidates VALUES (32, 2, 'exclusive-key', '', NULL, NULL, '[]', '[]')")
    connection.commit()
    connection.close()

    def preserve_shared(*, passage_source_ids, affected_object_keys, store_path, manifest_path):
        harness.passages.difference_update(passage_source_ids)
        connection = sqlite3.connect(runtime.db_path)
        remaining = connection.execute("SELECT COUNT(*) FROM object_candidates WHERE object_key='exclusive-key'").fetchone()[0]
        connection.close()
        if not remaining:
            harness.objects.discard("object:exclusive-key")
        return {
            "status": "ok",
            "deleted_passage_vectors": 1,
            "deleted_object_vectors": 0,
            "updated_shared_object_vectors": 1,
        }

    runtime = replace(runtime, cleanup_vectors=preserve_shared)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "completed"
    connection = sqlite3.connect(runtime.db_path)
    assert connection.execute("SELECT COUNT(*) FROM object_candidates WHERE id=32 AND document_id=2").fetchone()[0] == 1
    connection.close()
    assert "object:exclusive-key" in harness.objects


def test_batch_preflight_rejects_object_overlap_before_any_delete(tmp_path: Path) -> None:
    runtime, _harness = _runtime(tmp_path)
    connection = sqlite3.connect(runtime.db_path)
    connection.execute("INSERT INTO object_candidates VALUES (32, 2, 'exclusive-key', '', NULL, NULL, '[]', '[]')")
    connection.commit()
    connection.close()
    first = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    second = document_deletion_service.create_deletion_preview(2, runtime=runtime)
    requests = [
        {
            "document_id": preview["document_id"],
            "preview_token": preview["preview_token"],
            "expected_document_revision": preview["document_revision"],
            "deletion_options": preview["deletion_options"],
        }
        for preview in (first, second)
    ]
    with pytest.raises(document_deletion_service.DeletionError) as overlap:
        document_deletion_service.delete_documents_batch(
            document_ids=[1, 2],
            requests=requests,
            confirmation_text="删除",
            runtime=runtime,
        )
    assert overlap.value.error_code == "deletion_batch_shared_object_overlap"
    assert sqlite3.connect(runtime.db_path).execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_cleanup_recovery_is_dry_run_by_default_and_can_be_applied(tmp_path: Path) -> None:
    harness = _VectorHarness()

    def fail_vectors(**_kwargs):
        raise RuntimeError("isolated vector failure")

    runtime, _ = _runtime(tmp_path, vector=harness, cleanup_vectors=fail_vectors)
    preview = document_deletion_service.create_deletion_preview(1, runtime=runtime)
    result = document_deletion_service.delete_document(
        document_id=1,
        preview_token=preview["preview_token"],
        expected_document_revision=preview["document_revision"],
        confirmation_text="删除",
        runtime=runtime,
    )
    assert result["status"] == "cleanup_incomplete"
    retry_runtime = replace(runtime, cleanup_vectors=harness.cleanup)
    dry_run = document_deletion_service.retry_incomplete_cleanup(
        result["audit_id"],
        runtime=retry_runtime,
    )
    assert dry_run["status"] == "ready_to_retry"
    assert dry_run["apply"] is False
    assert "chunk:1:101" in harness.passages
    applied = document_deletion_service.retry_incomplete_cleanup(
        result["audit_id"],
        apply=True,
        runtime=retry_runtime,
    )
    assert applied["status"] == "completed"
    assert "chunk:1:101" not in harness.passages
