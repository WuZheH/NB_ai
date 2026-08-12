from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import pdf_import_job_process_service as jobs
from app.services import production_write_surface_guard as guard
from app.services import retrieval_generation_service as generations
from scripts.runtime import run_chaptered_import_job_worker as worker


@pytest.fixture(autouse=True)
def isolate_generation_coordinator(monkeypatch: pytest.MonkeyPatch):
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(generations, "PRODUCTION_GENERATION_COORDINATOR", coordinator)
    token = generations._PINNED_GENERATION.set(None)
    try:
        yield coordinator
    finally:
        generations._PINNED_GENERATION.reset(token)


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_fingerprints(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _legacy_source(tmp_path: Path) -> generations.RetrievalGenerationSnapshot:
    source = tmp_path / "legacy"
    _write(source / generations.FTS_INDEX_NAME, b"fts")
    _write(source / generations.FTS_MANIFEST_NAME, b"{}\n")
    _write(source / generations.VECTOR_STORE_NAME / "table.lance" / "data", b"vectors")
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


def _patch_formal_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_dir: Path,
    database: Path,
) -> Path:
    jobs_root = data_dir / "runtime" / "import_jobs"
    monkeypatch.setattr(guard, "DATA_DIR", data_dir)
    monkeypatch.setattr(guard, "DEFAULT_DB_PATH", database)
    monkeypatch.setattr(jobs, "DATA_DIR", data_dir)
    monkeypatch.setattr(jobs, "DEFAULT_DB_PATH", database)
    monkeypatch.setattr(jobs, "IMPORT_JOBS_ROOT", jobs_root)
    monkeypatch.setattr(jobs, "_processes", {})
    monkeypatch.setattr(worker, "DATA_DIR", data_dir)
    monkeypatch.setattr(worker, "DEFAULT_DB_PATH", database)
    return jobs_root


def _versioned_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "data"
    database = data_dir / "db" / "research_memory.db"
    _write(database, b"production revision")
    source = _legacy_source(tmp_path)
    candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data_dir,
        generation_id="g-active",
    )
    active = generations.finalize_candidate_generation(
        candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(active, data_dir=data_dir)
    jobs_root = _patch_formal_paths(
        monkeypatch,
        data_dir=data_dir,
        database=database,
    )
    return data_dir, database, jobs_root


def _legacy_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    write_manifest: bool = True,
) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "data"
    database = data_dir / "db" / "research_memory.db"
    _write(database, b"legacy production revision")
    if write_manifest:
        manifest = data_dir / "search_index" / generations.FTS_MANIFEST_NAME
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"production_db_sha256": generations.sha256_file(database)}),
            encoding="utf-8",
        )
    jobs_root = _patch_formal_paths(
        monkeypatch,
        data_dir=data_dir,
        database=database,
    )
    return data_dir, database, jobs_root


def _payload() -> dict[str, Any]:
    return {
        "pdf_path": "D:/isolated/chaptered.pdf",
        "document_type": "book",
        "object_import_mode": "chaptered",
        "backend": "pymupdf_text",
        "import_granularity": "chapter",
    }


def _assert_job_freeze(exc: guard.ProductionWriteSurfaceFrozenError) -> None:
    assert exc.error_code == "chaptered_import_job_versioned_frozen"
    assert exc.status_code == 503
    assert exc.detail()["safe_to_retry"] is False
    assert exc.detail()["writes_performed"] is False
    assert exc.detail()["production_data_modified"] is False


def test_service_freezes_versioned_job_before_artifacts_or_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database, jobs_root = _versioned_sandbox(monkeypatch, tmp_path)
    before = _tree_fingerprints(data_dir)
    popen_calls: list[object] = []
    monkeypatch.setattr(
        jobs.subprocess,
        "Popen",
        lambda *_args, **_kwargs: popen_calls.append(object()) or pytest.fail("Popen must not run"),
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        jobs.create_chaptered_import_job_process(_payload())

    _assert_job_freeze(caught.value)
    assert popen_calls == []
    assert not jobs_root.exists()
    assert _tree_fingerprints(data_dir) == before
    assert generations.sha256_file(database) == before["db/research_memory.db"]


def test_api_returns_stable_503_without_creating_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, _database, jobs_root = _versioned_sandbox(monkeypatch, tmp_path)
    before = _tree_fingerprints(data_dir)
    monkeypatch.setattr(
        jobs.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not run"),
    )

    response = TestClient(app).post(
        "/api/v1/library/import/pdf/chaptered/jobs",
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "chaptered_import_job_versioned_frozen"
    assert response.json()["safe_to_retry"] is False
    assert response.json()["writes_performed"] is False
    assert response.json()["production_data_modified"] is False
    assert not jobs_root.exists()
    assert _tree_fingerprints(data_dir) == before


def test_direct_worker_freezes_before_every_production_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, _database, _jobs_root = _versioned_sandbox(monkeypatch, tmp_path)
    before = _tree_fingerprints(data_dir)
    monkeypatch.setattr(
        worker,
        "apply_prepared_book_import",
        lambda *_args, **_kwargs: pytest.fail("DB apply must not run"),
    )
    monkeypatch.setattr(
        worker,
        "_patch_document_type",
        lambda *_args, **_kwargs: pytest.fail("document type patch must not run"),
    )
    monkeypatch.setattr(
        worker,
        "_sync_zotero_native_notes_for_chapters",
        lambda *_args, **_kwargs: pytest.fail("Zotero apply must not run"),
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        worker.run_worker(
            job_id="old-job",
            payload=_payload(),
            status_file=tmp_path / "old-job" / "status.json",
            worker_log=tmp_path / "old-job" / "worker.log",
        )

    _assert_job_freeze(caught.value)
    assert _tree_fingerprints(data_dir) == before


def test_worker_rechecks_generation_immediately_before_first_database_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    status_file = tmp_path / "job" / "status.json"
    worker_log = tmp_path / "job" / "worker.log"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "job_id": "transition-job",
                "status": "queued",
                "stage": "queued",
                "created_at": jobs.utcnow(),
                "heartbeat_at": jobs.utcnow(),
            }
        ),
        encoding="utf-8",
    )
    checks = 0

    def generation_gate() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise guard.ProductionWriteSurfaceFrozenError(
                "chaptered_import_job_versioned_frozen",
                "generation changed while the PDF was being prepared",
                reason_code="versioned_retrieval_generation_active",
            )

    chapter = SimpleNamespace(
        chapter_index=1,
        title="Chapter 1",
        pdf_page_start=1,
        pdf_page_end=1,
    )
    prepared = SimpleNamespace(
        chapters=[chapter],
        estimated_chunk_count=1,
        detection_method="pdf_outline",
    )
    monkeypatch.setattr(worker, "_require_legacy_job_worker_surface", generation_gate)
    monkeypatch.setattr(
        worker,
        "classify_pdf_import",
        lambda *_args, **_kwargs: {
            "duplicate": False,
            "title": "Book",
            "signals": {"page_count": 1},
        },
    )
    monkeypatch.setattr(worker, "prepare_book_import", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(
        worker,
        "evaluate_auto_apply_safety",
        lambda _prepared: {
            "auto_apply_eligible": True,
            "book_safety_decision": "PASS",
            "book_safety_blockers": [],
            "book_safety_warnings": [],
            "detected_chapter_count": 1,
            "chapter_title_quality": "ok",
        },
    )
    monkeypatch.setattr(
        worker,
        "apply_prepared_book_import",
        lambda *_args, **_kwargs: pytest.fail("DB apply must not run after state changes"),
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        worker.run_worker(
            job_id="transition-job",
            payload=_payload(),
            status_file=status_file,
            worker_log=worker_log,
        )

    _assert_job_freeze(caught.value)
    assert checks == 2


def test_service_freezes_surviving_activation_marker_before_job_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, _database, jobs_root = _legacy_sandbox(monkeypatch, tmp_path)
    (data_dir / generations.ACTIVATION_STATE_NAME).write_text("{}", encoding="utf-8")
    before = _tree_fingerprints(data_dir)

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        jobs.create_chaptered_import_job_process(_payload())

    _assert_job_freeze(caught.value)
    assert caught.value.reason_code == "retrieval_generation_degraded"
    assert not jobs_root.exists()
    assert _tree_fingerprints(data_dir) == before


def test_service_freezes_degraded_coordinator_before_job_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolate_generation_coordinator: generations.ProductionGenerationCoordinator,
) -> None:
    data_dir, _database, jobs_root = _legacy_sandbox(monkeypatch, tmp_path)
    before = _tree_fingerprints(data_dir)
    with isolate_generation_coordinator.write():
        isolate_generation_coordinator.mark_degraded("forced")

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        jobs.create_chaptered_import_job_process(_payload())

    _assert_job_freeze(caught.value)
    assert caught.value.reason_code == "retrieval_generation_degraded"
    assert not jobs_root.exists()
    assert _tree_fingerprints(data_dir) == before


def test_service_freezes_ambiguous_pointer_absence_before_job_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, _database, jobs_root = _legacy_sandbox(
        monkeypatch,
        tmp_path,
        write_manifest=False,
    )
    before = _tree_fingerprints(data_dir)

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        jobs.create_chaptered_import_job_process(_payload())

    _assert_job_freeze(caught.value)
    assert caught.value.reason_code == "active_index_invalid"
    assert not jobs_root.exists()
    assert _tree_fingerprints(data_dir) == before


class _FakeProcess:
    pid = 24680

    def poll(self) -> None:
        return None


def _install_fake_job_runtime(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        jobs,
        "probe_runtime",
        lambda **_kwargs: {
            "marker_importable": True,
            "surya_importable": True,
            "torch_cuda_available": False,
            "reason": "torch_cuda_unavailable",
        },
    )

    def fake_popen(command, **_kwargs):
        commands.append(list(command))
        return _FakeProcess()

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    return commands


def test_proven_legacy_job_behavior_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _data_dir, _database, jobs_root = _legacy_sandbox(monkeypatch, tmp_path)
    commands = _install_fake_job_runtime(monkeypatch)

    result = jobs.create_chaptered_import_job_process(_payload())

    assert result["status"] == "running"
    assert len(commands) == 1
    job_dir = jobs_root / result["job_id"]
    assert (job_dir / "payload.json").is_file()
    assert (job_dir / "status.json").is_file()
    assert (job_dir / "worker.log").is_file()


def test_explicit_isolated_job_root_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolated_data = tmp_path / "isolated"
    isolated_db = isolated_data / "db" / "test.db"
    _write(isolated_db, b"isolated")
    jobs_root = isolated_data / "runtime" / "jobs"
    monkeypatch.setattr(jobs, "DATA_DIR", isolated_data)
    monkeypatch.setattr(jobs, "DEFAULT_DB_PATH", isolated_db)
    monkeypatch.setattr(jobs, "IMPORT_JOBS_ROOT", jobs_root)
    monkeypatch.setattr(jobs, "_processes", {})
    commands = _install_fake_job_runtime(monkeypatch)

    result = jobs.create_chaptered_import_job_process(_payload())

    assert result["status"] == "running"
    assert len(commands) == 1
    assert (jobs_root / result["job_id"] / "status.json").is_file()
