from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.services import (
    vector_store_service,
    zotero_direction_b_import_service,
    zotero_selected_book_preview_service,
)
from app.services.retrieval import fts_index_service
from app.services.retrieval.fts_schema import ORDINARY_TABLE
from scripts.migrations import migrate_zotero_personal_notes_schema as migration


def _temp_db(root: Path) -> Path:
    root.mkdir(parents=True)
    path = root / "research.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                content_layer TEXT,
                object_import_mode TEXT,
                source_path TEXT,
                pdf_path TEXT,
                zotero_key TEXT,
                created_at TEXT,
                read_status TEXT
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
                heading_path TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                overlap_before TEXT,
                overlap_after TEXT,
                content_hash TEXT NOT NULL,
                pdf_path TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER,
                zotero_open_url TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                note_type VARCHAR(64) NOT NULL,
                scope_type VARCHAR(64),
                scope_path TEXT,
                source_path TEXT,
                content_hash VARCHAR(64),
                title VARCHAR(512) NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL,
                link_type VARCHAR(64) NOT NULL,
                evidence_role VARCHAR(64),
                quote_text TEXT,
                confidence FLOAT,
                created_by VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(note_id) REFERENCES personal_notes(id),
                FOREIGN KEY(chunk_id) REFERENCES knowledge_chunks(id)
            );
            CREATE TABLE zotero_inspiration_notes (
                id INTEGER PRIMARY KEY,
                marker TEXT NOT NULL
            );
            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                zotero_item_key TEXT,
                zotero_attachment_key TEXT,
                zotero_source_id TEXT,
                zotero_select_uri TEXT,
                zotero_open_pdf_uri TEXT,
                source_trace_json TEXT,
                pdf_path TEXT,
                created_at TEXT
            );
            INSERT INTO zotero_inspiration_notes VALUES (1, 'untouched');
            """
        )
        connection.commit()
    assert migration.migrate_database(path, dry_run=False)["status"] == "applied"
    return path


def _data_dir(root: Path) -> Path:
    search = root / "search_index"
    search.mkdir(parents=True)
    fts_index_service._build_database(search / "retrieval_fts_v1.db", [])
    (search / "retrieval_fts_v1_manifest.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    vector = root / "vector_store"
    vector.mkdir()
    (vector / "vector_manifest.json").write_text(
        json.dumps({"passage_count": 0, "object_count": 0, "note_count": 0}),
        encoding="utf-8",
    )
    return root


def _preview() -> dict:
    return {
        "status": "ready",
        "zotero_item": {
            "zotero_item_key": "BOOKKEY1",
            "library_id": 1,
            "title": "Selected Book",
            "item_type": "book",
        },
        "selected_attachment": {
            "zotero_attachment_key": "PDFKEY1",
            "pdf_sha256": "a" * 64,
            "page_count": 2,
        },
        "annotation_count": 1,
        "child_note_count": 1,
        "annotations": [
            {
                "source_identity": "zotero:1:annotation:ANNKEY1",
                "library_id": 1,
                "zotero_annotation_key": "ANNKEY1",
                "selected_text": "论文原文",
                "source_comment": "我的评论",
                "pdf_page": 1,
                "page_label": "1",
                "position_json": '{"pageIndex":0}',
                "source_created_at": "2026-07-01",
                "source_updated_at": "2026-07-02",
                "source_version": 1,
                "source_content_hash": "annotation-hash",
            }
        ],
        "child_notes": [
            {
                "source_identity": "zotero:1:child_note:NOTEKEY1",
                "library_id": 1,
                "zotero_note_key": "NOTEKEY1",
                "parent_kind": "regular_item",
                "zotero_attachment_key": None,
                "title": "Child note",
                "note_text": "完整 child note",
                "source_created_at": "2026-07-03",
                "source_updated_at": "2026-07-04",
                "source_version": 1,
                "source_content_hash": "child-hash",
            }
        ],
        "duplicate_check": {"duplicate_found": False},
        "warnings": [],
    }


def _install_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _preview()
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_source",
        lambda *_args, **_kwargs: (payload, Path(__file__).resolve()),
    )
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )


def _body_importer(*, preview: dict, db_path: Path) -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO documents(
                id, title, document_type, content_layer, object_import_mode,
                created_at, read_status, zotero_key
            ) VALUES (1, ?, 'book', 'body', 'full_document', '2026-07-26', 'read', 'BOOKKEY1')
            """,
            (preview["zotero_item"]["title"],),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_chunks(
                id, document_id, chunk_index, heading_path, chunk_text,
                content_hash, pdf_page_start, pdf_page_end, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, '2026-07-26', '2026-07-26')
            """,
            [
                (101, 0, '["One"]', "Context 论文原文 end", "chunk-1", 1, 1),
                (102, 1, '["Two"]', "Second passage", "chunk-2", 2, 2),
            ],
        )
        connection.commit()
    return {
        "status": "committed",
        "document_id": 1,
        "title": "Selected Book",
        "document_type": "book",
        "chunk_count": 2,
    }


@pytest.fixture
def chain(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path_factory.mktemp("b5")
    database = _temp_db(root / "db")
    data_dir = _data_dir(root / "data")
    _install_preview(monkeypatch)
    loads: list[dict] = []
    texts: list[str] = []
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda config: loads.append(config) or object(),
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_encode_text",
        lambda _model, text: texts.append(text) or [0.1, 0.2, 0.3],
    )
    monkeypatch.setattr(
        vector_store_service,
        "_active_embedding_model_path",
        lambda: "b5b3-fake-model",
    )
    return database, data_dir, loads, texts


def _commit(database: Path, data_dir: Path) -> dict:
    return zotero_direction_b_import_service.commit_selected_book_import_to_temp_db(
        preview_token="p" * 40,
        db_path=database,
        data_dir=data_dir,
        body_importer=_body_importer,
    )


def _tree_bytes(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def _assert_generated_roots_clean(data_dir: Path) -> None:
    for name in (".direction_b_index_staging", ".direction_b_index_rollback"):
        root = data_dir / name
        assert not root.exists() or not any(root.iterdir())


def test_full_temp_import_chain_publishes_real_derived_indexes(chain) -> None:
    database, data_dir, loads, texts = chain
    result = _commit(database, data_dir)
    assert result["status"] == "committed"
    assert result["fts_write_performed"] is True
    assert result["vector_store_write_performed"] is True
    assert result["derived_index_publish_performed"] is True
    assert result["full_rebuild_performed"] is False
    assert result["orphan_delete_performed"] is False
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 2
        notes = connection.execute(
            "SELECT content, selected_text FROM personal_notes ORDER BY id"
        ).fetchall()
        assert len(notes) == 2
        assert notes[0]["content"] == "我的评论"
        assert notes[0]["selected_text"] == "论文原文"
        assert connection.execute(
            "SELECT COUNT(*) FROM note_evidence_links"
        ).fetchone()[0] >= 1
    with sqlite3.connect(
        data_dir / "search_index" / "retrieval_fts_v1.db"
    ) as connection:
        counts = dict(
            connection.execute(
                f"SELECT source_type, COUNT(*) FROM {ORDINARY_TABLE} GROUP BY source_type"
            ).fetchall()
        )
    assert counts["pdf_chunk"] == 2
    assert counts["personal_note"] == 2
    db = vector_store_service.open_vector_store(
        data_dir / "vector_store" / "lancedb"
    )
    assert db.open_table(vector_store_service.PASSAGE_TABLE).count_rows() == 2
    assert db.open_table(vector_store_service.NOTE_TABLE).count_rows() == 2
    note_records = vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    )
    annotation = next(row for row in note_records if row["note_type"] == "zotero_annotation")
    assert annotation["note_text"] == "我的评论"
    assert annotation["selected_text"] == "论文原文"
    assert len(loads) == 2
    assert len(texts) == 4
    _assert_generated_roots_clean(data_dir)


@pytest.mark.parametrize("failure_stage", ["fts", "passage", "note"])
def test_staging_failures_restore_db_and_leave_actual_indexes(
    chain,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    database, data_dir, _loads, _texts = chain
    db_before = database.read_bytes()
    fts_index = data_dir / "search_index" / "retrieval_fts_v1.db"
    fts_manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    fts_before = fts_index.read_bytes()
    manifest_before = fts_manifest.read_bytes()
    vector_before = _tree_bytes(data_dir / "vector_store")
    if failure_stage == "fts":
        monkeypatch.setattr(
            fts_index_service,
            "upsert_document_retrieval_fts",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fts failure")),
        )
    elif failure_stage == "passage":
        monkeypatch.setattr(
            vector_store_service,
            "sync_affected_passage_embeddings",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("passage failure")
            ),
        )
    else:
        monkeypatch.setattr(
            vector_store_service,
            "sync_document_note_embeddings",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("note failure")
            ),
        )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as caught:
        _commit(database, data_dir)
    assert caught.value.code == "zotero_direction_b_temp_index_sync_failed"
    assert database.read_bytes() == db_before
    assert fts_index.read_bytes() == fts_before
    assert fts_manifest.read_bytes() == manifest_before
    assert _tree_bytes(data_dir / "vector_store") == vector_before
    _assert_generated_roots_clean(data_dir)


def test_partial_publish_failure_restores_all_derived_and_db(
    chain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, data_dir, _loads, _texts = chain
    db_before = database.read_bytes()
    fts_index = data_dir / "search_index" / "retrieval_fts_v1.db"
    fts_manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    fts_before = fts_index.read_bytes()
    manifest_before = fts_manifest.read_bytes()
    vector_before = _tree_bytes(data_dir / "vector_store")

    def partial_publish(**kwargs):
        os.replace(kwargs["staging_fts_index"], kwargs["fts_index_path"])
        raise RuntimeError("partial publish")

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        partial_publish,
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as caught:
        _commit(database, data_dir)
    assert caught.value.code == "zotero_direction_b_temp_index_publish_failed"
    assert database.read_bytes() == db_before
    assert fts_index.read_bytes() == fts_before
    assert fts_manifest.read_bytes() == manifest_before
    assert _tree_bytes(data_dir / "vector_store") == vector_before
    _assert_generated_roots_clean(data_dir)


PUBLISH_SUBSTAGES = [
    "fts_index_replace",
    "fts_manifest_replace",
    "vector_store_retire",
    "vector_store_publish",
    "vector_manifest_replace",
    "native_note_vector_retire",
    "native_note_vector_publish",
    "native_note_vector_cache_invalidate",
]


_B5B3_ERROR_BEFORE: dict[str, set[str]] = {
    "fts_index_replace": set(),
    "fts_manifest_replace": {"fts_index_replace"},
    "vector_store_retire": {"fts_index_replace", "fts_manifest_replace"},
    "vector_store_publish": {"fts_index_replace", "fts_manifest_replace"},
    "vector_manifest_replace": {
        "fts_index_replace", "fts_manifest_replace", "vector_store_publish",
    },
    "native_note_vector_retire": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
    },
    "native_note_vector_publish": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
    },
    "native_note_vector_cache_invalidate": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
        "native_note_vector_publish",
    },
}


def _simulate_publish_failure_at(**kwargs):
    """Run pre-steps then raise DirectionBDerivedPublishError at substage."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    _fk = kwargs
    substage = _simulate_publish_failure_at.substage
    before = _B5B3_ERROR_BEFORE[substage]

    if "fts_index_replace" in before:
        os.replace(_fk["staging_fts_index"], _fk["fts_index_path"])
    if "fts_manifest_replace" in before:
        os.replace(_fk["staging_fts_manifest"], _fk["fts_manifest_path"])
    if "vector_store_retire" in before:
        _fk["vector_store_path"].parent.mkdir(parents=True, exist_ok=True)
        if _fk["vector_store_path"].exists():
            os.replace(_fk["vector_store_path"],
                       _fk["staging_vector_store"].parent / ".retired-lancedb")
    if "vector_store_publish" in before:
        _fk["vector_store_path"].parent.mkdir(parents=True, exist_ok=True)
        if _fk["vector_store_path"].exists():
            os.replace(_fk["vector_store_path"],
                       _fk["staging_vector_store"].parent / ".retired-lancedb")
        os.replace(_fk["staging_vector_store"], _fk["vector_store_path"])
    if "vector_manifest_replace" in before:
        if _fk["staging_vector_manifest"].is_file():
            os.replace(_fk["staging_vector_manifest"], _fk["vector_manifest_path"])
    if "native_note_vector_retire" in before:
        if _fk["staging_zotero_note_vector_path"].is_dir():
            retired = _fk["staging_zotero_note_vector_path"].parent / ".retired-zotero-user-notes"
            if _fk["zotero_note_vector_path"].exists():
                os.replace(_fk["zotero_note_vector_path"], retired)
    if "native_note_vector_publish" in before:
        if _fk["staging_zotero_note_vector_path"].is_dir():
            retired = _fk["staging_zotero_note_vector_path"].parent / ".retired-zotero-user-notes"
            if _fk["zotero_note_vector_path"].exists():
                os.replace(_fk["zotero_note_vector_path"], retired)
            os.replace(_fk["staging_zotero_note_vector_path"],
                       _fk["zotero_note_vector_path"])

    raise DirectionBDerivedPublishError(
        publish_substage=substage,
        original_exception=PermissionError(f"fixture {substage} denied"),
    )


@pytest.mark.parametrize("substage", PUBLISH_SUBSTAGES)
def test_publish_substage_failure_restores_all_derived_and_reports_substage(
    chain,
    monkeypatch: pytest.MonkeyPatch,
    substage: str,
) -> None:
    database, data_dir, _loads, _texts = chain
    db_before_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    fts_index = data_dir / "search_index" / "retrieval_fts_v1.db"
    fts_manifest = data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    fts_before_sha = hashlib.sha256(fts_index.read_bytes()).hexdigest()
    manifest_before_sha = hashlib.sha256(fts_manifest.read_bytes()).hexdigest()
    vector_before = _tree_bytes(data_dir / "vector_store")
    note_vector_dir = data_dir / "vector_store" / "zotero_user_notes_v1"
    note_vector_before = _tree_bytes(note_vector_dir)

    _simulate_publish_failure_at.substage = substage
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        _simulate_publish_failure_at,
    )

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as caught:
        _commit(database, data_dir)

    assert caught.value.code == "zotero_direction_b_temp_index_publish_failed"
    assert caught.value.details["error_stage"] == "publish_started"
    assert caught.value.details["publish_substage"] == substage
    assert caught.value.details.get("cause_type") is not None
    assert caught.value.details.get("cause_message") is not None
    assert caught.value.details["rollback_attempted"] is True
    assert caught.value.details["rollback_completed"] is True
    assert caught.value.details["writes_performed"] is True
    assert caught.value.details.get("safe_to_retry") is not True

    # All derived indexes restored
    assert hashlib.sha256(database.read_bytes()).hexdigest() == db_before_sha
    assert hashlib.sha256(fts_index.read_bytes()).hexdigest() == fts_before_sha
    assert hashlib.sha256(fts_manifest.read_bytes()).hexdigest() == manifest_before_sha
    assert _tree_bytes(data_dir / "vector_store") == vector_before
    assert _tree_bytes(note_vector_dir) == note_vector_before
    _assert_generated_roots_clean(data_dir)


def test_production_guards_precede_preview_body_indexes_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _temp_db(tmp_path / "db")
    data_dir = _data_dir(tmp_path / "data")
    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("work must not start")
    )
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_source",
        forbidden,
    )
    monkeypatch.setattr(
        fts_index_service,
        "upsert_document_retrieval_fts",
        forbidden,
    )
    monkeypatch.setattr(vector_store_service, "open_vector_store", forbidden)
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        forbidden,
    )
    for db_path, root in (
        (DEFAULT_DB_PATH, data_dir),
        (database, DATA_DIR),
    ):
        with pytest.raises(
            zotero_direction_b_import_service.DirectionBSelectedBookImportError
        ) as caught:
            zotero_direction_b_import_service.commit_selected_book_import_to_temp_db(
                preview_token="unused",
                db_path=db_path,
                data_dir=root,
                body_importer=forbidden,
            )
        assert caught.value.code == "zotero_direction_b_production_not_enabled"
