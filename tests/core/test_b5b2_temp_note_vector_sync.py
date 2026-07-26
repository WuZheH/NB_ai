from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.paths import DEFAULT_DB_PATH, LANCEDB_DIR
from app.services import vector_store_service


def _note_database(root: Path) -> Path:
    path = root / "research.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                note_type TEXT,
                title TEXT,
                content TEXT,
                summary TEXT,
                content_hash TEXT,
                selected_text TEXT,
                source_comment TEXT,
                source_record_kind TEXT,
                source_identity TEXT,
                source_content_hash TEXT,
                source_missing INTEGER,
                pdf_page INTEGER,
                page_label TEXT,
                updated_at TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO personal_notes VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    1,
                    "zotero_annotation",
                    "Annotation with comment",
                    "我的评论",
                    "annotation summary",
                    "content-1",
                    "论文原文",
                    "我的评论",
                    "zotero_annotation",
                    "annotation:one",
                    "source-1",
                    0,
                    4,
                    "4",
                    "2026-07-26",
                ),
                (
                    2,
                    1,
                    "zotero_annotation",
                    "Annotation without comment",
                    "",
                    "",
                    "content-2",
                    "只有原文摘录",
                    "",
                    "zotero_annotation",
                    "annotation:two",
                    "source-2",
                    0,
                    5,
                    "5",
                    "2026-07-26",
                ),
                (
                    3,
                    1,
                    "zotero_child_note",
                    "Child note",
                    "完整 child note",
                    "child summary",
                    "content-3",
                    None,
                    "",
                    "zotero_child_note",
                    "child:three",
                    "source-3",
                    0,
                    None,
                    None,
                    "2026-07-26",
                ),
                (
                    4,
                    2,
                    "zotero_child_note",
                    "Other document",
                    "不得同步",
                    "",
                    "content-4",
                    None,
                    "",
                    "zotero_child_note",
                    "child:four",
                    "source-4",
                    0,
                    None,
                    None,
                    "2026-07-26",
                ),
            ],
        )
        connection.commit()
    return path


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch):
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
        lambda: "isolated-note-model",
    )
    return loads, texts


def _seed_unrelated_tables(store: Path) -> tuple[object, list[dict], list[dict]]:
    db = vector_store_service.open_vector_store(store)
    db.create_table(
        vector_store_service.PASSAGE_TABLE,
        data=[
            {
                "vector_id": "chunk:90:900",
                "source_id": "chunk:90:900",
                "vector": [1.0],
            }
        ],
        mode="create",
    )
    db.create_table(
        vector_store_service.OBJECT_TABLE,
        data=[
            {
                "vector_id": "object:keep",
                "source_id": "object:keep",
                "vector": [1.0],
            }
        ],
        mode="create",
    )
    return (
        db,
        vector_store_service._existing_records(db, vector_store_service.PASSAGE_TABLE),
        vector_store_service._existing_records(db, vector_store_service.OBJECT_TABLE),
    )


def test_note_source_semantics_and_document_scope(tmp_path: Path) -> None:
    database = _note_database(tmp_path)
    sources = vector_store_service.collect_personal_note_sources(
        document_id=1,
        source_db_path=database,
    )
    assert [source["source_id"] for source in sources] == [
        "note:1",
        "note:2",
        "note:3",
    ]
    records = [
        vector_store_service.build_note_schema_record(source)
        for source in sources
    ]
    assert records[0]["note_text"] == "我的评论"
    assert records[0]["selected_text"] == "论文原文"
    assert records[1]["note_text"] == ""
    assert records[1]["selected_text"] == "只有原文摘录"
    assert "Note:" not in records[1]["text_for_embedding"]
    assert "Selected evidence: 只有原文摘录" in records[1]["text_for_embedding"]
    assert records[2]["note_text"] == "完整 child note"
    assert records[2]["selected_text"] == ""
    assert all(record["document_id"] == 1 for record in records)
    assert "note:4" not in {source["source_id"] for source in sources}


def test_note_first_apply_noop_stale_and_orphan_preservation(
    tmp_path: Path,
    fake_embedding,
) -> None:
    loads, texts = fake_embedding
    database = _note_database(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    manifest.write_text(
        json.dumps({"passage_count": 7, "object_count": 5, "embedding_dim": 3}),
        encoding="utf-8",
    )
    db, passage_before, object_before = _seed_unrelated_tables(store)

    first = vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    assert first == {
        "kind": "notes",
        "scope": "document_only",
        "document_id": 1,
        "dry_run": False,
        "apply": True,
        "source_count": 3,
        "inserted_count": 3,
        "updated_count": 0,
        "skipped_count": 0,
        "note_count": 3,
        "full_rebuild_performed": False,
        "orphan_delete_performed": False,
        "lancedb_writes_performed": True,
        "production_data_modified": False,
    }
    assert len(loads) == 1
    assert len(texts) == 3
    note_rows = vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    )
    assert {row["source_id"] for row in note_rows} == {"note:1", "note:2", "note:3"}
    assert next(row for row in note_rows if row["source_id"] == "note:2")["note_text"] == ""
    assert "Selected evidence: 只有原文摘录" in texts[1]
    assert vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    ) == passage_before
    assert vector_store_service._existing_records(
        db,
        vector_store_service.OBJECT_TABLE,
    ) == object_before

    orphan = dict(note_rows[0])
    orphan.update(
        {
            "vector_id": "note:999999",
            "source_id": "note:999999",
            "note_id": 999999,
        }
    )
    db.open_table(vector_store_service.NOTE_TABLE).add([orphan])
    second = vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    assert second["inserted_count"] == 0
    assert second["updated_count"] == 0
    assert second["skipped_count"] == 3
    assert second["lancedb_writes_performed"] is False
    assert len(loads) == 1
    assert len(texts) == 3
    assert "note:999999" in {
        row["source_id"]
        for row in vector_store_service._existing_records(
            db,
            vector_store_service.NOTE_TABLE,
        )
    }

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE personal_notes SET selected_text = ? WHERE id = 1",
            ("论文原文更新",),
        )
        connection.commit()
    selected_stale = vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    assert selected_stale["updated_count"] == 1
    assert selected_stale["skipped_count"] == 2
    assert len(texts) == 4

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE personal_notes SET source_missing = 1 WHERE id = 1"
        )
        connection.commit()
    missing_stale = vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    assert missing_stale["updated_count"] == 1
    assert missing_stale["skipped_count"] == 2
    assert len(texts) == 5
    assert vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    ) == passage_before
    assert vector_store_service._existing_records(
        db,
        vector_store_service.OBJECT_TABLE,
    ) == object_before

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["note_profile_version"] == "note_profile_v1"
    assert payload["note_count"] == 4
    assert payload["passage_count"] == 7
    assert payload["object_count"] == 5


def test_note_schema_mismatch_never_rebuilds_or_loads_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _note_database(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "manifest.json"
    db = vector_store_service.open_vector_store(store)
    db.create_table(
        vector_store_service.NOTE_TABLE,
        data=[{"vector_id": "note:1", "source_id": "note:1", "vector": [0.1]}],
        mode="create",
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not load")
        ),
    )
    with pytest.raises(vector_store_service.VectorStoreSchemaMismatch):
        vector_store_service.sync_document_note_embeddings(
            1,
            dry_run=False,
            apply=True,
            source_db_path=database,
            store_path=store,
            manifest_path=manifest,
        )
    assert vector_store_service._table_names(db) == [vector_store_service.NOTE_TABLE]


def test_note_apply_production_guards_run_before_store_or_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _note_database(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(
        vector_store_service,
        "open_vector_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vector store must not open")
        ),
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not load")
        ),
    )
    cases = [
        {
            "source_db_path": DEFAULT_DB_PATH,
            "store_path": store,
            "manifest_path": manifest,
        },
        {
            "source_db_path": database,
            "store_path": LANCEDB_DIR,
            "manifest_path": manifest,
        },
        {
            "source_db_path": database,
            "store_path": store,
            "manifest_path": vector_store_service.MANIFEST_PATH,
        },
    ]
    for paths in cases:
        with pytest.raises(ValueError):
            vector_store_service.sync_document_note_embeddings(
                1,
                dry_run=False,
                apply=True,
                **paths,
            )
