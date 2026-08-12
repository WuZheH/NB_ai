from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.api import schemas as api_schemas
from app.api.library import importing as importing_api
from app.services import pdf_import_classifier_service
from app.services import production_write_surface_guard as guard
from app.services import retrieval_generation_service
from app.services import zotero_inspiration_note_service
from app.services import zotero_source_cache_service
from app.services.library import book_archive_service


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _formal_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    database = data_dir / "db" / "research_memory.db"
    database.parent.mkdir(parents=True)
    monkeypatch.setattr(guard, "DATA_DIR", data_dir)
    monkeypatch.setattr(guard, "DEFAULT_DB_PATH", database)
    monkeypatch.setattr(book_archive_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(zotero_inspiration_note_service, "DATA_DIR", data_dir)
    return data_dir, database


def _set_versioned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        retrieval_generation_service,
        "current_retrieval_generation",
        lambda **_kwargs: SimpleNamespace(mode="versioned"),
    )


def _assert_frozen(
    exc: guard.ProductionWriteSurfaceFrozenError,
    *,
    error_code: str,
    reason_code: str = "versioned_retrieval_generation_active",
) -> None:
    assert exc.error_code == error_code
    assert exc.status_code == 503
    assert exc.reason_code == reason_code
    assert exc.detail() == {
        "status": "error",
        "error_code": error_code,
        "message": str(exc),
        "reason_code": reason_code,
        "retryable": False,
        "safe_to_retry": False,
        "writes_performed": False,
        "production_data_modified": False,
    }


def test_guard_allows_only_a_proven_coherent_legacy_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"proven legacy database revision")
    manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"production_db_sha256": _sha256(database)}),
        encoding="utf-8",
    )

    guard.require_proven_legacy_for_legacy_write_surface(
        error_code="legacy_surface_frozen",
        message="frozen",
    )


def test_guard_fails_closed_for_ambiguous_pointer_absence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"database without a legacy manifest")

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        guard.require_proven_legacy_for_legacy_write_surface(
            error_code="legacy_surface_frozen",
            message="frozen",
        )

    _assert_frozen(
        caught.value,
        error_code="legacy_surface_frozen",
        reason_code="active_index_invalid",
    )


def test_guard_fails_closed_when_activation_marker_survives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"legacy database")
    manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"production_db_sha256": _sha256(database)}),
        encoding="utf-8",
    )
    (data_dir / "retrieval_generation_activation.json").write_text(
        "{}",
        encoding="utf-8",
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        guard.require_proven_legacy_for_legacy_write_surface(
            error_code="legacy_surface_frozen",
            message="frozen",
        )

    _assert_frozen(
        caught.value,
        error_code="legacy_surface_frozen",
        reason_code="retrieval_generation_degraded",
    )


def test_guard_fails_closed_for_half_production_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"database")

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        guard.require_proven_legacy_for_legacy_write_surface(
            error_code="legacy_surface_frozen",
            message="frozen",
            db_path=database,
            data_dir=data_dir / "different-root",
        )

    _assert_frozen(
        caught.value,
        error_code="legacy_surface_frozen",
        reason_code="production_write_target_ambiguous",
    )


def test_guard_preserves_explicit_isolated_database_compatibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"formal")
    calls = 0

    def unexpected_resolve(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("isolated targets do not resolve production generation state")

    monkeypatch.setattr(
        retrieval_generation_service,
        "resolve_active_retrieval_generation",
        unexpected_resolve,
    )
    guard.require_proven_legacy_for_legacy_write_surface(
        error_code="legacy_surface_frozen",
        message="frozen",
        db_path=tmp_path / "isolated" / "test.db",
        data_dir=tmp_path / "isolated",
    )
    assert calls == 0
    assert data_dir.is_dir()


def test_legacy_pdf_commit_is_frozen_before_classification_or_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"unchanged production db")
    pointer = data_dir / "active_index.json"
    pointer.write_bytes(b"unchanged pointer")
    before = (_sha256(database), _sha256(pointer))
    _set_versioned(monkeypatch)
    monkeypatch.setattr(
        pdf_import_classifier_service,
        "classify_pdf_import",
        lambda *_args, **_kwargs: pytest.fail("classification must not run"),
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        pdf_import_classifier_service.commit_pdf_import({})

    _assert_frozen(caught.value, error_code="legacy_pdf_commit_versioned_frozen")
    assert (_sha256(database), _sha256(pointer)) == before


def test_legacy_pdf_commit_api_exposes_stable_nonretryable_freeze(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"unchanged production db")
    _set_versioned(monkeypatch)
    request = api_schemas.PdfImportCommitRequest(
        pdf_path="D:/never-opened.pdf",
        document_type="book",
        object_import_mode="metadata_only",
    )

    response = importing_api.commit_pdf_import(request)
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["error_code"] == "legacy_pdf_commit_versioned_frozen"
    assert payload["safe_to_retry"] is False
    assert payload["writes_performed"] is False
    assert payload["production_data_modified"] is False


@pytest.mark.parametrize("operation", ["archive", "restore"])
def test_archive_and_restore_are_frozen_before_database_open(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"not even opened")
    pointer = data_dir / "active_index.json"
    pointer.write_bytes(b"unchanged pointer")
    before = (_sha256(database), _sha256(pointer))
    _set_versioned(monkeypatch)
    monkeypatch.setattr(
        book_archive_service,
        "_write_connection",
        lambda *_args, **_kwargs: pytest.fail("database must not be opened"),
    )

    function = (
        book_archive_service.archive_documents
        if operation == "archive"
        else book_archive_service.restore_documents
    )
    with pytest.raises(book_archive_service.ArchiveError) as caught:
        function([1], db_path=database)

    assert caught.value.error_code == "library_archive_versioned_frozen"
    assert caught.value.status_code == 503
    assert caught.value.details["safe_to_retry"] is False
    assert caught.value.details["writes_performed"] is False
    assert (_sha256(database), _sha256(pointer)) == before


@pytest.mark.parametrize(
    ("operation", "error_code", "forbidden_helper"),
    [
        (
            "refresh_snapshot",
            "zotero_snapshot_refresh_versioned_frozen",
            "_load_config",
        ),
        (
            "sync_pdf_sources",
            "zotero_pdf_source_sync_versioned_frozen",
            "_ensure_tables",
        ),
    ],
)
def test_zotero_snapshot_and_pdf_sync_freeze_before_copy_or_database_write(
    operation: str,
    error_code: str,
    forbidden_helper: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    database.write_bytes(b"unchanged production db")
    pointer = data_dir / "active_index.json"
    pointer.write_bytes(b"unchanged pointer")
    snapshot = data_dir / "zotero" / "snapshot" / "zotero.sqlite"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"unchanged snapshot")
    active_artifact = data_dir / "index_versions" / "g-active" / "artifact.bin"
    active_artifact.parent.mkdir(parents=True)
    active_artifact.write_bytes(b"unchanged active artifact")
    before = tuple(
        _sha256(path) for path in (database, pointer, snapshot, active_artifact)
    )
    _set_versioned(monkeypatch)
    monkeypatch.setattr(
        zotero_source_cache_service,
        forbidden_helper,
        lambda *_args, **_kwargs: pytest.fail("mutation preparation must not run"),
    )

    with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
        getattr(zotero_source_cache_service, operation)()

    _assert_frozen(caught.value, error_code=error_code)
    assert tuple(
        _sha256(path) for path in (database, pointer, snapshot, active_artifact)
    ) == before


def test_zotero_pdf_source_reads_never_create_missing_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        zotero_source_cache_service,
        "_require_tables_for_read",
        lambda: (_ for _ in ()).throw(ValueError("schema unavailable")),
    )
    monkeypatch.setattr(
        zotero_source_cache_service,
        "_ensure_tables",
        lambda: pytest.fail("read requests must not create tables"),
    )

    with pytest.raises(ValueError, match="schema unavailable"):
        zotero_source_cache_service.list_pdf_sources()


def test_zotero_pdf_source_read_schema_check_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "read-schema.db"
    isolated_engine = create_engine(f"sqlite:///{database.as_posix()}")
    monkeypatch.setattr(zotero_source_cache_service, "engine", isolated_engine)
    try:
        with pytest.raises(ValueError, match="schema is unavailable"):
            zotero_source_cache_service._require_tables_for_read()
        zotero_source_cache_service.Base.metadata.create_all(
            bind=isolated_engine,
            tables=[
                zotero_source_cache_service.ZoteroPdfSource.__table__,
                zotero_source_cache_service.DocumentSource.__table__,
            ],
        )
        before = _sha256(database)

        zotero_source_cache_service._require_tables_for_read()

        assert _sha256(database) == before
    finally:
        isolated_engine.dispose()


@pytest.mark.parametrize("operation", ["single", "batch"])
def test_inspiration_note_writes_freeze_before_schema_or_payload_work(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir, database = _formal_root(monkeypatch, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel (value) VALUES ('unchanged')")
    pointer = data_dir / "active_index.json"
    pointer.write_bytes(b"unchanged pointer")
    before = (_sha256(database), _sha256(pointer))
    _set_versioned(monkeypatch)
    monkeypatch.setattr(
        zotero_inspiration_note_service,
        "_require_schema",
        lambda *_args, **_kwargs: pytest.fail("schema/payload work must not run"),
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(guard.ProductionWriteSurfaceFrozenError) as caught:
            if operation == "single":
                zotero_inspiration_note_service.upsert_inspiration_note(
                    connection,
                    {},
                )
            else:
                zotero_inspiration_note_service.batch_upsert_inspiration_notes(
                    connection,
                    [],
                )

    _assert_frozen(
        caught.value,
        error_code="zotero_inspiration_note_write_versioned_frozen",
    )
    assert (_sha256(database), _sha256(pointer)) == before
