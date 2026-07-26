from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid5

import pytest

from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalFragment,
)
from app.services.retrieval import fts_index_service
from app.services.retrieval.fts_schema import (
    INDEX_SCHEMA_VERSION,
    TOKENIZER_CONFIG,
)
from app.services.retrieval.fts_status_service import (
    DEFAULT_QUERY_ALIASES_PATH,
    EXPECTED_ADAPTER_VERSIONS,
    get_index_status,
    sha256_file,
    source_fingerprints,
)
from app.services.retrieval.source_registry import (
    ALL_SOURCE_TYPES,
    SOURCE_REGISTRY_VERSION,
    RetrievalRegistryResult,
)


def _research_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                title TEXT,
                content TEXT,
                created_at TEXT
            );
            INSERT INTO documents VALUES (1, 'Book');
            INSERT INTO knowledge_chunks VALUES (10, 1, 0, 'chunk text');
            INSERT INTO personal_notes VALUES (
                20, 1, 'Personal note', 'note text', '2026-07-26'
            );
            """
        )
        connection.commit()
    return path


def _fragment(
    *,
    source_type: str,
    source_id: int,
    text: str,
    title: str,
) -> RetrievalFragment:
    locator = f"test:{source_type}:{source_id}"
    return RetrievalFragment(
        fragment_id=str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, locator)),
        display_id=f"{source_type}:{source_id}",
        source_type=source_type,
        origin_kind="manual_import",
        source_record_id=str(source_id),
        canonical_source_locator=locator,
        document_id=1,
        title=title,
        text=text,
        context_status="source_complete",
        index_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        adapter_version=f"{source_type}_test.v1",
    )


class DatabaseProjectionRegistry:
    def __init__(
        self,
        database: Path,
        zotero_snapshot: Path,
        notes_root: Path,
    ) -> None:
        self.research_db_path = database
        self.zotero_snapshot_path = zotero_snapshot
        self.notes_root = notes_root

    def read(self) -> RetrievalRegistryResult:
        with sqlite3.connect(self.research_db_path) as connection:
            chunks = connection.execute(
                """
                SELECT id, chunk_text
                FROM knowledge_chunks
                ORDER BY document_id, chunk_index, id
                """
            ).fetchall()
            notes = connection.execute(
                """
                SELECT id, title, content
                FROM personal_notes
                ORDER BY document_id, created_at, title, id
                """
            ).fetchall()
        fragments = tuple(
            [
                _fragment(
                    source_type="pdf_chunk",
                    source_id=int(row[0]),
                    text=str(row[1]),
                    title="Book",
                )
                for row in chunks
            ]
            + [
                _fragment(
                    source_type="personal_note",
                    source_id=int(row[0]),
                    text=str(row[2]),
                    title=str(row[1]),
                )
                for row in notes
                if str(row[2]).strip()
            ]
        )
        source_counts = {
            source_type: sum(
                fragment.source_type == source_type
                for fragment in fragments
            )
            for source_type in ALL_SOURCE_TYPES
        }
        return RetrievalRegistryResult(
            fragments=fragments,
            source_counts=source_counts,
            origin_counts={"manual_import": len(fragments)},
            source_record_counts=source_counts,
            warnings=(),
        )


def _fixture(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    database = _research_database(root / "research.db")
    zotero = root / "zotero.sqlite"
    zotero.write_bytes(b"readonly-zotero-snapshot")
    notes_root = root / "notes"
    notes_root.mkdir()
    (notes_root / "note.md").write_text("markdown v1\n", encoding="utf-8")
    registry = DatabaseProjectionRegistry(database, zotero, notes_root)
    fragments = list(registry.read().fragments)
    index = root / "retrieval_fts_v1.db"
    manifest_path = root / "retrieval_fts_v1_manifest.json"
    fts_index_service._build_database(index, fragments)
    hashes = source_fingerprints(
        production_db_path=database,
        zotero_snapshot_path=zotero,
        notes_root=notes_root,
    )
    manifest = {
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "source_registry_version": SOURCE_REGISTRY_VERSION,
        "adapter_versions": EXPECTED_ADAPTER_VERSIONS,
        **hashes,
        "query_aliases_sha256": sha256_file(DEFAULT_QUERY_ALIASES_PATH),
        "fragment_count": len(fragments),
        "source_type_counts": registry.read().source_counts,
        "origin_kind_counts": {"manual_import": len(fragments)},
        "duplicate_group_count": 0,
        "tokenizers": TOKENIZER_CONFIG,
        "built_at": "2026-07-26T00:00:00Z",
        "index_content_hash": sha256_file(index),
        "index_file_bytes": index.stat().st_size,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return {
        "database": database,
        "zotero": zotero,
        "notes_root": notes_root,
        "registry": registry,
        "index": index,
        "manifest": manifest_path,
    }


def _schema_change(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE personal_notes ADD COLUMN source_system TEXT")
        connection.execute(
            "CREATE INDEX ix_personal_notes_source_system "
            "ON personal_notes(source_system)"
        )
        connection.commit()


def _status(paths: dict) -> dict:
    return get_index_status(
        index_path=paths["index"],
        manifest_path=paths["manifest"],
        production_db_path=paths["database"],
        zotero_snapshot_path=paths["zotero"],
        notes_root=paths["notes_root"],
    )


def _rebind(paths: dict, before: str, after: str, projection: str) -> dict:
    return fts_index_service.rebind_retrieval_fts_after_schema_only_migration(
        expected_before_db_sha256=before,
        expected_after_db_sha256=after,
        expected_projection_sha256=projection,
        index_path=paths["index"],
        manifest_path=paths["manifest"],
        production_db_path=paths["database"],
        registry=paths["registry"],
    )


def test_schema_only_migration_reproduction_rebinds_without_index_write(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    before_projection = (
        fts_index_service.compute_retrieval_projection_sha256(
            registry=paths["registry"]
        )
    )
    index_before = paths["index"].read_bytes()
    assert _status(paths)["status"] == "ready"
    _schema_change(paths["database"])
    after_db = sha256_file(paths["database"])
    after_projection = (
        fts_index_service.compute_retrieval_projection_sha256(
            registry=paths["registry"]
        )
    )
    assert after_db != before_db
    assert after_projection == before_projection
    assert _status(paths)["status"] == "source_drift"
    result = _rebind(paths, before_db, after_db, before_projection)
    assert result["status"] == "ready"
    assert result["index_write_performed"] is False
    assert result["full_rebuild_performed"] is False
    assert _status(paths)["status"] == "ready"
    assert paths["index"].read_bytes() == index_before
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["production_db_sha256"] == after_db
    assert manifest["last_schema_only_rebind_from_db_sha256"] == before_db
    assert manifest["last_schema_only_rebind_to_db_sha256"] == after_db
    assert (
        manifest["last_schema_only_rebind_projection_sha256"]
        == before_projection
    )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("knowledge_chunks", "chunk_text", "changed chunk"),
        ("personal_notes", "content", "changed note"),
    ],
)
def test_retrieval_projection_change_refuses_rebind(
    tmp_path: Path,
    table: str,
    column: str,
    value: str,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    expected_projection = (
        fts_index_service.compute_retrieval_projection_sha256(
            registry=paths["registry"]
        )
    )
    manifest_before = paths["manifest"].read_bytes()
    index_before = paths["index"].read_bytes()
    _schema_change(paths["database"])
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute(f'UPDATE "{table}" SET "{column}" = ?', (value,))
        connection.commit()
    after_db = sha256_file(paths["database"])
    assert (
        fts_index_service.compute_retrieval_projection_sha256(
            registry=paths["registry"]
        )
        != expected_projection
    )
    with pytest.raises(RuntimeError, match="retrieval source projection changed"):
        _rebind(paths, before_db, after_db, expected_projection)
    assert paths["manifest"].read_bytes() == manifest_before
    assert paths["index"].read_bytes() == index_before


@pytest.mark.parametrize("source", ["zotero", "markdown"])
def test_non_database_source_drift_refuses_before_manifest_write(
    tmp_path: Path,
    source: str,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    projection = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    manifest_before = paths["manifest"].read_bytes()
    _schema_change(paths["database"])
    if source == "zotero":
        paths["zotero"].write_bytes(b"changed snapshot")
    else:
        (paths["notes_root"] / "note.md").write_text(
            "markdown changed\n",
            encoding="utf-8",
        )
    with pytest.raises(RuntimeError, match="changed"):
        _rebind(
            paths,
            before_db,
            sha256_file(paths["database"]),
            projection,
        )
    assert paths["manifest"].read_bytes() == manifest_before


def test_index_corruption_refuses_rebind(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    projection = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    manifest_before = paths["manifest"].read_bytes()
    _schema_change(paths["database"])
    with paths["index"].open("ab") as stream:
        stream.write(b"corruption")
    corrupt_index = paths["index"].read_bytes()
    with pytest.raises(RuntimeError, match="index content hash"):
        _rebind(
            paths,
            before_db,
            sha256_file(paths["database"]),
            projection,
        )
    assert paths["manifest"].read_bytes() == manifest_before
    assert paths["index"].read_bytes() == corrupt_index


def test_manifest_old_hash_and_current_database_hash_are_guarded(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    projection = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    _schema_change(paths["database"])
    after_db = sha256_file(paths["database"])
    with pytest.raises(RuntimeError, match="current production database"):
        _rebind(paths, before_db, "0" * 64, projection)
    with pytest.raises(RuntimeError, match="manifest database hash"):
        _rebind(paths, "1" * 64, after_db, projection)


def test_post_write_status_failure_restores_exact_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture(tmp_path)
    before_db = sha256_file(paths["database"])
    projection = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    manifest_before = paths["manifest"].read_bytes()
    index_before = paths["index"].read_bytes()
    _schema_change(paths["database"])
    monkeypatch.setattr(
        fts_index_service,
        "get_index_status",
        lambda **_kwargs: {"status": "source_drift", "ready": False},
    )
    with pytest.raises(RuntimeError, match="not ready"):
        _rebind(
            paths,
            before_db,
            sha256_file(paths["database"]),
            projection,
        )
    assert paths["manifest"].read_bytes() == manifest_before
    assert paths["index"].read_bytes() == index_before


def test_projection_fingerprint_is_deterministic(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    first = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    second = fts_index_service.compute_retrieval_projection_sha256(
        registry=paths["registry"]
    )
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
