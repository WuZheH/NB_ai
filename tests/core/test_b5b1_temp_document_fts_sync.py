from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.paths import DEFAULT_DB_PATH
from app.services.retrieval import fts_index_service
from app.services.retrieval.fts_schema import ORDINARY_TABLE, validate_index_database
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
)


def _temp_research_db(root: Path) -> Path:
    path = root / "research.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                document_type TEXT NOT NULL,
                object_import_mode TEXT,
                read_status TEXT NOT NULL,
                pdf_path TEXT,
                source_path TEXT,
                zotero_key TEXT
            );
            CREATE TABLE markdown_nodes (
                id INTEGER PRIMARY KEY,
                order_index INTEGER
            );
            CREATE TABLE book_chapters (
                id INTEGER PRIMARY KEY,
                chapter_index INTEGER,
                title TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                node_id INTEGER,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT,
                chunk_text TEXT,
                overlap_before TEXT,
                overlap_after TEXT,
                content_hash TEXT,
                pdf_path TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER,
                zotero_open_url TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                title TEXT,
                content TEXT,
                note_type TEXT,
                scope_type TEXT,
                scope_path TEXT,
                summary TEXT,
                content_hash TEXT,
                source_path TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Target Book", "book", "full_document", "read", None, None, None),
                (2, "Other Book", "book", "full_document", "read", None, None, None),
            ],
        )
        connection.executemany(
            "INSERT INTO knowledge_chunks VALUES "
            "(?, ?, NULL, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?, NULL, NULL, ?, ?)",
            [
                (11, 1, 0, '["Target"]', "target passage v1", "hash-target-v1", 1, 1, "2026-07-26", "2026-07-26"),
                (21, 2, 0, '["Other"]', "other passage", "hash-other", 2, 2, "2026-07-26", "2026-07-26"),
            ],
        )
        connection.executemany(
            "INSERT INTO personal_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (101, 1, "Target note", "note v1", "note", "document", "Target", None, "note-hash-v1", None, "2026-07-26", "2026-07-26"),
                (201, 2, "Other note", "other note", "note", "document", "Other", None, "note-hash-other", None, "2026-07-26", "2026-07-26"),
            ],
        )
        connection.commit()
    return path


def _empty_index(root: Path) -> tuple[Path, Path]:
    index = root / "retrieval.sqlite"
    manifest = root / "retrieval-manifest.json"
    fts_index_service._build_database(index, [])
    manifest.write_text("{}\n", encoding="utf-8")
    return index, manifest


def _document_rows(index: Path, document_id: int) -> list[tuple]:
    with sqlite3.connect(index) as connection:
        return connection.execute(
            f"SELECT fragment_id, source_type, text, note_comment "
            f"FROM {ORDINARY_TABLE} WHERE document_id = ? ORDER BY fragment_id",
            (document_id,),
        ).fetchall()


def test_temp_document_fts_upsert_is_incremental_and_idempotent(
    tmp_path: Path,
) -> None:
    database = _temp_research_db(tmp_path)
    index, manifest = _empty_index(tmp_path)
    other = fts_index_service.upsert_document_retrieval_fts(
        document_id=2,
        index_path=index,
        manifest_path=manifest,
        research_db_path=database,
    )
    assert other["inserted_fragment_rows"] == 2
    other_rows_before = _document_rows(index, 2)

    first = fts_index_service.upsert_document_retrieval_fts(
        document_id=1,
        index_path=index,
        manifest_path=manifest,
        research_db_path=database,
    )
    assert first["pdf_chunk_rows"] == 1
    assert first["personal_note_rows"] == 1
    assert first["inserted_fragment_rows"] == 2
    assert first["full_rebuild_performed"] is False
    assert first["production_db_write_performed"] is False
    assert _document_rows(index, 2) == other_rows_before

    second = fts_index_service.upsert_document_retrieval_fts(
        document_id=1,
        index_path=index,
        manifest_path=manifest,
        research_db_path=database,
    )
    assert second["removed_fragment_rows"] == 2
    assert second["inserted_fragment_rows"] == 2
    assert len(_document_rows(index, 1)) == 2

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE knowledge_chunks SET chunk_text = ?, content_hash = ? WHERE id = 11",
            ("target passage v2", "hash-target-v2"),
        )
        connection.execute(
            "UPDATE personal_notes SET content = ?, content_hash = ? WHERE id = 101",
            ("note v2", "note-hash-v2"),
        )
        connection.commit()
    replaced = fts_index_service.upsert_document_retrieval_fts(
        document_id=1,
        index_path=index,
        manifest_path=manifest,
        research_db_path=database,
    )
    assert replaced["removed_fragment_rows"] == 2
    target_rows = _document_rows(index, 1)
    assert "target passage v1" not in str(target_rows)
    assert "note v1" not in str(target_rows)
    assert "target passage v2" in str(target_rows)
    assert "note v2" in str(target_rows)
    assert _document_rows(index, 2) == other_rows_before

    with sqlite3.connect(index) as connection:
        validation = validate_index_database(
            connection,
            expected_fragment_count=4,
        )
    assert validation["valid"] is True
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["fragment_count"] == 4
    assert payload["source_type_counts"]["pdf_chunk"] == 2
    assert payload["source_type_counts"]["personal_note"] == 2
    assert payload["last_document_upsert_at"]
    assert payload["last_document_upsert_duration_ms"] >= 0


def test_temp_document_fts_manifest_failure_restores_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _temp_research_db(tmp_path)
    index, manifest = _empty_index(tmp_path)
    index_before = index.read_bytes()
    manifest_before = manifest.read_bytes()
    monkeypatch.setattr(
        fts_index_service,
        "_refresh_manifest_after_document_change",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest failed")),
    )
    with pytest.raises(RuntimeError, match="manifest failed"):
        fts_index_service.upsert_document_retrieval_fts(
            document_id=1,
            index_path=index,
            manifest_path=manifest,
            research_db_path=database,
        )
    assert index.read_bytes() == index_before
    assert manifest.read_bytes() == manifest_before
    assert not list(tmp_path.glob("*.backup"))


def test_temp_document_fts_rejects_every_production_path_before_change(
    tmp_path: Path,
) -> None:
    database = _temp_research_db(tmp_path)
    index, manifest = _empty_index(tmp_path)
    original = index.read_bytes()
    cases = [
        {
            "research_db_path": DEFAULT_DB_PATH,
            "index_path": index,
            "manifest_path": manifest,
        },
        {
            "research_db_path": database,
            "index_path": DEFAULT_INDEX_PATH,
            "manifest_path": manifest,
        },
        {
            "research_db_path": database,
            "index_path": index,
            "manifest_path": DEFAULT_MANIFEST_PATH,
        },
    ]
    for paths in cases:
        with pytest.raises(ValueError):
            fts_index_service.upsert_document_retrieval_fts(
                document_id=1,
                **paths,
            )
        assert index.read_bytes() == original
