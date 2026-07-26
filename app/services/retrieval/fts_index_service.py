from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.database import connect_existing_readwrite_sqlite
from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fts_schema import (
    INDEX_SCHEMA_VERSION,
    ORDINARY_TABLE,
    TOKENIZER_CONFIG,
    TRIGRAM_FTS_TABLE,
    UNICODE_FTS_TABLE,
    initialize_index_schema,
    validate_index_database,
)
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_QUERY_ALIASES_PATH,
    EXPECTED_ADAPTER_VERSIONS,
    get_index_status,
    sha256_file,
    source_fingerprints,
)
from app.services.retrieval.source_registry import (
    ALL_SOURCE_TYPES,
    SOURCE_REGISTRY_VERSION,
    RetrievalSourceRegistry,
)


_INSERT_FRAGMENT_SQL = f"""
INSERT INTO {ORDINARY_TABLE} (
    row_id,
    fragment_id,
    display_id,
    source_type,
    origin_kind,
    document_id,
    zotero_library_id,
    zotero_item_key,
    zotero_attachment_key,
    zotero_annotation_key,
    parent_fragment_id,
    duplicate_group_id,
    duplicate_candidate,
    title,
    authors_text,
    year,
    collections_text,
    tags_text,
    page_number,
    page_label,
    section,
    heading_path_json,
    text,
    note_comment,
    context_before,
    context_after,
    context_text,
    original_file_path,
    zotero_uri,
    content_hash,
    source_order,
    has_note_comment,
    has_zotero_uri,
    normalized_search_text,
    provenance_json,
    warnings_json,
    raw_metadata_json,
    adapter_version
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""

_INSERT_FTS_SQL = f"""
INSERT INTO {{table_name}} (
    rowid, title, section, tags, text, note_comment, context
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_FTS_MAINTENANCE_LOCK = threading.RLock()


def compute_retrieval_projection_sha256(
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> str:
    source_registry = registry or RetrievalSourceRegistry()
    registry_result = source_registry.read()
    digest = hashlib.sha256()
    for row_id, fragment in enumerate(
        registry_result.fragments,
        start=1,
    ):
        projection = {
            "fragment_row": _fragment_row(row_id, fragment),
            "fts_row": _fts_row(row_id, fragment),
        }
        digest.update(
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def rebind_retrieval_fts_after_schema_only_migration(
    *,
    expected_before_db_sha256: str,
    expected_after_db_sha256: str,
    expected_projection_sha256: str,
    index_path: str | Path = DEFAULT_INDEX_PATH,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    production_db_path: str | Path = DEFAULT_DB_PATH,
    registry: RetrievalSourceRegistry | None = None,
) -> dict[str, Any]:
    before_hash = _normalized_sha256(
        expected_before_db_sha256,
        name="expected_before_db_sha256",
    )
    after_hash = _normalized_sha256(
        expected_after_db_sha256,
        name="expected_after_db_sha256",
    )
    projection_hash = _normalized_sha256(
        expected_projection_sha256,
        name="expected_projection_sha256",
    )
    target_index = Path(index_path)
    target_manifest = Path(manifest_path)
    source_database = Path(production_db_path)
    for path, label in (
        (source_database, "production database"),
        (target_index, "retrieval index"),
        (target_manifest, "retrieval manifest"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")

    actual_database_hash = sha256_file(source_database).lower()
    if actual_database_hash != after_hash:
        raise RuntimeError("current production database hash does not match")

    manifest_before_bytes = target_manifest.read_bytes()
    manifest = json.loads(manifest_before_bytes.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("retrieval manifest must contain a JSON object")
    if str(manifest.get("production_db_sha256") or "").lower() != before_hash:
        raise RuntimeError("retrieval manifest database hash does not match")

    index_hash = sha256_file(target_index).lower()
    if str(manifest.get("index_content_hash") or "").lower() != index_hash:
        raise RuntimeError("retrieval index content hash does not match")
    with closing(sqlite3.connect(f"file:{target_index.as_posix()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA temp_store = MEMORY")
        validation = validate_index_database(
            connection,
            expected_fragment_count=int(manifest["fragment_count"]),
        )
    if not validation["valid"]:
        raise RuntimeError("retrieval index validation failed")

    source_registry = registry or RetrievalSourceRegistry()
    source_paths = {
        "production_db_path": Path(source_registry.research_db_path),
        "zotero_snapshot_path": Path(source_registry.zotero_snapshot_path),
        "notes_root": Path(source_registry.notes_root),
    }
    current_sources = source_fingerprints(**source_paths)
    for key in (
        "zotero_snapshot_sha256",
        "local_markdown_aggregate_hash",
    ):
        if str(manifest.get(key) or "").lower() != str(
            current_sources.get(key) or ""
        ).lower():
            raise RuntimeError(f"{key} changed")

    current_projection_hash = compute_retrieval_projection_sha256(
        registry=source_registry
    )
    if current_projection_hash != projection_hash:
        raise RuntimeError("retrieval source projection changed")

    token = uuid4().hex
    temporary_manifest = target_manifest.with_name(
        f".{target_manifest.name}.{token}.tmp"
    )
    backup_manifest = target_manifest.with_name(
        f".{target_manifest.name}.{token}.backup"
    )
    updated_manifest = dict(manifest)
    updated_manifest.update(
        {
            "production_db_sha256": after_hash,
            "last_schema_only_rebind_at": datetime.now(timezone.utc).isoformat(),
            "last_schema_only_rebind_from_db_sha256": before_hash,
            "last_schema_only_rebind_to_db_sha256": after_hash,
            "last_schema_only_rebind_projection_sha256": projection_hash,
        }
    )
    published = False
    retain_backup = False
    try:
        shutil.copy2(target_manifest, backup_manifest)
        temporary_manifest.write_text(
            json.dumps(
                updated_manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, target_manifest)
        published = True
        status = get_index_status(
            index_path=target_index,
            manifest_path=target_manifest,
            **source_paths,
        )
        if status.get("status") != "ready" or status.get("ready") is not True:
            raise RuntimeError("rebound retrieval index is not ready")
        if sha256_file(target_index).lower() != index_hash:
            raise RuntimeError("retrieval index changed during manifest rebind")
    except Exception:
        if published:
            try:
                os.replace(backup_manifest, target_manifest)
            except Exception as rollback_exc:
                retain_backup = True
                raise RuntimeError(
                    "retrieval manifest rebind rollback failed"
                ) from rollback_exc
        raise
    finally:
        temporary_manifest.unlink(missing_ok=True)
        if not retain_backup:
            backup_manifest.unlink(missing_ok=True)

    return {
        "status": "ready",
        "before_db_sha256": before_hash,
        "after_db_sha256": after_hash,
        "projection_sha256": projection_hash,
        "index_content_hash": index_hash,
        "manifest_sha256": sha256_file(target_manifest),
        "index_write_performed": False,
        "manifest_write_performed": True,
        "full_rebuild_performed": False,
        "source_projection_changed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }


def build_retrieval_fts(
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    registry: RetrievalSourceRegistry | None = None,
    query_aliases_path: str | Path = DEFAULT_QUERY_ALIASES_PATH,
) -> dict[str, Any]:
    started = time.perf_counter()
    target_index = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    target_manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    aliases_path = Path(query_aliases_path)
    _assert_project_local(target_index)
    _assert_project_local(target_manifest)
    target_index.parent.mkdir(parents=True, exist_ok=True)
    target_manifest.parent.mkdir(parents=True, exist_ok=True)

    source_registry = registry or RetrievalSourceRegistry()
    source_paths = {
        "production_db_path": source_registry.research_db_path,
        "zotero_snapshot_path": source_registry.zotero_snapshot_path,
        "notes_root": source_registry.notes_root,
    }
    source_hashes = source_fingerprints(**source_paths)
    registry_result = source_registry.read()
    fragments = list(registry_result.fragments)
    token = uuid4().hex
    temporary_index = target_index.with_name(f".{target_index.name}.{token}.tmp")
    temporary_manifest = target_manifest.with_name(f".{target_manifest.name}.{token}.tmp")
    previous_exists = target_index.exists() or target_manifest.exists()

    try:
        _build_database(temporary_index, fragments)
        with closing(sqlite3.connect(temporary_index)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA temp_store = MEMORY")
            validation = validate_index_database(
                connection,
                expected_fragment_count=len(fragments),
            )
        if not validation["valid"]:
            raise RuntimeError(f"temporary retrieval index validation failed: {validation}")

        index_hash = sha256_file(temporary_index)
        source_type_counts = {
            source_type: int(registry_result.source_counts.get(source_type, 0))
            for source_type in ALL_SOURCE_TYPES
        }
        duplicate_groups = {
            item.duplicate_group_id
            for item in fragments
            if item.duplicate_candidate and item.duplicate_group_id
        }
        built_at = datetime.now(timezone.utc).isoformat()
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        manifest = {
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "source_registry_version": SOURCE_REGISTRY_VERSION,
            "adapter_versions": EXPECTED_ADAPTER_VERSIONS,
            "production_db_sha256": source_hashes["production_db_sha256"],
            "zotero_snapshot_sha256": source_hashes["zotero_snapshot_sha256"],
            "local_markdown_aggregate_hash": source_hashes["local_markdown_aggregate_hash"],
            "query_aliases_sha256": sha256_file(aliases_path),
            "fragment_count": len(fragments),
            "source_type_counts": source_type_counts,
            "origin_kind_counts": registry_result.origin_counts,
            "duplicate_group_count": len(duplicate_groups),
            "tokenizers": TOKENIZER_CONFIG,
            "built_at": built_at,
            "build_duration_ms": duration_ms,
            "index_content_hash": index_hash,
            "index_file_bytes": temporary_index.stat().st_size,
            "registry_warnings": list(registry_result.warnings),
        }
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status = _publish_with_rollback(
            temporary_index=temporary_index,
            temporary_manifest=temporary_manifest,
            target_index=target_index,
            target_manifest=target_manifest,
            status_kwargs={
                "production_db_path": source_registry.research_db_path,
                "zotero_snapshot_path": source_registry.zotero_snapshot_path,
                "notes_root": source_registry.notes_root,
                "query_aliases_path": aliases_path,
            },
        )
    finally:
        temporary_index.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)

    return {
        "status": "ready",
        "index_path": str(target_index),
        "manifest_path": str(target_manifest),
        "fragment_count": len(fragments),
        "source_type_counts": manifest["source_type_counts"],
        "duplicate_group_count": manifest["duplicate_group_count"],
        "tokenizers": TOKENIZER_CONFIG,
        "build_duration_ms": manifest["build_duration_ms"],
        "index_content_hash": manifest["index_content_hash"],
        "manifest_sha256": status["manifest_sha256"],
        "validation": status["validation"],
        "previous_index_replaced": previous_exists,
        "derived_index_write_performed": True,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }


def cleanup_document_retrieval_fts(
    *,
    document_id: int,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    production_db_path: str | Path,
) -> dict[str, Any]:
    """Remove one document from the existing FTS index without replacing its file.

    Search processes can keep short-lived read-only SQLite connections open on
    Windows.  Mutating the existing database lets SQLite coordinate those
    readers; replacing the database file cannot do that reliably.
    """
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
        raise ValueError("document_id must be a positive integer")

    target_index = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    target_manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    source_database = Path(production_db_path)
    _assert_project_local(target_index)
    _assert_project_local(target_manifest)
    _assert_project_local(source_database)
    if not target_index.is_file():
        raise FileNotFoundError(f"retrieval index does not exist: {target_index.name}")
    if not target_manifest.is_file():
        raise FileNotFoundError(f"retrieval manifest does not exist: {target_manifest.name}")

    with _FTS_MAINTENANCE_LOCK:
        started = time.perf_counter()
        connection = connect_existing_readwrite_sqlite(
            target_index,
            resolve_strict=True,
            timeout=30.0,
            row_factory=sqlite3.Row,
            temp_store="MEMORY",
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            row_ids = [
                int(row[0])
                for row in connection.execute(
                    f"SELECT row_id FROM {ORDINARY_TABLE} WHERE document_id = ? ORDER BY row_id",
                    (document_id,),
                )
            ]
            if row_ids:
                connection.execute(
                    f"DELETE FROM {UNICODE_FTS_TABLE} WHERE rowid IN "
                    f"(SELECT row_id FROM {ORDINARY_TABLE} WHERE document_id = ?)",
                    (document_id,),
                )
                connection.execute(
                    f"DELETE FROM {TRIGRAM_FTS_TABLE} WHERE rowid IN "
                    f"(SELECT row_id FROM {ORDINARY_TABLE} WHERE document_id = ?)",
                    (document_id,),
                )
                connection.execute(
                    f"DELETE FROM {ORDINARY_TABLE} WHERE document_id = ?",
                    (document_id,),
                )

            fragment_count = int(
                connection.execute(f"SELECT COUNT(*) FROM {ORDINARY_TABLE}").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO index_metadata (key, value) VALUES ('fragment_count', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(fragment_count),),
            )
            validation = validate_index_database(
                connection,
                expected_fragment_count=fragment_count,
            )
            if not validation["valid"]:
                raise RuntimeError(f"retrieval index cleanup validation failed: {validation}")
            manifest_counts = _manifest_counts(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        manifest = _refresh_manifest_after_cleanup(
            manifest_path=target_manifest,
            index_path=target_index,
            production_db_path=source_database,
            fragment_count=fragment_count,
            manifest_counts=manifest_counts,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    return {
        "status": "ready",
        "document_id": document_id,
        "removed_fragment_rows": len(row_ids),
        "already_absent": not row_ids,
        "fragment_count": fragment_count,
        "index_content_hash": manifest["index_content_hash"],
        "manifest_sha256": sha256_file(target_manifest),
        "validation": validation,
        "derived_index_write_performed": True,
        "production_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }


def upsert_document_retrieval_fts(
    *,
    document_id: int,
    index_path: str | Path,
    manifest_path: str | Path,
    research_db_path: str | Path,
    allow_production: bool = False,
    expected_before_db_sha256: str | None = None,
    expected_after_db_sha256: str | None = None,
) -> dict[str, Any]:
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
        raise ValueError("document_id must be a positive integer")

    target_index = Path(index_path).resolve(strict=False)
    target_manifest = Path(manifest_path).resolve(strict=False)
    source_database = Path(research_db_path).resolve(strict=False)
    production = source_database == Path(DEFAULT_DB_PATH).resolve(strict=False)
    if production and not allow_production:
        raise ValueError("production document FTS upsert requires explicit opt-in")
    if not production and allow_production:
        raise ValueError("production opt-in requires the production database")
    if production:
        if target_index != Path(DEFAULT_INDEX_PATH).resolve(strict=False) or target_manifest != Path(DEFAULT_MANIFEST_PATH).resolve(strict=False):
            raise ValueError("production upsert requires default FTS targets")
        if not expected_before_db_sha256 or not expected_after_db_sha256:
            raise ValueError("production upsert requires before/after DB hashes")
        manifest_probe = json.loads(target_manifest.read_text(encoding="utf-8"))
        if str(manifest_probe.get("production_db_sha256", "")).lower() != expected_before_db_sha256.lower():
            raise ValueError("production FTS manifest does not point to expected DB revision")
        if sha256_file(source_database).lower() != expected_after_db_sha256.lower():
            raise ValueError("production DB hash does not match expected after revision")
        status_probe = get_index_status()
        if status_probe.get("status") != "source_drift" or status_probe.get("reasons") != ["production_db_sha256_changed"]:
            raise ValueError("production FTS upsert requires DB-only source drift")
    if not production and target_index == Path(DEFAULT_INDEX_PATH).resolve(strict=False):
        raise ValueError("temp document FTS upsert forbids the production index")
    if not production and target_manifest == Path(DEFAULT_MANIFEST_PATH).resolve(strict=False):
        raise ValueError("temp document FTS upsert forbids the production manifest")
    if not source_database.is_file():
        raise ValueError("research_db_path must identify an existing temp database")
    if not target_index.is_file():
        raise ValueError("index_path must identify an existing temp index")
    if not target_manifest.is_file():
        raise ValueError("manifest_path must identify an existing temp manifest")

    missing_zotero = source_database.with_name(".b5b1-zotero-snapshot-absent.sqlite")
    missing_notes = source_database.with_name(".b5b1-notes-absent")
    registry = RetrievalSourceRegistry() if production else RetrievalSourceRegistry(
        research_db_path=source_database, zotero_snapshot_path=missing_zotero, notes_root=missing_notes)
    registry_result = registry.read(
        source_types=("pdf_chunk", "personal_note"),
        document_ids=(document_id,),
    )
    fragments = list(registry_result.fragments)

    token = uuid4().hex
    backup_index = target_index.with_name(f".{target_index.name}.{token}.backup")
    backup_manifest = target_manifest.with_name(
        f".{target_manifest.name}.{token}.backup"
    )
    with _FTS_MAINTENANCE_LOCK:
        started = time.perf_counter()
        shutil.copy2(target_index, backup_index)
        shutil.copy2(target_manifest, backup_manifest)
        connection: sqlite3.Connection | None = None
        try:
            connection = connect_existing_readwrite_sqlite(
                target_index,
                resolve_strict=True,
                timeout=30.0,
                row_factory=sqlite3.Row,
                temp_store="MEMORY",
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            row_ids = [
                int(row[0])
                for row in connection.execute(
                    f"SELECT row_id FROM {ORDINARY_TABLE} "
                    "WHERE document_id = ? ORDER BY row_id",
                    (document_id,),
                )
            ]
            connection.execute(
                f"DELETE FROM {UNICODE_FTS_TABLE} WHERE rowid IN "
                f"(SELECT row_id FROM {ORDINARY_TABLE} WHERE document_id = ?)",
                (document_id,),
            )
            connection.execute(
                f"DELETE FROM {TRIGRAM_FTS_TABLE} WHERE rowid IN "
                f"(SELECT row_id FROM {ORDINARY_TABLE} WHERE document_id = ?)",
                (document_id,),
            )
            connection.execute(
                f"DELETE FROM {ORDINARY_TABLE} WHERE document_id = ?",
                (document_id,),
            )
            next_row_id = int(
                connection.execute(
                    f"SELECT COALESCE(MAX(row_id), 0) + 1 FROM {ORDINARY_TABLE}"
                ).fetchone()[0]
            )
            fragment_rows = [
                _fragment_row(next_row_id + offset, fragment)
                for offset, fragment in enumerate(fragments)
            ]
            fts_rows = [
                _fts_row(next_row_id + offset, fragment)
                for offset, fragment in enumerate(fragments)
            ]
            connection.executemany(_INSERT_FRAGMENT_SQL, fragment_rows)
            connection.executemany(
                _INSERT_FTS_SQL.format(table_name=UNICODE_FTS_TABLE),
                fts_rows,
            )
            connection.executemany(
                _INSERT_FTS_SQL.format(table_name=TRIGRAM_FTS_TABLE),
                fts_rows,
            )
            fragment_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {ORDINARY_TABLE}"
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO index_metadata (key, value) VALUES ('fragment_count', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(fragment_count),),
            )
            validation = validate_index_database(
                connection,
                expected_fragment_count=fragment_count,
            )
            if not validation["valid"]:
                raise RuntimeError(
                    f"retrieval index document upsert validation failed: {validation}"
                )
            manifest_counts = _manifest_counts(connection)
            connection.commit()
            connection.close()
            connection = None
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            manifest = _refresh_manifest_after_document_change(
                manifest_path=target_manifest,
                index_path=target_index,
                production_db_path=source_database,
                fragment_count=fragment_count,
                manifest_counts=manifest_counts,
                operation="upsert",
                duration_ms=duration_ms,
            )
        except Exception:
            if connection is not None:
                connection.rollback()
                connection.close()
            shutil.copyfile(backup_index, target_index)
            shutil.copyfile(backup_manifest, target_manifest)
            raise
        finally:
            backup_index.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)

    source_counts = {
        source_type: sum(
            1 for fragment in fragments if fragment.source_type == source_type
        )
        for source_type in ("pdf_chunk", "personal_note")
    }
    return {
        "status": "ready",
        "document_id": document_id,
        "removed_fragment_rows": len(row_ids),
        "inserted_fragment_rows": len(fragments),
        "pdf_chunk_rows": source_counts["pdf_chunk"],
        "personal_note_rows": source_counts["personal_note"],
        "fragment_count": fragment_count,
        "index_content_hash": manifest["index_content_hash"],
        "manifest_sha256": sha256_file(target_manifest),
        "validation": validation,
        "full_rebuild_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }


def _manifest_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    source_type_counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            f"SELECT source_type, COUNT(*) FROM {ORDINARY_TABLE} GROUP BY source_type"
        )
    }
    origin_kind_counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            f"SELECT origin_kind, COUNT(*) FROM {ORDINARY_TABLE} GROUP BY origin_kind"
        )
    }
    duplicate_group_count = int(
        connection.execute(
            f"""
            SELECT COUNT(DISTINCT duplicate_group_id)
            FROM {ORDINARY_TABLE}
            WHERE duplicate_candidate = 1 AND duplicate_group_id IS NOT NULL
            """
        ).fetchone()[0]
    )
    return {
        "source_type_counts": {
            source_type: int(source_type_counts.get(source_type, 0))
            for source_type in ALL_SOURCE_TYPES
        },
        "origin_kind_counts": origin_kind_counts,
        "duplicate_group_count": duplicate_group_count,
    }


def _refresh_manifest_after_cleanup(
    *,
    manifest_path: Path,
    index_path: Path,
    production_db_path: Path,
    fragment_count: int,
    manifest_counts: dict[str, Any],
    duration_ms: float,
) -> dict[str, Any]:
    return _refresh_manifest_after_document_change(
        manifest_path=manifest_path,
        index_path=index_path,
        production_db_path=production_db_path,
        fragment_count=fragment_count,
        manifest_counts=manifest_counts,
        operation="cleanup",
        duration_ms=duration_ms,
    )


def _refresh_manifest_after_document_change(
    *,
    manifest_path: Path,
    index_path: Path,
    production_db_path: Path,
    fragment_count: int,
    manifest_counts: dict[str, Any],
    operation: str,
    duration_ms: float,
) -> dict[str, Any]:
    if operation not in {"cleanup", "upsert"}:
        raise ValueError("unsupported document manifest operation")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("retrieval manifest must contain a JSON object")
    manifest.update(
        {
            "production_db_sha256": sha256_file(production_db_path),
            "fragment_count": fragment_count,
            "source_type_counts": manifest_counts["source_type_counts"],
            "origin_kind_counts": manifest_counts["origin_kind_counts"],
            "duplicate_group_count": manifest_counts["duplicate_group_count"],
            "index_content_hash": sha256_file(index_path),
            "index_file_bytes": index_path.stat().st_size,
            f"last_document_{operation}_at": datetime.now(timezone.utc).isoformat(),
            f"last_document_{operation}_duration_ms": duration_ms,
        }
    )
    temporary_manifest = manifest_path.with_name(
        f".{manifest_path.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, manifest_path)
    finally:
        temporary_manifest.unlink(missing_ok=True)
    return manifest


def _build_database(path: Path, fragments: list[RetrievalFragment]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        initialize_index_schema(connection)
        fragment_rows = []
        fts_rows = []
        for row_id, fragment in enumerate(fragments, start=1):
            fragment_rows.append(_fragment_row(row_id, fragment))
            fts_rows.append(_fts_row(row_id, fragment))
        connection.executemany(_INSERT_FRAGMENT_SQL, fragment_rows)
        connection.executemany(
            _INSERT_FTS_SQL.format(table_name=UNICODE_FTS_TABLE),
            fts_rows,
        )
        connection.executemany(
            _INSERT_FTS_SQL.format(table_name=TRIGRAM_FTS_TABLE),
            fts_rows,
        )
        connection.executemany(
            "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
            [
                ("source_registry_version", SOURCE_REGISTRY_VERSION),
                ("fragment_count", str(len(fragments))),
            ],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _fragment_row(row_id: int, fragment: RetrievalFragment) -> tuple[Any, ...]:
    authors_text = "\n".join(fragment.authors)
    collections_text = "\n".join(fragment.collections)
    tags_text = "\n".join(fragment.tags)
    context_text = "\n".join(
        value for value in (fragment.context_before, fragment.context_after) if value
    )
    normalized_search_text = _normalize_search_text(
        "\n".join(
            value
            for value in (
                fragment.title,
                authors_text,
                fragment.section,
                tags_text,
                fragment.text,
                fragment.note_comment,
                context_text,
            )
            if value
        )
    )
    return (
        row_id,
        fragment.fragment_id,
        fragment.display_id,
        fragment.source_type,
        fragment.origin_kind,
        fragment.document_id,
        fragment.zotero_library_id,
        fragment.zotero_item_key,
        fragment.zotero_attachment_key,
        fragment.zotero_annotation_key,
        fragment.parent_fragment_id,
        fragment.duplicate_group_id,
        int(fragment.duplicate_candidate),
        fragment.title,
        authors_text,
        fragment.year,
        collections_text,
        tags_text,
        fragment.page_number,
        fragment.page_label,
        fragment.section,
        _json(fragment.heading_path),
        fragment.text,
        fragment.note_comment,
        fragment.context_before,
        fragment.context_after,
        context_text,
        fragment.original_file_path,
        fragment.zotero_uri,
        fragment.content_hash,
        fragment.source_order,
        int(bool(fragment.note_comment)),
        int(bool(fragment.zotero_uri)),
        normalized_search_text,
        _json(fragment.provenance),
        _json(fragment.warnings),
        _json(fragment.raw_metadata),
        fragment.adapter_version,
    )


def _fts_row(row_id: int, fragment: RetrievalFragment) -> tuple[Any, ...]:
    context_text = "\n".join(
        value for value in (fragment.context_before, fragment.context_after) if value
    )
    return (
        row_id,
        fragment.title or "",
        fragment.section or "",
        "\n".join(fragment.tags),
        fragment.text,
        fragment.note_comment or "",
        context_text,
    )


def _publish_with_rollback(
    *,
    temporary_index: Path,
    temporary_manifest: Path,
    target_index: Path,
    target_manifest: Path,
    status_kwargs: dict[str, Any],
) -> dict[str, Any]:
    token = uuid4().hex
    backup_index = target_index.with_name(f".{target_index.name}.{token}.backup")
    backup_manifest = target_manifest.with_name(f".{target_manifest.name}.{token}.backup")
    backed_up_index = False
    backed_up_manifest = False
    published_index = False
    published_manifest = False
    try:
        if target_index.exists():
            os.replace(target_index, backup_index)
            backed_up_index = True
        if target_manifest.exists():
            os.replace(target_manifest, backup_manifest)
            backed_up_manifest = True
        os.replace(temporary_index, target_index)
        published_index = True
        os.replace(temporary_manifest, target_manifest)
        published_manifest = True
        status = get_index_status(
            index_path=target_index,
            manifest_path=target_manifest,
            **status_kwargs,
        )
        if status.get("status") != "ready":
            raise RuntimeError(f"published retrieval index is not ready: {status}")
    except Exception:
        if published_index:
            target_index.unlink(missing_ok=True)
        if published_manifest:
            target_manifest.unlink(missing_ok=True)
        if backed_up_index:
            os.replace(backup_index, target_index)
        if backed_up_manifest:
            os.replace(backup_manifest, target_manifest)
        raise
    else:
        backup_index.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)
        return status
    finally:
        backup_index.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("‐", "-").replace("‑", "-").replace("–", "-")
    normalized = "".join(
        character if character.isalnum() else " "
        for character in normalized
    )
    return " ".join(normalized.split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_sha256(value: str, *, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError(f"{name} must be a 64-character SHA256")
    return normalized


def _assert_project_local(path: Path) -> None:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(DATA_DIR.resolve()):
        raise ValueError(f"retrieval index path must stay inside SEARCH_DATA_DIR: {path}")
