from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.paths import DEFAULT_DB_PATH, LANCEDB_DIR
from app.services import vector_store_service
from app.services import zotero_direction_b_import_service


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
                read_status TEXT NOT NULL
            );
            CREATE TABLE book_chapters (
                id INTEGER PRIMARY KEY,
                title TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT,
                chunk_text TEXT,
                content_hash TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER,
                updated_at TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Target", "book", "full_document", "read"),
                (2, "Other", "book", "full_document", "read"),
                (3, "Archived", "book", "full_document", "archived"),
            ],
        )
        connection.executemany(
            "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (11, 1, 0, "Target", "target text", "target-hash", 1, 1, None, "2026-07-26"),
                (12, 1, 1, "Metadata", "backend: metadata only", "metadata-hash", None, None, None, "2026-07-26"),
                (21, 2, 0, "Other", "other text", "other-hash", 2, 2, None, "2026-07-26"),
                (31, 3, 0, "Archived", "archived text", "archived-hash", 3, 3, None, "2026-07-26"),
            ],
        )
        connection.commit()
    return path


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch):
    loads: list[dict] = []
    encodes: list[str] = []
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_load_model",
        lambda config: loads.append(config) or object(),
    )
    monkeypatch.setattr(
        vector_store_service.local_embedding_service,
        "_encode_text",
        lambda _model, text: encodes.append(text) or [0.1, 0.2, 0.3],
    )
    monkeypatch.setattr(
        vector_store_service,
        "_active_embedding_model_path",
        lambda: "isolated-model",
    )
    return loads, encodes


def test_temp_passage_source_is_readonly_exact_and_avoids_sessionlocal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _temp_research_db(tmp_path)
    monkeypatch.setattr(
        vector_store_service,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("SessionLocal must not run")),
    )
    sources = vector_store_service.collect_passage_sources(
        source_ids=["chunk:1:11", "chunk:1:12", "chunk:2:21", "chunk:3:31"],
        source_db_path=database,
    )
    assert [item["source_id"] for item in sources] == [
        "chunk:1:11",
        "chunk:2:21",
    ]
    assert all(item["source_type"] == "passage" for item in sources)


def test_direction_b_passage_expectations_reuse_authoritative_vector_sources(
    tmp_path: Path,
) -> None:
    database = _temp_research_db(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (13, 1, 2, "Empty", "   ", "empty-hash", None, None, None, "2026-07-26"),
        )
        connection.commit()

    sources = vector_store_service.collect_passage_sources(
        document_id=1,
        source_db_path=database,
    )
    expected = [source["source_id"] for source in sources]
    direction_expected = (
        zotero_direction_b_import_service._passage_source_ids_for_document(
            database,
            1,
        )
    )

    assert expected == ["chunk:1:11"]
    assert direction_expected == expected


def test_temp_passage_source_batches_more_than_sqlite_expression_depth(
    tmp_path: Path,
) -> None:
    database = _temp_research_db(tmp_path)
    chunk_count = 1_205
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    chunk_id,
                    1,
                    chunk_index,
                    "Target",
                    f"target text {chunk_index}",
                    f"target-hash-{chunk_index}",
                    chunk_index + 1,
                    chunk_index + 1,
                    None,
                    "2026-07-28",
                )
                for chunk_index, chunk_id in enumerate(
                    range(1_000, 1_000 + chunk_count)
                )
            ],
        )
        connection.commit()

    requested = [
        f"chunk:1:{chunk_id}"
        for chunk_id in range(1_000, 1_000 + chunk_count)
    ]
    sources = vector_store_service.collect_passage_sources(
        source_ids=list(reversed(requested)),
        source_db_path=database,
    )

    assert len(sources) == chunk_count
    assert {item["source_id"] for item in sources} == set(requested)
    assert sources[0]["source_id"] == "chunk:1:1000"
    assert sources[-1]["source_id"] == f"chunk:1:{999 + chunk_count}"


def test_sqlite_passage_batches_preserve_document_filter(
    tmp_path: Path,
) -> None:
    database = _temp_research_db(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1_000 + offset,
                    1,
                    10 + offset,
                    "Other",
                    f"other {offset}",
                    f"other-hash-{offset}",
                    None,
                    None,
                    None,
                    "2026-08-09",
                )
                for offset in range(450)
            ]
            + [
                (
                    2_000 + offset,
                    2,
                    10 + offset,
                    "Target",
                    f"target {offset}",
                    f"target-hash-{offset}",
                    None,
                    None,
                    None,
                    "2026-08-09",
                )
                for offset in range(10)
            ],
        )
        connection.commit()

    requested = [f"chunk:1:{1_000 + offset}" for offset in range(450)] + [
        f"chunk:2:{2_000 + offset}" for offset in range(10)
    ]
    sources = vector_store_service.collect_passage_sources(
        source_ids=requested,
        source_db_path=database,
        document_id=2,
    )

    assert [source["source_id"] for source in sources] == [
        f"chunk:2:{2_000 + offset}" for offset in range(10)
    ]


def test_orm_passage_limit_is_applied_after_authoritative_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = SimpleNamespace(id=1)
    metadata = SimpleNamespace(
        id=11,
        document_id=1,
        chunk_index=0,
        chapter_id=None,
        chunk_text="backend: metadata only",
    )
    empty = SimpleNamespace(
        id=12,
        document_id=1,
        chunk_index=1,
        chapter_id=None,
        chunk_text="   ",
    )
    passage = SimpleNamespace(
        id=13,
        document_id=1,
        chunk_index=2,
        chapter_id=None,
        chunk_text="real passage",
    )
    rows = [(document, metadata), (document, empty), (document, passage)]

    class _Result:
        def __init__(self, selected):
            self._selected = selected

        def all(self):
            return list(self._selected)

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            limit_clause = getattr(statement, "_limit_clause", None)
            selected = rows
            if limit_clause is not None:
                selected = rows[: int(limit_clause.value)]
            return _Result(selected)

    monkeypatch.setattr(vector_store_service, "SessionLocal", _Session)

    selected = vector_store_service._passage_source_rows(limit=1)

    assert [(doc.id, chunk.id) for doc, chunk in selected] == [(1, 13)]


def test_temp_passage_apply_is_affected_only_and_second_sync_is_noop(
    tmp_path: Path,
    fake_embedding,
) -> None:
    loads, encodes = fake_embedding
    database = _temp_research_db(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    db = vector_store_service.open_vector_store(store)
    db.create_table(
        vector_store_service.OBJECT_TABLE,
        data=[{"vector_id": "object:keep", "source_id": "object:keep", "vector": [1.0]}],
        mode="create",
    )
    object_before = vector_store_service._existing_records(
        db,
        vector_store_service.OBJECT_TABLE,
    )

    first = vector_store_service.sync_affected_passage_embeddings(
        ["chunk:1:11"],
        dry_run=False,
        apply=True,
        store_path=store,
        manifest_path=manifest,
        source_db_path=database,
    )
    assert first["scope"] == "affected_source_ids_only"
    assert first["full_rebuild_allowed"] is False
    assert first["delete_orphans_allowed"] is False
    assert first["upserted_count"] == 1
    assert first["requested_source_ids"] == ["chunk:1:11"]
    assert len(loads) == 1
    assert len(encodes) == 1
    passage_rows = vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    )
    assert [row["source_id"] for row in passage_rows] == ["chunk:1:11"]
    orphan = dict(passage_rows[0])
    orphan.update(
        {
            "vector_id": "chunk:99:999",
            "record_id": "chunk:99:999",
            "source_id": "chunk:99:999",
            "document_id": 99,
            "chunk_id": 999,
        }
    )
    db.open_table(vector_store_service.PASSAGE_TABLE).add([orphan])
    assert vector_store_service._existing_records(
        db,
        vector_store_service.OBJECT_TABLE,
    ) == object_before

    second = vector_store_service.sync_affected_passage_embeddings(
        ["chunk:1:11"],
        dry_run=False,
        apply=True,
        store_path=store,
        manifest_path=manifest,
        source_db_path=database,
    )
    assert second["upserted_count"] == 0
    assert second["lancedb_writes_performed"] is False
    assert len(loads) == 1
    assert len(encodes) == 1
    assert {
        row["source_id"]
        for row in vector_store_service._existing_records(
            db,
            vector_store_service.PASSAGE_TABLE,
        )
    } == {"chunk:1:11", "chunk:99:999"}
    assert vector_store_service._existing_records(
        db,
        vector_store_service.OBJECT_TABLE,
    ) == object_before
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["passage_count"] == 2
    assert payload["object_count"] == 1
    assert payload["embedding_dim"] == 3


def test_affected_passage_sync_repairs_identity_metadata_drift(
    tmp_path: Path,
    fake_embedding,
) -> None:
    database = _temp_research_db(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    vector_store_service.sync_affected_passage_embeddings(
        ["chunk:1:11"],
        dry_run=False,
        apply=True,
        store_path=store,
        manifest_path=manifest,
        source_db_path=database,
    )
    db = vector_store_service.open_vector_store(store)
    table = db.open_table(vector_store_service.PASSAGE_TABLE)
    row = dict(vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    )[0])
    table.delete("source_id = 'chunk:1:11'")
    row.update(
        {
            "vector_id": "chunk:2:21",
            "document_id": 2,
            "chunk_id": 21,
        }
    )
    table.add([row])

    result = vector_store_service.sync_affected_passage_embeddings(
        ["chunk:1:11"],
        dry_run=False,
        apply=True,
        store_path=store,
        manifest_path=manifest,
        source_db_path=database,
    )

    assert result["upserted_count"] == 1
    repaired = vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    )
    assert len(repaired) == 1
    assert repaired[0]["source_id"] == "chunk:1:11"
    assert repaired[0]["vector_id"] == "chunk:1:11"
    assert repaired[0]["document_id"] == 1
    assert repaired[0]["chunk_id"] == 11


def test_strict_passage_state_exposes_duplicates_and_stale_identity(
    tmp_path: Path,
    fake_embedding,
) -> None:
    database = _temp_research_db(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "vector-manifest.json"
    expected = vector_store_service.collect_passage_sources(
        source_db_path=database,
        document_id=1,
    )
    vector_store_service.sync_affected_passage_embeddings(
        ["chunk:1:11"],
        dry_run=False,
        apply=True,
        store_path=store,
        manifest_path=manifest,
        source_db_path=database,
    )
    db = vector_store_service.open_vector_store(store)
    table = db.open_table(vector_store_service.PASSAGE_TABLE)
    row = dict(vector_store_service._existing_records(
        db,
        vector_store_service.PASSAGE_TABLE,
    )[0])
    duplicate = dict(row)
    duplicate["document_id"] = 2
    duplicate["chunk_id"] = 21
    table.add([duplicate])

    state = vector_store_service.inspect_document_passage_vector_state(
        document_id=1,
        expected_sources=expected,
        store_path=store,
    )

    assert state["duplicate_source_ids"] == ["chunk:1:11"]
    assert state["duplicate_count"] == 1
    assert state["stale_source_ids"] == []


def test_explicit_source_apply_rejects_production_targets_before_vector_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _temp_research_db(tmp_path)
    store = tmp_path / "lancedb"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(
        vector_store_service,
        "open_vector_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vector store must not open")
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
            vector_store_service.sync_affected_passage_embeddings(
                ["chunk:1:11"],
                dry_run=False,
                apply=True,
                **paths,
            )
