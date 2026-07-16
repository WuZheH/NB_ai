from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.core.database import connect_immutable_readonly_sqlite
from app.core.paths import (
    DEFAULT_DB_PATH,
    FTS_DB_PATH,
    FTS_INDEX_DIR,
    FTS_MANIFEST_PATH,
    RUNTIME_PROJECT_ROOT,
)
from app.services.retrieval.fts_schema import (
    INDEX_SCHEMA_VERSION,
    TOKENIZER_CONFIG,
    validate_index_database,
)
from app.services.retrieval.source_registry import (
    DEFAULT_NOTES_ROOT,
    DEFAULT_ZOTERO_SNAPSHOT_PATH,
    SOURCE_REGISTRY_VERSION,
)
from app.services.retrieval.sources.markdown_note_adapter import (
    ADAPTER_VERSION as MARKDOWN_NOTE_ADAPTER_VERSION,
)
from app.services.retrieval.sources.pdf_chunk_adapter import (
    ADAPTER_VERSION as PDF_CHUNK_ADAPTER_VERSION,
)
from app.services.retrieval.sources.personal_note_adapter import (
    ADAPTER_VERSION as PERSONAL_NOTE_ADAPTER_VERSION,
)
from app.services.retrieval.sources.zotero_child_note_adapter import (
    ADAPTER_VERSION as ZOTERO_CHILD_NOTE_ADAPTER_VERSION,
)
from app.services.retrieval.sources.zotero_inspiration_note_adapter import (
    ADAPTER_VERSION as ZOTERO_INSPIRATION_NOTE_ADAPTER_VERSION,
)
from app.services.retrieval.sources.zotero_native_annotation_adapter import (
    ADAPTER_VERSION as ZOTERO_NATIVE_ANNOTATION_ADAPTER_VERSION,
)


DEFAULT_INDEX_DIR = FTS_INDEX_DIR
DEFAULT_INDEX_PATH = FTS_DB_PATH
DEFAULT_MANIFEST_PATH = FTS_MANIFEST_PATH
DEFAULT_QUERY_ALIASES_PATH = RUNTIME_PROJECT_ROOT / "config" / "retrieval_query_aliases.json"

EXPECTED_ADAPTER_VERSIONS = {
    "pdf_chunk": PDF_CHUNK_ADAPTER_VERSION,
    "zotero_highlight": ZOTERO_NATIVE_ANNOTATION_ADAPTER_VERSION,
    "zotero_annotation_comment": ZOTERO_NATIVE_ANNOTATION_ADAPTER_VERSION,
    "zotero_child_note": ZOTERO_CHILD_NOTE_ADAPTER_VERSION,
    "zotero_inspiration_note": ZOTERO_INSPIRATION_NOTE_ADAPTER_VERSION,
    "personal_note": PERSONAL_NOTE_ADAPTER_VERSION,
    "markdown_note": MARKDOWN_NOTE_ADAPTER_VERSION,
}


def get_index_status(
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    production_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_snapshot_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    notes_root: str | Path = DEFAULT_NOTES_ROOT,
    query_aliases_path: str | Path = DEFAULT_QUERY_ALIASES_PATH,
) -> dict[str, Any]:
    index = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    index_exists = index.is_file()
    manifest_exists = manifest_file.is_file()
    base = {
        "index_path": str(index),
        "manifest_path": str(manifest_file),
        "index_exists": index_exists,
        "manifest_exists": manifest_exists,
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }
    if not index_exists and not manifest_exists:
        return {
            "status": "missing",
            "ready": False,
            "reasons": ["index_and_manifest_missing"],
            **base,
        }
    if index_exists != manifest_exists:
        return {
            "status": "corrupt",
            "ready": False,
            "reasons": ["index_manifest_pair_incomplete"],
            **base,
        }

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return {
            "status": "corrupt",
            "ready": False,
            "reasons": [f"manifest_unreadable:{type(exc).__name__}"],
            **base,
        }

    expected_index_hash = str(manifest.get("index_content_hash") or "")
    actual_index_hash = sha256_file(index)
    if not expected_index_hash or actual_index_hash != expected_index_hash:
        return {
            "status": "corrupt",
            "ready": False,
            "reasons": ["index_content_hash_mismatch"],
            "manifest": manifest,
            "index_content_hash": actual_index_hash,
            **base,
        }

    try:
        with closing(connect_readonly_index(index)) as connection:
            validation = validate_index_database(
                connection,
                expected_fragment_count=_int_or_none(manifest.get("fragment_count")),
            )
    except sqlite3.Error as exc:
        return {
            "status": "corrupt",
            "ready": False,
            "reasons": [f"index_open_failed:{type(exc).__name__}"],
            "manifest": manifest,
            **base,
        }
    if not validation["valid"]:
        return {
            "status": "corrupt",
            "ready": False,
            "reasons": ["index_validation_failed"],
            "validation": validation,
            "manifest": manifest,
            **base,
        }

    stale_reasons = _stale_reasons(
        manifest,
        query_aliases_path=Path(query_aliases_path),
    )
    if stale_reasons:
        return {
            "status": "stale",
            "ready": False,
            "reasons": stale_reasons,
            "validation": validation,
            "manifest": manifest,
            "index_content_hash": actual_index_hash,
            **base,
        }

    current_sources = source_fingerprints(
        production_db_path=Path(production_db_path),
        zotero_snapshot_path=Path(zotero_snapshot_path),
        notes_root=Path(notes_root),
    )
    source_reasons = [
        f"{key}_changed"
        for key, expected_key in (
            ("production_db_sha256", "production_db_sha256"),
            ("zotero_snapshot_sha256", "zotero_snapshot_sha256"),
            ("local_markdown_aggregate_hash", "local_markdown_aggregate_hash"),
        )
        if str(manifest.get(expected_key) or "") != str(current_sources.get(key) or "")
    ]
    if source_reasons:
        return {
            "status": "source_drift",
            "ready": False,
            "reasons": source_reasons,
            "validation": validation,
            "manifest": manifest,
            "current_source_fingerprints": current_sources,
            "index_content_hash": actual_index_hash,
            **base,
        }

    return {
        "status": "ready",
        "ready": True,
        "reasons": [],
        "validation": validation,
        "manifest": manifest,
        "manifest_sha256": sha256_file(manifest_file),
        "index_content_hash": actual_index_hash,
        "current_source_fingerprints": current_sources,
        **base,
    }


def source_fingerprints(
    *,
    production_db_path: Path = DEFAULT_DB_PATH,
    zotero_snapshot_path: Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    notes_root: Path = DEFAULT_NOTES_ROOT,
) -> dict[str, str]:
    return {
        "production_db_sha256": sha256_file(production_db_path),
        "zotero_snapshot_sha256": sha256_file(zotero_snapshot_path),
        "local_markdown_aggregate_hash": aggregate_markdown_hash(notes_root),
    }


def aggregate_markdown_hash(notes_root: Path) -> str:
    rows: list[str] = []
    if notes_root.is_dir():
        for path in sorted(notes_root.rglob("*.md"), key=lambda item: item.as_posix().casefold()):
            relative = path.relative_to(notes_root).as_posix()
            rows.append(f"{relative}|{sha256_file(path)}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect_readonly_index(path: Path) -> sqlite3.Connection:
    return connect_immutable_readonly_sqlite(path)


def _stale_reasons(
    manifest: dict[str, Any],
    *,
    query_aliases_path: Path,
) -> list[str]:
    reasons: list[str] = []
    if manifest.get("index_schema_version") != INDEX_SCHEMA_VERSION:
        reasons.append("index_schema_version_changed")
    if manifest.get("source_registry_version") != SOURCE_REGISTRY_VERSION:
        reasons.append("source_registry_version_changed")
    if manifest.get("adapter_versions") != EXPECTED_ADAPTER_VERSIONS:
        reasons.append("adapter_versions_changed")
    if manifest.get("tokenizers") != TOKENIZER_CONFIG:
        reasons.append("tokenizer_configuration_changed")
    current_alias_hash = sha256_file(query_aliases_path) if query_aliases_path.is_file() else None
    if manifest.get("query_aliases_sha256") != current_alias_hash:
        reasons.append("query_aliases_changed")
    return reasons


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
