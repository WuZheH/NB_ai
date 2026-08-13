from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations
from app.services.chat_pdf_production_import_service import (
    ChatPdfImportRuntime,
    import_document_to_production,
    import_staging_document_to_production,
)
from app.services.local_pdf_source_binding_service import (
    LocalPdfSourceBinding,
    record_document_source,
)


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


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                chunk_index INTEGER,
                chunk_text TEXT,
                heading_path TEXT,
                content_hash TEXT
            );
            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_trace_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def _binding(data: Path) -> LocalPdfSourceBinding:
    relative = "pdfs/chat_imports/fixture.pdf"
    managed = data / Path(relative)
    _write(managed, b"%PDF-1.4 generation fixture")
    digest = hashlib.sha256(managed.read_bytes()).hexdigest()
    revision = "b" * 64
    return LocalPdfSourceBinding(
        source_identity=f"local_pdf:sha256:{digest}",
        pdf_sha256=digest,
        source_revision_fingerprint=revision,
        managed_pdf_relative_path=relative,
        import_history={
            "previewed_at": "2026-08-09T00:00:00+00:00",
            "confirmed_at": "2026-08-09T00:01:00+00:00",
            "transaction_fingerprint": "c" * 64,
            "confirmation_token_fingerprint": "d" * 64,
            "source_revision_fingerprint": revision,
            "lifecycle_events": [
                "previewed",
                "confirmed",
                "transaction_started",
            ],
        },
    )


def _versioned_runtime(tmp_path: Path) -> tuple[ChatPdfImportRuntime, Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "research_memory.db"
    _database(database)

    legacy = tmp_path / "fixed-legacy"
    fts = legacy / generations.FTS_INDEX_NAME
    fts_manifest = legacy / generations.FTS_MANIFEST_NAME
    vectors = legacy / generations.VECTOR_STORE_NAME
    vector_manifest = legacy / generations.VECTOR_MANIFEST_NAME
    native = legacy / generations.NATIVE_NOTE_VECTOR_NAME
    _write(fts, b"legacy-fts")
    _write(
        fts_manifest,
        json.dumps(
            {"production_db_sha256": generations.sha256_file(database)}
        ).encode("utf-8"),
    )
    _write(vectors / "passage.lance" / "data.bin", b"legacy-vectors")
    _write(vector_manifest, b"{}\n")
    _write(native / "manifest.json", b"{}\n")

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

    def body(_job: str, _kind: str, binding: LocalPdfSourceBinding):
        with sqlite3.connect(database) as connection:
            cursor = connection.execute(
                "INSERT INTO documents(title, document_type) VALUES (?, ?)",
                ("Fixture", "paper"),
            )
            document_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO knowledge_chunks(
                    document_id, chunk_index, chunk_text, heading_path, content_hash
                ) VALUES (?, 0, 'fixture text', '', ?)
                """,
                (document_id, "e" * 64),
            )
            connection.commit()
        record_document_source(
            db_path=database,
            document_id=document_id,
            binding=binding,
        )
        return {
            "document_id": document_id,
            "title": "Fixture",
            "chunk_count": 1,
        }

    runtime = ChatPdfImportRuntime(
        database,
        data,
        fts,
        fts_manifest,
        vectors,
        vector_manifest,
        None,
        body,
        persistence_scope="production",
    )
    return runtime, database, legacy


def _install_generation_seams(
    monkeypatch,
    *,
    fail_sync: bool = False,
    fail_candidate_validation: bool = False,
    fail_active_validation: bool = False,
) -> dict[str, Path]:
    seen: dict[str, Path] = {}
    status_calls = 0

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes",
        lambda **_kwargs: {"note_count": 0, "evidence_link_count": 0},
    )

    def fts_sync(**kwargs):
        if fail_sync:
            raise RuntimeError("candidate sync failed")
        seen["fts"] = Path(kwargs["index_path"])
        Path(kwargs["index_path"]).write_bytes(b"candidate-fts")
        Path(kwargs["manifest_path"]).write_text(
            json.dumps(
                {
                    "production_db_sha256": generations.sha256_file(
                        Path(kwargs["research_db_path"])
                    )
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts",
        fts_sync,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_passage_sources",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_personal_note_sources",
        lambda **_kwargs: [],
    )

    def passage_sync(_ids, **kwargs):
        seen["vectors"] = Path(kwargs["store_path"])
        return {
            "scope": "affected_source_ids_only",
            "full_rebuild_allowed": False,
            "delete_orphans_allowed": False,
            "upserted_count": 0,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings",
        passage_sync,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_document_note_embeddings",
        lambda *args, **kwargs: {
            "scope": "document_only",
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
        },
    )

    def strict_state(**_kwargs):
        return {
            "status": "unavailable" if fail_candidate_validation else "ok",
            "missing_count": 1 if fail_candidate_validation else 0,
            "orphan_count": 0,
            "duplicate_count": 0,
            "stale_count": 0,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_passage_vector_state",
        strict_state,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_note_vector_state",
        strict_state,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._native_note_fragments_for_document",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.note_vector_index.inspect_zotero_note_vector_document_impact",
        lambda *args, **kwargs: {
            "status": "ready",
            "document_entry_count": 0,
            "fragment_ids": [],
        },
    )

    def fts_status(**_kwargs):
        nonlocal status_calls
        status_calls += 1
        # Preflight is call 1, candidate validation is call 2, and the
        # post-pointer active validation is call 3.
        if fail_active_validation and status_calls >= 3:
            return {"status": "broken", "ready": False}
        return {"status": "ready", "ready": True}

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._generation_fts_status",
        fts_status,
    )
    return seen


def test_production_local_pdf_uses_immutable_generation_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, database, legacy = _versioned_runtime(tmp_path)
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
    seen: dict[str, Path] = {}
    stages: list[str] = []

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._fts_status",
        lambda _runtime: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes",
        lambda **_kwargs: {"note_count": 0, "evidence_link_count": 0},
    )

    def fts_sync(**kwargs):
        seen["fts"] = Path(kwargs["index_path"])
        Path(kwargs["index_path"]).write_bytes(b"candidate-fts")
        Path(kwargs["manifest_path"]).write_text(
            json.dumps(
                {
                    "production_db_sha256": generations.sha256_file(
                        Path(kwargs["research_db_path"])
                    )
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts",
        fts_sync,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_passage_sources",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_personal_note_sources",
        lambda **_kwargs: [],
    )

    def passage_sync(_ids, **kwargs):
        seen["vectors"] = Path(kwargs["store_path"])
        return {
            "scope": "affected_source_ids_only",
            "full_rebuild_allowed": False,
            "delete_orphans_allowed": False,
            "upserted_count": 0,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings",
        passage_sync,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_document_note_embeddings",
        lambda *args, **kwargs: {
            "scope": "document_only",
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_passage_vector_state",
        lambda **_kwargs: {
            "status": "ok",
            "missing_count": 0,
            "orphan_count": 0,
            "duplicate_count": 0,
            "stale_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_note_vector_state",
        lambda **_kwargs: {
            "status": "ok",
            "missing_count": 0,
            "orphan_count": 0,
            "duplicate_count": 0,
            "stale_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._native_note_fragments_for_document",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.note_vector_index.inspect_zotero_note_vector_document_impact",
        lambda *args, **kwargs: {
            "status": "ready",
            "document_entry_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._generation_fts_status",
        lambda **_kwargs: {"status": "ready", "ready": True},
    )
    result = import_document_to_production(
        import_job_id="fixture",
        document_type="paper",
        source_binding=_binding(runtime.data_dir),
        allow_production=True,
        runtime=runtime,
        stage_callback=lambda stage, _metadata: stages.append(stage),
    )

    assert result["status"] == "completed"
    assert result["derived_index_publish_performed"] is True
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert generations.tree_fingerprint(previous.generation_dir) == before_previous_generation
    assert seen["fts"].is_relative_to(runtime.data_dir / generations.GENERATION_ROOT_NAME)
    assert seen["vectors"].is_relative_to(runtime.data_dir / generations.GENERATION_ROOT_NAME)
    assert seen["fts"] != runtime.fts_path
    assert seen["vectors"] != runtime.vector_store_path
    assert generations.sha256_file(database)
    assert stages == [
        "body_import_completed",
        "staging_snapshot_created",
        "staging_fts_started",
        "staging_fts_completed",
        "staging_vector_started",
        "staging_vector_completed",
        "derived_backup_started",
        "derived_backup_completed",
        "publish_started",
        "publish_completed",
        "final_verification_started",
        "final_verification_completed",
    ]


@pytest.mark.parametrize(
    "failure",
    ["body", "candidate_sync", "candidate_validation", "pointer_write", "active_validation"],
)
def test_production_local_pdf_failure_rolls_back_database_pointer_and_candidate(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    runtime, database, legacy = _versioned_runtime(tmp_path)
    binding = _binding(runtime.data_dir)
    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    generation_root = runtime.data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)
    before_legacy = generations.tree_fingerprint(legacy)
    _install_generation_seams(
        monkeypatch,
        fail_sync=failure == "candidate_sync",
        fail_candidate_validation=failure == "candidate_validation",
        fail_active_validation=failure == "active_validation",
    )

    if failure == "body":
        body = runtime.body_commit

        def failing_body(*args):
            body(*args)
            raise RuntimeError("body failed after write")

        runtime = replace(runtime, body_commit=failing_body)
    elif failure == "pointer_write":
        monkeypatch.setattr(
            generations,
            "publish_active_generation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("pointer write failed")
            ),
        )

    with pytest.raises(RuntimeError):
        import_document_to_production(
            import_job_id="fixture",
            document_type="paper",
            source_binding=binding,
            allow_production=True,
            runtime=runtime,
        )

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert not generations.activation_state_path(runtime.data_dir).exists()
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is False


def test_production_local_pdf_pointer_rollback_failure_is_durably_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, database, legacy = _versioned_runtime(tmp_path)
    binding = _binding(runtime.data_dir)
    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    before_legacy = generations.tree_fingerprint(legacy)
    _install_generation_seams(monkeypatch, fail_active_validation=True)

    def rollback_fails(*_args, **_kwargs):
        raise PermissionError("pointer rollback denied")

    session_class = mutations.ProductionGenerationMutationSession

    def session_factory(**kwargs):
        return session_class(
            **kwargs,
            pointer_restorer=rollback_fails,
        )

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.retrieval_generation_mutation_service.ProductionGenerationMutationSession",
        session_factory,
    )

    with pytest.raises(mutations.ProductionGenerationRollbackError) as caught:
        import_document_to_production(
            import_job_id="fixture",
            document_type="paper",
            source_binding=binding,
            allow_production=True,
            runtime=runtime,
        )

    assert caught.value.stage is mutations.MutationStage.DEGRADED
    assert database.read_bytes() != before_db
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    marker = json.loads(
        generations.activation_state_path(runtime.data_dir).read_text(encoding="utf-8")
    )
    assert marker["status"] in {"activating", "degraded"}
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is True
    with generations.PRODUCTION_GENERATION_COORDINATOR.write(allow_degraded=True):
        pass
    with pytest.raises(generations.RetrievalGenerationError) as read_error:
        generations.assert_activation_allows_read(data_dir=runtime.data_dir)
    assert read_error.value.safe_to_retry is False


def test_legacy_staging_commit_uses_generation_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    _write(fts, b"legacy-fts")
    _write(
        fts_manifest,
        json.dumps(
            {"production_db_sha256": generations.sha256_file(database)}
        ).encode("utf-8"),
    )
    _write(vectors / "passage.lance" / "data.bin", b"legacy-vectors")
    _write(vector_manifest, b"{}\n")
    _write(native / "manifest.json", b"{}\n")

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
    before_pointer = generations.read_active_pointer_bytes(data_dir=data)
    before_legacy = generations.tree_fingerprint(legacy)

    def body(_job: str, _kind: str, binding):
        assert binding is None
        with sqlite3.connect(database) as connection:
            cursor = connection.execute(
                "INSERT INTO documents(title, document_type) VALUES (?, ?)",
                ("Legacy Fixture", "paper"),
            )
            document_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO knowledge_chunks(
                    document_id, chunk_index, chunk_text, heading_path,
                    content_hash
                ) VALUES (?, 0, 'legacy text', '', ?)
                """,
                (document_id, "e" * 64),
            )
            connection.commit()
        return {
            "document_id": document_id,
            "title": "Legacy Fixture",
            "chunk_count": 1,
        }

    runtime = ChatPdfImportRuntime(
        database,
        data,
        fts,
        fts_manifest,
        vectors,
        vector_manifest,
        None,
        body,
        persistence_scope="production",
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes",
        lambda **_kwargs: {"note_count": 0, "evidence_link_count": 0},
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._native_note_fragments_for_document",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._generation_fts_status",
        lambda **_kwargs: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_passage_vector_state",
        lambda **_kwargs: {
            "status": "ok",
            "missing_count": 0,
            "orphan_count": 0,
            "duplicate_count": 0,
            "stale_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.inspect_document_note_vector_state",
        lambda **_kwargs: {
            "status": "ok",
            "missing_count": 0,
            "orphan_count": 0,
            "duplicate_count": 0,
            "stale_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.note_vector_index.inspect_zotero_note_vector_document_impact",
        lambda *args, **kwargs: {
            "status": "ready",
            "document_entry_count": 0,
            "fragment_ids": [],
        },
    )

    def fts_sync(**kwargs):
        Path(kwargs["index_path"]).write_bytes(b"candidate-fts")
        Path(kwargs["manifest_path"]).write_text(
            json.dumps(
                {
                    "production_db_sha256": generations.sha256_file(
                        Path(kwargs["research_db_path"])
                    )
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        }

    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts",
        fts_sync,
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_passage_sources",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.collect_personal_note_sources",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings",
        lambda *_args, **_kwargs: {
            "scope": "affected_source_ids_only",
            "full_rebuild_allowed": False,
            "delete_orphans_allowed": False,
            "upserted_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service.vector_store_service.sync_document_note_embeddings",
        lambda *args, **kwargs: {
            "scope": "document_only",
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
        },
    )

    result = import_staging_document_to_production(
        import_job_id="legacy-job",
        document_type="paper",
        allow_production=True,
        runtime=runtime,
    )

    assert result["status"] == "completed"
    assert result["derived_index_publish_performed"] is True
    assert result["generation_id"] and result["generation_id"] != "g-old"
    assert generations.read_active_pointer_bytes(data_dir=data) != before_pointer
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert not generations.activation_state_path(data).exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 1


def test_final_journal_stage_failure_happens_before_activation_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, database, legacy = _versioned_runtime(tmp_path)
    binding = _binding(runtime.data_dir)
    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=runtime.data_dir)
    generation_root = runtime.data_dir / generations.GENERATION_ROOT_NAME
    before_generations = generations.tree_fingerprint(generation_root)
    before_legacy = generations.tree_fingerprint(legacy)
    _install_generation_seams(monkeypatch)

    def callback(stage: str, _metadata: dict) -> None:
        if stage == "final_verification_completed":
            raise RuntimeError("journal unavailable")

    with pytest.raises(RuntimeError, match="journal unavailable"):
        import_document_to_production(
            import_job_id="fixture",
            document_type="paper",
            source_binding=binding,
            allow_production=True,
            runtime=runtime,
            stage_callback=callback,
        )

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=runtime.data_dir) == before_pointer
    assert generations.tree_fingerprint(generation_root) == before_generations
    assert generations.tree_fingerprint(legacy) == before_legacy
    assert not generations.activation_state_path(runtime.data_dir).exists()
