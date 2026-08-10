from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

from app.domains.retrieval import result_contracts
from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalFragment,
)
from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations
from app.services import vector_store_service
from app.services.library import document_deletion_service as deletion
from app.services.retrieval import fts_index_service


@pytest.fixture(autouse=True)
def isolate_generation_coordinator(monkeypatch):
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(
        generations,
        "PRODUCTION_GENERATION_COORDINATOR",
        coordinator,
    )
    token = generations._PINNED_GENERATION.set(None)
    try:
        yield
    finally:
        generations._PINNED_GENERATION.reset(token)


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


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                content_layer TEXT,
                object_import_mode TEXT,
                source_path TEXT,
                pdf_path TEXT,
                zotero_key TEXT,
                created_at TEXT,
                read_status TEXT
            );

            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                node_id INTEGER,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding_id TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER,
                updated_at TEXT
            );

            CREATE TABLE markdown_nodes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                parent_id INTEGER,
                heading_level INTEGER,
                heading_title TEXT,
                heading_path TEXT,
                order_index INTEGER,
                raw_content TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE book_chapters (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                title TEXT
            );

            CREATE TABLE object_candidates (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                chapter_id INTEGER,
                import_job_id VARCHAR(255) NOT NULL,
                object_key VARCHAR(255) NOT NULL,
                object_name VARCHAR(512) NOT NULL,
                object_type VARCHAR(64) NOT NULL,
                review_status VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'candidate',
                confidence VARCHAR(16),
                aliases_json TEXT NOT NULL DEFAULT '[]',
                description TEXT,
                topic_tags_json TEXT NOT NULL DEFAULT '[]',
                problem_tags_json TEXT NOT NULL DEFAULT '[]',
                mechanism_tags_json TEXT NOT NULL DEFAULT '[]',
                inspiration_tags_json TEXT NOT NULL DEFAULT '[]',
                evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                note_refs_json TEXT NOT NULL DEFAULT '[]',
                source_note_ids_json TEXT NOT NULL DEFAULT '[]',
                source_origin VARCHAR(64),
                necessity_judgment VARCHAR(64),
                importance_score VARCHAR(32),
                source_package_path TEXT,
                source_import_manifest_path TEXT,
                mapping_status VARCHAR(32) NOT NULL DEFAULT 'not_mapped',
                mapped_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                user_comment TEXT,
                created_by VARCHAR(64) NOT NULL DEFAULT 'user_reviewed',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );

            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                note_type VARCHAR(64) NOT NULL,
                scope_type VARCHAR(64),
                scope_path TEXT,
                source_path TEXT,
                content_hash VARCHAR(64),
                title VARCHAR(512) NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );

            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL,
                link_type VARCHAR(64) NOT NULL,
                evidence_role VARCHAR(64),
                quote_text TEXT,
                confidence FLOAT,
                created_by VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL
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
                evidence_chunk_id INTEGER
            );

            CREATE TABLE inspiration_card_sources (
                id INTEGER PRIMARY KEY,
                source_doc_id INTEGER,
                source_chunk_id INTEGER
            );

            CREATE TABLE chunk_tags (
                id INTEGER PRIMARY KEY,
                chunk_id INTEGER NOT NULL
            );

            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                zotero_item_key TEXT,
                zotero_attachment_key TEXT,
                zotero_source_id TEXT,
                zotero_select_uri TEXT,
                zotero_open_pdf_uri TEXT,
                source_trace_json TEXT,
                pdf_path TEXT,
                created_at TEXT
            );
            """
        )
        now = "2026-08-09T00:00:00+00:00"
        connection.execute(
            "INSERT INTO documents VALUES (1, 'Book 1', 'book', 'source', 'chaptered', NULL, NULL, NULL, ?, 'read')",
            (now,),
        )
        connection.execute(
            "INSERT INTO documents VALUES (2, 'Book 2', 'book', 'source', 'chaptered', NULL, NULL, NULL, ?, 'read')",
            (now,),
        )
        connection.execute("INSERT INTO markdown_nodes VALUES (11, 1, NULL, 1, 'H', 'H', 0, 'raw', ?, ?)", (now, now))
        connection.execute("INSERT INTO markdown_nodes VALUES (12, 2, NULL, 1, 'H', 'H', 0, 'raw', ?, ?)", (now, now))
        connection.execute("INSERT INTO knowledge_chunks VALUES (101, 1, 11, 0, 'H', 'chunk one', 'hash-1', 'chunk:1:101', NULL, NULL, 21, ?)", (now,))
        connection.execute("INSERT INTO knowledge_chunks VALUES (102, 2, 12, 0, 'H', 'chunk two', 'hash-2', 'chunk:2:102', NULL, NULL, 22, ?)", (now,))
        connection.execute("INSERT INTO book_chapters VALUES (21, 1, 'C')")
        connection.execute("INSERT INTO book_chapters VALUES (22, 2, 'C')")
        now = "2026-08-10T00:00:00+00:00"
        connection.execute(
            "INSERT INTO object_candidates ("
            "id, document_id, import_job_id, object_key, object_name, "
            "object_type, review_status, status, aliases_json, "
            "evidence_refs_json, note_refs_json, source_note_ids_json, "
            "mapping_status, mapped_chunk_ids_json, warnings_json, "
            "created_by, created_at, updated_at"
            ") VALUES (31, 1, 'delete-job', 'exclusive-key', 'Exclusive', "
            "'mechanism', 'accepted', 'candidate', '[]', '[]', '[]', '[]', "
            "'not_mapped', '[101]', '[]', 'user_reviewed', ?, ?)",
            (now, now),
        )
        connection.execute("INSERT INTO personal_notes VALUES (41, 1, 'personal', NULL, NULL, NULL, NULL, 'note', 'private', NULL, ?, ?)", (now, now))
        connection.execute(
            "INSERT INTO note_evidence_links VALUES (51, 41, 101, 'quote', 'evidence', '', 1.0, 'test', ?)",
            (now,),
        )
        connection.execute("INSERT INTO zotero_inspiration_notes VALUES (61, 1, 101, '[101]', '[31]', 'matched', 'aligned')")
        connection.execute(
            "INSERT INTO document_sources VALUES (1, 1, 'local_pdf', NULL, NULL, NULL, NULL, NULL, '{}', NULL, ?)",
            (now,),
        )
        connection.commit()

    migration = __import__(
        "scripts.migrations.migrate_zotero_personal_notes_schema",
        fromlist=["migrate_database"],
    )
    result = migration.migrate_database(path, dry_run=False)
    assert result["status"] == "applied"


def _passage_record(document_id: int, chunk_id: int) -> dict:
    document = SimpleNamespace(
        id=document_id,
        title=f"Book {document_id}",
        document_type="book",
        object_import_mode="chaptered",
        read_status="read",
    )
    chunk = SimpleNamespace(
        id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        heading_path="H",
        chunk_text="chunk fixture",
        content_hash=f"hash-{document_id}",
        pdf_page_start=None,
        pdf_page_end=None,
        chapter_id=21 if document_id == 1 else 22,
        updated_at=None,
        _vector_chapter_title="C",
    )
    return vector_store_service.build_passage_schema_record(document, chunk)


def _versioned_fixture(
    tmp_path: Path,
    after_legacy_store: object | None = None,
) -> dict[str, Path]:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "research_memory.db"
    _database(database)

    legacy = tmp_path / "fixed-legacy"
    legacy.mkdir(parents=True)
    fts = legacy / generations.FTS_INDEX_NAME
    fts_manifest = legacy / generations.FTS_MANIFEST_NAME
    vectors = legacy / generations.VECTOR_STORE_NAME
    vector_manifest = legacy / generations.VECTOR_MANIFEST_NAME
    native = legacy / generations.NATIVE_NOTE_VECTOR_NAME
    fts_index_service._build_database(
        fts,
        [_retrieval_fragment(1), _retrieval_fragment(2)],
    )
    fts_manifest.write_text(
        json.dumps(
            {
                "production_db_sha256": generations.sha256_file(database),
                "fragment_count": 2,
                "index_content_hash": generations.sha256_file(fts),
            }
        ),
        encoding="utf-8",
    )
    vectors.mkdir(parents=True)
    store = vector_store_service.open_vector_store(vectors)
    store.create_table(
        vector_store_service.PASSAGE_TABLE,
        data=[
            _passage_record(1, 101),
            _passage_record(2, 102),
        ],
        mode="create",
    )
    vector_manifest.write_text("{}\n", encoding="utf-8")
    native.mkdir(parents=True)
    if callable(after_legacy_store):
        after_legacy_store(vectors)

    source = generations.RetrievalGenerationSnapshot(
        mode="legacy",
        generation_id=None,
        production_db_sha256=generations.sha256_file(database),
        fts_index_path=fts,
        fts_manifest_path=fts_manifest,
        vector_store_path=vectors,
        vector_manifest_path=vector_manifest,
        native_note_vector_path=native,
    )
    candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data,
        generation_id="g-old",
    )
    active = generations.finalize_candidate_generation(
        candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(active, data_dir=data)

    runtime = deletion.DeletionRuntime(
        db_path=database,
        data_dir=data,
        fts_path=fts,
        fts_manifest_path=fts_manifest,
        vector_store_path=vectors,
        vector_manifest_path=vector_manifest,
        archive_root=tmp_path / "archives",
        persistence_scope="production",
    )
    return {
        "runtime": runtime,
        "database": database,
        "legacy": legacy,
    }


def _test_cleanup_fts(*, document_id, index_path, manifest_path, production_db_path):
    """Mirror cleanup_document_retrieval_fts without the SEARCH_DATA_DIR assert."""
    connection = sqlite3.connect(index_path)
    try:
        row_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT row_id FROM retrieval_fragments "
                "WHERE document_id = ? ORDER BY row_id",
                (document_id,),
            )
        ]
        if row_ids:
            connection.execute(
                "DELETE FROM retrieval_fts_unicode WHERE rowid IN ("
                "SELECT row_id FROM retrieval_fragments WHERE document_id = ?)",
                (document_id,),
            )
            connection.execute(
                "DELETE FROM retrieval_fts_trigram WHERE rowid IN ("
                "SELECT row_id FROM retrieval_fragments WHERE document_id = ?)",
                (document_id,),
            )
            connection.execute(
                "DELETE FROM retrieval_fragments WHERE document_id = ?",
                (document_id,),
            )
        fragment_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM retrieval_fragments"
            ).fetchone()[0]
        )
        connection.commit()
    finally:
        connection.close()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    manifest.update(
        {
            "production_db_sha256": generations.sha256_file(
                Path(production_db_path)
            ),
            "fragment_count": fragment_count,
            "index_content_hash": generations.sha256_file(
                Path(index_path)
            ),
        }
    )
    Path(manifest_path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ready",
        "fragment_count": fragment_count,
    }


def _install_delete_seams(
    monkeypatch,
    *,
    fail_active_validation: bool = False,
) -> deletion.DeletionRuntime | None:
    status_calls = 0

    def fts_status(**_kwargs):
        nonlocal status_calls
        status_calls += 1
        # Candidate validation is call 1, post-pointer active validation is
        # call 2.
        if fail_active_validation and status_calls >= 2:
            return {"status": "broken", "ready": False}
        return {"status": "ready", "ready": True}

    monkeypatch.setattr(
        deletion,
        "_generation_fts_status",
        fts_status,
    )
    monkeypatch.setattr(
        deletion.vector_store_service,
        "collect_object_sources",
        lambda **kwargs: [],
    )


def _preview_and_delete(
    runtime: deletion.DeletionRuntime,
    *,
    document_id: int = 1,
) -> dict:
    preview = deletion.create_deletion_preview(
        document_id,
        runtime=runtime,
    )
    return deletion.delete_document(
        document_id=document_id,
        preview_token=str(preview["preview_token"]),
        expected_document_revision=str(preview["document_revision"]),
        confirmation_text=str(preview["title"]),
        runtime=runtime,
    )


def test_delete_preview_impact_reads_active_generation_not_frozen_legacy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    legacy = fixture["legacy"]
    _install_delete_seams(monkeypatch)

    # Freeze-legacy simulation: remove document 1 from the fixed legacy FTS
    # and passage store after the active generation was published.  The fixed
    # artifacts are intentionally stale; only the active generation still
    # reflects the document.
    with sqlite3.connect(legacy / generations.FTS_INDEX_NAME) as connection:
        connection.execute(
            "DELETE FROM retrieval_fragments WHERE document_id = 1"
        )
        connection.commit()
    legacy_store = vector_store_service.open_vector_store(
        legacy / generations.VECTOR_STORE_NAME
    )
    legacy_store.open_table(vector_store_service.PASSAGE_TABLE).delete(
        "document_id = 1"
    )

    preview = deletion.create_deletion_preview(1, runtime=runtime)

    assert preview["fts_row_count"] == 1
    assert preview["passage_vector_count"] == 1
    assert "fts_impact_unavailable" not in preview["warnings"]
    assert "vector_impact_unavailable" not in preview["warnings"]
    assert preview["manifest_index_impact"]["fts_rebuild_required"] is True
    active = generations.resolve_active_retrieval_generation(
        data_dir=runtime.data_dir,
        db_path=database,
    )
    assert runtime.fts_path != active.fts_index_path


def test_delete_strict_validation_blocks_dangling_zotero_notes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    data_dir = runtime.data_dir
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    generation_root = data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)

    monkeypatch.setattr(
        deletion,
        "_detach_zotero_notes",
        lambda _connection, _plan: 0,
    )

    with pytest.raises(deletion.DeletionError):
        _preview_and_delete(runtime)

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert not generations.activation_state_path(data_dir).exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = 1"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("row_sql", "params"),
    [
        ("(61, 1, NULL, '[]', '[]', 'matched', 'aligned')", ()),
        ("(61, NULL, 101, '[]', '[]', 'matched', 'aligned')", ()),
        ("(61, NULL, NULL, '[101]', '[]', 'matched', 'aligned')", ()),
        ("(61, NULL, NULL, '[]', '[31]', 'matched', 'aligned')", ()),
    ],
)
def test_delete_strict_validation_blocks_each_dangling_zotero_dimension(
    tmp_path: Path,
    monkeypatch,
    row_sql: str,
    params: tuple,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    data_dir = runtime.data_dir
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM zotero_inspiration_notes")
        connection.execute(
            "INSERT INTO zotero_inspiration_notes (id, matched_document_id, "
            "matched_chunk_id, matched_chunk_ids_json, matched_object_ids_json, "
            "match_status, evidence_alignment_status) VALUES " + row_sql,
            params,
        )
        connection.commit()

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    generation_root = data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)

    monkeypatch.setattr(
        deletion,
        "_detach_zotero_notes",
        lambda _connection, _plan: 0,
    )

    with pytest.raises(deletion.DeletionError):
        _preview_and_delete(runtime)

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert not generations.activation_state_path(data_dir).exists()


def test_delete_reconciles_legacy_case_variant_object_vectors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def seed_legacy_objects(vectors: Path) -> None:
        store = vector_store_service.open_vector_store(vectors)
        rows = [
            vector_store_service.build_object_schema_record(
                {
                    "object_key": "EXCLUSIVE-KEY",
                    "object_name": "Exclusive",
                    "object_type": "mechanism",
                    "document_id": 1,
                    "evidence_refs": [],
                    "top_documents": [{"document_id": 1, "title": "Book 1"}],
                }
            ),
            vector_store_service.build_object_schema_record(
                {
                    "object_key": "shared",
                    "object_name": "Shared",
                    "object_type": "concept",
                    "document_id": 2,
                    "evidence_refs": [],
                    "top_documents": [{"document_id": 2, "title": "Book 2"}],
                }
            ),
        ]
        store.create_table(
            vector_store_service.OBJECT_TABLE,
            data=rows,
            mode="create",
        )

    fixture = _versioned_fixture(
        tmp_path,
        after_legacy_store=seed_legacy_objects,
    )
    runtime = fixture["runtime"]
    database = fixture["database"]
    data_dir = runtime.data_dir
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    result = _preview_and_delete(runtime)

    assert result["status"] == "completed"
    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    active_db = vector_store_service.open_vector_store(active.vector_store_path)
    rows = active_db.open_table(
        vector_store_service.OBJECT_TABLE
    ).search().limit(100).to_list()
    assert [row["source_id"] for row in rows] == ["object:shared"]
    assert all(row["document_id"] == 2 for row in rows)


def test_explicit_production_delete_uses_generation_runtime(tmp_path: Path) -> None:
    runtime = deletion.DeletionRuntime(
        db_path=tmp_path / "data" / "db" / "research_memory.db",
        data_dir=tmp_path / "data",
        persistence_scope="production",
    )

    assert deletion._uses_production_generation(runtime) is True


def test_default_production_runtime_uses_generation_path() -> None:
    assert (
        deletion._uses_production_generation(deletion.DeletionRuntime())
        is True
    )


def test_temp_runtime_never_uses_generation_path(tmp_path: Path) -> None:
    runtime = deletion.DeletionRuntime(
        db_path=tmp_path / "db" / "research_memory.db",
        data_dir=tmp_path / "data",
        persistence_scope="temp",
    )
    assert deletion._uses_production_generation(runtime) is False


def test_production_delete_commits_through_generation_and_preserves_legacy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    legacy = fixture["legacy"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    before_legacy = generations.tree_fingerprint(legacy)
    previous = generations.resolve_active_retrieval_generation(
        data_dir=runtime.data_dir,
        db_path=database,
    )
    assert previous.generation_dir is not None
    before_previous_generation = generations.tree_fingerprint(
        previous.generation_dir
    )

    result = _preview_and_delete(runtime)

    assert result["status"] == "completed"
    assert result["derived_index_publish_performed"] is True
    assert result["generation_id"] and result["generation_id"] != "g-old"
    assert result["database"]["deleted_rows"] >= 1
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert (
        generations.tree_fingerprint(previous.generation_dir)
        == before_previous_generation
    )
    assert not generations.activation_state_path(runtime.data_dir).exists()

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = 1"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = 1"
        ).fetchone()[0] == 0

    active = generations.resolve_active_retrieval_generation(
        data_dir=runtime.data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    assert active.generation_id == result["generation_id"]
    with sqlite3.connect(active.fts_index_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_fragments WHERE document_id = 1"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM retrieval_fragments WHERE document_id = 2"
        ).fetchone()[0] == 1
    passage_state = vector_store_service.inspect_document_passage_vector_state(
        document_id=1,
        expected_sources=[],
        store_path=active.vector_store_path,
    )
    assert passage_state["status"] == "ok"
    assert passage_state["orphan_count"] == 0
    active_db = vector_store_service.open_vector_store(active.vector_store_path)
    table = active_db.open_table(vector_store_service.PASSAGE_TABLE)
    assert int(table.count_rows("document_id = 2")) == 1

    generation_root = runtime.data_dir / generations.GENERATION_ROOT_NAME
    candidates = [
        entry for entry in generation_root.iterdir() if entry.name.startswith(".c-")
    ]
    assert candidates == []
    assert result["orphan_scan"]["ok"] is True


@pytest.mark.parametrize(
    "failure",
    ["body", "fts", "passage_cleanup", "validation", "pointer_write"],
)
def test_production_delete_failure_rolls_back_database_pointer_and_candidate(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    legacy = fixture["legacy"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    generation_root = runtime.data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)
    before_legacy = generations.tree_fingerprint(legacy)

    if failure == "body":
        original = deletion._execute_database_transaction

        def failing_body(plan, *, runtime):
            original(plan, runtime=runtime)
            raise RuntimeError("db body failed after write")

        monkeypatch.setattr(
            deletion,
            "_execute_database_transaction",
            failing_body,
        )
    elif failure == "fts":
        runtime = replace(
            runtime,
            cleanup_fts=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("fts cleanup failed")
            ),
        )
    elif failure == "passage_cleanup":
        runtime = replace(
            runtime,
            cleanup_vectors=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("passage cleanup failed")
            ),
        )
    elif failure == "validation":
        def failing_validation(**_kwargs):
            raise RuntimeError("strict validation failed")

        monkeypatch.setattr(
            deletion,
            "_strict_delete_scope_validation",
            failing_validation,
        )
    elif failure == "pointer_write":
        monkeypatch.setattr(
            generations,
            "publish_active_generation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("pointer write failed")
            ),
        )

    with pytest.raises(deletion.DeletionError):
        _preview_and_delete(runtime)

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert not generations.activation_state_path(runtime.data_dir).exists()
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is False
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = 1"
        ).fetchone()[0] == 1


def test_production_delete_pointer_rollback_failure_is_durably_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    legacy = fixture["legacy"]
    _install_delete_seams(monkeypatch, fail_active_validation=True)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    before_legacy = generations.tree_fingerprint(legacy)

    def rollback_fails(*_args, **_kwargs):
        raise PermissionError("pointer rollback denied")

    session_class = mutations.ProductionGenerationMutationSession

    def session_factory(**kwargs):
        return session_class(
            **kwargs,
            pointer_restorer=rollback_fails,
        )

    monkeypatch.setattr(
        "app.services.library.document_deletion_service.retrieval_generation_mutation_service.ProductionGenerationMutationSession",
        session_factory,
    )

    with pytest.raises(deletion.DeletionError) as caught:
        _preview_and_delete(runtime)

    assert caught.value.error_code == "deletion_generation_rollback_failed"
    assert caught.value.details["rollback_completed"] is False
    assert caught.value.details["production_data_modified"] is True
    assert database.read_bytes() != before_db
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    marker = json.loads(
        generations.activation_state_path(runtime.data_dir).read_text(
            encoding="utf-8"
        )
    )
    assert marker["status"] in {"activating", "degraded"}
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is True
    with pytest.raises(generations.RetrievalGenerationError) as read_error:
        generations.assert_activation_allows_read(data_dir=runtime.data_dir)
    assert read_error.value.safe_to_retry is False


def test_production_delete_post_commit_file_failure_is_cleanup_incomplete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    runtime = fixture["runtime"]
    database = fixture["database"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    original_files = deletion._cleanup_files

    def failing_files(*_args, **_kwargs):
        raise PermissionError("file cleanup denied")

    monkeypatch.setattr(deletion, "_cleanup_files", failing_files)

    result = _preview_and_delete(runtime)

    assert result["status"] == "cleanup_incomplete"
    assert result["error_code"] == "deletion_cleanup_incomplete"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents WHERE id = 1"
        ).fetchone()[0] == 0
    active = generations.resolve_active_retrieval_generation(
        data_dir=runtime.data_dir,
        db_path=database,
    )
    assert active.generation_id != "g-old"
    assert not generations.activation_state_path(runtime.data_dir).exists()

    retry = deletion.retry_incomplete_cleanup(
        str(result["audit_id"]),
        apply=False,
        runtime=runtime,
    )
    assert retry["status"] == "ready_to_retry"
    monkeypatch.setattr(deletion, "_cleanup_files", original_files)
    applied = deletion.retry_incomplete_cleanup(
        str(result["audit_id"]),
        apply=True,
        runtime=runtime,
    )
    assert applied["status"] == "completed"
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir).decode(
        "utf-8"
    )
    active_after = generations.resolve_active_retrieval_generation(
        data_dir=runtime.data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    assert active_after.generation_id == active.generation_id
