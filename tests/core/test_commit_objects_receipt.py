from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services import commit_objects_service as objects_commit
from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations
from tests.core.test_commit_objects_generation import (
    _install_seams,
    _new_job_id,
    _versioned_fixture,
    _write_job_files,
)


def _overwrite_package(
    job_dir: Path,
    *,
    object_key: str = "mdm",
    description: str,
    review_status: str = "accepted",
) -> None:
    (job_dir / "reviewed_object_tag_package.json").write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "object_key": object_key,
                        "object_name": "MDM 机制",
                        "object_type": "mechanism",
                        "review_status": review_status,
                        "confidence": "medium",
                        "aliases": [],
                        "topic_tags": [],
                        "problem_tags": [],
                        "mechanism_tags": [],
                        "inspiration_tags": [],
                        "evidence_refs": [],
                        "source_note_ids": [],
                        "description": description,
                        "user_comment": "",
                        "warnings": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _overwrite_remap(job_dir: Path, *, mapped_chunk_ids: list[int]) -> None:
    (job_dir / "object_evidence_remap_preview.json").write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "object_key": "mdm",
                        "mapped_chunk_ids": mapped_chunk_ids,
                        "mapping_status": "mapped",
                        "warnings": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _receipt_rows(database: Path) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT import_job_id, phase, document_id, input_fingerprint "
                "FROM object_commit_receipts ORDER BY phase"
            ).fetchall()
        ]


def test_same_input_second_commit_is_already_committed_no_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_same")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"

    session_class = mutations.ProductionGenerationMutationSession
    instantiated: list[int] = []

    def no_session_factory(**kwargs):
        instantiated.append(1)
        return session_class(**kwargs)

    monkeypatch.setattr(
        "app.services.commit_objects_service.retrieval_generation_mutation_service.ProductionGenerationMutationSession",
        no_session_factory,
    )

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()

    second = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )

    assert second["status"] == "already_committed"
    assert second["core_db_write_performed"] is False
    assert instantiated == []
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db


def test_changed_input_after_commit_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_changed")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"

    _overwrite_package(job_dir, description="input B")

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()

    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "object_commit_input_changed_after_commit" in str(error.value)
    assert "already_committed" not in str(error.value)
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db
    assert not generations.activation_state_path(data_dir).exists()


def test_plain_phase_receipt_does_not_satisfy_reviewed_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_phases")
    job_dir = _write_job_files(job_id, reviewed=True)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    plain = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert plain["status"] == "committed"

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)

    reviewed = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert reviewed["status"] == "committed"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) != before_pointer
    assert [row[1] for row in _receipt_rows(database)] == [
        "commit_objects",
        "commit_reviewed_objects",
    ]


def test_reviewed_remap_preview_change_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_remap")
    job_dir = _write_job_files(job_id, reviewed=True)
    _overwrite_package(job_dir, description="input A")
    _overwrite_remap(job_dir, mapped_chunk_ids=[101])
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"

    _overwrite_remap(job_dir, mapped_chunk_ids=[])

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()

    with pytest.raises(Exception) as error:
        objects_commit.commit_reviewed_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "object_commit_input_changed_after_commit" in str(error.value)
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db


def test_receipt_survives_marker_loss_and_forgery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_marker")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"

    marker = job_dir / "commit_objects_result.json"
    marker.write_text(
        json.dumps({"status": "committed", "inserted_count": 999}),
        encoding="utf-8",
    )

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_db = database.read_bytes()

    second = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert second["status"] == "already_committed"
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert database.read_bytes() == before_db


def test_malformed_receipt_fingerprint_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_malformed")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"

    malformed_db = tmp_path / "malformed.db"
    with sqlite3.connect(malformed_db) as connection:
        connection.execute(
            "CREATE TABLE object_commit_receipts ("
            "import_job_id VARCHAR(255) NOT NULL, "
            "phase VARCHAR(64) NOT NULL, "
            "document_id INTEGER, "
            "input_fingerprint VARCHAR(64) NOT NULL, "
            "committed_at TEXT NOT NULL, "
            "revision INTEGER NOT NULL DEFAULT 1"
            ")"
        )
        connection.execute(
            "INSERT INTO object_commit_receipts VALUES (?, ?, ?, ?, ?, ?)",
            ("job-y", "commit_objects", 1, "not-a-hash", "2026-08-10", 1),
        )
        connection.commit()
    with pytest.raises(Exception) as error:
        objects_commit._read_object_commit_receipt(
            import_job_id="job-y",
            phase="commit_objects",
            db_path=malformed_db,
        )
    assert "object_commit_receipt_invalid" in str(error.value)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE object_commit_receipts SET input_fingerprint = 'not-a-hash'"
        )
        connection.commit()

    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    with pytest.raises(Exception) as error:
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert "already_committed" not in str(error.value)
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer


def test_ambiguous_receipt_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE object_commit_receipts ("
            "import_job_id VARCHAR(255) NOT NULL, "
            "phase VARCHAR(64) NOT NULL, "
            "document_id INTEGER, "
            "input_fingerprint VARCHAR(64) NOT NULL, "
            "committed_at TEXT NOT NULL, "
            "revision INTEGER NOT NULL DEFAULT 1"
            ")"
        )
        connection.executemany(
            "INSERT INTO object_commit_receipts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("job-x", "commit_objects", 1, "a" * 64, "2026-08-10", 1),
                ("job-x", "commit_objects", 1, "b" * 64, "2026-08-10", 1),
            ],
        )
        connection.commit()

    with pytest.raises(Exception) as error:
        objects_commit._read_object_commit_receipt(
            import_job_id="job-x",
            phase="commit_objects",
            db_path=database,
        )
    assert "object_commit_receipt_ambiguous" in str(error.value)


def test_failed_commit_leaves_no_receipt_and_retry_commits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_retry")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    engine = _install_seams(monkeypatch, database=database)

    original = objects_commit.commit_objects_from_staging

    def failing_body(job_id, *, persist_result=True, receipt=None):
        original(job_id, persist_result=persist_result, receipt=receipt)
        raise RuntimeError("body failed after write")

    monkeypatch.setattr(
        objects_commit,
        "commit_objects_from_staging",
        failing_body,
    )

    with pytest.raises(Exception):
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )

    with sqlite3.connect(database) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='object_commit_receipts'"
        ).fetchone()
    assert table_exists is None

    engine.dispose()

    monkeypatch.setattr(
        objects_commit,
        "commit_objects_from_staging",
        original,
    )

    retried = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert retried["status"] == "committed"
    assert len(_receipt_rows(database)) == 1


def test_successful_commit_persists_durable_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_receipt_durable")
    job_dir = _write_job_files(job_id)
    _overwrite_package(job_dir, description="input A")
    _install_seams(monkeypatch, database=database)

    result = objects_commit.commit_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert result["status"] == "committed"

    rows = _receipt_rows(database)
    assert len(rows) == 1
    phase, fingerprint = rows[0][1], rows[0][3]
    assert phase == "commit_objects"
    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    expected = objects_commit._phase_input_fingerprint(
        import_job_id=job_id,
        document_id=1,
        reviewed_package=json.loads(
            (job_dir / "reviewed_object_tag_package.json").read_text(
                encoding="utf-8"
            )
        ),
        phase="commit_objects",
    )
    assert fingerprint == expected


def test_all_deprecated_reviewed_edge_has_durable_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_receipt_deprecated")

    def seed_old_candidate(database: Path) -> None:
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO object_candidates (id, document_id, import_job_id, "
                "object_key, object_name, object_type, review_status, status, "
                "aliases_json, topic_tags_json, problem_tags_json, "
                "mechanism_tags_json, inspiration_tags_json, evidence_refs_json, "
                "note_refs_json, source_note_ids_json, mapping_status, "
                "mapped_chunk_ids_json, warnings_json, created_by, created_at, "
                "updated_at) VALUES (9, 1, ?, 'old-key', 'Old', 'mechanism', "
                "'accepted', 'candidate', '[]', '[]', '[]', '[]', '[]', '[]', "
                "'[]', '[]', 'not_mapped', '[]', '[]', 'user_reviewed', "
                "'2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')",
                (job_id,),
            )
            connection.commit()

    fixture = _versioned_fixture(tmp_path, after_database=seed_old_candidate)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_dir = _write_job_files(job_id, reviewed=True)
    _overwrite_package(job_dir, description="rejected input", review_status="rejected")
    _overwrite_remap(job_dir, mapped_chunk_ids=[])
    _install_seams(monkeypatch, database=database)

    first = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert first["status"] == "committed"
    assert first["deprecated_count"] == 1
    assert first["inserted_count"] == 0

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT phase FROM object_commit_receipts"
        ).fetchall()
    assert [(row[0],) for row in rows] == [("commit_reviewed_objects",)]

    second = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert second["status"] == "already_committed"
    assert second["core_db_write_performed"] is False
