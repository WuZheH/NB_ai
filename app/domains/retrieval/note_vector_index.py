from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock
import time
from typing import Any
from uuid import uuid4

from app.core.paths import ZOTERO_NOTE_VECTOR_DIR
from app.domains.retrieval.fragment_repository import (
    EXCLUDED_NOTE_ORIGIN_KINDS,
    list_notebook_fragments,
)
from app.domains.retrieval.result_contracts import (
    NOTE_SOURCE_TYPES,
    NotebookFragment,
    NotebookSourceType,
)
from app.services import local_embedding_service


INDEX_SCHEMA_VERSION = "zotero_user_notes_vector_index.v1"
PASSAGE_TEMPLATE_VERSION = "user_note_selected_source_context.v1"
FRAGMENT_SNAPSHOT_VERSION = "notebook_fragment_snapshot.v1"
MANIFEST_NAME = "manifest.json"
DEFAULT_RECALL_LIMIT = 30
NORMALIZE_EMBEDDINGS = True
ENCODE_BATCH_SIZE = 1

_INDEX_CACHE_LOCK = RLock()
_INDEX_CACHE: dict[
    str,
    tuple[
        tuple[int, int],
        str,
        tuple[int, int],
        dict[str, Any],
        list[dict[str, Any]],
    ],
] = {}


class NoteVectorIndexUnavailable(RuntimeError):
    pass


def build_zotero_note_vectors(
    *,
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
    fragments: Iterable[NotebookFragment] | None = None,
    encode_text: Callable[[str], list[float]] | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    return sync_zotero_note_vectors(
        index_dir=index_dir,
        fragments=fragments,
        encode_text=encode_text,
        built_at=built_at,
        force=True,
    )


def sync_zotero_note_vectors(
    *,
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
    fragments: Iterable[NotebookFragment] | None = None,
    encode_text: Callable[[str], list[float]] | None = None,
    built_at: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Atomically publish a derived, incrementally reusable note vector index."""

    started = time.perf_counter()
    root = Path(index_dir)
    current = list(
        fragments
        if fragments is not None
        else list_notebook_fragments(source_types=NOTE_SOURCE_TYPES)
    )
    current.sort(key=lambda item: item.fragment_id)
    invalid = [item.source_type for item in current if item.source_type not in NOTE_SOURCE_TYPES]
    if invalid:
        raise ValueError(f"note vector index received unsupported source types: {sorted(set(invalid))}")

    previous_manifest, previous_entries = _load_existing(
        root,
        required=False,
        validate_embeddings=False,
        validate_profile=False,
    )
    previous_by_id = {
        str(entry.get("fragment_id")): entry for entry in previous_entries
    }
    template_compatible = bool(
        previous_manifest
        and previous_manifest.get("schema_version") == INDEX_SCHEMA_VERSION
        and previous_manifest.get("passage_template") == PASSAGE_TEMPLATE_VERSION
        and previous_manifest.get("model") == local_embedding_service.MODEL_NAME
        and previous_manifest.get("normalization") is NORMALIZE_EMBEDDINGS
        and previous_manifest.get("batch_size") == ENCODE_BATCH_SIZE
    )

    encoder = encode_text
    entries: list[dict[str, Any]] = []
    reused = 0
    recomputed = 0
    added = 0
    updated = 0
    metadata_updated = 0
    for fragment in current:
        passage_text = build_note_passage_text(fragment)
        vector_content_hash = _vector_content_hash(fragment, passage_text)
        fragment_payload = fragment.model_dump(mode="json")
        fragment_snapshot_hash = _fragment_snapshot_hash(fragment_payload)
        previous = previous_by_id.get(fragment.fragment_id)
        previous_snapshot_hash = (
            _entry_fragment_snapshot_hash(previous)
            if previous is not None
            else None
        )
        if previous is not None and previous_snapshot_hash != fragment_snapshot_hash:
            metadata_updated += 1

        can_reuse = bool(
            not force
            and template_compatible
            and previous
            and previous.get("vector_content_hash") == vector_content_hash
            and previous.get("passage_text") == passage_text
            and _embedding_is_valid(
                previous.get("embedding"),
                dimension=int((previous_manifest or {}).get("dimension") or 0),
            )
        )
        if can_reuse:
            embedding = [float(value) for value in previous["embedding"]]
            reused += 1
        else:
            if encoder is None:
                encoder = _default_encoder()
            embedding = [float(value) for value in encoder(passage_text)]
            if not embedding:
                raise ValueError(f"empty embedding for fragment {fragment.fragment_id}")
            recomputed += 1
            if previous is None:
                added += 1
            else:
                updated += 1

        entries.append(
            {
                "fragment_id": fragment.fragment_id,
                "source_type": fragment.source_type,
                "document_id": fragment.document_id,
                "content_hash": fragment.content_hash,
                "vector_content_hash": vector_content_hash,
                "fragment_snapshot_hash": fragment_snapshot_hash,
                "passage_text": passage_text,
                "fragment": fragment_payload,
                "embedding": embedding,
            }
        )

    dimensions = {len(entry["embedding"]) for entry in entries}
    if len(dimensions) > 1:
        raise ValueError(f"inconsistent note embedding dimensions: {sorted(dimensions)}")
    dimension = next(iter(dimensions), int((previous_manifest or {}).get("dimension") or 0))
    removed = len(set(previous_by_id).difference(item.fragment_id for item in current))
    content_hash = _aggregate_content_hash(entries)
    if (
        not force
        and previous_manifest
        and recomputed == 0
        and metadata_updated == 0
        and removed == 0
        and previous_manifest.get("fragment_snapshot_version")
        == FRAGMENT_SNAPSHOT_VERSION
        and previous_manifest.get("content_hash") == content_hash
        and int(previous_manifest.get("count") or 0) == len(entries)
    ):
        return {
            **previous_manifest,
            "reused_count": reused,
            "recomputed_count": 0,
            "added_count": 0,
            "updated_count": 0,
            "metadata_updated_count": 0,
            "removed_count": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "vector_write_performed": False,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "llm_called": False,
        }
    created_at = built_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version": FRAGMENT_SNAPSHOT_VERSION,
        "content_hash": content_hash,
        "count": len(entries),
        "built_at": created_at,
        "entries": entries,
    }
    payload_bytes = _json_bytes(payload)
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()
    generation_name = f"notes-{content_hash[:16]}-{payload_sha[:16]}.json"
    manifest = {
        "status": "ready",
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "source_types": list(NOTE_SOURCE_TYPES),
        "excluded_origin_kinds": list(EXCLUDED_NOTE_ORIGIN_KINDS),
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version": FRAGMENT_SNAPSHOT_VERSION,
        "count": len(entries),
        "content_hash": content_hash,
        "index_file": generation_name,
        "index_sha256": payload_sha,
        "built_at": created_at,
        "origin_counts": dict(
            sorted(Counter(_fragment_origin(entry["fragment"]) for entry in entries).items())
        ),
    }

    root.mkdir(parents=True, exist_ok=True)
    generation = root / generation_name
    generation_created = False
    try:
        if not generation.exists():
            _atomic_write_bytes(generation, payload_bytes)
            generation_created = True
        elif hashlib.sha256(generation.read_bytes()).hexdigest() != payload_sha:
            raise ValueError(f"existing note index generation hash mismatch: {generation.name}")
        _atomic_write_bytes(root / MANIFEST_NAME, _json_bytes(manifest))
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE.pop(str(root.resolve()), None)
        _prune_old_generations(root, keep=generation_name)
    except Exception:
        if generation_created and generation.is_file():
            generation.unlink()
        raise

    return {
        **manifest,
        "reused_count": reused,
        "recomputed_count": recomputed,
        "added_count": added,
        "updated_count": updated,
        "metadata_updated_count": metadata_updated,
        "removed_count": removed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "vector_write_performed": True,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
    }


def refresh_zotero_note_vector_document_scope(
    document_id: int,
    *,
    fragments: Iterable[NotebookFragment],
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
    encode_text: Callable[[str], list[float]] | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    """Refresh only note-vector entries formerly scoped to one document."""

    started = time.perf_counter()

    if (
        isinstance(document_id, bool)
        or not isinstance(document_id, int)
        or document_id <= 0
    ):
        raise ValueError("document_id must be a positive integer")

    root = Path(index_dir)
    previous_manifest, previous_entries = _load_existing(
        root,
        required=True,
    )

    scoped_previous = [
        entry
        for entry in previous_entries
        if entry.get("document_id") == document_id
    ]

    if not scoped_previous:
        return {
            **previous_manifest,
            "document_id": document_id,
            "scoped_entry_count_before": 0,
            "scoped_entry_count_after": 0,
            "reused_count": 0,
            "recomputed_count": 0,
            "metadata_updated_count": 0,
            "removed_count": 0,
            "unrelated_preserved_count": len(previous_entries),
            "elapsed_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
            "vector_write_performed": False,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "llm_called": False,
        }

    target_ids = {
        str(entry["fragment_id"])
        for entry in scoped_previous
    }

    fresh_by_id: dict[str, NotebookFragment] = {}

    for fragment in fragments:
        if fragment.source_type not in NOTE_SOURCE_TYPES:
            raise ValueError(
                "scoped note refresh received unsupported source type: "
                f"{fragment.source_type}"
            )

        if fragment.fragment_id not in target_ids:
            continue

        if fragment.fragment_id in fresh_by_id:
            raise ValueError(
                "duplicate fresh fragment in scoped note refresh: "
                f"{fragment.fragment_id}"
            )

        if fragment.document_id == document_id:
            raise ValueError(
                "fresh fragment remains mapped to deleted document: "
                f"{fragment.fragment_id}"
            )

        fresh_by_id[fragment.fragment_id] = fragment

    template_compatible = bool(
        previous_manifest
        and previous_manifest.get("schema_version")
        == INDEX_SCHEMA_VERSION
        and previous_manifest.get("passage_template")
        == PASSAGE_TEMPLATE_VERSION
        and previous_manifest.get("model")
        == local_embedding_service.MODEL_NAME
        and previous_manifest.get("normalization")
        is NORMALIZE_EMBEDDINGS
        and previous_manifest.get("batch_size")
        == ENCODE_BATCH_SIZE
    )

    entries: list[dict[str, Any]] = []
    encoder = encode_text

    reused = 0
    recomputed = 0
    metadata_updated = 0
    removed = 0

    for previous in previous_entries:
        fragment_id = str(previous["fragment_id"])

        if previous.get("document_id") != document_id:
            # Deleting one Search document must not migrate or refresh
            # unrelated note-vector entries. Preserve the prior entry
            # exactly, including legacy entries without snapshot hashes.
            entries.append(dict(previous))
            continue

        fresh = fresh_by_id.get(fragment_id)

        if fresh is None:
            removed += 1
            continue

        passage_text = build_note_passage_text(fresh)
        vector_content_hash = _vector_content_hash(
            fresh,
            passage_text,
        )
        fragment_payload = fresh.model_dump(mode="json")
        fragment_snapshot_hash = _fragment_snapshot_hash(
            fragment_payload
        )

        if (
            _entry_fragment_snapshot_hash(previous)
            != fragment_snapshot_hash
        ):
            metadata_updated += 1

        can_reuse = bool(
            template_compatible
            and previous.get("vector_content_hash")
            == vector_content_hash
            and previous.get("passage_text")
            == passage_text
            and isinstance(previous.get("embedding"), list)
        )

        if can_reuse:
            embedding = [
                float(value)
                for value in previous["embedding"]
            ]
            reused += 1
        else:
            if encoder is None:
                encoder = _default_encoder()

            embedding = [
                float(value)
                for value in encoder(passage_text)
            ]

            if not embedding:
                raise ValueError(
                    "empty embedding for scoped fragment "
                    f"{fragment_id}"
                )

            recomputed += 1

        entries.append(
            {
                "fragment_id": fragment_id,
                "source_type": fresh.source_type,
                "document_id": fresh.document_id,
                "content_hash": fresh.content_hash,
                "vector_content_hash": vector_content_hash,
                "fragment_snapshot_hash":
                    fragment_snapshot_hash,
                "passage_text": passage_text,
                "fragment": fragment_payload,
                "embedding": embedding,
            }
        )

    entries.sort(
        key=lambda entry: str(entry["fragment_id"])
    )

    dimensions = {
        len(entry["embedding"])
        for entry in entries
    }

    if len(dimensions) > 1:
        raise ValueError(
            "inconsistent note embedding dimensions after scoped refresh"
        )

    expected_dimension = int(
        previous_manifest.get("dimension") or 0
    )

    if dimensions and dimensions != {expected_dimension}:
        raise ValueError(
            "scoped note refresh changed embedding dimension"
        )

    scoped_after = sum(
        1
        for entry in entries
        if entry.get("document_id") == document_id
    )

    if scoped_after:
        raise ValueError(
            "scoped note refresh retained deleted document mappings"
        )

    content_hash = _aggregate_content_hash(entries)
    created_at = (
        built_at
        or datetime.now(timezone.utc).isoformat()
    )

    # Scoped deletion cleanup must not implicitly migrate the whole
    # note-vector index. A legacy index remains legacy until an explicit
    # full sync performs the schema migration.
    snapshot_version = previous_manifest.get(
        "fragment_snapshot_version"
    )

    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": expected_dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version":
            snapshot_version,
        "content_hash": content_hash,
        "count": len(entries),
        "built_at": created_at,
        "entries": entries,
    }

    payload_bytes = _json_bytes(payload)
    payload_sha = hashlib.sha256(
        payload_bytes
    ).hexdigest()

    generation_name = (
        f"notes-{content_hash[:16]}-"
        f"{payload_sha[:16]}.json"
    )

    manifest = {
        "status": "ready",
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": expected_dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "source_types": list(NOTE_SOURCE_TYPES),
        "excluded_origin_kinds":
            list(EXCLUDED_NOTE_ORIGIN_KINDS),
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version":
            snapshot_version,
        "count": len(entries),
        "content_hash": content_hash,
        "index_file": generation_name,
        "index_sha256": payload_sha,
        "built_at": created_at,
        "origin_counts": dict(
            sorted(
                Counter(
                    _fragment_origin(entry["fragment"])
                    for entry in entries
                ).items()
            )
        ),
    }

    root.mkdir(parents=True, exist_ok=True)
    generation = root / generation_name
    generation_created = False

    try:
        if not generation.exists():
            _atomic_write_bytes(
                generation,
                payload_bytes,
            )
            generation_created = True
        elif (
            hashlib.sha256(
                generation.read_bytes()
            ).hexdigest()
            != payload_sha
        ):
            raise ValueError(
                "existing scoped note generation hash mismatch"
            )

        _atomic_write_bytes(
            root / MANIFEST_NAME,
            _json_bytes(manifest),
        )

        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE.pop(
                str(root.resolve()),
                None,
            )

        _prune_old_generations(
            root,
            keep=generation_name,
        )

    except Exception:
        if (
            generation_created
            and generation.is_file()
        ):
            generation.unlink()
        raise

    return {
        **manifest,
        "document_id": document_id,
        "scoped_entry_count_before":
            len(scoped_previous),
        "scoped_entry_count_after":
            scoped_after,
        "reused_count": reused,
        "recomputed_count": recomputed,
        "metadata_updated_count":
            metadata_updated,
        "removed_count": removed,
        "unrelated_preserved_count":
            len(previous_entries)
            - len(scoped_previous),
        "elapsed_ms": round(
            (time.perf_counter() - started) * 1000,
            2,
        ),
        "vector_write_performed": True,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
    }


def attach_zotero_note_vector_document_scope(
    document_id: int,
    *,
    fragments: Iterable[NotebookFragment],
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
    encode_text: Callable[[str], list[float]] | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    """Attach only one imported document's native Zotero note fragments."""

    started = time.perf_counter()
    if (
        isinstance(document_id, bool)
        or not isinstance(document_id, int)
        or document_id <= 0
    ):
        raise ValueError("document_id must be a positive integer")

    root = Path(index_dir)
    previous_manifest, previous_entries = _load_existing(
        root,
        required=True,
    )
    previous_by_id = {
        str(entry["fragment_id"]): entry
        for entry in previous_entries
    }
    fresh_by_id: dict[str, NotebookFragment] = {}
    for fragment in fragments:
        if fragment.source_type not in NOTE_SOURCE_TYPES:
            raise ValueError(
                "scoped note attach received unsupported source type: "
                f"{fragment.source_type}"
            )
        if fragment.document_id != document_id:
            raise ValueError(
                "scoped note attach received a fragment for another document: "
                f"{fragment.fragment_id}"
            )
        if fragment.fragment_id in fresh_by_id:
            raise ValueError(
                "duplicate fresh fragment in scoped note attach: "
                f"{fragment.fragment_id}"
            )
        previous = previous_by_id.get(fragment.fragment_id)
        if (
            previous is not None
            and previous.get("document_id") not in {None, document_id}
        ):
            raise ValueError(
                "scoped note attach would steal another document mapping: "
                f"{fragment.fragment_id}"
            )
        fresh_by_id[fragment.fragment_id] = fragment

    if not fresh_by_id:
        return {
            **previous_manifest,
            "status": "ready",
            "scope": "affected_fragment_ids_only",
            "document_id": document_id,
            "scoped_entry_count_before": 0,
            "scoped_entry_count_after": 0,
            "reused_count": 0,
            "recomputed_count": 0,
            "added_count": 0,
            "metadata_updated_count": 0,
            "unrelated_preserved_count": len(previous_entries),
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
            "vector_write_performed": False,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "llm_called": False,
        }

    template_compatible = bool(
        previous_manifest
        and previous_manifest.get("schema_version")
        == INDEX_SCHEMA_VERSION
        and previous_manifest.get("passage_template")
        == PASSAGE_TEMPLATE_VERSION
        and previous_manifest.get("model")
        == local_embedding_service.MODEL_NAME
        and previous_manifest.get("normalization")
        is NORMALIZE_EMBEDDINGS
        and previous_manifest.get("batch_size")
        == ENCODE_BATCH_SIZE
    )
    target_ids = set(fresh_by_id)
    entries = [
        dict(entry)
        for entry in previous_entries
        if str(entry["fragment_id"]) not in target_ids
    ]
    encoder = encode_text
    reused = 0
    recomputed = 0
    added = 0
    metadata_updated = 0

    for fragment_id in sorted(fresh_by_id):
        fresh = fresh_by_id[fragment_id]
        previous = previous_by_id.get(fragment_id)
        passage_text = build_note_passage_text(fresh)
        vector_content_hash = _vector_content_hash(
            fresh,
            passage_text,
        )
        fragment_payload = fresh.model_dump(mode="json")
        fragment_snapshot_hash = _fragment_snapshot_hash(
            fragment_payload
        )
        if (
            previous is None
            or _entry_fragment_snapshot_hash(previous)
            != fragment_snapshot_hash
        ):
            metadata_updated += 1

        can_reuse = bool(
            template_compatible
            and previous
            and previous.get("vector_content_hash")
            == vector_content_hash
            and previous.get("passage_text")
            == passage_text
            and isinstance(previous.get("embedding"), list)
        )
        if can_reuse:
            embedding = [
                float(value)
                for value in previous["embedding"]
            ]
            reused += 1
        else:
            if encoder is None:
                encoder = _default_encoder()
            embedding = [
                float(value)
                for value in encoder(passage_text)
            ]
            if not embedding:
                raise ValueError(
                    "empty embedding for scoped fragment "
                    f"{fragment_id}"
                )
            recomputed += 1
            if previous is None:
                added += 1

        entries.append(
            {
                "fragment_id": fragment_id,
                "source_type": fresh.source_type,
                "document_id": fresh.document_id,
                "content_hash": fresh.content_hash,
                "vector_content_hash": vector_content_hash,
                "fragment_snapshot_hash":
                    fragment_snapshot_hash,
                "passage_text": passage_text,
                "fragment": fragment_payload,
                "embedding": embedding,
            }
        )

    entries.sort(
        key=lambda entry: str(entry["fragment_id"])
    )
    dimensions = {
        len(entry["embedding"])
        for entry in entries
    }
    if len(dimensions) > 1:
        raise ValueError(
            "inconsistent note embedding dimensions after scoped attach"
        )
    expected_dimension = int(
        previous_manifest.get("dimension") or 0
    )
    if dimensions and dimensions != {expected_dimension}:
        raise ValueError(
            "scoped note attach changed embedding dimension"
        )

    scoped_after = sum(
        1
        for entry in entries
        if entry.get("document_id") == document_id
        and str(entry["fragment_id"]) in target_ids
    )
    if scoped_after != len(fresh_by_id):
        raise ValueError(
            "scoped note attach did not publish every target fragment"
        )

    content_hash = _aggregate_content_hash(entries)
    created_at = (
        built_at
        or datetime.now(timezone.utc).isoformat()
    )
    snapshot_version = previous_manifest.get(
        "fragment_snapshot_version"
    )
    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": expected_dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version":
            snapshot_version,
        "content_hash": content_hash,
        "count": len(entries),
        "built_at": created_at,
        "entries": entries,
    }
    payload_bytes = _json_bytes(payload)
    payload_sha = hashlib.sha256(
        payload_bytes
    ).hexdigest()
    generation_name = (
        f"notes-{content_hash[:16]}-"
        f"{payload_sha[:16]}.json"
    )
    manifest = {
        "status": "ready",
        "schema_version": INDEX_SCHEMA_VERSION,
        "model": local_embedding_service.MODEL_NAME,
        "dimension": expected_dimension,
        "normalization": NORMALIZE_EMBEDDINGS,
        "batch_size": ENCODE_BATCH_SIZE,
        "source_types": list(NOTE_SOURCE_TYPES),
        "excluded_origin_kinds":
            list(EXCLUDED_NOTE_ORIGIN_KINDS),
        "passage_template": PASSAGE_TEMPLATE_VERSION,
        "fragment_snapshot_version":
            snapshot_version,
        "count": len(entries),
        "content_hash": content_hash,
        "index_file": generation_name,
        "index_sha256": payload_sha,
        "built_at": created_at,
        "origin_counts": dict(
            sorted(
                Counter(
                    _fragment_origin(entry["fragment"])
                    for entry in entries
                ).items()
            )
        ),
    }

    root.mkdir(parents=True, exist_ok=True)
    generation = root / generation_name
    generation_created = False
    try:
        if not generation.exists():
            _atomic_write_bytes(
                generation,
                payload_bytes,
            )
            generation_created = True
        elif (
            hashlib.sha256(
                generation.read_bytes()
            ).hexdigest()
            != payload_sha
        ):
            raise ValueError(
                "existing scoped note generation hash mismatch"
            )
        _atomic_write_bytes(
            root / MANIFEST_NAME,
            _json_bytes(manifest),
        )
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE.pop(
                str(root.resolve()),
                None,
            )
        _prune_old_generations(
            root,
            keep=generation_name,
        )
    except Exception:
        if (
            generation_created
            and generation.is_file()
        ):
            generation.unlink()
        raise

    return {
        **manifest,
        "scope": "affected_fragment_ids_only",
        "document_id": document_id,
        "scoped_entry_count_before": sum(
            1
            for entry in previous_entries
            if str(entry["fragment_id"]) in target_ids
            and entry.get("document_id") == document_id
        ),
        "scoped_entry_count_after": scoped_after,
        "reused_count": reused,
        "recomputed_count": recomputed,
        "added_count": added,
        "metadata_updated_count": metadata_updated,
        "unrelated_preserved_count":
            len(previous_entries)
            - sum(
                1
                for entry in previous_entries
                if str(entry["fragment_id"]) in target_ids
            ),
        "full_rebuild_performed": False,
        "orphan_delete_performed": False,
        "elapsed_ms": round(
            (time.perf_counter() - started) * 1000,
            2,
        ),
        "vector_write_performed": True,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
    }


def get_zotero_note_vector_status(
    *, index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR
) -> dict[str, Any]:
    root = Path(index_dir)
    try:
        manifest, entries = _load_existing(root, required=True)
    except NoteVectorIndexUnavailable as exc:
        return {
            "status": "not_ready",
            "reason": str(exc),
            "index_dir": str(root),
            "read_only": True,
        }
    return {
        **manifest,
        "status": "ready",
        "validated_count": len(entries),
        "index_dir": str(root),
        "read_only": True,
    }


def plan_zotero_note_vector_sync(
    *,
    index_dir: str | Path,
    fragments: Iterable[NotebookFragment],
) -> dict[str, Any]:
    """Compute the exact incremental note-vector work without mutating files."""

    root = Path(index_dir)
    manifest, previous_entries = _load_existing(
        root,
        required=True,
        validate_embeddings=False,
        validate_profile=False,
    )
    assert manifest is not None
    current = sorted(list(fragments), key=lambda item: item.fragment_id)
    invalid_sources = sorted(
        {
            item.source_type
            for item in current
            if item.source_type not in NOTE_SOURCE_TYPES
        }
    )
    if invalid_sources:
        raise ValueError(
            f"note vector plan received unsupported source types: {invalid_sources}"
        )
    current_ids = [item.fragment_id for item in current]
    duplicate_current = sorted(
        fragment_id
        for fragment_id, count in Counter(current_ids).items()
        if count > 1
    )
    if duplicate_current:
        raise ValueError(
            f"duplicate authoritative note fragment identities: {duplicate_current[:5]}"
        )

    previous_by_id = {str(entry["fragment_id"]): entry for entry in previous_entries}
    template_compatible = bool(
        manifest.get("schema_version") == INDEX_SCHEMA_VERSION
        and manifest.get("passage_template") == PASSAGE_TEMPLATE_VERSION
        and manifest.get("model") == local_embedding_service.MODEL_NAME
        and manifest.get("normalization") is NORMALIZE_EMBEDDINGS
        and manifest.get("batch_size") == ENCODE_BATCH_SIZE
    )
    dimension = int(manifest.get("dimension") or 0)
    reused_ids: list[str] = []
    added_ids: list[str] = []
    changed_ids: list[str] = []
    metadata_only_ids: list[str] = []
    added_by_type: Counter[str] = Counter()
    changed_by_type: Counter[str] = Counter()
    reused_by_type: Counter[str] = Counter()

    for fragment in current:
        passage_text = build_note_passage_text(fragment)
        content_hash = _vector_content_hash(fragment, passage_text)
        previous = previous_by_id.get(fragment.fragment_id)
        can_reuse = bool(
            template_compatible
            and previous is not None
            and previous.get("vector_content_hash") == content_hash
            and previous.get("passage_text") == passage_text
            and _embedding_is_valid(previous.get("embedding"), dimension=dimension)
        )
        if previous is None:
            added_ids.append(fragment.fragment_id)
            added_by_type[fragment.source_type] += 1
        elif not can_reuse:
            changed_ids.append(fragment.fragment_id)
            changed_by_type[fragment.source_type] += 1
        else:
            reused_ids.append(fragment.fragment_id)
            reused_by_type[fragment.source_type] += 1
            if _entry_fragment_snapshot_hash(previous) != _fragment_snapshot_hash(fragment):
                metadata_only_ids.append(fragment.fragment_id)

    current_id_set = set(current_ids)
    removed_entries = [
        entry
        for entry in previous_entries
        if str(entry["fragment_id"]) not in current_id_set
    ]
    removed_by_type = Counter(str(entry.get("source_type") or "unknown") for entry in removed_entries)
    return {
        "status": "ready",
        "expected_total": len(current),
        "previous_total": len(previous_entries),
        "reused_count": len(reused_ids),
        "added_count": len(added_ids),
        "removed_count": len(removed_entries),
        "changed_count": len(changed_ids),
        "metadata_only_count": len(metadata_only_ids),
        "expected_inference_count": len(added_ids) + len(changed_ids),
        "reused_fragment_ids": reused_ids,
        "added_fragment_ids": added_ids,
        "removed_fragment_ids": [str(entry["fragment_id"]) for entry in removed_entries],
        "changed_fragment_ids": changed_ids,
        "metadata_only_fragment_ids": metadata_only_ids,
        "added_by_source_type": dict(sorted(added_by_type.items())),
        "removed_by_source_type": dict(sorted(removed_by_type.items())),
        "changed_by_source_type": dict(sorted(changed_by_type.items())),
        "reused_by_source_type": dict(sorted(reused_by_type.items())),
        "model": manifest.get("model"),
        "dimension": dimension,
        "normalization": manifest.get("normalization"),
        "passage_template": manifest.get("passage_template"),
        "template_compatible": template_compatible,
        "read_only": True,
    }


def validate_zotero_note_vector_projection(
    *,
    index_dir: str | Path,
    fragments: Iterable[NotebookFragment],
) -> dict[str, Any]:
    """Validate one materialized note-vector index against authoritative notes."""

    manifest, entries = _load_existing(Path(index_dir), required=True)
    assert manifest is not None
    expected = sorted(list(fragments), key=lambda item: item.fragment_id)
    expected_ids = [item.fragment_id for item in expected]
    duplicate_expected = sorted(
        fragment_id
        for fragment_id, count in Counter(expected_ids).items()
        if count > 1
    )
    actual_ids = [str(entry["fragment_id"]) for entry in entries]
    duplicate_actual = sorted(
        fragment_id
        for fragment_id, count in Counter(actual_ids).items()
        if count > 1
    )
    expected_by_id = {item.fragment_id: item for item in expected}
    actual_by_id = {str(entry["fragment_id"]): entry for entry in entries}
    missing = sorted(set(expected_by_id) - set(actual_by_id))
    orphan = sorted(set(actual_by_id) - set(expected_by_id))
    mismatched: list[str] = []
    for fragment_id in sorted(set(expected_by_id).intersection(actual_by_id)):
        fragment = expected_by_id[fragment_id]
        entry = actual_by_id[fragment_id]
        passage_text = build_note_passage_text(fragment)
        if (
            entry.get("source_type") != fragment.source_type
            or entry.get("document_id") != fragment.document_id
            or entry.get("content_hash") != fragment.content_hash
            or entry.get("passage_text") != passage_text
            or entry.get("vector_content_hash")
            != _vector_content_hash(fragment, passage_text)
            or entry.get("fragment_snapshot_hash")
            != _fragment_snapshot_hash(fragment)
            or entry.get("fragment") != fragment.model_dump(mode="json")
        ):
            mismatched.append(fragment_id)
    ready = not (
        duplicate_expected
        or duplicate_actual
        or missing
        or orphan
        or mismatched
    )
    return {
        "status": "ready" if ready else "invalid",
        "ready": ready,
        "expected_count": len(expected),
        "actual_count": len(entries),
        "missing_count": len(missing),
        "orphan_count": len(orphan),
        "duplicate_count": len(duplicate_actual),
        "authoritative_duplicate_count": len(duplicate_expected),
        "mismatched_count": len(mismatched),
        "missing_fragment_ids": missing,
        "orphan_fragment_ids": orphan,
        "duplicate_fragment_ids": duplicate_actual,
        "mismatched_fragment_ids": mismatched,
        "model": manifest.get("model"),
        "dimension": manifest.get("dimension"),
        "normalization": manifest.get("normalization"),
        "passage_template": manifest.get("passage_template"),
        "read_only": True,
    }


def inspect_zotero_note_vector_document_impact(
    document_id: int,
    *,
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
) -> dict[str, Any]:
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
        raise ValueError("document_id must be a positive integer")

    root = Path(index_dir)
    if not (root / MANIFEST_NAME).is_file():
        return {
            "status": "not_present",
            "document_id": document_id,
            "document_entry_count": 0,
            "fragment_ids": [],
            "read_only": True,
        }

    _manifest, entries = _load_existing(root, required=True)
    matches = [
        entry
        for entry in entries
        if entry.get("document_id") == document_id
    ]
    return {
        "status": "ready",
        "document_id": document_id,
        "document_entry_count": len(matches),
        "fragment_ids": [
            str(entry.get("fragment_id") or "")
            for entry in matches
            if entry.get("fragment_id")
        ],
        "read_only": True,
    }


def search_zotero_note_vectors(
    query: str,
    *,
    limit: int = DEFAULT_RECALL_LIMIT,
    source_types: Iterable[NotebookSourceType] = NOTE_SOURCE_TYPES,
    document_ids: Iterable[int] | None = None,
    index_dir: str | Path | None = None,
    encode_query: Callable[[str], list[float]] | None = None,
) -> dict[str, Any]:
    normalized_query = " ".join(str(query or "").split())
    if not normalized_query:
        raise ValueError("query must not be empty")
    requested = set(source_types)
    if not requested.issubset(NOTE_SOURCE_TYPES):
        raise ValueError(f"unsupported note source types: {sorted(requested.difference(NOTE_SOURCE_TYPES))}")
    selected_documents = {int(value) for value in document_ids or []}
    if index_dir is None:
        from app.services.retrieval_generation_service import (
            current_retrieval_generation,
        )

        resolved_index_dir = current_retrieval_generation().native_note_vector_path
    else:
        resolved_index_dir = Path(index_dir)
    manifest, entries = _load_existing(resolved_index_dir, required=True)
    encoder = encode_query or _default_encoder()
    query_embedding = [float(value) for value in encoder(normalized_query)]
    if len(query_embedding) != int(manifest["dimension"]):
        raise NoteVectorIndexUnavailable("query embedding dimension does not match note index")

    scored: list[dict[str, Any]] = []
    for entry in entries:
        if entry["source_type"] not in requested:
            continue
        document_id = entry.get("document_id")
        if selected_documents and document_id not in selected_documents:
            continue
        score = local_embedding_service._cosine_similarity(  # noqa: SLF001 - exact legacy math
            query_embedding,
            [float(value) for value in entry["embedding"]],
        )
        scored.append(
            {
                "fragment": entry["fragment"],
                "passage_text": entry["passage_text"],
                "semantic_score": float(score),
            }
        )
    scored.sort(
        key=lambda item: (
            -float(item["semantic_score"]),
            str(item["fragment"]["fragment_id"]),
        )
    )
    safe_limit = max(1, min(int(limit or DEFAULT_RECALL_LIMIT), 50))
    return {
        "status": "ok",
        "backend": "derived_json_vector_index",
        "model": manifest["model"],
        "dimension": manifest["dimension"],
        "normalization": manifest["normalization"],
        "content_hash": manifest["content_hash"],
        "results": scored[:safe_limit],
        "read_only": True,
        "fts_fallback_used": False,
    }


def build_note_passage_text(fragment: NotebookFragment) -> str:
    parts = ["[User note]", fragment.note_text or ""]
    if fragment.selected_text:
        parts.extend(["", "[Selected source text]", fragment.selected_text])
    context = "\n".join(
        value for value in (fragment.context_before, fragment.context_after) if value
    )
    if context:
        parts.extend(["", "[Context]", context])
    return "\n".join(parts).strip()


def _default_encoder() -> Callable[[str], list[float]]:
    timings: dict[str, float] = {}
    model = local_embedding_service._load_model(timings)  # noqa: SLF001 - exact legacy model

    def encode(value: str) -> list[float]:
        return local_embedding_service._encode_text(model, value)  # noqa: SLF001

    return encode


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return int(stat.st_mtime_ns), int(stat.st_size)


def _load_existing(
    root: Path,
    *,
    required: bool,
    validate_embeddings: bool = True,
    validate_profile: bool = True,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        if required:
            raise NoteVectorIndexUnavailable("zotero note vector manifest is missing")
        return None, []

    cache_key = str(root.resolve())
    try:
        manifest_signature = _file_signature(manifest_path)

        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(cache_key)

        if cached is not None and cached[0] == manifest_signature:
            cached_index_path = root / cached[1]
            if _file_signature(cached_index_path) == cached[2]:
                _validate_loaded_index(
                    cached[3],
                    {**cached[3], "entries": cached[4]},
                    cached[4],
                    validate_embeddings=validate_embeddings,
                    validate_profile=validate_profile,
                )
                return cached[3], cached[4]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index_name = str(manifest["index_file"])
        if Path(index_name).name != index_name:
            raise ValueError("unsafe note index file name")

        index_path = root / index_name
        payload_bytes = index_path.read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != manifest["index_sha256"]:
            raise ValueError("note index SHA256 mismatch")

        payload = json.loads(payload_bytes)
        entries = list(payload["entries"])
        _validate_loaded_index(
            manifest,
            payload,
            entries,
            validate_embeddings=validate_embeddings,
            validate_profile=validate_profile,
        )
        index_signature = _file_signature(index_path)

        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE[cache_key] = (
                manifest_signature,
                index_name,
                index_signature,
                manifest,
                entries,
            )

        return manifest, entries
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        with _INDEX_CACHE_LOCK:
            _INDEX_CACHE.pop(cache_key, None)
        if required:
            raise NoteVectorIndexUnavailable(f"invalid zotero note vector index: {exc}") from exc
        return None, []


def _validate_loaded_index(
    manifest: dict[str, Any],
    payload: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    validate_embeddings: bool = True,
    validate_profile: bool = True,
) -> None:
    if manifest.get("status") != "ready":
        raise ValueError("manifest is not ready")
    if manifest.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise ValueError("unsupported note index schema")
    if validate_profile:
        if manifest.get("model") != local_embedding_service.MODEL_NAME:
            raise ValueError("note index embedding model mismatch")
        if manifest.get("normalization") is not NORMALIZE_EMBEDDINGS:
            raise ValueError("note index normalization mismatch")
        if manifest.get("batch_size") != ENCODE_BATCH_SIZE:
            raise ValueError("note index batch size mismatch")
        if manifest.get("passage_template") != PASSAGE_TEMPLATE_VERSION:
            raise ValueError("note index passage template mismatch")
    if manifest.get("source_types") != list(NOTE_SOURCE_TYPES):
        raise ValueError("note index source type contract mismatch")
    if int(manifest.get("count", -1)) != len(entries):
        raise ValueError("note index count mismatch")
    if payload.get("content_hash") != manifest.get("content_hash"):
        raise ValueError("note index content hash mismatch")
    dimension = int(manifest.get("dimension") or 0)
    if validate_embeddings and any(
        not _embedding_is_valid(entry.get("embedding"), dimension=dimension)
        for entry in entries
    ):
        raise ValueError("note index vector dimension mismatch")
    fragment_ids = [str(entry.get("fragment_id") or "") for entry in entries]
    if any(not fragment_id for fragment_id in fragment_ids):
        raise ValueError("note index fragment identity missing")
    if len(set(fragment_ids)) != len(fragment_ids):
        raise ValueError("duplicate note index fragment identity")
    if any(entry.get("source_type") not in NOTE_SOURCE_TYPES for entry in entries):
        raise ValueError("note index entry source type mismatch")
    if any(not isinstance(entry.get("passage_text"), str) for entry in entries):
        raise ValueError("note index passage text missing")

    snapshot_version = manifest.get("fragment_snapshot_version")
    if snapshot_version not in {None, FRAGMENT_SNAPSHOT_VERSION}:
        raise ValueError("unsupported fragment snapshot version")
    if payload.get("fragment_snapshot_version") != snapshot_version:
        raise ValueError("fragment snapshot version mismatch")
    if snapshot_version == FRAGMENT_SNAPSHOT_VERSION:
        for entry in entries:
            fragment_payload = entry.get("fragment")
            if not isinstance(fragment_payload, dict):
                raise ValueError("note index fragment snapshot missing")
            if entry.get("fragment_snapshot_hash") != _fragment_snapshot_hash(
                fragment_payload
            ):
                raise ValueError("note index fragment snapshot hash mismatch")
    if payload.get("schema_version") != manifest.get("schema_version"):
        raise ValueError("note index payload schema mismatch")
    if payload.get("model") != manifest.get("model"):
        raise ValueError("note index payload model mismatch")
    if int(payload.get("dimension") or 0) != dimension:
        raise ValueError("note index payload dimension mismatch")
    if payload.get("normalization") is not manifest.get("normalization"):
        raise ValueError("note index payload normalization mismatch")
    if payload.get("batch_size") != manifest.get("batch_size"):
        raise ValueError("note index payload batch size mismatch")
    if payload.get("passage_template") != manifest.get("passage_template"):
        raise ValueError("note index payload passage template mismatch")
    if int(payload.get("count", -1)) != len(entries):
        raise ValueError("note index payload count mismatch")
    if _aggregate_content_hash(entries) != manifest.get("content_hash"):
        raise ValueError("note index aggregate content hash mismatch")


def _embedding_is_valid(value: Any, *, dimension: int) -> bool:
    if not isinstance(value, list) or dimension <= 0 or len(value) != dimension:
        return False
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return False
        if not math.isfinite(float(item)):
            return False
    return True


def _fragment_snapshot_hash(fragment: NotebookFragment | dict[str, Any]) -> str:
    payload = (
        fragment.model_dump(mode="json")
        if isinstance(fragment, NotebookFragment)
        else dict(fragment)
    )
    serialized = json.dumps(
        {
            "version": FRAGMENT_SNAPSHOT_VERSION,
            "fragment": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _entry_fragment_snapshot_hash(entry: dict[str, Any]) -> str | None:
    stored = entry.get("fragment_snapshot_hash")
    if isinstance(stored, str) and stored:
        return stored
    fragment_payload = entry.get("fragment")
    if not isinstance(fragment_payload, dict):
        return None
    return _fragment_snapshot_hash(fragment_payload)


def _aggregate_content_hash(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        snapshot_hash = str(
            entry.get("fragment_snapshot_hash")
            or _fragment_snapshot_hash(entry["fragment"])
        )
        digest.update(
            (
                f"{entry['fragment_id']}|"
                f"{entry['vector_content_hash']}|"
                f"{snapshot_hash}|"
                f"{PASSAGE_TEMPLATE_VERSION}|"
                f"{FRAGMENT_SNAPSHOT_VERSION}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _vector_content_hash(fragment: NotebookFragment, passage_text: str) -> str:
    return hashlib.sha256(
        (
            PASSAGE_TEMPLATE_VERSION
            + "\u241f"
            + fragment.fragment_id
            + "\u241f"
            + fragment.content_hash
            + "\u241f"
            + passage_text
        ).encode("utf-8")
    ).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    # Keep the owned temporary name independent of the content-addressed target
    # name.  Repeating that long name can exceed the Win32 MAX_PATH boundary in
    # an otherwise valid candidate generation path.
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _prune_old_generations(root: Path, *, keep: str) -> None:
    for candidate in root.glob("notes-*.json"):
        if candidate.name == keep:
            continue
        try:
            candidate.unlink()
        except OSError:
            # The newly published manifest remains valid even if Windows has a
            # stale reader handle on an older generation. A later sync retries.
            continue


def _fragment_origin(fragment: dict[str, Any]) -> str:
    provenance = fragment.get("provenance") or []
    return str(fragment.get("source_type") or "unknown") + ":" + str(
        next(
            (
                entry.get("source")
                for entry in provenance
                if isinstance(entry, dict) and entry.get("source")
            ),
            "unspecified",
        )
    )
