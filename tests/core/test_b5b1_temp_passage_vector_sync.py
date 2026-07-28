from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.paths import DEFAULT_DB_PATH, LANCEDB_DIR
from app.services import vector_store_service


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
