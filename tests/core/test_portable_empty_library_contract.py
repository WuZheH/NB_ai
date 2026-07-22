from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import HTTPException
import pytest

from app.api.library import search as library_search_api
from app.services.retrieval.fts_status_service import get_index_status


def test_read_shelf_reports_actionable_empty_library(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing_database = tmp_path / "data" / "db" / "research_memory.db"
    monkeypatch.setattr(library_search_api, "DEFAULT_DB_PATH", missing_database)

    payload = library_search_api.read_shelf()

    assert payload["status"] == "empty_library"
    assert payload["implementation_status"] == "ready_without_data"
    assert payload["items"] == []
    assert "SEARCH_DATA_DIR" in payload["message"]
    assert payload["db_write_performed"] is False
    assert payload["core_db_write_performed"] is False


def test_search_reports_empty_results_without_creating_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing_database = tmp_path / "data" / "db" / "research_memory.db"
    monkeypatch.setattr(library_search_api, "DEFAULT_DB_PATH", missing_database)

    payload = library_search_api.search_library(q="portable search")

    assert payload["status"] == "empty_library"
    assert payload["results"] == []
    assert payload["objects"] == []
    assert not missing_database.exists()


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (sqlite3.OperationalError("private database path"), "read_shelf_database_read_failed"),
        (RuntimeError("private service detail"), "read_shelf_internal_error"),
    ],
)
def test_read_shelf_returns_stable_sanitized_failure(
    tmp_path: Path,
    monkeypatch,
    failure: Exception,
    expected_code: str,
) -> None:
    database = tmp_path / "data" / "db" / "research_memory.db"
    database.parent.mkdir(parents=True)
    database.touch()
    monkeypatch.setattr(library_search_api, "DEFAULT_DB_PATH", database)

    def fail_read(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(library_search_api.library_service, "get_library_home", fail_read)

    with pytest.raises(HTTPException) as raised:
        library_search_api.read_shelf()

    assert raised.value.status_code == 500
    assert raised.value.detail["status"] == "error"
    assert raised.value.detail["error_code"] == expected_code
    assert "private" not in str(raised.value.detail)
    assert raised.value.detail["db_write_performed"] is False


def test_retrieval_status_distinguishes_empty_library_from_missing_index(
    tmp_path: Path,
) -> None:
    payload = get_index_status(
        index_path=tmp_path / "index.db",
        manifest_path=tmp_path / "manifest.json",
        production_db_path=tmp_path / "research_memory.db",
        zotero_snapshot_path=tmp_path / "zotero.sqlite",
        notes_root=tmp_path / "notes",
        query_aliases_path=tmp_path / "aliases.json",
    )

    assert payload["status"] == "missing"
    assert payload["data_state"] == "empty_library"
    assert payload["library_database_exists"] is False
    assert payload["library_has_documents"] is False
    assert payload["production_db_write_performed"] is False


def test_retrieval_status_accepts_schema_only_database_as_empty_library(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research_memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")

    payload = get_index_status(
        index_path=tmp_path / "index.db",
        manifest_path=tmp_path / "manifest.json",
        production_db_path=database,
        zotero_snapshot_path=tmp_path / "zotero.sqlite",
        notes_root=tmp_path / "notes",
        query_aliases_path=tmp_path / "aliases.json",
    )

    assert payload["status"] == "missing"
    assert payload["data_state"] == "empty_library"
    assert payload["library_database_exists"] is True
    assert payload["library_has_documents"] is False


def test_retrieval_status_requires_index_when_library_has_documents(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research_memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO documents (id) VALUES (1)")

    payload = get_index_status(
        index_path=tmp_path / "index.db",
        manifest_path=tmp_path / "manifest.json",
        production_db_path=database,
        zotero_snapshot_path=tmp_path / "zotero.sqlite",
        notes_root=tmp_path / "notes",
        query_aliases_path=tmp_path / "aliases.json",
    )

    assert payload["data_state"] == "configured"
    assert payload["library_has_documents"] is True


def test_retrieval_status_fails_closed_for_unreadable_library_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research_memory.db"
    database.write_bytes(b"not a sqlite database")

    payload = get_index_status(
        index_path=tmp_path / "index.db",
        manifest_path=tmp_path / "manifest.json",
        production_db_path=database,
        zotero_snapshot_path=tmp_path / "zotero.sqlite",
        notes_root=tmp_path / "notes",
        query_aliases_path=tmp_path / "aliases.json",
    )

    assert payload["data_state"] == "configured"
    assert payload["library_database_exists"] is True
    assert payload["library_has_documents"] is None
