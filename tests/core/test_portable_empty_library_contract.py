from __future__ import annotations

from pathlib import Path

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
    assert payload["production_db_write_performed"] is False
