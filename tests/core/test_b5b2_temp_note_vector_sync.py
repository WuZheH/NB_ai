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


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [("document_id", 8), ("note_id", 999)],
)
def test_note_sync_repairs_identity_metadata_drift(
    tmp_path: Path,
    fake_embedding,
    field: str,
    wrong_value: int,
) -> None:
    loads, texts = fake_embedding
    database = _note_database(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    db = vector_store_service.open_vector_store(store)
    table = db.open_table(vector_store_service.NOTE_TABLE)
    row = next(
        record
        for record in vector_store_service._existing_records(
            db,
            vector_store_service.NOTE_TABLE,
        )
        if record["source_id"] == "note:1"
    )
    table.delete("source_id = 'note:1'")
    row[field] = wrong_value
    table.add([row])

    result = vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )

    repaired = next(
        record
        for record in vector_store_service._existing_records(
            db,
            vector_store_service.NOTE_TABLE,
        )
        if record["source_id"] == "note:1"
    )
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 2
    assert repaired["document_id"] == 1
    assert repaired["note_id"] == 1
    assert len(loads) == 2
    assert len(texts) == 4


@pytest.mark.parametrize(
    "corruption",
    ("missing", "wrong_document", "wrong_note", "orphan", "duplicate"),
)
def test_strict_document_note_state_detects_candidate_corruption(
    tmp_path: Path,
    fake_embedding,
    corruption: str,
) -> None:
    database = _note_database(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    sources = vector_store_service.collect_personal_note_sources(
        document_id=1,
        source_db_path=database,
    )
    vector_store_service.sync_document_note_embeddings(
        1,
        dry_run=False,
        apply=True,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )
    db = vector_store_service.open_vector_store(store)
    table = db.open_table(vector_store_service.NOTE_TABLE)
    row = next(
        dict(record)
        for record in vector_store_service._existing_records(
            db,
            vector_store_service.NOTE_TABLE,
        )
        if record["source_id"] == "note:1"
    )
    if corruption != "duplicate":
        table.delete("source_id = 'note:1'")
    if corruption == "wrong_document":
        row["document_id"] = 8
        table.add([row])
    elif corruption == "wrong_note":
        row["note_id"] = 999
        table.add([row])
    elif corruption == "orphan":
        table.add([row])
        orphan = dict(row)
        orphan["source_id"] = "note:999"
        orphan["vector_id"] = "note:999"
        orphan["note_id"] = 999
        table.add([orphan])
    elif corruption == "duplicate":
        table.add([row])

    state = vector_store_service.inspect_document_note_vector_state(
        document_id=1,
        expected_sources=sources,
        store_path=store,
    )

    assert state["status"] == "ok"
    if corruption in {"missing", "wrong_document"}:
        assert state["missing_source_ids"] == ["note:1"]
    if corruption in {"wrong_document", "wrong_note"}:
        assert state["stale_source_ids"] == ["note:1"]
    if corruption == "orphan":
        assert state["orphan_source_ids"] == ["note:999"]
    if corruption == "duplicate":
        assert state["duplicate_source_ids"] == ["note:1"]


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


def _reused_scope_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    corrupt_field: str | None = None,
    corrupt_value: object = None,
    duplicate: bool = False,
) -> tuple[Path, Path, Path, dict]:
    database = _note_database(tmp_path)
    store = tmp_path / "candidate-lancedb"
    manifest = tmp_path / "candidate-vector-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "embedding_dim": 3,
                "note_count": 1,
                "passage_count": 0,
                "object_count": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        vector_store_service,
        "_expected_embedding_dim",
        lambda: 3,
    )
    monkeypatch.setattr(
        vector_store_service,
        "_active_embedding_model_path",
        lambda: "isolated-note-model",
    )
    source = vector_store_service.collect_personal_note_sources(
        document_id=2,
        source_db_path=database,
    )[0]
    row = vector_store_service.build_note_schema_record(source)
    row["document_id"] = 1
    row["vector"] = [0.125, 0.25, 0.5]
    if corrupt_field is not None:
        row[corrupt_field] = corrupt_value
    records = [row, dict(row)] if duplicate else [row]
    vector_store_service.open_vector_store(store).create_table(
        vector_store_service.NOTE_TABLE,
        data=records,
        mode="create",
    )
    return database, store, manifest, row


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    (
        pytest.param("vector_id", "note:999", id="wrong_vector_id"),
        pytest.param("source_type", "passage", id="wrong_source_type"),
        pytest.param("note_type", "wrong-note-type", id="wrong_note_type"),
        pytest.param("title", "wrong materialized title", id="wrong_title"),
        pytest.param(
            "text_for_embedding",
            "wrong embedding materialization",
            id="wrong_text_for_embedding",
        ),
        pytest.param("embedding_dim", 2, id="wrong_embedding_dim"),
        pytest.param("source_hash", "f" * 64, id="stale_source_hash"),
    ),
)
def test_reused_note_scope_reconciliation_fails_before_mutation_on_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    wrong_value: object,
) -> None:
    database, store, manifest, _row = _reused_scope_candidate(
        tmp_path,
        monkeypatch,
        corrupt_field=field,
        corrupt_value=wrong_value,
    )
    db = vector_store_service.open_vector_store(store)
    before_rows = vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    )
    before_manifest = manifest.read_bytes()

    with pytest.raises(vector_store_service.VectorStoreSchemaMismatch):
        vector_store_service.reconcile_reused_document_note_vector_scope(
            document_id=1,
            source_db_path=database,
            store_path=store,
            manifest_path=manifest,
        )

    assert vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    ) == before_rows
    assert manifest.read_bytes() == before_manifest


def test_reused_note_scope_reconciliation_rejects_duplicate_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, manifest, _row = _reused_scope_candidate(
        tmp_path,
        monkeypatch,
        duplicate=True,
    )
    db = vector_store_service.open_vector_store(store)
    before_rows = vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    )
    before_manifest = manifest.read_bytes()

    with pytest.raises(vector_store_service.VectorStoreSchemaMismatch):
        vector_store_service.reconcile_reused_document_note_vector_scope(
            document_id=1,
            source_db_path=database,
            store_path=store,
            manifest_path=manifest,
        )

    assert vector_store_service._existing_records(
        db,
        vector_store_service.NOTE_TABLE,
    ) == before_rows
    assert manifest.read_bytes() == before_manifest


def test_reused_note_scope_reconciliation_preserves_embedding_for_scope_only_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, manifest, seeded = _reused_scope_candidate(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scope-only reconciliation must not load the encoder")
        ),
    )

    result = vector_store_service.reconcile_reused_document_note_vector_scope(
        document_id=1,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )

    rows = vector_store_service._existing_records(
        vector_store_service.open_vector_store(store),
        vector_store_service.NOTE_TABLE,
    )
    assert len(rows) == 1
    assert rows[0]["source_id"] == "note:4"
    assert rows[0]["document_id"] == 2
    assert rows[0]["vector"] == seeded["vector"]
    assert result["reassigned_note_vectors"] == 1
    assert result["deleted_orphan_note_vectors"] == 0
    assert result["scoped_orphan_delete_performed"] is False
    assert result["global_orphan_sweep_performed"] is False


def test_reused_note_scope_reconciliation_reports_scoped_orphan_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, manifest, seeded = _reused_scope_candidate(
        tmp_path,
        monkeypatch,
    )
    orphan = dict(seeded)
    orphan.update(
        {
            "vector_id": "note:999",
            "source_id": "note:999",
            "note_id": 999,
        }
    )
    vector_store_service.open_vector_store(store).open_table(
        vector_store_service.NOTE_TABLE
    ).add([orphan])

    result = vector_store_service.reconcile_reused_document_note_vector_scope(
        document_id=1,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )

    assert result["deleted_orphan_note_vectors"] == 1
    assert result["scoped_orphan_delete_performed"] is True
    assert result["global_orphan_sweep_performed"] is False
    assert result["orphan_delete_performed"] is False


def test_reused_note_scope_reconciliation_keeps_candidate_dimension_when_table_empties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, manifest, seeded = _reused_scope_candidate(
        tmp_path,
        monkeypatch,
    )
    table = vector_store_service.open_vector_store(store).open_table(
        vector_store_service.NOTE_TABLE
    )
    table.delete("source_id = 'note:4'")
    orphan = dict(seeded)
    orphan.update(
        {
            "vector_id": "note:999",
            "source_id": "note:999",
            "note_id": 999,
        }
    )
    table.add([orphan])
    monkeypatch.setattr(
        vector_store_service,
        "_embedding_dim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty candidate table must not consult another manifest")
        ),
    )

    result = vector_store_service.reconcile_reused_document_note_vector_scope(
        document_id=1,
        source_db_path=database,
        store_path=store,
        manifest_path=manifest,
    )

    assert result["note_count"] == 0
    assert result["deleted_orphan_note_vectors"] == 1
    assert json.loads(manifest.read_text(encoding="utf-8"))["embedding_dim"] == 3
