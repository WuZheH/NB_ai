from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import commit_objects_service as objects_commit
from app.services import object_evidence_remap_service as remap_service
from app.services import retrieval_generation_service as generations
from app.services.import_preview_service import ImportPreviewError
from tests.core.test_commit_objects_generation import (
    _install_seams,
    _new_job_id,
    _versioned_fixture,
    _write_job_files,
)


def _generate_remap(job_id: str, monkeypatch) -> tuple[Path, dict]:
    job_dir = _write_job_files(job_id, reviewed=True)
    monkeypatch.setattr(remap_service, "_build_chunk_index", lambda _document_id: {})
    result = remap_service.remap_reviewed_objects_preview(job_id)
    return job_dir, result


def _assert_rejected_before_mutation(
    *,
    job_id: str,
    database: Path,
    data_dir: Path,
    monkeypatch,
    code: str,
) -> None:
    class ForbiddenMutationSession:
        def __init__(self, **_kwargs):
            raise AssertionError("mutation session must not be created")

    monkeypatch.setattr(
        objects_commit.retrieval_generation_mutation_service,
        "ProductionGenerationMutationSession",
        ForbiddenMutationSession,
    )
    before_db = database.read_bytes()
    before_pointer = objects_commit.retrieval_generation_service.read_active_pointer_bytes(
        data_dir=data_dir
    )
    with pytest.raises(ImportPreviewError, match=code):
        objects_commit.commit_reviewed_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )
    assert database.read_bytes() == before_db
    assert (
        objects_commit.retrieval_generation_service.read_active_pointer_bytes(
            data_dir=data_dir
        )
        == before_pointer
    )


def test_remap_preview_binds_current_reviewed_semantic_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job_id = _new_job_id("job_fix8_remap_source")
    job_dir, result = _generate_remap(job_id, monkeypatch)

    assert result["import_job_id"] == job_id
    assert result["document_id"] == 1
    assert len(result["reviewed_input_fingerprint"]) == 64
    assert result["reviewed_input_fingerprint"] == result[
        "reviewed_input_fingerprint"
    ].lower()
    persisted = json.loads(
        (job_dir / "object_evidence_remap_preview.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["reviewed_input_fingerprint"] == result[
        "reviewed_input_fingerprint"
    ]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("import_job_id", "forged-job", "object_remap_preview_job_mismatch"),
        ("document_id", 999, "object_remap_preview_document_mismatch"),
    ],
)
def test_remap_preview_rejects_reviewed_package_route_conflict(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value,
    code: str,
) -> None:
    job_id = _new_job_id("job_fix8_preview_identity")
    job_dir = _write_job_files(job_id, reviewed=True)
    package_path = job_dir / "reviewed_object_tag_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package[field] = value
    package_path.write_text(json.dumps(package), encoding="utf-8")
    monkeypatch.setattr(remap_service, "_build_chunk_index", lambda _document_id: {})

    with pytest.raises(ImportPreviewError, match=code):
        remap_service.remap_reviewed_objects_preview(job_id)


def test_reviewed_package_semantic_change_makes_remap_stale_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_stale_remap")
    job_dir, _ = _generate_remap(job_id, monkeypatch)
    package_path = job_dir / "reviewed_object_tag_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["objects"][0]["description"] = "semantically changed after remap"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    _install_seams(monkeypatch, database=database)

    _assert_rejected_before_mutation(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        monkeypatch=monkeypatch,
        code="object_remap_preview_stale",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("import_job_id", "forged-job", "object_remap_preview_job_mismatch"),
        ("document_id", 999, "object_remap_preview_document_mismatch"),
        (
            "reviewed_input_fingerprint",
            "not-a-sha",
            "object_remap_preview_fingerprint_invalid",
        ),
    ],
)
def test_reviewed_remap_identity_mismatch_fails_before_mutation(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value,
    code: str,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_remap_identity")
    job_dir, _ = _generate_remap(job_id, monkeypatch)
    remap_path = job_dir / "object_evidence_remap_preview.json"
    remap = json.loads(remap_path.read_text(encoding="utf-8"))
    remap[field] = value
    remap_path.write_text(json.dumps(remap), encoding="utf-8")
    _install_seams(monkeypatch, database=database)

    _assert_rejected_before_mutation(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        monkeypatch=monkeypatch,
        code=code,
    )


def test_legacy_remap_without_source_fingerprint_requires_regeneration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_legacy_remap")
    job_dir = _write_job_files(job_id, reviewed=True)
    remap_path = job_dir / "object_evidence_remap_preview.json"
    remap = json.loads(remap_path.read_text(encoding="utf-8"))
    remap.pop("reviewed_input_fingerprint")
    remap_path.write_text(json.dumps(remap), encoding="utf-8")
    _install_seams(monkeypatch, database=database)

    _assert_rejected_before_mutation(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        monkeypatch=monkeypatch,
        code="object_remap_preview_legacy_requires_regeneration",
    )


@pytest.mark.parametrize(
    "metadata_change",
    [
        {"reviewed_at": "2026-08-11T12:00:00+00:00"},
        {
            "reviewed_by": "second-reviewer",
            "status": "user_reviewed",
            "safety": {"committed_to_library": False},
        },
    ],
)
def test_reviewed_metadata_only_change_does_not_stale_remap(
    tmp_path: Path,
    monkeypatch,
    metadata_change: dict,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_metadata")
    job_dir, _ = _generate_remap(job_id, monkeypatch)
    package_path = job_dir / "reviewed_object_tag_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package.update(metadata_change)
    package_path.write_text(json.dumps(package), encoding="utf-8")
    _install_seams(monkeypatch, database=database)

    result = objects_commit.commit_reviewed_objects_to_production_with_generation(
        job_id,
        db_path=database,
        data_dir=data_dir,
    )
    assert result["status"] == "committed"


def _assert_object_commit_failure_rolls_back(
    *,
    job_id: str,
    database: Path,
    data_dir: Path,
    expected_code: str,
) -> None:
    before_db = database.read_bytes()
    before_pointer = generations.read_active_pointer_bytes(data_dir=data_dir)
    before_active = generations.resolve_active_retrieval_generation(
        data_dir=data_dir,
        db_path=database,
        verify_fingerprints=True,
    )
    before_active_tree = generations.tree_fingerprint(
        before_active.generation_dir
    )
    generation_root = data_dir / generations.GENERATION_ROOT_NAME
    before_generation_root = generations.tree_fingerprint(generation_root)

    with pytest.raises(ImportPreviewError, match=expected_code):
        objects_commit.commit_objects_to_production_with_generation(
            job_id,
            db_path=database,
            data_dir=data_dir,
        )

    assert database.read_bytes() == before_db
    assert generations.read_active_pointer_bytes(data_dir=data_dir) == before_pointer
    assert generations.tree_fingerprint(before_active.generation_dir) == before_active_tree
    assert generations.tree_fingerprint(generation_root) == before_generation_root
    assert not generations.activation_state_path(data_dir).exists()


def test_object_commit_projection_change_fails_closed_before_rebind(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_projection_changed")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)
    calls = 0

    def changed_projection(*, registry):
        nonlocal calls
        calls += 1
        return ("a" if calls == 1 else "b") * 64

    monkeypatch.setattr(
        objects_commit.fts_index_service,
        "compute_retrieval_projection_sha256",
        changed_projection,
    )

    _assert_object_commit_failure_rolls_back(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        expected_code="object_commit_fts_projection_changed",
    )
    assert calls == 2


def test_object_commit_candidate_manifest_rebind_failure_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_rebind_write_fail")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)

    def fail_rebind(**_kwargs):
        raise OSError("candidate manifest write failed")

    monkeypatch.setattr(
        objects_commit.fts_index_service,
        "rebind_retrieval_fts_after_proven_unchanged_projection",
        fail_rebind,
    )

    _assert_object_commit_failure_rolls_back(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        expected_code="object_commit_failed",
    )


def test_object_commit_candidate_fts_status_failure_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_candidate_fts_status")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)

    def fail_candidate_status(**_kwargs):
        raise RuntimeError("object_commit_fts_not_ready")

    monkeypatch.setattr(
        objects_commit,
        "_strict_object_commit_fts_ready",
        fail_candidate_status,
    )

    _assert_object_commit_failure_rolls_back(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        expected_code="object_commit_failed",
    )


def test_object_commit_active_fts_status_failure_rolls_back_before_clear(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _versioned_fixture(tmp_path)
    database = fixture["database"]
    data_dir = fixture["data_dir"]
    job_id = _new_job_id("job_fix8_active_fts_status")
    _write_job_files(job_id)
    _install_seams(monkeypatch, database=database)
    original = objects_commit._strict_object_commit_fts_ready
    calls = 0

    def fail_active_status(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("object_commit_fts_not_ready")
        return original(**kwargs)

    monkeypatch.setattr(
        objects_commit,
        "_strict_object_commit_fts_ready",
        fail_active_status,
    )

    _assert_object_commit_failure_rolls_back(
        job_id=job_id,
        database=database,
        data_dir=data_dir,
        expected_code="object_commit_failed",
    )
    assert calls == 2
