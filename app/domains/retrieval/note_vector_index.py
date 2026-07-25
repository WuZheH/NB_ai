from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import hashlib
import json
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

    previous_manifest, previous_entries = _load_existing(root, required=False)
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
    for fragment in current:
        passage_text = build_note_passage_text(fragment)
        vector_content_hash = _vector_content_hash(fragment, passage_text)
        previous = previous_by_id.get(fragment.fragment_id)
        can_reuse = bool(
            not force
            and template_compatible
            and previous
            and previous.get("vector_content_hash") == vector_content_hash
            and previous.get("passage_text") == passage_text
            and isinstance(previous.get("embedding"), list)
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
                "passage_text": passage_text,
                "fragment": fragment.model_dump(mode="json"),
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
        and removed == 0
        and previous_manifest.get("content_hash") == content_hash
        and int(previous_manifest.get("count") or 0) == len(entries)
    ):
        return {
            **previous_manifest,
            "reused_count": reused,
            "recomputed_count": 0,
            "added_count": 0,
            "updated_count": 0,
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
        "removed_count": removed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
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


def search_zotero_note_vectors(
    query: str,
    *,
    limit: int = DEFAULT_RECALL_LIMIT,
    source_types: Iterable[NotebookSourceType] = NOTE_SOURCE_TYPES,
    document_ids: Iterable[int] | None = None,
    index_dir: str | Path = ZOTERO_NOTE_VECTOR_DIR,
    encode_query: Callable[[str], list[float]] | None = None,
) -> dict[str, Any]:
    normalized_query = " ".join(str(query or "").split())
    if not normalized_query:
        raise ValueError("query must not be empty")
    requested = set(source_types)
    if not requested.issubset(NOTE_SOURCE_TYPES):
        raise ValueError(f"unsupported note source types: {sorted(requested.difference(NOTE_SOURCE_TYPES))}")
    selected_documents = {int(value) for value in document_ids or []}
    manifest, entries = _load_existing(Path(index_dir), required=True)
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
    root: Path, *, required: bool
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
        _validate_loaded_index(manifest, payload, entries)
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
) -> None:
    if manifest.get("status") != "ready":
        raise ValueError("manifest is not ready")
    if manifest.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise ValueError("unsupported note index schema")
    if manifest.get("model") != local_embedding_service.MODEL_NAME:
        raise ValueError("note index embedding model mismatch")
    if manifest.get("normalization") is not NORMALIZE_EMBEDDINGS:
        raise ValueError("note index normalization mismatch")
    if int(manifest.get("count", -1)) != len(entries):
        raise ValueError("note index count mismatch")
    if payload.get("content_hash") != manifest.get("content_hash"):
        raise ValueError("note index content hash mismatch")
    dimension = int(manifest.get("dimension") or 0)
    if any(len(entry.get("embedding") or []) != dimension for entry in entries):
        raise ValueError("note index vector dimension mismatch")


def _aggregate_content_hash(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(
            f"{entry['fragment_id']}|{entry['vector_content_hash']}|{PASSAGE_TEMPLATE_VERSION}\n".encode(
                "utf-8"
            )
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
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
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
