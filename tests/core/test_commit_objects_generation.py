from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid5

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.paths import OUTPUTS_DIR
from app.domains.retrieval import result_contracts
from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalFragment,
)
from app.services import object_semantic_search_service
from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations
from app.services import vector_store_service
from app.services import commit_objects_service as objects_commit
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
        content_hash=hashlib_sha256(text),
        adapter_version="test.v1",
    )


def hashlib_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _insert_object_candidate(
    connection: sqlite3.Connection,
    *,
    row_id: int,
    job_id: str,
    key: str,
    name: str,
    status: str = "candidate",
    now: str = "2026-08-10T00:00:00+00:00",
) -> None:
    connection.execute(
        "INSERT INTO object_candidates ("
        "id, document_id, import_job_id, object_key, object_name, object_type, "
        "review_status, status, confidence, aliases_json, description, "
        "topic_tags_json, problem_tags_json, mechanism_tags_json, "
        "inspiration_tags_json, evidence_refs_json, note_refs_json, "
        "source_note_ids_json, mapping_status, mapped_chunk_ids_json, "
        "warnings_json, created_by, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            1,
            job_id,
            key,
            name,
            "mechanism",
            "accepted",
            status,
            "medium",
            "[]",
            None,
            "[]",
            "[]",
            "[]",
            "[]",
            "[]",
            "[]",
            "[]",
            "not_mapped",
            "[]",
            "[]",
            "user_reviewed",
            now,
            now,
        ),
    )


def _database(path: Path) -> None:
    import app.models  # noqa: F401 - registers all model tables
    from app.db.session import Base

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        now = "2026-08-10T00:00:00+00:00"
        connection.execute(
            "INSERT INTO documents (id, title, document_type, content_layer, read_status, created_at, updated_at, object_import_mode) VALUES (1, 'Book 1', 'book', 'source', 'read', ?, ?, 'chaptered')",
            (now, now),
        )
        connection.execute(
            "INSERT INTO knowledge_chunks (id, document_id, chunk_index, heading_path, chunk_text, content_hash, pdf_page_start, pdf_page_end, created_at, updated_at) VALUES (101, 1, 0, 'H', 'chunk one', 'hash-1', 1, 1, ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO personal_notes (id, document_id, note_type, title, content, created_at, updated_at) VALUES (1, 1, 'personal', 'note', 'private', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO note_evidence_links (id, note_id, chunk_id, link_type, created_by, created_at) VALUES (1, 1, 101, 'quote', 'test', ?)",
            (now,),
        )
        _insert_object_candidate(
            connection,
            row_id=1,
            job_id="unrelated-job",
            key="historical-drift-key",
            name="Historical",
        )
        connection.commit()


def _passage_record(document_id: int, chunk_id: int) -> dict:
    from types import SimpleNamespace

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
        pdf_page_start=1,
        pdf_page_end=1,
        chapter_id=None,
        updated_at=None,
        _vector_chapter_title="",
    )
    return vector_store_service.build_passage_schema_record(document, chunk)


def _versioned_fixture(
    tmp_path: Path,
    after_database: object | None = None,
) -> dict[str, Path]:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "research_memory.db"
    _database(database)
    if callable(after_database):
        after_database(database)

    legacy = tmp_path / "fixed-legacy"
    legacy.mkdir(parents=True)
    fts = legacy / generations.FTS_INDEX_NAME
    fts_manifest = legacy / generations.FTS_MANIFEST_NAME
    vectors = legacy / generations.VECTOR_STORE_NAME
    vector_manifest = legacy / generations.VECTOR_MANIFEST_NAME
    native = legacy / generations.NATIVE_NOTE_VECTOR_NAME
    fts_index_service._build_database(fts, [_retrieval_fragment(1)])
    fts_manifest.write_text(
        json.dumps(
            {
                "production_db_sha256": generations.sha256_file(database),
                "fragment_count": 1,
                "index_content_hash": generations.sha256_file(fts),
            }
        ),
        encoding="utf-8",
    )
    vectors.mkdir(parents=True)
    store = vector_store_service.open_vector_store(vectors)
    store.create_table(
        vector_store_service.PASSAGE_TABLE,
        data=[_passage_record(1, 101)],
        mode="create",
    )
    vector_manifest.write_text("{}\n", encoding="utf-8")
    native.mkdir(parents=True)

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
    return {
        "database": database,
        "legacy": legacy,
        "data_dir": data,
    }


def _new_job_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _job_dir(job_id: str) -> Path:
    job_dir = OUTPUTS_DIR / "import_staging" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _write_job_files(job_id: str, *, reviewed: bool = False) -> Path:
    job_dir = _job_dir(job_id)
    (job_dir / "commit_result.json").write_text(
        json.dumps({"document_id": 1, "title": "Book 1"}),
        encoding="utf-8",
    )
    (job_dir / "reviewed_object_tag_package.json").write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "object_key": "mdm",
                        "object_name": "MDM 机制",
                        "object_type": "mechanism",
                        "review_status": "accepted",
                        "confidence": "medium",
                        "aliases": ["MDM"],
                        "topic_tags": [],
                        "problem_tags": [],
                        "mechanism_tags": [],
                        "inspiration_tags": [],
                        "evidence_refs": [],
                        "source_note_ids": [],
                        "description": "mechanism description",
                        "user_comment": "",
                        "warnings": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    if reviewed:
        (job_dir / "object_evidence_remap_preview.json").write_text(
            json.dumps(
                {
                    "objects": [
                        {
                            "object_key": "mdm",
                            "mapped_chunk_ids": [101],
                            "mapping_status": "mapped",
                            "warnings": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    return job_dir


def _install_seams(
    monkeypatch,
    *,
    database: Path,
    fail_sync: bool = False,
    fail_validation: bool = False,
    fail_active_validation: bool = False,
) -> None:
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(objects_commit, "SessionLocal", test_session)
    monkeypatch.setattr(
        object_semantic_search_service,
        "SessionLocal",
        test_session,
    )
    from app.services import object_candidate_service

    monkeypatch.setattr(
        object_candidate_service,
        "SessionLocal",
        test_session,
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda config: object(),
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_encode_text",
        lambda _model, text: [0.1, 0.2, 0.3],
    )
    monkeypatch.setattr(
        vector_store_service,
        "_active_embedding_model_path",
        lambda: "isolated-model",
    )

    original_sync = vector_store_service.sync_affected_object_embeddings

    def guarded_sync(*args, **kwargs):
        if fail_sync:
            raise RuntimeError("object sync failed")
        return original_sync(*args, **kwargs)

    monkeypatch.setattr(
        vector_store_service,
        "sync_affected_object_embeddings",
        guarded_sync,
    )

    original_validate = objects_commit._strict_affected_object_validation
    validation_calls = 0

    def guarded_validate(**kwargs):
        nonlocal validation_calls
        validation_calls += 1
        # Candidate validation is call 1, post-pointer active validation is
        # call 2.
        if (fail_validation and validation_calls == 1) or (
            fail_active_validation and validation_calls >= 2
        ):
            raise RuntimeError("object validation failed")
        return original_validate(**kwargs)

    monkeypatch.setattr(
        objects_commit,
        "_strict_affected_object_validation",
        guarded_validate,
    )


def test_commit_objects_uses_generation_and_preserves_unrelated_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    legacy = fixture["legacy"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_commit_objects")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_legacy = generations.tree_fingerprint(legacy)
    previous = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    before_previous_generation = generations.tree_fingerprint(
        previous.generation_dir
    )

    result = objects_commit.commit_objects_to_production_with_generation(job_id, db_path=database, data_dir=data_dir)

    assert result["status"] == "committed"
    assert result["derived_index_publish_performed"] is True
    assert result["generation_id"] and result["generation_id"] != "g-old"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert (
        generations.tree_fingerprint(previous.generation_dir)
        == before_previous_generation
    )
    assert not generations.activation_state_path(data_dir).exists()

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    assert active.generation_id == result["generation_id"]
    state = vector_store_service.inspect_affected_object_vector_state(
        object_keys=["mdm"],
        expected_sources=[
            source
            for source in vector_store_service.collect_object_sources()
            if source["source_id"] == "object:mdm"
        ],
        store_path=active.vector_store_path,
    )
    assert state["status"] == "ok"
    assert state["missing_count"] == 0
    assert state["duplicate_count"] == 0
    assert state["stale_count"] == 0

    generation_root = data_dir / generations.GENERATION_ROOT_NAME
    assert [
        entry for entry in generation_root.iterdir() if entry.name.startswith(".c-")
    ] == []


def test_commit_objects_second_call_is_stable_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_commit_objects_idem")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_objects_to_production_with_generation(job_id, db_path=database, data_dir=data_dir)
    assert first["status"] == "committed"

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()
    before_rows = _object_rows(database, job_id)

    second = objects_commit.commit_objects_to_production_with_generation(job_id, db_path=database, data_dir=data_dir)

    assert second["status"] == "already_committed"
    assert second["core_db_write_performed"] is False
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db
    assert _object_rows(database, job_id) == before_rows
    assert not generations.activation_state_path(data_dir).exists()


def _object_rows(database: Path, job_id: str) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT object_key, status FROM object_candidates "
                "WHERE import_job_id = ? ORDER BY object_key",
                (job_id,),
            ).fetchall()
        ]


def test_commit_reviewed_objects_deprecates_affected_and_keeps_unrelated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def seed_deprecated(database: Path) -> None:
        with sqlite3.connect(database) as connection:
            _insert_object_candidate(
                connection,
                row_id=2,
                job_id=job_id,
                key="old-deprecated-key",
                name="Old",
            )
            connection.commit()

    job_id = _new_job_id("job_commit_reviewed")
    fixture = _versioned_fixture(tmp_path, after_database=seed_deprecated)
    database = fixture["database"]
    legacy = fixture["legacy"]
    data_dir = fixture["data_dir"]
    _write_job_files(job_id, reviewed=True)
    _install_seams(monkeypatch, database=database)

    before_legacy = generations.tree_fingerprint(legacy)
    result = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )

    assert result["status"] == "committed"
    assert result["deprecated_count"] == 1
    assert result["inserted_count"] == 1
    assert generations.tree_fingerprint(legacy) == before_legacy

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    expected = [
        source
        for source in vector_store_service.collect_object_sources()
        if source["source_id"]
        in {"object:mdm", "object:old-deprecated-key"}
    ]
    state = vector_store_service.inspect_affected_object_vector_state(
        object_keys=["mdm", "old-deprecated-key"],
        expected_sources=expected,
        store_path=active.vector_store_path,
    )
    assert state["status"] == "ok"
    assert state["missing_count"] == 0
    assert state["removed_but_present_count"] == 0

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status FROM object_candidates "
            "WHERE import_job_id = ? AND object_key = 'old-deprecated-key'",
            (job_id,),
        ).fetchone()
    assert row[0] == "deprecated"

    with sqlite3.connect(database) as connection:
        unrelated = connection.execute(
            "SELECT status, object_key FROM object_candidates "
            "WHERE import_job_id = 'unrelated-job'"
        ).fetchone()
    assert unrelated[0] == "candidate"
    assert unrelated[1] == "historical-drift-key"


@pytest.mark.parametrize(
    "failure",
    ["body", "sync", "validation", "pointer_write"],
)
def test_commit_objects_failure_rolls_back_database_pointer_and_candidate(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    legacy = fixture["legacy"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_commit_objects_fail")
    _write_job_files(job_id)
    _install_seams(
        monkeypatch,
        database=database,
        fail_sync=failure == "sync",
        fail_validation=failure == "validation",
    )

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    generation_root = data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)
    before_legacy = generations.tree_fingerprint(legacy)

    if failure == "body":
        original = objects_commit.commit_objects_from_staging

        def failing_body(job_id, *, persist_result=True):
            original(job_id, persist_result=persist_result)
            raise RuntimeError("body failed after write")

        monkeypatch.setattr(
            objects_commit,
            "commit_objects_from_staging",
            failing_body,
        )
    elif failure == "pointer_write":
        monkeypatch.setattr(
            generations,
            "publish_active_generation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("pointer write failed")
            ),
        )

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(job_id, db_path=database, data_dir=data_dir)

    assert any(
        code in str(error.value)
        for code in (
            "object_commit_transaction_rolled_back",
            "object_commit_generation_rollback_failed",
        )
    )
    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert not generations.activation_state_path(data_dir).exists()
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is False
    assert _object_rows(database, job_id) == []


def test_commit_objects_pointer_rollback_failure_is_durably_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    legacy = fixture["legacy"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_commit_objects_rollback")
    _write_job_files(job_id)
    _install_seams(
        monkeypatch,
        database=database,
        fail_active_validation=True,
    )

    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
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
        "app.services.commit_objects_service.retrieval_generation_mutation_service.ProductionGenerationMutationSession",
        session_factory,
    )

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(job_id, db_path=database, data_dir=data_dir)

    assert "object_commit_generation_rollback_failed" in str(error.value)
    assert database.read_bytes() != before_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    marker = json.loads(
        generations.activation_state_path(data_dir).read_text(encoding="utf-8")
    )
    assert marker["status"] in {"activating", "degraded"}
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is True
    with pytest.raises(generations.RetrievalGenerationError) as read_error:
        generations.assert_activation_allows_read(data_dir=data_dir)
    assert read_error.value.safe_to_retry is False



