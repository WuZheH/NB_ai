from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import retrieval_generation_service as generations
from app.services import vector_store_service
from app.services import zotero_direction_b_import_service as direction_import
from app.domains.retrieval import note_vector_index
from app.services.retrieval import evidence_loader


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
        yield coordinator
    finally:
        generations._PINNED_GENERATION.reset(token)


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _source(tmp_path: Path) -> generations.RetrievalGenerationSnapshot:
    source = tmp_path / "legacy"
    _write(source / generations.FTS_INDEX_NAME, b"fts-old")
    _write(source / generations.FTS_MANIFEST_NAME, b"{}\n")
    _write(source / generations.VECTOR_STORE_NAME / "table.lance" / "data", b"vectors-old")
    _write(source / generations.VECTOR_MANIFEST_NAME, b"{}\n")
    _write(source / generations.NATIVE_NOTE_VECTOR_NAME / "manifest.json", b"{}\n")
    return generations.RetrievalGenerationSnapshot(
        mode="legacy",
        generation_id=None,
        production_db_sha256="0" * 64,
        fts_index_path=source / generations.FTS_INDEX_NAME,
        fts_manifest_path=source / generations.FTS_MANIFEST_NAME,
        vector_store_path=source / generations.VECTOR_STORE_NAME,
        vector_manifest_path=source / generations.VECTOR_MANIFEST_NAME,
        native_note_vector_path=source / generations.NATIVE_NOTE_VECTOR_NAME,
    )


def _db(tmp_path: Path, value: bytes = b"database") -> Path:
    path = tmp_path / "research_memory.db"
    path.write_bytes(value)
    return path


def _write_legacy_manifest(
    data: Path,
    database: Path,
    *,
    production_db_sha256: str | None = None,
) -> Path:
    manifest = data / "search_index" / generations.FTS_MANIFEST_NAME
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "production_db_sha256": production_db_sha256
                or generations.sha256_file(database)
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _ready_generation(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    source = _source(tmp_path)
    candidate = generations.prepare_candidate_generation(
        source, data_dir=data, generation_id="gen-1"
    )
    snapshot = generations.finalize_candidate_generation(
        candidate, production_db_sha256=generations.sha256_file(database)
    )
    return data, database, source, snapshot


def _next_generation(
    data: Path,
    database: Path,
    source: generations.RetrievalGenerationSnapshot,
    *,
    generation_id: str = "gen-2",
) -> generations.RetrievalGenerationSnapshot:
    candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data,
        generation_id=generation_id,
    )
    return generations.finalize_candidate_generation(
        candidate,
        production_db_sha256=generations.sha256_file(database),
    )


def test_active_pointer_absent_uses_legacy(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    _write_legacy_manifest(data, database)
    source = _source(tmp_path)
    monkeypatch.setattr(generations, "DEFAULT_INDEX_PATH", source.fts_index_path)
    monkeypatch.setattr(generations, "DEFAULT_MANIFEST_PATH", source.fts_manifest_path)
    monkeypatch.setattr(generations, "LANCEDB_DIR", source.vector_store_path)
    monkeypatch.setattr(generations, "VECTOR_STORE_DIR", source.vector_manifest_path.parent)

    result = generations.resolve_active_retrieval_generation(
        data_dir=data, db_path=database
    )

    assert result.mode == "legacy"
    assert result.generation_id is None
    assert result.production_db_sha256 == generations.sha256_file(database)


def test_active_pointer_absent_with_empty_generation_root_uses_legacy(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    _write_legacy_manifest(data, database)
    (data / generations.GENERATION_ROOT_NAME).mkdir()

    result = generations.resolve_active_retrieval_generation(
        data_dir=data,
        db_path=database,
    )

    assert result.mode == "legacy"
    assert result.production_db_sha256 == generations.sha256_file(database)


@pytest.mark.parametrize("entry_name", [".c-dead", "g-valid", "unknown-state"])
def test_active_pointer_absent_with_generation_state_fails_closed(
    tmp_path: Path,
    entry_name: str,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    _write_legacy_manifest(data, database)
    (data / generations.GENERATION_ROOT_NAME / entry_name).mkdir(parents=True)

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_invalid"
    assert caught.value.safe_to_retry is False


@pytest.mark.parametrize("generation_root", [False, True])
def test_active_pointer_absent_with_legacy_database_mismatch_fails_closed(
    tmp_path: Path,
    generation_root: bool,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path, b"database-new")
    _write_legacy_manifest(data, database, production_db_sha256="0" * 64)
    if generation_root:
        (data / generations.GENERATION_ROOT_NAME).mkdir()

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_database_revision_mismatch"
    assert caught.value.safe_to_retry is False


def test_first_activation_database_write_crash_fails_closed_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path, b"database-old")
    _write_legacy_manifest(data, database)
    source = _source(tmp_path)
    generations.prepare_candidate_generation(
        source,
        data_dir=data,
        generation_id="generation-1",
    )
    database.write_bytes(b"database-new")
    generations.invalidate_generation_validation_cache()
    _simulate_process_restart(monkeypatch)

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        with generations.production_read_generation(
            data_dir=data,
            db_path=database,
        ):
            pytest.fail("interrupted first activation must not expose legacy")

    assert caught.value.code == "active_index_invalid"
    assert caught.value.safe_to_retry is False


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"production_db_sha256":"invalid"}',
    ],
)
def test_active_pointer_absent_with_invalid_legacy_manifest_fails_closed(
    tmp_path: Path,
    payload: bytes,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    manifest = data / "search_index" / generations.FTS_MANIFEST_NAME
    _write(manifest, payload)

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_invalid"
    assert caught.value.safe_to_retry is False


def test_active_pointer_absent_with_missing_legacy_manifest_fails_closed(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_invalid"
    assert caught.value.safe_to_retry is False


def test_active_pointer_absent_with_legacy_manifest_symlink_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    real_manifest = tmp_path / "legacy-manifest.json"
    real_manifest.write_text(
        json.dumps({"production_db_sha256": generations.sha256_file(database)}),
        encoding="utf-8",
    )
    manifest = data / "search_index" / generations.FTS_MANIFEST_NAME
    manifest.parent.mkdir(parents=True)
    try:
        os.symlink(real_manifest, manifest)
    except OSError:
        manifest.write_bytes(real_manifest.read_bytes())
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == manifest else real_is_symlink(self),
        )

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_invalid"


@pytest.mark.parametrize("dangling", [False, True])
def test_active_pointer_symlink_fails_closed(
    tmp_path: Path,
    dangling: bool,
    monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    _write_legacy_manifest(data, database)
    target = tmp_path / "pointer-target.json"
    if not dangling:
        target.write_text("{}", encoding="utf-8")
    try:
        os.symlink(target, data / generations.ACTIVE_POINTER_NAME)
    except OSError:
        pointer = data / generations.ACTIVE_POINTER_NAME
        if not dangling:
            pointer.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == pointer else real_is_symlink(self),
        )

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(
            data_dir=data,
            db_path=database,
        )

    assert caught.value.code == "active_index_invalid"
    assert caught.value.safe_to_retry is False


def test_legacy_to_candidate_and_restart_resolution(tmp_path: Path) -> None:
    data, database, source, snapshot = _ready_generation(tmp_path)
    generations.publish_active_generation(snapshot, data_dir=data)

    resolved = generations.resolve_active_retrieval_generation(
        data_dir=data, db_path=database
    )

    assert resolved == snapshot
    assert source.fts_index_path.read_bytes() == b"fts-old"
    assert source.vector_store_path.joinpath("table.lance", "data").read_bytes() == b"vectors-old"


def test_versioned_database_revision_mismatch_still_fails_closed(
    tmp_path: Path,
) -> None:
    data, database, _, snapshot = _ready_generation(tmp_path)
    generations.publish_active_generation(snapshot, data_dir=data)
    database.write_bytes(b"database-new")
    generations.invalidate_generation_validation_cache()

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        with generations.production_read_generation(
            data_dir=data,
            db_path=database,
        ):
            pytest.fail("versioned database mismatch must not be readable")

    assert caught.value.code == "active_index_database_revision_mismatch"
    assert caught.value.safe_to_retry is False


def test_active_generation_to_next_candidate_does_not_modify_old(tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    old_fingerprint = generations.tree_fingerprint(first.generation_dir)
    second_candidate = generations.prepare_candidate_generation(
        first, data_dir=data, generation_id="gen-2"
    )
    second_candidate.fts_index_path.write_bytes(b"fts-new")
    second = generations.finalize_candidate_generation(
        second_candidate, production_db_sha256=generations.sha256_file(database)
    )
    generations.publish_active_generation(second, data_dir=data)

    assert generations.tree_fingerprint(first.generation_dir) == old_fingerprint
    assert generations.resolve_active_retrieval_generation(
        data_dir=data, db_path=database
    ).generation_id == "gen-2"


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"[]", b'{"schema_version":999}', b'{"schema_version":1,"generation_id":"../escape","production_db_sha256":"' + b"0" * 64 + b'"}'],
)
def test_existing_invalid_pointer_fails_closed(tmp_path: Path, payload: bytes) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    (data / generations.ACTIVE_POINTER_NAME).write_bytes(payload)

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.resolve_active_retrieval_generation(data_dir=data, db_path=database)

    assert caught.value.code == "active_index_invalid"


def test_absolute_and_symlink_generation_escape_rejected(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    pointer = {
        "schema_version": 1,
        "generation_id": str(tmp_path.resolve()),
        "production_db_sha256": generations.sha256_file(database),
    }
    (data / generations.ACTIVE_POINTER_NAME).write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(generations.RetrievalGenerationError):
        generations.resolve_active_retrieval_generation(data_dir=data, db_path=database)

    if hasattr(os, "symlink"):
        outside = tmp_path / "outside"
        outside.mkdir()
        root = data / generations.GENERATION_ROOT_NAME
        root.mkdir()
        try:
            os.symlink(outside, root / "gen-link", target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        pointer["generation_id"] = "gen-link"
        (data / generations.ACTIVE_POINTER_NAME).write_text(json.dumps(pointer), encoding="utf-8")
        with pytest.raises(generations.RetrievalGenerationError):
            generations.resolve_active_retrieval_generation(data_dir=data, db_path=database)


def test_pointer_write_failure_leaves_old_pointer_exact(monkeypatch, tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    old = generations.read_active_pointer_bytes(data_dir=data)
    candidate = generations.prepare_candidate_generation(first, data_dir=data, generation_id="gen-2")
    second = generations.finalize_candidate_generation(
        candidate, production_db_sha256=generations.sha256_file(database)
    )

    def fail_write(*args, **kwargs):
        raise PermissionError("write denied")

    monkeypatch.setattr(generations, "_write_json_fsync", fail_write)
    with pytest.raises(generations.ActivePointerPublishError) as caught:
        generations.publish_active_generation(second, data_dir=data)

    assert caught.value.publish_substage == "active_pointer_write"
    assert generations.read_active_pointer_bytes(data_dir=data) == old


def test_pointer_replace_failure_leaves_old_pointer_exact(monkeypatch, tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    old = generations.read_active_pointer_bytes(data_dir=data)
    candidate = generations.prepare_candidate_generation(first, data_dir=data, generation_id="gen-2")
    second = generations.finalize_candidate_generation(
        candidate, production_db_sha256=generations.sha256_file(database)
    )
    real_replace = generations.os.replace

    def fail_pointer(source, target):
        if Path(target) == data / generations.ACTIVE_POINTER_NAME:
            raise PermissionError("replace denied")
        return real_replace(source, target)

    monkeypatch.setattr(generations.os, "replace", fail_pointer)
    with pytest.raises(generations.ActivePointerPublishError) as caught:
        generations.publish_active_generation(second, data_dir=data)

    assert caught.value.publish_substage == "active_pointer_replace"
    assert generations.read_active_pointer_bytes(data_dir=data) == old


def test_old_sqlite_handle_can_remain_open_during_pointer_switch(tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    sqlite_path = first.fts_index_path
    sqlite_path.unlink()
    connection = sqlite3.connect(sqlite_path)
    connection.execute("CREATE TABLE marker(value TEXT)")
    connection.execute("INSERT INTO marker VALUES ('old')")
    connection.commit()
    manifest = first.generation_dir / generations.GENERATION_MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["fts_index_sha256"] = generations.sha256_file(sqlite_path)
    manifest.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    generations.publish_active_generation(first, data_dir=data)
    candidate = generations.prepare_candidate_generation(first, data_dir=data, generation_id="gen-2")
    second = generations.finalize_candidate_generation(
        candidate, production_db_sha256=generations.sha256_file(database)
    )

    generations.publish_active_generation(second, data_dir=data)

    assert connection.execute("SELECT value FROM marker").fetchone()[0] == "old"
    assert sqlite_path.is_file()
    connection.close()


def test_old_lancedb_connection_and_table_remain_open_during_pointer_switch(
    tmp_path: Path,
) -> None:
    lancedb = pytest.importorskip("lancedb")
    data = tmp_path / "data"
    data.mkdir()
    database = _db(tmp_path)
    source = _source(tmp_path)
    shutil.rmtree(source.vector_store_path)
    db = lancedb.connect(str(source.vector_store_path))
    table = db.create_table(
        "passage_embeddings",
        data=[
            {
                "vector": [1.0, 0.0],
                "source_id": "passage:1:1",
                "document_id": 1,
                "chunk_id": 1,
                "passage_text": "old generation",
            }
        ],
        mode="overwrite",
    )
    first_candidate = generations.prepare_candidate_generation(
        source, data_dir=data, generation_id="gen-1"
    )
    first = generations.finalize_candidate_generation(
        first_candidate, production_db_sha256=generations.sha256_file(database)
    )
    generations.publish_active_generation(first, data_dir=data)
    old_db = lancedb.connect(str(first.vector_store_path))
    old_table = old_db.open_table("passage_embeddings")
    second_candidate = generations.prepare_candidate_generation(
        first, data_dir=data, generation_id="gen-2"
    )
    second = generations.finalize_candidate_generation(
        second_candidate, production_db_sha256=generations.sha256_file(database)
    )

    generations.publish_active_generation(second, data_dir=data)

    assert old_table.count_rows() == 1
    assert table.count_rows() == 1
    assert first.vector_store_path.is_dir()
    assert generations.resolve_active_retrieval_generation(
        data_dir=data, db_path=database
    ).generation_id == "gen-2"


def test_old_native_note_vector_cache_and_root_survive_pointer_switch(
    tmp_path: Path,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    cache_key = str(first.native_note_vector_path.resolve())
    sentinel = ((1, 1), "notes.json", (1, 1), {"schema": 1}, [])
    with note_vector_index._INDEX_CACHE_LOCK:
        note_vector_index._INDEX_CACHE[cache_key] = sentinel
    second_candidate = generations.prepare_candidate_generation(
        first, data_dir=data, generation_id="gen-2"
    )
    second = generations.finalize_candidate_generation(
        second_candidate, production_db_sha256=generations.sha256_file(database)
    )

    generations.publish_active_generation(second, data_dir=data)

    with note_vector_index._INDEX_CACHE_LOCK:
        assert note_vector_index._INDEX_CACHE.get(cache_key) is sentinel
        note_vector_index._INDEX_CACHE.pop(cache_key, None)
    assert first.native_note_vector_path.is_dir()


def test_existing_vector_reader_never_creates_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing-lancedb"
    with pytest.raises(Exception):
        from app.services import vector_store_service

        vector_store_service.connect_existing_vector_store(missing)
    assert not missing.exists()


def test_writer_reentrant_read_has_no_self_deadlock(tmp_path: Path) -> None:
    coordinator = generations.ProductionGenerationCoordinator()
    with coordinator.write():
        with coordinator.read():
            pass


def test_reader_writer_visibility_never_mixes_generations(tmp_path: Path) -> None:
    coordinator = generations.ProductionGenerationCoordinator()
    state = {"db": "old", "generation": "N"}
    observed: list[tuple[str, str]] = []
    writer_entered = threading.Event()
    writer_release = threading.Event()

    def writer() -> None:
        with coordinator.write():
            state["db"] = "new"
            writer_entered.set()
            writer_release.wait(timeout=2)
            state["generation"] = "N+1"

    def reader() -> None:
        with coordinator.read():
            observed.append((state["db"], state["generation"]))

    thread = threading.Thread(target=writer)
    thread.start()
    assert writer_entered.wait(timeout=1)
    waiting_reader = threading.Thread(target=reader)
    waiting_reader.start()
    time.sleep(0.05)
    assert not observed
    writer_release.set()
    thread.join(timeout=2)
    waiting_reader.join(timeout=2)

    assert observed == [("new", "N+1")]
    assert ("new", "N") not in observed
    assert ("old", "N+1") not in observed


def test_real_pointer_and_database_transition_is_atomic_to_readers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(
        generations,
        "PRODUCTION_GENERATION_COORDINATOR",
        coordinator,
    )
    before: list[tuple[str, str | None]] = []
    during: list[tuple[str, str | None]] = []
    after: list[tuple[str, str | None]] = []
    with generations.production_read_generation(
        data_dir=data, db_path=database
    ) as snapshot:
        before.append((generations.sha256_file(database), snapshot.generation_id))

    writer_has_new_db = threading.Event()
    allow_switch = threading.Event()

    def writer() -> None:
        with generations.production_write_generation():
            database.write_bytes(b"new-database-revision")
            candidate = generations.prepare_candidate_generation(
                first, data_dir=data, generation_id="gen-2"
            )
            second = generations.finalize_candidate_generation(
                candidate,
                production_db_sha256=generations.sha256_file(database),
            )
            writer_has_new_db.set()
            allow_switch.wait(timeout=2)
            generations.publish_active_generation(second, data_dir=data)

    def reader() -> None:
        with generations.production_read_generation(
            data_dir=data, db_path=database
        ) as snapshot:
            during.append(
                (generations.sha256_file(database), snapshot.generation_id)
            )

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert writer_has_new_db.wait(timeout=1)
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    time.sleep(0.05)
    assert not during
    allow_switch.set()
    writer_thread.join(timeout=2)
    reader_thread.join(timeout=2)
    with generations.production_read_generation(
        data_dir=data, db_path=database
    ) as snapshot:
        after.append((generations.sha256_file(database), snapshot.generation_id))

    assert before == [(first.production_db_sha256, "gen-1")]
    assert during == [(generations.sha256_file(database), "gen-2")]
    assert after == during


def test_nested_request_keeps_pinned_generation(monkeypatch, tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    with generations.production_read_generation(data_dir=data, db_path=database) as pinned:
        candidate = generations.prepare_candidate_generation(first, data_dir=data, generation_id="gen-2")
        second = generations.finalize_candidate_generation(
            candidate, production_db_sha256=generations.sha256_file(database)
        )
        # Simulate an external pointer change without taking this process's coordinator.
        generations.publish_active_generation(second, data_dir=data)
        assert generations.current_retrieval_generation(data_dir=data, db_path=database) is pinned
        assert pinned.generation_id == "gen-1"


def test_degraded_generation_blocks_reads_until_explicit_full_revalidation(
    tmp_path: Path,
    isolate_generation_coordinator,
) -> None:
    data, database, _, active = _ready_generation(tmp_path)
    generations.publish_active_generation(active, data_dir=data)
    coordinator = isolate_generation_coordinator

    with generations.production_write_generation():
        coordinator.mark_degraded("post_switch_verification_and_pointer_rollback_failed")

    assert coordinator.degraded is True
    with pytest.raises(generations.RetrievalGenerationError) as blocked:
        with generations.production_read_generation(data_dir=data, db_path=database):
            pass
    assert blocked.value.code == "retrieval_generation_degraded"
    assert blocked.value.safe_to_retry is False

    with pytest.raises(RuntimeError, match="still invalid"):
        generations.revalidate_active_generation(
            data_dir=data,
            db_path=database,
            validator=lambda _snapshot: (_ for _ in ()).throw(
                RuntimeError("still invalid")
            ),
        )
    assert coordinator.degraded is True

    observed: list[generations.RetrievalGenerationSnapshot] = []
    recovered = generations.revalidate_active_generation(
        data_dir=data,
        db_path=database,
        validator=lambda snapshot: observed.append(snapshot),
    )
    assert observed == [active]
    assert recovered == active
    assert coordinator.degraded is False
    with generations.production_read_generation(
        data_dir=data,
        db_path=database,
    ) as readable:
        assert readable == active


def test_degraded_revalidation_never_falls_back_to_legacy(
    tmp_path: Path,
    isolate_generation_coordinator,
) -> None:
    data, database, _, active = _ready_generation(tmp_path)
    generations.publish_active_generation(active, data_dir=data)
    coordinator = isolate_generation_coordinator
    with generations.production_write_generation():
        coordinator.mark_degraded("forced")
    (data / generations.ACTIVE_POINTER_NAME).unlink()

    with pytest.raises(generations.RetrievalGenerationError) as caught:
        generations.revalidate_active_generation(
            data_dir=data,
            db_path=database,
            validator=lambda _snapshot: None,
        )
    assert caught.value.code == "retrieval_generation_revalidation_failed"
    assert coordinator.degraded is True
    with pytest.raises(generations.RetrievalGenerationError) as blocked:
        with generations.production_read_generation(data_dir=data, db_path=database):
            pass
    assert blocked.value.code == "retrieval_generation_degraded"


def _simulate_process_restart(monkeypatch) -> generations.ProductionGenerationCoordinator:
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(
        generations,
        "PRODUCTION_GENERATION_COORDINATOR",
        coordinator,
    )
    generations._PINNED_GENERATION.set(None)
    generations.invalidate_generation_validation_cache()
    return coordinator


def _assert_durable_reader_blocked(data: Path, database: Path) -> None:
    with pytest.raises(generations.RetrievalGenerationError) as caught:
        with generations.production_read_generation(
            data_dir=data,
            db_path=database,
        ):
            pass
    assert caught.value.code == "retrieval_generation_degraded"
    assert caught.value.safe_to_retry is False


def test_restart_blocks_activating_marker_before_pointer_switch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)

    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    _simulate_process_restart(monkeypatch)

    assert generations.resolve_active_retrieval_generation(
        data_dir=data,
        db_path=database,
    ).generation_id == "gen-1"
    _assert_durable_reader_blocked(data, database)


def test_restart_blocks_activating_marker_after_pointer_switch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)

    _simulate_process_restart(monkeypatch)
    _assert_durable_reader_blocked(data, database)


def test_restart_blocks_degraded_marker_after_pointer_rollback_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)
    generations.mark_activation_degraded(data_dir=data)

    _simulate_process_restart(monkeypatch)
    state = json.loads(
        generations.activation_state_path(data).read_text(encoding="utf-8")
    )
    assert state["status"] == "degraded"
    _assert_durable_reader_blocked(data, database)


def test_failed_degraded_update_preserves_activating_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)
    marker = generations.activation_state_path(data)
    activating_bytes = marker.read_bytes()

    monkeypatch.setattr(
        generations,
        "_replace_json_fsync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            generations.ActivationStatePublishError(
                "activation_state_degraded_replace",
                OSError("forced degraded state failure"),
            )
        ),
    )
    with pytest.raises(generations.ActivationStatePublishError):
        generations.mark_activation_degraded(data_dir=data)

    assert marker.read_bytes() == activating_bytes
    assert json.loads(marker.read_text(encoding="utf-8"))["status"] == "activating"


def test_successful_activation_clear_survives_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)
    generations.clear_activation_state(data_dir=data)

    _simulate_process_restart(monkeypatch)
    with generations.production_read_generation(
        data_dir=data,
        db_path=database,
    ) as resolved:
        assert resolved.generation_id == "gen-2"


def test_successful_rollback_clear_survives_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    previous = generations.read_active_pointer_bytes(data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)
    generations.restore_active_pointer(previous, data_dir=data)
    generations.clear_activation_state(data_dir=data)

    _simulate_process_restart(monkeypatch)
    with generations.production_read_generation(
        data_dir=data,
        db_path=database,
    ) as resolved:
        assert resolved.generation_id == "gen-1"


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps(
            {
                "schema_version": 999,
                "status": "activating",
                "previous_generation_id": "gen-1",
                "candidate_generation_id": "gen-2",
                "production_db_sha256": "0" * 64,
                "created_at": "now",
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "status": "unknown",
                "previous_generation_id": "gen-1",
                "candidate_generation_id": "gen-2",
                "production_db_sha256": "0" * 64,
                "created_at": "now",
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "status": "activating",
                "previous_generation_id": "gen-1",
                "candidate_generation_id": "../escape",
                "production_db_sha256": "0" * 64,
                "created_at": "now",
            }
        ),
    ],
)
def test_corrupt_durable_activation_state_fails_closed_after_restart(
    tmp_path: Path,
    monkeypatch,
    payload: str,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    generations.activation_state_path(data).write_text(payload, encoding="utf-8")

    _simulate_process_restart(monkeypatch)
    _assert_durable_reader_blocked(data, database)


def test_degraded_marker_inconsistent_with_active_pointer_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    state = json.loads(
        generations.activation_state_path(data).read_text(encoding="utf-8")
    )
    state["status"] = "degraded"
    generations.activation_state_path(data).write_text(
        json.dumps(state),
        encoding="utf-8",
    )

    _simulate_process_restart(monkeypatch)
    _assert_durable_reader_blocked(data, database)


def test_durable_revalidation_is_only_recovery_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    second = _next_generation(data, database, first)
    generations.begin_generation_activation(
        first,
        second,
        production_db_sha256=second.production_db_sha256,
        data_dir=data,
    )
    generations.publish_active_generation(second, data_dir=data)
    generations.mark_activation_degraded(data_dir=data)
    coordinator = _simulate_process_restart(monkeypatch)

    with pytest.raises(RuntimeError, match="still invalid"):
        generations.revalidate_active_generation(
            data_dir=data,
            db_path=database,
            validator=lambda _snapshot: (_ for _ in ()).throw(
                RuntimeError("still invalid")
            ),
        )
    assert generations.activation_state_path(data).is_file()
    _assert_durable_reader_blocked(data, database)

    validated: list[str | None] = []
    recovered = generations.revalidate_active_generation(
        data_dir=data,
        db_path=database,
        validator=lambda snapshot: validated.append(snapshot.generation_id),
    )
    assert recovered.generation_id == "gen-2"
    assert validated == ["gen-2"]
    assert not generations.activation_state_path(data).exists()
    assert coordinator.degraded is False
    with generations.production_read_generation(
        data_dir=data,
        db_path=database,
    ) as readable:
        assert readable.generation_id == "gen-2"


def test_activation_marker_write_failure_prevents_pointer_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    old_pointer = generations.read_active_pointer_bytes(data_dir=data)
    second = _next_generation(data, database, first)
    real_write = generations._write_json_fsync

    def fail_activation_write(path: Path, payload: dict) -> None:
        if generations.ACTIVATION_STATE_NAME in path.name:
            raise OSError("activation state write failed")
        real_write(path, payload)

    monkeypatch.setattr(generations, "_write_json_fsync", fail_activation_write)
    with pytest.raises(generations.ActivationStatePublishError) as caught:
        generations.begin_generation_activation(
            first,
            second,
            production_db_sha256=second.production_db_sha256,
            data_dir=data,
        )

    assert caught.value.publish_substage == "activation_state_write"
    assert generations.read_active_pointer_bytes(data_dir=data) == old_pointer
    assert not generations.activation_state_path(data).exists()


def test_product_guard_returns_stable_degraded_contract(
    isolate_generation_coordinator,
) -> None:
    with generations.production_write_generation():
        isolate_generation_coordinator.mark_degraded("forced")

    @generations.product_read_generation_guard
    def guarded_reader():
        raise AssertionError("degraded reader body must not run")

    with pytest.raises(HTTPException) as caught:
        guarded_reader()
    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "status": "error",
        "error_code": "retrieval_generation_degraded",
        "message": "Retrieval generation is degraded and read access is blocked.",
        "retryable": False,
        "safe_to_retry": False,
        "writes_performed": False,
    }


def test_contextvar_resets_after_reader_writer_and_exception_paths(
    tmp_path: Path,
    isolate_generation_coordinator,
) -> None:
    data, database, _, active = _ready_generation(tmp_path)
    generations.publish_active_generation(active, data_dir=data)

    with generations.production_read_generation(data_dir=data, db_path=database):
        assert generations._PINNED_GENERATION.get() == active
    assert generations._PINNED_GENERATION.get() is None

    with pytest.raises(RuntimeError, match="reader failure"):
        with generations.production_read_generation(data_dir=data, db_path=database):
            assert generations._PINNED_GENERATION.get() == active
            raise RuntimeError("reader failure")
    assert generations._PINNED_GENERATION.get() is None

    with generations.production_write_generation():
        with generations.production_read_generation(data_dir=data, db_path=database):
            assert generations._PINNED_GENERATION.get() == active
    assert generations._PINNED_GENERATION.get() is None

    with pytest.raises(RuntimeError, match="writer rollback"):
        with generations.production_write_generation():
            with generations.production_read_generation(data_dir=data, db_path=database):
                assert generations._PINNED_GENERATION.get() == active
                raise RuntimeError("writer rollback")
    assert generations._PINNED_GENERATION.get() is None

    with generations.production_write_generation():
        with generations.production_read_generation(data_dir=data, db_path=database):
            isolate_generation_coordinator.mark_degraded("catastrophic")
    assert generations._PINNED_GENERATION.get() is None


def test_independent_request_does_not_inherit_previous_generation(tmp_path: Path) -> None:
    data, database, _, first = _ready_generation(tmp_path)
    generations.publish_active_generation(first, data_dir=data)
    with generations.production_read_generation(data_dir=data, db_path=database) as pinned:
        assert pinned.generation_id == "gen-1"
    assert generations._PINNED_GENERATION.get() is None

    candidate = generations.prepare_candidate_generation(
        first,
        data_dir=data,
        generation_id="gen-2",
    )
    second = generations.finalize_candidate_generation(
        candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(second, data_dir=data)
    with generations.production_read_generation(data_dir=data, db_path=database) as current:
        assert current.generation_id == "gen-2"


def test_evidence_loader_uses_resolved_generation_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data, database, _, active = _ready_generation(tmp_path)
    generations.publish_active_generation(active, data_dir=data)
    monkeypatch.setattr(
        generations,
        "current_retrieval_generation",
        lambda: active,
    )
    monkeypatch.setattr(
        evidence_loader,
        "_cached_index_status",
        lambda *_args: {"status": "ready", "ready": True},
    )

    index, manifest, status = evidence_loader.require_ready_index()

    assert index == active.fts_index_path
    assert manifest == active.fts_manifest_path
    assert status["ready"] is True


def test_vector_status_uses_resolved_generation_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, _, _, active = _ready_generation(tmp_path)
    observed: list[Path] = []
    monkeypatch.setattr(
        generations,
        "current_retrieval_generation",
        lambda: active,
    )
    monkeypatch.setattr(vector_store_service, "_import_lancedb", lambda: object())
    monkeypatch.setattr(
        vector_store_service,
        "get_vector_manifest",
        lambda path: {"manifest_path": str(path)},
    )

    class Store:
        pass

    def connect(path):
        observed.append(Path(path))
        return Store()

    monkeypatch.setattr(vector_store_service, "_connect_existing_vector_store", connect)
    monkeypatch.setattr(
        vector_store_service,
        "_table_status",
        lambda _db, _table: {"exists": True},
    )
    monkeypatch.setattr(vector_store_service, "collect_passage_sources", lambda: [])
    monkeypatch.setattr(vector_store_service, "collect_object_sources", lambda: [])
    monkeypatch.setattr(
        vector_store_service,
        "_sync_status",
        lambda _table, _sources, _db: {"complete": True},
    )
    monkeypatch.setattr(
        vector_store_service,
        "_stale_reason",
        lambda _manifest: None,
    )
    monkeypatch.setattr(
        vector_store_service,
        "evaluate_vector_store_freshness",
        lambda *_args, **_kwargs: {"complete": True, "reason": None},
    )

    result = vector_store_service.check_vector_store_status()

    assert observed == [active.vector_store_path]
    assert result["available"] is True


def test_writer_final_verification_uses_explicit_post_switch_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = tmp_path / "research_memory.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE documents(id INTEGER PRIMARY KEY);"
            "CREATE TABLE knowledge_chunks(id INTEGER PRIMARY KEY, document_id INTEGER);"
            "INSERT INTO documents(id) VALUES (1);"
            "INSERT INTO knowledge_chunks(id, document_id) VALUES (1, 1);"
        )
    source = _source(tmp_path)
    first_candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data,
        generation_id="gen-1",
    )
    first = generations.finalize_candidate_generation(
        first_candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(first, data_dir=data)

    monkeypatch.setattr(
        direction_import.fts_status_service,
        "get_index_status",
        lambda **_kwargs: {"status": "ready", "ready": True},
    )
    monkeypatch.setattr(
        direction_import.note_vector_index,
        "inspect_zotero_note_vector_document_impact",
        lambda *_args, **_kwargs: {"document_entry_count": 0},
    )

    with generations.production_write_generation():
        with generations.production_read_generation(
            data_dir=data,
            db_path=database,
        ) as writer_pin:
            assert writer_pin.generation_id == "gen-1"
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO documents(id) VALUES (2)")
                connection.commit()
            new_sha = generations.sha256_file(database)
            candidate = generations.prepare_candidate_generation(
                first,
                data_dir=data,
                generation_id="gen-2",
            )
            candidate.fts_manifest_path.write_text(
                json.dumps({"production_db_sha256": new_sha}),
                encoding="utf-8",
            )
            second = generations.finalize_candidate_generation(
                candidate,
                production_db_sha256=new_sha,
            )
            generations.publish_active_generation(second, data_dir=data)
            assert generations.current_retrieval_generation(
                data_dir=data,
                db_path=database,
            ) is writer_pin

            resolved: list[generations.RetrievalGenerationSnapshot] = []
            real_resolve = generations.resolve_active_retrieval_generation

            def capture_resolve(**kwargs):
                snapshot = real_resolve(**kwargs)
                resolved.append(snapshot)
                return snapshot

            monkeypatch.setattr(
                generations,
                "resolve_active_retrieval_generation",
                capture_resolve,
            )
            runtime = direction_import.SelectedBookImportRuntime(
                db_path=database,
                data_dir=data,
                fts_index_path=second.fts_index_path,
                fts_manifest_path=second.fts_manifest_path,
                vector_store_path=second.vector_store_path,
                vector_manifest_path=second.vector_manifest_path,
                persistence_scope="production",
            )
            direction_import._verify_production_final_state(
                runtime=runtime,
                document_id=1,
                expected_db_sha256=new_sha,
                expected_native_note_vector_count=0,
                generation=second,
            )

    assert resolved[-1].generation_id == "gen-2"
    assert resolved[-1].fts_index_path == second.fts_index_path
    assert resolved[-1].vector_store_path == second.vector_store_path
    assert resolved[-1].native_note_vector_path == second.native_note_vector_path
    assert resolved[-1].production_db_sha256 == new_sha
    assert generations._PINNED_GENERATION.get() is None
