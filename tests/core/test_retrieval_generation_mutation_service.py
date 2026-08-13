from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations


@pytest.fixture(autouse=True)
def isolate_generation_coordinator(monkeypatch: pytest.MonkeyPatch):
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(
        generations,
        "PRODUCTION_GENERATION_COORDINATOR",
        coordinator,
    )
    token = generations._PINNED_GENERATION.set(None)
    try:
        yield coordinator
    finally:
        generations._PINNED_GENERATION.reset(token)


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _legacy_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = tmp_path / "research_memory.db"
    database.write_bytes(b"database-old")

    fts_index = data_dir / "search_index" / generations.FTS_INDEX_NAME
    fts_manifest = data_dir / "search_index" / generations.FTS_MANIFEST_NAME
    vector_store = data_dir / "vector_store" / generations.VECTOR_STORE_NAME
    vector_manifest = data_dir / "vector_store" / generations.VECTOR_MANIFEST_NAME
    native_notes = data_dir / "vector_store" / generations.NATIVE_NOTE_VECTOR_NAME
    _write(fts_index, b"fts-old")
    _write(
        fts_manifest,
        json.dumps(
            {"production_db_sha256": generations.sha256_file(database)}
        ).encode("utf-8"),
    )
    _write(vector_store / "table.lance" / "data", b"vectors-old")
    _write(vector_manifest, b"{}\n")
    _write(native_notes / "manifest.json", b"{}\n")

    return data_dir, database


def _commit_session(
    session: mutations.ProductionGenerationMutationSession,
    database: Path,
) -> generations.RetrievalGenerationSnapshot:
    database.write_bytes(b"database-new")
    session.mark_body_db_mutated()
    session.capture_post_write_database()
    session.mark_candidate_synced()
    session.validate_candidate(lambda _candidate, _db_sha256: None)
    finalized = session.finalize_candidate()
    session.begin_activation()
    session.publish_active()
    session.verify_active(
        lambda snapshot: (
            snapshot.generation_id == finalized.generation_id
            or (_ for _ in ()).throw(AssertionError("stale generation"))
        )
    )
    session.clear_activation()
    return finalized


def test_successful_session_owns_complete_activation_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    with mutations.ProductionGenerationMutationSession(
        data_dir=data_dir,
        db_path=database,
        generation_id="g-success",
    ) as session:
        assert session.stage is mutations.MutationStage.PREPARED
        assert session.previous_generation.mode == "legacy"
        assert session.previous_pointer_bytes is None
        finalized = _commit_session(session, database)
        assert session.stage is mutations.MutationStage.ACTIVATION_CLEARED

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    assert active.generation_id == finalized.generation_id
    assert active.production_db_sha256 == generations.sha256_file(database)
    assert not generations.activation_state_path(data_dir).exists()
    assert session.stage_history == (
        mutations.MutationStage.PREPARE,
        mutations.MutationStage.PREPARED,
        mutations.MutationStage.BODY_DB_MUTATED,
        mutations.MutationStage.POST_WRITE_SNAPSHOT,
        mutations.MutationStage.CANDIDATE_SYNCED,
        mutations.MutationStage.CANDIDATE_VALIDATED,
        mutations.MutationStage.FINALIZED,
        mutations.MutationStage.ACTIVATING,
        mutations.MutationStage.POINTER_SWITCHED,
        mutations.MutationStage.POST_SWITCH_VERIFIED,
        mutations.MutationStage.ACTIVATION_CLEARED,
    )


def test_backup_failure_after_candidate_creation_leaves_no_candidate_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    def fail_backup(_path: Path) -> mutations.DatabaseRollbackSnapshot:
        assert any(generations.generation_root(data_dir).iterdir())
        raise OSError("backup failed")

    with pytest.raises(OSError, match="backup failed"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-backup-fail",
            rollback_snapshot_factory=fail_backup,
        ):
            raise AssertionError("unreachable")

    root = generations.generation_root(data_dir)
    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert database.read_bytes() == b"database-old"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) is None


def test_backup_failure_in_versioned_mode_preserves_only_active_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    with mutations.ProductionGenerationMutationSession(
        data_dir=data_dir,
        db_path=database,
        generation_id="g-active",
    ) as first:
        active = _commit_session(first, database)
    active_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    active_db = database.read_bytes()

    def fail_backup(_path: Path) -> mutations.DatabaseRollbackSnapshot:
        assert any(
            path.name.startswith(".c-")
            for path in generations.generation_root(data_dir).iterdir()
        )
        raise OSError("backup failed")

    with pytest.raises(OSError, match="backup failed"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-never-active",
            rollback_snapshot_factory=fail_backup,
        ):
            raise AssertionError("unreachable")

    assert database.read_bytes() == active_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == active_pointer
    entries = sorted(
        path.name for path in generations.generation_root(data_dir).iterdir()
    )
    assert entries == [active.generation_id]
    restored = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    assert restored.generation_id == active.generation_id


def test_failure_before_pointer_restores_database_and_removes_only_owned_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    source = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    previous_candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data_dir,
        generation_id="g-unrelated",
    )
    previous = generations.finalize_candidate_generation(
        previous_candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(previous, data_dir=data_dir)

    with pytest.raises(RuntimeError, match="candidate mutation failed"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-owned",
        ) as session:
            database.write_bytes(b"database-new")
            session.capture_post_write_database()
            raise RuntimeError("candidate mutation failed")

    assert database.read_bytes() == b"database-old"
    assert previous.generation_dir.is_dir()
    assert not (generations.generation_root(data_dir) / "g-owned").exists()
    assert not any(
        item.name.startswith(".c-")
        for item in generations.generation_root(data_dir).iterdir()
    )
    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    assert active.generation_id == previous.generation_id
    assert not generations.activation_state_path(data_dir).exists()
    assert session.stage is mutations.MutationStage.ROLLED_BACK


def test_pointer_rollback_precedes_database_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    events: list[str] = []
    real_pointer_restore = generations.restore_active_pointer
    real_db_restore = mutations.restore_database_rollback_snapshot

    def restore_pointer(payload: bytes | None, *, data_dir: Path) -> None:
        events.append("pointer")
        real_pointer_restore(payload, data_dir=data_dir)

    def restore_database(path: Path, snapshot: mutations.DatabaseRollbackSnapshot) -> None:
        events.append("database")
        real_db_restore(path, snapshot)

    with pytest.raises(RuntimeError, match="post-switch failure"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-rollback",
            pointer_restorer=restore_pointer,
            rollback_snapshot_restorer=restore_database,
        ) as session:
            database.write_bytes(b"database-new")
            session.capture_post_write_database()
            session.validate_candidate(lambda _candidate, _db_sha256: None)
            session.finalize_candidate()
            session.begin_activation()
            session.publish_active()
            raise RuntimeError("post-switch failure")

    assert events == ["pointer", "database"]
    assert database.read_bytes() == b"database-old"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) is None
    assert not generations.activation_state_path(data_dir).exists()
    assert list(generations.generation_root(data_dir).iterdir()) == []
    assert session.stage is mutations.MutationStage.ROLLED_BACK


def test_pointer_rollback_failure_keeps_new_database_and_generation_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    def fail_pointer_restore(_payload: bytes | None, *, data_dir: Path) -> None:
        raise PermissionError("pointer locked")

    with pytest.raises(mutations.ProductionGenerationRollbackError) as caught:
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-degraded",
            pointer_restorer=fail_pointer_restore,
        ) as session:
            finalized = _commit_session_through_pointer(session, database)
            raise RuntimeError("post-switch failure")

    assert caught.value.stage is mutations.MutationStage.DEGRADED
    assert database.read_bytes() == b"database-new"
    pointer = json.loads(
        generations.active_pointer_path(data_dir).read_text(encoding="utf-8")
    )
    assert pointer["generation_id"] == finalized.generation_id
    marker = json.loads(
        generations.activation_state_path(data_dir).read_text(encoding="utf-8")
    )
    assert marker["status"] == "degraded"
    assert marker["candidate_generation_id"] == finalized.generation_id
    assert finalized.generation_dir.is_dir()
    assert session.stage is mutations.MutationStage.DEGRADED
    assert generations.PRODUCTION_GENERATION_COORDINATOR.degraded is True
    assert generations.PRODUCTION_GENERATION_COORDINATOR._writer_thread is None


def test_pointer_restore_exception_after_exact_replace_continues_database_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    real_pointer_restore = generations.restore_active_pointer

    def restore_then_report_fsync_failure(
        payload: bytes | None,
        *,
        data_dir: Path,
    ) -> None:
        real_pointer_restore(payload, data_dir=data_dir)
        raise OSError("directory fsync failed after replace")

    with pytest.raises(RuntimeError, match="post-switch failure"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-post-replace-error",
            pointer_restorer=restore_then_report_fsync_failure,
        ) as session:
            _commit_session_through_pointer(session, database)
            raise RuntimeError("post-switch failure")

    assert database.read_bytes() == b"database-old"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) is None
    assert not generations.activation_state_path(data_dir).exists()
    assert list(generations.generation_root(data_dir).iterdir()) == []
    assert session.stage is mutations.MutationStage.ROLLED_BACK


def test_rollback_keeps_activation_marker_until_old_generation_is_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    real_resolve = generations.resolve_active_retrieval_generation
    observed_marker: list[bool] = []

    with pytest.raises(RuntimeError, match="post-switch failure"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-marker-order",
        ) as session:
            _commit_session_through_pointer(session, database)

            def resolve_during_rollback(*args, **kwargs):
                observed_marker.append(
                    generations.activation_state_path(data_dir).is_file()
                )
                return real_resolve(*args, **kwargs)

            monkeypatch.setattr(
                generations,
                "resolve_active_retrieval_generation",
                resolve_during_rollback,
            )
            raise RuntimeError("post-switch failure")

    assert observed_marker == [True]
    assert not generations.activation_state_path(data_dir).exists()
    assert session.stage is mutations.MutationStage.ROLLED_BACK


def test_database_rollback_failure_after_pointer_restore_stays_durably_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    def fail_database_restore(
        _path: Path,
        _snapshot: mutations.DatabaseRollbackSnapshot,
    ) -> None:
        raise PermissionError("database locked")

    with pytest.raises(mutations.ProductionGenerationRollbackError):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-db-rollback-fail",
            rollback_snapshot_restorer=fail_database_restore,
        ) as session:
            finalized = _commit_session_through_pointer(session, database)
            raise RuntimeError("post-switch failure")

    assert database.read_bytes() == b"database-new"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) is None
    marker = json.loads(
        generations.activation_state_path(data_dir).read_text(encoding="utf-8")
    )
    assert marker["status"] == "activating"
    assert marker["candidate_generation_id"] == finalized.generation_id
    assert finalized.generation_dir.is_dir()
    assert session.stage is mutations.MutationStage.DEGRADED
    assert generations.PRODUCTION_GENERATION_COORDINATOR._writer_thread is None


def test_exception_after_activation_clear_keeps_committed_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="receipt failed"):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-committed",
        ) as session:
            finalized = _commit_session(session, database)
            raise RuntimeError("receipt failed")

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
    )
    assert active.generation_id == finalized.generation_id
    assert database.read_bytes() == b"database-new"
    assert not generations.activation_state_path(data_dir).exists()
    assert session.stage is mutations.MutationStage.ACTIVATION_CLEARED
    assert session.rollback_snapshot is None
    assert generations.PRODUCTION_GENERATION_COORDINATOR._writer_thread is None


def _commit_session_through_pointer(
    session: mutations.ProductionGenerationMutationSession,
    database: Path,
) -> generations.RetrievalGenerationSnapshot:
    database.write_bytes(b"database-new")
    session.capture_post_write_database()
    session.validate_candidate(lambda _candidate, _db_sha256: None)
    finalized = session.finalize_candidate()
    session.begin_activation()
    session.publish_active()
    return finalized


def test_protocol_rejects_pointer_publish_before_durable_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)

    with pytest.raises(mutations.ProductionGenerationProtocolError):
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=database,
            generation_id="g-order",
        ) as session:
            database.write_bytes(b"database-new")
            session.capture_post_write_database()
            session.validate_candidate(lambda _candidate, _db_sha256: None)
            session.finalize_candidate()
            session.publish_active()

    assert database.read_bytes() == b"database-old"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) is None


def test_final_verifier_receives_explicit_new_generation_not_stale_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir, database = _legacy_runtime(tmp_path, monkeypatch)
    observed: list[str | None] = []

    with mutations.ProductionGenerationMutationSession(
        data_dir=data_dir,
        db_path=database,
        generation_id="g-explicit",
    ) as session:
        database.write_bytes(b"database-new")
        session.capture_post_write_database()
        session.validate_candidate(lambda _candidate, _db_sha256: None)
        finalized = session.finalize_candidate()
        session.begin_activation()
        session.publish_active()
        session.verify_active(
            lambda snapshot: observed.append(snapshot.generation_id)
        )
        session.clear_activation()

    assert observed == [finalized.generation_id]
