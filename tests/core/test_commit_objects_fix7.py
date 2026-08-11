from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import commit_objects_service as objects_commit
from app.services import retrieval_generation_service as generations
from app.services.library import document_deletion_service as deletion
from tests.core.test_commit_objects_generation import (
    _install_seams as _install_object_seams,
    _new_job_id,
    _versioned_fixture,
    _write_job_files,
)
from tests.core.test_commit_objects_receipt import (
    _overwrite_package,
    _overwrite_remap,
)
from tests.core.test_document_deletion_generation import (
    _install_delete_seams,
    _preview_and_delete,
    _test_cleanup_fts,
    _versioned_fixture as _delete_versioned_fixture,
)


def _receipt_fingerprint(frozen) -> str:
    return objects_commit._phase_input_fingerprint(frozen)


def _freeze_for(
    job_dir: Path,
    *,
    job_id: str,
    phase: str,
    document_id: int = 1,
    reviewed: bool = False,
):
    package = json.loads(
        (job_dir / "reviewed_object_tag_package.json").read_text(encoding="utf-8")
    )
    remap_objects = None
    if reviewed:
        remap = json.loads(
            (job_dir / "object_evidence_remap_preview.json").read_text(
                encoding="utf-8"
            )
        )
        remap_objects = remap.get("objects") or []
    return objects_commit._freeze_commit_input(
        import_job_id=job_id,
        phase=phase,
        document_id=document_id,
        reviewed_objects=package.get("objects") or [],
        remap_objects=remap_objects,
    )


def _seed_receipt_row(
    database: Path,
    *,
    job_id: str,
    phase: str,
    document_id: int,
    fingerprint: str,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(objects_commit._RECEIPT_TABLE_DDL)
        connection.execute(
            "INSERT INTO object_commit_receipts ("
            "import_job_id, phase, document_id, input_fingerprint, committed_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (job_id, phase, document_id, fingerprint, "2026-08-10T00:00:00+00:00"),
        )
        connection.commit()


# ---------------------------------------------------------------------------
# Blocker A — TOCTOU: body must consume the frozen input, never re-read
# ---------------------------------------------------------------------------


def test_tocjou_staging_overwrite_after_freeze_cannot_change_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_tocjou")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    original_package = json.loads(
        (job_dir / "reviewed_object_tag_package.json").read_text(encoding="utf-8")
    )
    _install_object_seams(monkeypatch, database=database)

    original = objects_commit.commit_objects_from_staging

    def overwrite_then_commit(
        import_job_id, *, persist_result=True, receipt=None, frozen_input=None
    ):
        # Simulate a concurrent staging overwrite landing between the wrapper
        # freeze/fingerprint and the body mutation.
        _overwrite_package(job_dir, description="input B")
        return original(
            import_job_id,
            persist_result=persist_result,
            receipt=receipt,
            frozen_input=frozen_input,
        )

    monkeypatch.setattr(
        objects_commit,
        "commit_objects_from_staging",
        overwrite_then_commit,
    )

    result = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert result["status"] == "committed"

    package_a_content = original_package
    frozen_a = objects_commit._freeze_commit_input(
        import_job_id=job_id,
        phase="commit_objects",
        document_id=1,
        reviewed_objects=package_a_content.get("objects") or [],
    )
    with sqlite3.connect(database) as connection:
        receipt = connection.execute(
            "SELECT input_fingerprint FROM object_commit_receipts "
            "WHERE import_job_id = ? AND phase = 'commit_objects'",
            (job_id,),
        ).fetchone()
        description = connection.execute(
            "SELECT description FROM object_candidates "
            "WHERE import_job_id = ?",
            (job_id,),
        ).fetchone()
    assert receipt[0] == _receipt_fingerprint(frozen_a)
    assert description[0] == "input A"

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    expected = objects_commit.vector_store_service.collect_affected_object_sources(
        db_path=database,
        object_keys=["mdm"],
    )
    state = objects_commit.vector_store_service.inspect_affected_object_vector_state(
        object_keys=["mdm"],
        expected_sources=expected,
        store_path=active.vector_store_path,
    )
    assert state["status"] == "ok"
    assert state["missing_count"] == 0


# ---------------------------------------------------------------------------
# Blocker B — semantic fingerprint (volatile metadata must not matter)
# ---------------------------------------------------------------------------


def _package_with_metadata(objects, **metadata):
    payload = {
        "schema_version": "search_object_reviewed_package.v1",
        "import_job_id": "job-x",
        "status": "user_reviewed",
        "reviewed_by": "user",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
        "objects": objects,
        "safety": {
            "core_db_write_performed": False,
            "committed_to_library": False,
        },
    }
    payload.update(metadata)
    return payload


def _fp(objects, *, phase="commit_objects", remap_objects=None):
    frozen = objects_commit._freeze_commit_input(
        import_job_id="job-x",
        phase=phase,
        document_id=1,
        reviewed_objects=objects,
        remap_objects=remap_objects,
    )
    return _receipt_fingerprint(frozen)


def test_fingerprint_ignores_volatile_upload_metadata() -> None:
    objects = [
        {
            "object_key": "mdm",
            "object_name": "MDM",
            "object_type": "mechanism",
            "review_status": "accepted",
            "description": "same",
        }
    ]
    base = _fp(objects)
    assert (
        _fp(
            objects,
            phase="commit_objects",
        )
        == base
    )
    assert _fp(objects) == _fp(objects, phase="commit_objects")
    fp_a = _fp([dict(objects[0])])
    fp_volatile = _fp(
        [
            dict(
                objects[0],
                **{
                    "reviewed_at": "2026-08-11T00:00:00+00:00",
                    "reviewed_by": "someone-else",
                    "status": "user_reviewed",
                    "safety": {"x": 1},
                },
            )
        ]
    )
    assert fp_volatile == fp_a


def test_fingerprint_changes_on_semantic_change() -> None:
    base = _fp(
        [
            {
                "object_key": "mdm",
                "object_name": "MDM",
                "object_type": "mechanism",
                "review_status": "accepted",
                "description": "desc A",
            }
        ]
    )
    changed = _fp(
        [
            {
                "object_key": "mdm",
                "object_name": "MDM",
                "object_type": "mechanism",
                "review_status": "accepted",
                "description": "desc B",
            }
        ]
    )
    assert changed != base
    key_changed = _fp(
        [
            {
                "object_key": "other",
                "object_name": "MDM",
                "object_type": "mechanism",
                "review_status": "accepted",
                "description": "desc A",
            }
        ]
    )
    assert key_changed != base


def test_fingerprint_canonicalizes_key_whitespace() -> None:
    a = _fp(
        [
            {
                "object_key": " MDM ",
                "object_name": "MDM",
                "object_type": "mechanism",
                "review_status": "accepted",
            }
        ]
    )
    b = _fp(
        [
            {
                "object_key": "mdm",
                "object_name": "MDM",
                "object_type": "mechanism",
                "review_status": "accepted",
            }
        ]
    )
    assert a == b


def test_reviewed_fingerprint_binds_remap_semantics_only() -> None:
    reviewed_objects = [
        {
            "object_key": "mdm",
            "object_name": "MDM",
            "object_type": "mechanism",
            "review_status": "accepted",
        }
    ]
    base = _fp(
        reviewed_objects,
        phase="commit_reviewed_objects",
        remap_objects=[
            {
                "object_key": "mdm",
                "mapped_chunk_ids": [101],
                "mapping_status": "mapped",
                "warnings": [],
            }
        ],
    )
    changed = _fp(
        reviewed_objects,
        phase="commit_reviewed_objects",
        remap_objects=[
            {
                "object_key": "mdm",
                "mapped_chunk_ids": [],
                "mapping_status": "mapped",
                "warnings": [],
            }
        ],
    )
    assert changed != base
    volatile = _fp(
        reviewed_objects,
        phase="commit_reviewed_objects",
        remap_objects=[
            {
                "object_key": "mdm",
                "mapped_chunk_ids": [101],
                "mapping_status": "mapped",
                "warnings": [],
                "summary": "preview summary",
                "generated_at": "2026-08-11T00:00:00+00:00",
                "preview_path": "/tmp/preview.json",
            }
        ],
    )
    assert volatile == base


# ---------------------------------------------------------------------------
# Blocker C — receipt DELETE lifecycle
# ---------------------------------------------------------------------------


def _delete_fixture_with_receipt(
    tmp_path: Path,
    *,
    job_id: str,
    phase: str = "commit_objects",
    fingerprint: str = "a" * 64,
):
    def seed(database: Path) -> None:
        _seed_receipt_row(
            database,
            job_id=job_id,
            phase=phase,
            document_id=1,
            fingerprint=fingerprint,
        )

    fixture = _delete_versioned_fixture(tmp_path, after_database=seed)
    return fixture


def test_delete_preview_does_not_block_on_receipt_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _delete_fixture_with_receipt(
        tmp_path,
        job_id=_new_job_id("job_preview_receipt"),
    )
    runtime = fixture["runtime"]
    _install_delete_seams(monkeypatch)

    preview = deletion.create_deletion_preview(1, runtime=runtime)

    assert "unknown_schema_reference" not in str(preview["deletion_blockers"])
    assert all(
        "object_commit_receipts" not in str(blocker)
        for blocker in preview["deletion_blockers"]
    )


def test_delete_commits_receipt_tombstone_and_keeps_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_delete_receipt")
    fixture = _delete_fixture_with_receipt(tmp_path, job_id=job_id)
    runtime = fixture["runtime"]
    database = fixture["database"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    result = _preview_and_delete(runtime)
    assert result["status"] == "completed"

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT import_job_id, phase, document_id, input_fingerprint "
            "FROM object_commit_receipts"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == job_id
    assert rows[0][2] is None
    assert rows[0][3] == "a" * 64


def test_delete_recovery_package_contains_receipt_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_delete_recovery")
    fixture = _delete_fixture_with_receipt(tmp_path, job_id=job_id)
    runtime = fixture["runtime"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    preview = deletion.create_deletion_preview(1, runtime=runtime)
    result = deletion.delete_document(
        document_id=1,
        preview_token=str(preview["preview_token"]),
        expected_document_revision=str(preview["document_revision"]),
        confirmation_text=str(preview["title"]),
        runtime=runtime,
    )
    assert result["status"] == "completed"

    archive = result["recovery_package"]
    assert archive["exists"] is True
    archive_root = Path(runtime.resolved_archive_root())
    matches = list(archive_root.glob(f"{result['audit_id']}/database_rows.json"))
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    assert "object_commit_receipts" in payload["tables"]
    assert len(payload["tables"]["object_commit_receipts"]) == 1
    assert payload["tables"]["object_commit_receipts"][0]["document_id"] == 1


def test_deleted_source_commit_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_deleted_source")
    fixture = _delete_fixture_with_receipt(tmp_path, job_id=job_id)
    runtime = fixture["runtime"]
    database = fixture["database"]
    data_dir = runtime.data_dir
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)
    _preview_and_delete(runtime)

    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="old input")
    _install_object_seams(monkeypatch, database=database)

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "object_commit_source_document_deleted" in str(error.value)
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db


def test_delete_failure_rollback_restores_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_delete_rollback_receipt")
    fixture = _delete_fixture_with_receipt(tmp_path, job_id=job_id)
    runtime = fixture["runtime"]
    database = fixture["database"]
    _install_delete_seams(monkeypatch)
    runtime = replace(runtime, cleanup_fts=_test_cleanup_fts)

    original_transaction = deletion._execute_database_transaction

    def failing_transaction(plan, *, runtime):
        original_transaction(plan, runtime=runtime)
        raise RuntimeError("delete body failed after write")

    monkeypatch.setattr(
        deletion,
        "_execute_database_transaction",
        failing_transaction,
    )

    with pytest.raises(deletion.DeletionError):
        _preview_and_delete(runtime)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT document_id FROM object_commit_receipts"
        ).fetchone()
    assert row[0] == 1


# ---------------------------------------------------------------------------
# Blocker D — pre-FIX6 legacy jobs without receipts
# ---------------------------------------------------------------------------


def _seed_legacy_object_row(
    database: Path,
    *,
    job_id: str,
    key: str = "mdm",
    reviewed_evidence: bool = False,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO object_candidates (id, document_id, import_job_id, "
            "object_key, object_name, object_type, review_status, status, "
            "aliases_json, topic_tags_json, problem_tags_json, "
            "mechanism_tags_json, inspiration_tags_json, evidence_refs_json, "
            "note_refs_json, source_note_ids_json, mapping_status, "
            "mapped_chunk_ids_json, warnings_json, source_package_path, "
            "source_import_manifest_path, created_by, created_at, updated_at) "
            "VALUES (9, 1, ?, ?, 'Old', 'mechanism', 'accepted', "
            "CASE WHEN ? THEN 'deprecated' ELSE 'candidate' END, "
            "'[]', '[]', '[]', '[]', '[]', '[]', '[]', '[]', 'not_mapped', "
            "'[]', '[]', NULL, "
            "CASE WHEN ? THEN 'outputs/import_staging/x/import_manifest.json' "
            "ELSE NULL END, 'user_reviewed', '2026-08-01T00:00:00+00:00', "
            "'2026-08-01T00:00:00+00:00')",
            (job_id, key, reviewed_evidence, reviewed_evidence),
        )
        connection.commit()


def test_legacy_plain_rows_without_receipt_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_legacy_plain")

    def seed(database: Path) -> None:
        _seed_legacy_object_row(database, job_id=job_id)

    fixture = _delete_versioned_fixture(tmp_path, after_database=seed)
    database = fixture["database"]
    runtime = fixture["runtime"]
    data_dir = runtime.data_dir
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_object_seams(monkeypatch, database=database)

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()
    before_generations = generations.tree_fingerprint(
        data_dir / generations.GENERATION_ROOT_NAME
    )

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "object_commit_legacy_state_requires_reconciliation" in str(error.value)
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db
    assert (
        generations.tree_fingerprint(data_dir / generations.GENERATION_ROOT_NAME)
        == before_generations
    )
    assert not generations.activation_state_path(data_dir).exists()


def test_legacy_reviewed_evidence_without_receipt_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_legacy_reviewed")

    def seed(database: Path) -> None:
        _seed_legacy_object_row(database, job_id=job_id, reviewed_evidence=True)

    fixture = _delete_versioned_fixture(tmp_path, after_database=seed)
    database = fixture["database"]
    runtime = fixture["runtime"]
    data_dir = runtime.data_dir
    job_dir = _write_job_files(job_id, reviewed=True)
    _overwrite_package(job_dir, description="input A")
    _overwrite_remap(job_dir, mapped_chunk_ids=[101])
    _install_object_seams(monkeypatch, database=database)

    with pytest.raises(Exception) as error:
        objects_commit.commit_reviewed_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "object_commit_legacy_state_requires_reconciliation" in str(error.value)


def test_plain_rows_do_not_block_first_reviewed_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_plain_then_reviewed")

    def seed(database: Path) -> None:
        _seed_legacy_object_row(database, job_id=job_id)

    fixture = _delete_versioned_fixture(tmp_path, after_database=seed)
    database = fixture["database"]
    runtime = fixture["runtime"]
    data_dir = runtime.data_dir
    job_dir = _write_job_files(job_id, reviewed=True)
    _overwrite_package(job_dir, description="input A")
    _overwrite_remap(job_dir, mapped_chunk_ids=[101])
    _install_object_seams(monkeypatch, database=database)

    result = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert result["status"] == "committed"


def test_legacy_marker_alone_never_produces_already_committed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_legacy_marker")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    (job_dir / "commit_objects_result.json").write_text(
        json.dumps({"status": "committed", "inserted_count": 1}),
        encoding="utf-8",
    )
    _install_object_seams(monkeypatch, database=database)

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "already_committed" not in str(error.value)
    assert "object_commit_legacy_state_requires_reconciliation" in str(error.value)



