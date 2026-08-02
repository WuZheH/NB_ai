from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.core.database import connect_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH, LANCEDB_DIR, VECTOR_STORE_DIR
from app.db.session import SessionLocal
from app.models import BookChapter, Document, KnowledgeChunk
from app.runtime.machine_config import (
    MachineConfigUnavailable,
    default_machine_config_path,
    load_machine_config,
    require_runtime_machine_config,
)
from app.services import local_embedding_service, object_semantic_search_service
from app.services.library_service import READ_LIBRARY_STATUSES, is_metadata_chunk_text


BACKEND = "lancedb"
EMBEDDING_MODEL = local_embedding_service.MODEL_NAME
EMBEDDING_MODEL_PATH = str(local_embedding_service.DEFAULT_MODEL_PATH)
PASSAGE_PROFILE_VERSION = "passage_profile_v1"
OBJECT_PROFILE_VERSION = "object_profile_v1"
NOTE_PROFILE_VERSION = "note_profile_v1"
VECTOR_STORE_ROOT = VECTOR_STORE_DIR
MANIFEST_PATH = VECTOR_STORE_ROOT / "vector_manifest.json"
PASSAGE_TABLE = "passage_embeddings"
OBJECT_TABLE = "object_embeddings"
NOTE_TABLE = "note_embeddings"
SOURCE_TYPES = {"passage", "object", "note", "inspiration", "relation"}
LIFECYCLE_FIELDS = {
    "vector_id",
    "source_type",
    "source_id",
    "source_hash",
    "profile_version",
    "embedding_model",
    "embedding_model_path",
    "embedding_dim",
    "created_at",
    "updated_at",
}
PASSAGE_EXPECTED_RECORD_FIELDS = {
    "vector_id",
    "record_id",
    "source_type",
    "source_kind",
    "source_id",
    "source_hash",
    "document_id",
    "document_title",
    "document_type",
    "object_import_mode",
    "chunk_id",
    "chapter_id",
    "chapter_title",
    "title",
    "heading_path",
    "passage_text",
    "text_for_embedding",
    "text_hash",
    "pdf_page",
    "page",
    "pdf_page_start",
    "pdf_page_end",
    "embedding_model",
    "embedding_model_path",
    "embedding_dim",
    "profile_version",
    "source_updated_at",
    "created_at",
    "updated_at",
    "vector",
}
OBJECT_EXPECTED_RECORD_FIELDS = {
    "vector_id",
    "source_type",
    "source_id",
    "source_hash",
    "object_key",
    "object_id",
    "canonical_name",
    "object_type",
    "object_type_label",
    "document_id",
    "document_title",
    "evidence_count",
    "object_profile_text",
    "text_for_embedding",
    "profile_hash",
    "embedding_model",
    "embedding_model_path",
    "embedding_dim",
    "profile_version",
    "created_at",
    "updated_at",
    "vector",
}
NOTE_EXPECTED_RECORD_FIELDS = {
    "vector_id",
    "source_type",
    "source_id",
    "source_hash",
    "note_id",
    "document_id",
    "note_type",
    "title",
    "note_text",
    "summary",
    "selected_text",
    "source_comment",
    "source_record_kind",
    "source_identity",
    "source_missing",
    "pdf_page",
    "page_label",
    "text_for_embedding",
    "embedding_model",
    "embedding_model_path",
    "embedding_dim",
    "profile_version",
    "created_at",
    "updated_at",
    "vector",
}


class VectorStoreUnavailable(RuntimeError):
    pass


class VectorStoreSchemaMismatch(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _active_embedding_model_path() -> str:
    """Return the embedding model directory selected for this machine.

    Runtime-provided configuration takes precedence. Direct CLI and maintenance
    processes fall back to the normal roaming machine-config before using the
    source-tree default.
    """

    try:
        config = require_runtime_machine_config()
    except MachineConfigUnavailable:
        try:
            config = load_machine_config(default_machine_config_path())
        except RuntimeError:
            return EMBEDDING_MODEL_PATH

    if not config.ready or config.embedding is None:
        return EMBEDDING_MODEL_PATH

    return str(config.embedding.path)


def _normalized_model_path(value: Any) -> str:
    return os.path.normcase(
        str(Path(str(value or "")).expanduser().resolve(strict=False))
    )


def _same_model_path(left: Any, right: Any) -> bool:
    if not str(left or "").strip() or not str(right or "").strip():
        return False
    return _normalized_model_path(left) == _normalized_model_path(right)


def open_vector_store(path: Path | None = None) -> Any:
    lancedb = _import_lancedb()
    target = path or LANCEDB_DIR
    target.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(target))


def get_vector_manifest(path: Path | None = None) -> dict[str, Any] | None:
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def write_vector_manifest(manifest: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    current = dict(manifest)
    now = _utc_now()
    current.setdefault("created_at", now)
    current["updated_at"] = now
    manifest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def check_vector_store_status(
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    try:
        _import_lancedb()
    except VectorStoreUnavailable as exc:
        return _status_payload(available=False, reason=str(exc), stale=False, manifest=None, tables={})

    actual_store_path = store_path or LANCEDB_DIR
    actual_manifest_path = manifest_path or MANIFEST_PATH
    if not actual_store_path.exists():
        return _status_payload(available=False, reason="vector_store_missing", stale=False, manifest=None, tables={})
    manifest = get_vector_manifest(actual_manifest_path)
    if manifest is None:
        return _status_payload(available=False, reason="vector_manifest_missing", stale=False, manifest=None, tables={})

    db = open_vector_store(actual_store_path)
    tables = {
        PASSAGE_TABLE: _table_status(db, PASSAGE_TABLE),
        OBJECT_TABLE: _table_status(db, OBJECT_TABLE),
    }
    sync = {
        "passages": _sync_status(PASSAGE_TABLE, collect_passage_sources(), db),
        "objects": _sync_status(OBJECT_TABLE, collect_object_sources(), db),
    }
    manifest_reason = _stale_reason(manifest)
    freshness = evaluate_vector_store_freshness(
        sync,
        available=True,
        manifest_reason=manifest_reason,
    )
    return _status_payload(
        available=True,
        reason=freshness["reason"],
        stale=not freshness["complete"],
        manifest=manifest,
        tables=tables,
        sync=sync,
        freshness=freshness,
    )


def build_passage_embeddings(
    *,
    model_path: str | None = None,
    reset: bool = False,
    limit: int | None = None,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    model = local_embedding_service._load_model({})
    rows = _passage_source_rows(limit=limit)
    records = [build_passage_record(document, chunk, model=model, model_path=model_path) for document, chunk in rows]
    _write_table(PASSAGE_TABLE, records, reset=reset, store_path=store_path)
    manifest = _updated_manifest(
        manifest_path=manifest_path,
        embedding_dim=_embedding_dim(records),
        passage_count=len(records),
    )
    return {
        "kind": "passages",
        "count": len(records),
        "embedding_dim": _embedding_dim(records),
        "manifest": manifest,
        "elapsed_ms": round(_elapsed_ms(started), 2),
    }


def build_object_embeddings(
    *,
    model_path: str | None = None,
    reset: bool = False,
    limit: int | None = None,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    model = local_embedding_service._load_model({})
    objects = object_semantic_search_service._load_all_objects()
    if limit is not None:
        objects = objects[: max(0, int(limit))]
    records = [record for obj in objects for record in [build_object_record(obj, model=model, model_path=model_path)] if record]
    _write_table(OBJECT_TABLE, records, reset=reset, store_path=store_path)
    manifest = _updated_manifest(
        manifest_path=manifest_path,
        embedding_dim=_embedding_dim(records),
        object_count=len(records),
    )
    return {
        "kind": "objects",
        "count": len(records),
        "embedding_dim": _embedding_dim(records),
        "manifest": manifest,
        "elapsed_ms": round(_elapsed_ms(started), 2),
    }


def build_passage_record(document: Document, chunk: KnowledgeChunk, *, model: Any, model_path: str | None = None) -> dict[str, Any]:
    passage_text = _compact_text(chunk.chunk_text)
    source_id = make_passage_source_id(document.id, chunk.id)
    document_title = str(_safe_attr(document, "title", "") or "")
    heading_path = str(_safe_attr(chunk, "heading_path", "") or "")
    text_for_embedding = " ".join([document_title, heading_path, passage_text])
    source_hash = compute_source_hash(
        _safe_attr(chunk, "content_hash", None) or f"{passage_text}\n{heading_path}\n{document.id}\n{chunk.id}"
    )
    vector = local_embedding_service._encode_text(model, text_for_embedding)
    return _passage_record_from_parts(
        document=document,
        chunk=chunk,
        passage_text=passage_text,
        source_id=source_id,
        source_hash=source_hash,
        text_for_embedding=text_for_embedding,
        vector=vector,
        model_path=model_path,
        now=_utc_now(),
    )


def build_passage_schema_record(document: Document, chunk: KnowledgeChunk, *, model_path: str | None = None) -> dict[str, Any]:
    passage_text = _compact_text(_safe_attr(chunk, "chunk_text", ""))
    source_id = make_passage_source_id(int(_safe_attr(document, "id", 0) or 0), int(_safe_attr(chunk, "id", 0) or 0))
    document_title = str(_safe_attr(document, "title", "") or "")
    heading_path = str(_safe_attr(chunk, "heading_path", "") or "")
    text_for_embedding = " ".join([document_title, heading_path, passage_text])
    source_hash = compute_source_hash(
        _safe_attr(chunk, "content_hash", None) or f"{passage_text}\n{heading_path}\n{_safe_attr(document, 'id', 0)}\n{_safe_attr(chunk, 'id', 0)}"
    )
    return _passage_record_from_parts(
        document=document,
        chunk=chunk,
        passage_text=passage_text,
        source_id=source_id,
        source_hash=source_hash,
        text_for_embedding=text_for_embedding,
        vector=[0.0] * _expected_embedding_dim(),
        model_path=model_path,
        now=_utc_now(),
    )


def _passage_record_from_parts(
    *,
    document: Document,
    chunk: KnowledgeChunk,
    passage_text: str,
    source_id: str,
    source_hash: str,
    text_for_embedding: str,
    vector: list[float],
    model_path: str | None,
    now: str,
) -> dict[str, Any]:
    pdf_page_start = _safe_int(_safe_attr(chunk, "pdf_page_start", None))
    pdf_page_end = _safe_int(_safe_attr(chunk, "pdf_page_end", None))
    chapter_id = _safe_int(_safe_attr(chunk, "chapter_id", None))
    now = _utc_now()
    return {
        "vector_id": source_id,
        "record_id": source_id,
        "source_type": "passage",
        "source_kind": "passage",
        "source_id": source_id,
        "source_hash": source_hash,
        "document_id": int(document.id),
        "document_title": str(_safe_attr(document, "title", "") or ""),
        "document_type": str(_safe_attr(document, "document_type", "") or ""),
        "object_import_mode": _safe_attr(document, "object_import_mode", None),
        "chunk_id": int(chunk.id),
        "chapter_id": chapter_id,
        "chapter_title": _safe_attr(chunk, "_vector_chapter_title", None) or "",
        "title": str(_safe_attr(document, "title", "") or ""),
        "heading_path": str(_safe_attr(chunk, "heading_path", "") or ""),
        "passage_text": passage_text,
        "text_for_embedding": text_for_embedding,
        "text_hash": source_hash,
        "pdf_page": pdf_page_start,
        "page": pdf_page_start,
        "pdf_page_start": pdf_page_start,
        "pdf_page_end": pdf_page_end,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_path": model_path or _active_embedding_model_path(),
        "embedding_dim": len(vector),
        "profile_version": PASSAGE_PROFILE_VERSION,
        "source_updated_at": _format_datetime(_safe_attr(chunk, "updated_at", None)),
        "created_at": now,
        "updated_at": now,
        "vector": vector,
    }


def load_chunk_page_metadata(chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Batch query DB for pdf_page_start/pdf_page_end by chunk_id.

    Returns a dict keyed by chunk_id with pdf_page_start, pdf_page_end, document_id.
    Chunks with null page are included but with null values.
    """
    if not chunk_ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.pdf_page_start, KnowledgeChunk.pdf_page_end, KnowledgeChunk.document_id)
            .where(KnowledgeChunk.id.in_(chunk_ids))
        ).all()
    return {
        row.id: {
            "pdf_page_start": int(row.pdf_page_start) if row.pdf_page_start is not None else None,
            "pdf_page_end": int(row.pdf_page_end) if row.pdf_page_end is not None else None,
            "document_id": int(row.document_id) if row.document_id is not None else None,
        }
        for row in rows
    }


def build_object_record(obj: dict[str, Any], *, model: Any, model_path: str | None = None) -> dict[str, Any] | None:
    profile_text = object_semantic_search_service._build_object_profile(obj)
    if not profile_text:
        return None
    vector = local_embedding_service._encode_text(model, profile_text)
    return _object_record_from_parts(obj=obj, profile_text=profile_text, vector=vector, model_path=model_path, now=_utc_now())


def build_object_schema_record(obj: dict[str, Any], *, model_path: str | None = None) -> dict[str, Any] | None:
    profile_text = object_semantic_search_service._build_object_profile(obj)
    if not profile_text:
        return None
    return _object_record_from_parts(
        obj=obj,
        profile_text=profile_text,
        vector=[0.0] * _expected_embedding_dim(),
        model_path=model_path,
        now=_utc_now(),
    )


def _object_record_from_parts(
    *,
    obj: dict[str, Any],
    profile_text: str,
    vector: list[float],
    model_path: str | None,
    now: str,
) -> dict[str, Any]:
    obj_type = str(obj.get("object_type") or "other")
    object_key = str(obj.get("object_key") or obj.get("object_name") or "").strip()
    source_id = make_object_source_id(object_key)
    source_hash = compute_source_hash(profile_text)
    return {
        "vector_id": source_id,
        "source_type": "object",
        "source_id": source_id,
        "source_hash": source_hash,
        "object_key": object_key,
        "object_id": obj.get("id"),
        "canonical_name": obj.get("object_name") or "",
        "object_type": obj_type,
        "object_type_label": object_semantic_search_service._object_type_label(obj_type),
        "document_id": obj.get("document_id"),
        "document_title": object_semantic_search_service._primary_document_title(obj),
        "evidence_count": len(obj.get("evidence_refs") or []),
        "object_profile_text": profile_text,
        "text_for_embedding": profile_text,
        "profile_hash": source_hash,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_path": model_path or _active_embedding_model_path(),
        "embedding_dim": len(vector),
        "profile_version": OBJECT_PROFILE_VERSION,
        "created_at": now,
        "updated_at": now,
        "vector": vector,
    }


def compute_source_hash(text: str) -> str:
    return _hash_text(text)


def make_passage_source_id(document_id: int, chunk_id: int) -> str:
    return f"chunk:{int(document_id)}:{int(chunk_id)}"


def make_object_source_id(object_key: str) -> str:
    return f"object:{str(object_key).strip()}"


def make_note_source_id(note_id: int) -> str:
    return f"note:{int(note_id)}"


def collect_personal_note_sources(
    *,
    document_id: int,
    source_db_path: str | Path,
) -> list[dict[str, Any]]:
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
        raise ValueError("document_id must be a positive integer")
    database = Path(source_db_path).resolve(strict=False)
    if not database.is_file():
        raise ValueError("source_db_path must identify an existing SQLite database")
    with connect_readonly_sqlite(
        database,
        resolve_strict=True,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    ) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                document_id,
                note_type,
                title,
                content,
                summary,
                content_hash,
                selected_text,
                source_comment,
                source_record_kind,
                source_identity,
                source_content_hash,
                source_missing,
                pdf_page,
                page_label,
                updated_at
            FROM personal_notes
            WHERE document_id = ?
            ORDER BY id
            """,
            (document_id,),
        ).fetchall()
    sources = []
    for row in rows:
        note = dict(row)
        note_id = int(note["id"])
        source_id = make_note_source_id(note_id)
        sources.append(
            {
                "source_type": "note",
                "source_id": source_id,
                "vector_id": source_id,
                "source_hash": _personal_note_source_hash(note),
                "profile_version": NOTE_PROFILE_VERSION,
                "embedding_model": EMBEDDING_MODEL,
                "note": note,
            }
        )
    return sources


def build_note_record(
    source: dict[str, Any],
    *,
    model: Any,
    model_path: str | None = None,
) -> dict[str, Any]:
    text_for_embedding = _note_text_for_embedding(source["note"])
    vector = local_embedding_service._encode_text(model, text_for_embedding)
    return _note_record_from_parts(
        source=source,
        text_for_embedding=text_for_embedding,
        vector=vector,
        model_path=model_path,
    )


def build_note_schema_record(
    source: dict[str, Any],
    *,
    model_path: str | None = None,
) -> dict[str, Any]:
    return _note_record_from_parts(
        source=source,
        text_for_embedding=_note_text_for_embedding(source["note"]),
        vector=[0.0] * _expected_embedding_dim(),
        model_path=model_path,
    )


def _note_record_from_parts(
    *,
    source: dict[str, Any],
    text_for_embedding: str,
    vector: list[float],
    model_path: str | None,
) -> dict[str, Any]:
    note = source["note"]
    source_id = str(source["source_id"])
    now = _utc_now()
    return {
        "vector_id": source_id,
        "source_type": "note",
        "source_id": source_id,
        "source_hash": str(source["source_hash"]),
        "note_id": int(note["id"]),
        "document_id": int(note["document_id"]),
        "note_type": str(note.get("note_type") or ""),
        "title": str(note.get("title") or ""),
        "note_text": str(note.get("content") or ""),
        "summary": str(note.get("summary") or ""),
        "selected_text": str(note.get("selected_text") or ""),
        "source_comment": str(note.get("source_comment") or ""),
        "source_record_kind": str(note.get("source_record_kind") or ""),
        "source_identity": str(note.get("source_identity") or ""),
        "source_missing": bool(note.get("source_missing")),
        "pdf_page": _safe_int(note.get("pdf_page")),
        "page_label": str(note.get("page_label") or ""),
        "text_for_embedding": text_for_embedding,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_path": model_path or _active_embedding_model_path(),
        "embedding_dim": len(vector),
        "profile_version": NOTE_PROFILE_VERSION,
        "created_at": now,
        "updated_at": now,
        "vector": vector,
    }


def _note_text_for_embedding(note: dict[str, Any]) -> str:
    parts = []
    for label, field in (
        ("Title", "title"),
        ("Note", "content"),
        ("Selected evidence", "selected_text"),
        ("Summary", "summary"),
    ):
        value = str(note.get(field) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def _personal_note_source_hash(note: dict[str, Any]) -> str:
    fields = (
        "note_type",
        "title",
        "content",
        "summary",
        "selected_text",
        "source_comment",
        "source_record_kind",
        "source_identity",
        "source_missing",
        "pdf_page",
        "page_label",
    )
    normalized = {
        field: (
            bool(note.get(field))
            if field == "source_missing"
            else str(note.get(field) or "").strip()
        )
        for field in fields
    }
    return compute_source_hash(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def collect_passage_sources(
    limit: int | None = None,
    source_ids: list[str] | None = None,
    source_db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    sources = []
    rows = (
        _passage_source_rows_from_sqlite(
            source_db_path,
            source_ids=source_ids,
            limit=limit,
        )
        if source_db_path is not None
        else _passage_source_rows(limit=limit, source_ids=source_ids)
    )
    for document, chunk in rows:
        passage_text = _compact_text(chunk.chunk_text)
        source_id = make_passage_source_id(document.id, chunk.id)
        source_hash = compute_source_hash(
            getattr(chunk, "content_hash", None) or f"{passage_text}\n{chunk.heading_path}\n{document.id}\n{chunk.id}"
        )
        sources.append({
            "source_type": "passage",
            "source_id": source_id,
            "vector_id": source_id,
            "source_hash": source_hash,
            "profile_version": PASSAGE_PROFILE_VERSION,
            "embedding_model": EMBEDDING_MODEL,
            "document": document,
            "chunk": chunk,
        })
    return sources


def sync_document_note_embeddings(
    document_id: int,
    *,
    dry_run: bool = True,
    apply: bool = False,
    source_db_path: str | Path,
    store_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    if dry_run == apply:
        raise ValueError("specify exactly one of dry_run or apply for note sync")
    if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
        raise ValueError("document_id must be a positive integer")
    source_database = Path(source_db_path).resolve(strict=False)
    actual_store_path = Path(store_path).resolve(strict=False)
    actual_manifest_path = Path(manifest_path).resolve(strict=False)
    if not source_database.is_file():
        raise ValueError("source_db_path must identify an existing temp database")
    if source_database == Path(DEFAULT_DB_PATH).resolve(strict=False):
        raise ValueError("note sync forbids the production database")
    if actual_store_path == Path(LANCEDB_DIR).resolve(strict=False):
        raise ValueError("note sync forbids the production vector store")
    if actual_manifest_path == Path(MANIFEST_PATH).resolve(strict=False):
        raise ValueError("note sync forbids the production vector manifest")

    sources = collect_personal_note_sources(
        document_id=document_id,
        source_db_path=source_database,
    )
    source_ids = [str(source["source_id"]) for source in sources]
    existing_by_id: dict[str, dict[str, Any]] = {}
    db = None
    if actual_store_path.exists():
        db = (
            _connect_existing_vector_store(actual_store_path)
            if dry_run
            else open_vector_store(actual_store_path)
        )
        existing_by_id = _existing_records_by_source_ids(
            db,
            NOTE_TABLE,
            source_ids,
        ) if source_ids else {}

    inserted_sources = []
    updated_sources = []
    skipped_sources = []
    for source in sources:
        current = existing_by_id.get(str(source["source_id"]))
        if current is None:
            inserted_sources.append(source)
        elif _record_stale(current, source):
            updated_sources.append(source)
        else:
            skipped_sources.append(source)
    changed_sources = [*inserted_sources, *updated_sources]

    note_count = (
        int(db.open_table(NOTE_TABLE).count_rows())
        if db is not None and NOTE_TABLE in _table_names(db)
        else 0
    )
    writes_performed = False
    if apply:
        if db is None:
            db = open_vector_store(actual_store_path)
        table_exists = NOTE_TABLE in _table_names(db)
        if table_exists:
            missing_fields = NOTE_EXPECTED_RECORD_FIELDS - _table_schema_fields(
                db,
                NOTE_TABLE,
            )
            if missing_fields:
                raise VectorStoreSchemaMismatch(
                    "document note apply forbids schema rebuild; missing fields: "
                    + ", ".join(sorted(missing_fields))
                )
        model = local_embedding_service._load_model({}) if changed_sources else None
        records = [
            build_note_record(source, model=model)
            for source in changed_sources
        ]
        if records:
            if table_exists:
                table = db.open_table(NOTE_TABLE)
                _delete_vector_ids(
                    table,
                    [str(source["source_id"]) for source in changed_sources],
                )
                table.add(records)
            else:
                db.create_table(NOTE_TABLE, data=records, mode="create")
            writes_performed = True
        table_names = _table_names(db)
        note_records = _existing_records(db, NOTE_TABLE)
        note_count = (
            int(db.open_table(NOTE_TABLE).count_rows())
            if NOTE_TABLE in table_names
            else 0
        )
        current_manifest = get_vector_manifest(actual_manifest_path) or {}
        embedding_dim = (
            _embedding_dim(note_records)
            or int(current_manifest.get("embedding_dim") or 0)
        )
        _updated_manifest(
            manifest_path=actual_manifest_path,
            embedding_dim=embedding_dim,
            note_count=note_count,
        )

    return {
        "kind": "notes",
        "scope": "document_only",
        "document_id": document_id,
        "dry_run": dry_run,
        "apply": apply,
        "source_count": len(sources),
        "inserted_count": len(inserted_sources) if apply else 0,
        "updated_count": len(updated_sources) if apply else 0,
        "skipped_count": len(skipped_sources),
        "note_count": note_count,
        "full_rebuild_performed": False,
        "orphan_delete_performed": False,
        "lancedb_writes_performed": writes_performed,
        "production_data_modified": False,
    }


def collect_object_sources(limit: int | None = None) -> list[dict[str, Any]]:
    objects = object_semantic_search_service._load_all_objects()
    if limit is not None:
        objects = objects[: max(0, int(limit))]
    sources = []
    for obj in objects:
        object_key = str(obj.get("object_key") or obj.get("object_name") or "").strip()
        if not object_key:
            continue
        profile_text = object_semantic_search_service._build_object_profile(obj)
        if not profile_text:
            continue
        source_id = make_object_source_id(object_key)
        sources.append({
            "source_type": "object",
            "source_id": source_id,
            "vector_id": source_id,
            "source_hash": compute_source_hash(profile_text),
            "profile_version": OBJECT_PROFILE_VERSION,
            "embedding_model": EMBEDDING_MODEL,
            "object": obj,
        })
    return sources


def sync_passage_embeddings(
    *,
    limit: int | None = None,
    dry_run: bool = False,
    delete_orphans: bool = False,
    rebuild_if_schema_mismatch: bool = True,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    sources = collect_passage_sources(limit=limit)
    return _sync_records(
        kind="passages",
        table_name=PASSAGE_TABLE,
        sources=sources,
        dry_run=dry_run,
        delete_orphans=delete_orphans,
        rebuild_if_schema_mismatch=rebuild_if_schema_mismatch,
        store_path=store_path,
        manifest_path=manifest_path,
        record_builder=lambda source, model: build_passage_record(source["document"], source["chunk"], model=model),
        schema_record_builder=lambda source: (
            build_passage_schema_record(source["document"], source["chunk"])
            if "document" in source and "chunk" in source
            else None
        ),
        expected_record_fields=PASSAGE_EXPECTED_RECORD_FIELDS,
    )


def sync_affected_passage_embeddings(
    source_ids: list[str],
    *,
    dry_run: bool = True,
    apply: bool = False,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
    source_db_path: str | Path | None = None,
) -> dict[str, Any]:
    if dry_run == apply:
        raise ValueError("specify exactly one of dry_run or apply for affected passage sync")
    if apply and source_db_path is not None:
        source_database = Path(source_db_path).resolve(strict=False)
        if source_database == Path(DEFAULT_DB_PATH).resolve(strict=False):
            raise ValueError("explicit-source affected apply forbids the production database")
        if store_path is None or Path(store_path).resolve(strict=False) == Path(
            LANCEDB_DIR
        ).resolve(strict=False):
            raise ValueError("explicit-source affected apply requires a temp vector store")
        if manifest_path is None or Path(manifest_path).resolve(strict=False) == Path(
            MANIFEST_PATH
        ).resolve(strict=False):
            raise ValueError("explicit-source affected apply requires a temp vector manifest")
    requested = _validated_passage_source_ids(source_ids)
    sources = collect_passage_sources(
        source_ids=requested,
        source_db_path=source_db_path,
    )
    source_by_id = {source["source_id"]: source for source in sources}
    actual_store_path = Path(store_path or LANCEDB_DIR)
    existing_by_id: dict[str, dict[str, Any]] = {}
    indexed_lookup = "store_missing"
    db = None
    if actual_store_path.exists():
        db = _connect_existing_vector_store(actual_store_path) if dry_run else open_vector_store(actual_store_path)
        existing_by_id = _existing_records_by_source_ids(db, PASSAGE_TABLE, requested)
        indexed_lookup = "source_id_filter"
    items = []
    changed_sources = []
    for source_id in requested:
        source = source_by_id.get(source_id)
        indexed = existing_by_id.get(source_id)
        if source and indexed:
            stale = _record_stale(indexed, source)
            status = "stale" if stale else "up_to_date"
            planned_action = "upsert" if stale else "skip"
        elif source:
            status = "missing"
            planned_action = "upsert"
        elif indexed:
            status = "orphan"
            planned_action = "none"
        else:
            status = "missing"
            planned_action = "none"
        if planned_action == "upsert" and source:
            changed_sources.append(source)
        items.append(
            {
                "source_id": source_id,
                "exists_in_db": source is not None,
                "exists_in_lancedb": indexed is not None,
                "source_hash_current": source.get("source_hash") if source else None,
                "source_hash_indexed": indexed.get("source_hash") if indexed else None,
                "status": status,
                "planned_action": planned_action,
            }
        )
    if apply:
        if db is None:
            db = open_vector_store(actual_store_path)
        table_exists = PASSAGE_TABLE in _table_names(db)
        if table_exists:
            table_fields = _table_schema_fields(db, PASSAGE_TABLE)
            missing_fields = PASSAGE_EXPECTED_RECORD_FIELDS - table_fields
            if missing_fields:
                raise VectorStoreSchemaMismatch(
                    "affected-only apply forbids schema rebuild; missing fields: " + ", ".join(sorted(missing_fields))
                )
        model = local_embedding_service._load_model({}) if changed_sources else None
        records = [
            build_passage_record(source["document"], source["chunk"], model=model)
            for source in changed_sources
        ]
        changed_ids = [source["source_id"] for source in changed_sources]
        if records:
            if table_exists:
                table = db.open_table(PASSAGE_TABLE)
                _delete_vector_ids(table, changed_ids)
                table.add(records)
            else:
                db.create_table(PASSAGE_TABLE, data=records, mode="create")
        if apply and manifest_path is not None:
            table_names = _table_names(db)
            passage_records = _existing_records(db, PASSAGE_TABLE)
            passage_count = (
                int(db.open_table(PASSAGE_TABLE).count_rows())
                if PASSAGE_TABLE in table_names
                else 0
            )
            object_count = (
                int(db.open_table(OBJECT_TABLE).count_rows())
                if OBJECT_TABLE in table_names
                else 0
            )
            current_manifest = get_vector_manifest(Path(manifest_path)) or {}
            embedding_dim = (
                _embedding_dim(passage_records)
                or int(current_manifest.get("embedding_dim") or 0)
            )
            _updated_manifest(
                manifest_path=Path(manifest_path),
                embedding_dim=embedding_dim,
                passage_count=passage_count,
                object_count=object_count,
            )
    return {
        "kind": "passages",
        "scope": "affected_source_ids_only",
        "dry_run": dry_run,
        "apply": apply,
        "full_rebuild_allowed": False,
        "delete_orphans_allowed": False,
        "store_path": str(actual_store_path),
        "manifest_path": str(Path(manifest_path or MANIFEST_PATH)),
        "requested_source_ids": requested,
        "scanned_count": len(requested),
        "indexed_lookup": indexed_lookup,
        "items": items,
        "would_upsert": sum(1 for item in items if item["planned_action"] == "upsert"),
        "upserted_count": len(changed_sources) if apply else 0,
        "lancedb_writes_performed": apply and bool(changed_sources),
    }


def inspect_document_vector_impact(
    *,
    passage_source_ids: list[str],
    object_keys: list[str],
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Read only: count exact vector identities affected by one document."""

    actual_store_path = Path(store_path or LANCEDB_DIR)
    if not actual_store_path.exists():
        raise VectorStoreUnavailable(f"vector store path missing: {actual_store_path}")
    db = _connect_existing_vector_store(actual_store_path)
    passage_ids = (
        sorted(set(_validated_passage_source_ids(passage_source_ids)))
        if passage_source_ids
        else []
    )
    object_ids = sorted(
        {
            make_object_source_id(value)
            for value in object_keys
            if str(value or "").strip()
        }
    )
    passage_records = _existing_records_by_source_ids(
        db, PASSAGE_TABLE, passage_ids
    ) if passage_ids else {}
    object_records = _existing_records_by_source_ids(
        db, OBJECT_TABLE, object_ids
    ) if object_ids else {}
    return {
        "status": "ok",
        "read_only": True,
        "passage_vector_count": len(passage_records),
        "object_vector_count": len(object_records),
        "passage_source_ids": sorted(passage_records),
        "object_source_ids": sorted(object_records),
    }


def inspect_note_vector_impact(
    *,
    note_source_ids: list[str],
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Read only: count exact personal-note vector identities."""

    actual_store_path = Path(store_path or LANCEDB_DIR)
    if not actual_store_path.exists():
        raise VectorStoreUnavailable(f"vector store path missing: {actual_store_path}")
    db = _connect_existing_vector_store(actual_store_path)
    requested = sorted({str(value) for value in note_source_ids if str(value)})
    records = (
        _existing_records_by_source_ids(db, NOTE_TABLE, requested)
        if requested
        else {}
    )
    return {
        "status": "ok",
        "read_only": True,
        "note_vector_count": len(records),
        "note_source_ids": sorted(records),
    }


def inspect_document_vector_state(
    *,
    document_id: int,
    expected_passage_source_ids: list[str],
    expected_note_source_ids: list[str],
    store_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect one document's passage and note vectors without mutation."""

    if (
        isinstance(document_id, bool)
        or not isinstance(document_id, int)
        or document_id <= 0
    ):
        raise ValueError("document_id must be a positive integer")
    passage_expected = sorted(
        set(_validated_passage_source_ids(expected_passage_source_ids))
    )
    note_expected = sorted(
        {str(value) for value in expected_note_source_ids if str(value)}
    )
    actual_store_path = Path(store_path or LANCEDB_DIR)
    if not actual_store_path.exists():
        return {
            "status": "unavailable",
            "read_only": True,
            "passage": _unavailable_document_vector_state(
                passage_expected,
                "vector_store_unavailable",
            ),
            "note": _unavailable_document_vector_state(
                note_expected,
                "vector_store_unavailable",
            ),
        }
    try:
        db = _connect_existing_vector_store(actual_store_path)
    except Exception:
        return {
            "status": "unavailable",
            "read_only": True,
            "passage": _unavailable_document_vector_state(
                passage_expected,
                "vector_store_connection_failed",
            ),
            "note": _unavailable_document_vector_state(
                note_expected,
                "vector_store_connection_failed",
            ),
        }

    passage = _inspect_document_table_state(
        db,
        PASSAGE_TABLE,
        document_id=document_id,
        expected_source_ids=passage_expected,
        kind="passage",
    )
    note = _inspect_document_table_state(
        db,
        NOTE_TABLE,
        document_id=document_id,
        expected_source_ids=note_expected,
        kind="note",
    )
    statuses = {str(passage["status"]), str(note["status"])}
    status = (
        "unavailable"
        if "unavailable" in statuses
        else "capability_unavailable"
        if "capability_unavailable" in statuses
        else "ok"
    )
    return {
        "status": status,
        "read_only": True,
        "passage": passage,
        "note": note,
    }


def _inspect_document_table_state(
    db: Any,
    table_name: str,
    *,
    document_id: int,
    expected_source_ids: list[str],
    kind: str,
) -> dict[str, Any]:
    expected = set(expected_source_ids)
    try:
        table_names = _table_names(db)
    except Exception:
        return _unavailable_document_vector_state(
            sorted(expected),
            f"{kind}_table_list_failed",
        )
    if table_name not in table_names:
        return _complete_document_vector_state(expected, set())
    try:
        table = db.open_table(table_name)
    except Exception:
        return _unavailable_document_vector_state(
            sorted(expected),
            f"{kind}_table_open_failed",
        )
    try:
        fields = _scoped_table_schema_fields(table)
    except Exception:
        return _unavailable_document_vector_state(
            sorted(expected),
            f"{kind}_schema_read_failed",
        )
    if fields is None or "document_id" not in fields or "source_id" not in fields:
        try:
            indexed = set(
                _existing_records_by_source_ids(
                    db,
                    table_name,
                    sorted(expected),
                )
            ) if expected else set()
        except Exception:
            indexed = set()
        return {
            "status": "capability_unavailable",
            "reason": f"{kind}_schema_document_id_unavailable",
            "expected_source_ids": sorted(expected),
            "actual_source_ids": sorted(indexed),
            "missing_source_ids": sorted(expected - indexed),
            "orphan_source_ids": "not_available",
            "missing_count": len(expected - indexed),
            "orphan_count": "not_available",
        }

    where_clause = f"document_id = {document_id}"
    try:
        count = int(table.count_rows(where_clause))
        records = (
            table.search()
            .where(where_clause)
            .limit(max(count, 1))
            .to_list()
            if count
            else []
        )
    except Exception:
        return _unavailable_document_vector_state(
            sorted(expected),
            f"{kind}_document_query_failed",
        )
    try:
        actual = _parse_document_vector_rows(
            records,
            document_id=document_id,
        )
    except (TypeError, ValueError):
        return _unavailable_document_vector_state(
            sorted(expected),
            f"{kind}_row_parse_failed",
        )
    return _complete_document_vector_state(expected, actual)


def _scoped_table_schema_fields(table: Any) -> set[str] | None:
    schema = getattr(table, "schema", None)
    if callable(schema):
        schema = schema()
    if schema is None:
        return None
    return {str(field.name) for field in schema}


def _parse_document_vector_rows(
    records: Any,
    *,
    document_id: int,
) -> set[str]:
    if not isinstance(records, list):
        raise TypeError("document vector rows must be a list")
    actual: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("document vector row must be a mapping")
        raw_document_id = record.get("document_id")
        if isinstance(raw_document_id, bool):
            raise ValueError("document vector row has invalid document_id")
        try:
            row_document_id = int(raw_document_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "document vector row has invalid document_id"
            ) from exc
        if row_document_id <= 0 or str(raw_document_id).strip() != str(
            row_document_id
        ):
            raise ValueError("document vector row has invalid document_id")
        source_id = str(record.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("document vector row has invalid source_id")
        if row_document_id == document_id:
            actual.add(source_id)
    return actual


def _complete_document_vector_state(
    expected: set[str],
    actual: set[str],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": None,
        "expected_source_ids": sorted(expected),
        "actual_source_ids": sorted(actual),
        "missing_source_ids": sorted(expected - actual),
        "orphan_source_ids": sorted(actual - expected),
        "missing_count": len(expected - actual),
        "orphan_count": len(actual - expected),
    }


def _unavailable_document_vector_state(
    expected_source_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "expected_source_ids": list(expected_source_ids),
        "actual_source_ids": [],
        "missing_source_ids": list(expected_source_ids),
        "orphan_source_ids": "not_available",
        "missing_count": len(expected_source_ids),
        "orphan_count": "not_available",
    }


def cleanup_document_vectors(
    *,
    passage_source_ids: list[str],
    affected_object_keys: list[str],
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Delete exact passage vectors and reconcile only affected object keys."""

    actual_store_path = Path(store_path or LANCEDB_DIR)
    actual_manifest_path = Path(manifest_path or MANIFEST_PATH)
    db = open_vector_store(actual_store_path)
    passage_ids = (
        sorted(set(_validated_passage_source_ids(passage_source_ids)))
        if passage_source_ids
        else []
    )
    object_source_ids = sorted(
        {
            make_object_source_id(value)
            for value in affected_object_keys
            if str(value or "").strip()
        }
    )
    passage_before = _existing_records_by_source_ids(
        db, PASSAGE_TABLE, passage_ids
    ) if passage_ids else {}
    if passage_before and PASSAGE_TABLE in _table_names(db):
        _delete_vector_ids(db.open_table(PASSAGE_TABLE), list(passage_before))

    current_object_sources = {
        source["source_id"]: source
        for source in collect_object_sources()
        if source["source_id"] in object_source_ids
    }
    existing_objects = _existing_records_by_source_ids(
        db, OBJECT_TABLE, object_source_ids
    ) if object_source_ids else {}
    delete_object_ids = sorted(set(existing_objects) - set(current_object_sources))
    changed_sources = [
        source
        for source_id, source in current_object_sources.items()
        if source_id not in existing_objects
        or _record_stale(existing_objects[source_id], source)
    ]
    if delete_object_ids and OBJECT_TABLE in _table_names(db):
        _delete_vector_ids(db.open_table(OBJECT_TABLE), delete_object_ids)

    upserted_ids: list[str] = []
    if changed_sources:
        table_fields = _table_schema_fields(db, OBJECT_TABLE)
        missing_fields = OBJECT_EXPECTED_RECORD_FIELDS - table_fields
        if missing_fields:
            raise VectorStoreSchemaMismatch(
                "affected object cleanup forbids schema rebuild; missing fields: "
                + ", ".join(sorted(missing_fields))
            )
        model = local_embedding_service._load_model({})
        records = [
            build_object_record(source["object"], model=model)
            for source in changed_sources
        ]
        upserted_ids = [source["source_id"] for source in changed_sources]
        if OBJECT_TABLE in _table_names(db):
            table = db.open_table(OBJECT_TABLE)
            _delete_vector_ids(table, upserted_ids)
            table.add(records)
        else:
            db.create_table(OBJECT_TABLE, data=records, mode="create")

    passage_count = (
        _table_status(db, PASSAGE_TABLE)["count"]
        if PASSAGE_TABLE in _table_names(db)
        else 0
    )
    object_count = (
        _table_status(db, OBJECT_TABLE)["count"]
        if OBJECT_TABLE in _table_names(db)
        else 0
    )
    current_manifest = get_vector_manifest(actual_manifest_path) or {}
    _updated_manifest(
        manifest_path=actual_manifest_path,
        embedding_dim=int(current_manifest.get("embedding_dim") or 1024),
        passage_count=passage_count,
        object_count=object_count,
    )
    return {
        "status": "ok",
        "deleted_passage_vectors": len(passage_before),
        "deleted_object_vectors": len(delete_object_ids),
        "updated_shared_object_vectors": len(upserted_ids),
        "preserved_object_source_ids": sorted(current_object_sources),
        "passage_count": passage_count,
        "object_count": object_count,
        "full_rebuild_performed": False,
    }


def sync_object_embeddings(
    *,
    limit: int | None = None,
    dry_run: bool = False,
    delete_orphans: bool = False,
    rebuild_if_schema_mismatch: bool = True,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    sources = collect_object_sources(limit=limit)
    return _sync_records(
        kind="objects",
        table_name=OBJECT_TABLE,
        sources=sources,
        dry_run=dry_run,
        delete_orphans=delete_orphans,
        rebuild_if_schema_mismatch=rebuild_if_schema_mismatch,
        store_path=store_path,
        manifest_path=manifest_path,
        record_builder=lambda source, model: build_object_record(source["object"], model=model),
        schema_record_builder=lambda source: build_object_schema_record(source["object"]),
        expected_record_fields=OBJECT_EXPECTED_RECORD_FIELDS,
    )


def sync_vector_store(
    kind: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    delete_orphans: bool = False,
    rebuild_if_schema_mismatch: bool = True,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    if kind not in {"all", "passages", "objects"}:
        raise ValueError("kind must be all, passages, or objects")
    results = []
    if kind in {"all", "objects"}:
        results.append(sync_object_embeddings(limit=limit, dry_run=dry_run, delete_orphans=delete_orphans, rebuild_if_schema_mismatch=rebuild_if_schema_mismatch, store_path=store_path, manifest_path=manifest_path))
    if kind in {"all", "passages"}:
        results.append(sync_passage_embeddings(limit=limit, dry_run=dry_run, delete_orphans=delete_orphans, rebuild_if_schema_mismatch=rebuild_if_schema_mismatch, store_path=store_path, manifest_path=manifest_path))
    return results


def vector_table_fallback_reason(
    status: dict[str, Any],
    table_name: str,
) -> str | None:
    """Return a fallback reason scoped to the table being queried."""

    if table_name not in {PASSAGE_TABLE, OBJECT_TABLE}:
        raise ValueError(f"unsupported vector table: {table_name}")

    if not status.get("available"):
        reason = status.get("reason")
        if reason == "vector_manifest_missing":
            return "vector_store_unavailable"
        return str(reason or "vector_store_unavailable")

    manifest = status.get("manifest") or {}
    manifest_reason = _stale_reason(manifest)
    if manifest_reason:
        return manifest_reason

    table = (status.get("tables") or {}).get(table_name) or {}
    if not table.get("exists"):
        return "vector_table_missing"

    kind = "passages" if table_name == PASSAGE_TABLE else "objects"
    table_freshness = (
        ((status.get("freshness") or {}).get("tables") or {}).get(kind)
        or (status.get("sync") or {}).get(kind)
        or {}
    )

    source_count = int(table_freshness.get("source_count") or 0)
    indexed_count = int(table_freshness.get("indexed_count") or 0)
    drift_count = sum(
        int(table_freshness.get(field) or 0)
        for field in ("missing_count", "stale_count", "orphan_count")
    )

    if source_count != indexed_count or drift_count:
        return "vector_store_source_drift"

    return None


def search_passage_vectors(
    query: str,
    limit: int = 10,
    store_path: Path | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _search_table(
        query=query,
        table_name=PASSAGE_TABLE,
        limit=limit,
        store_path=store_path,
        status=status,
    )


def search_object_vectors(
    query: str,
    limit: int = 10,
    store_path: Path | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _search_table(
        query=query,
        table_name=OBJECT_TABLE,
        limit=limit,
        store_path=store_path,
        status=status,
    )


def _search_table(
    query: str,
    table_name: str,
    limit: int,
    store_path: Path | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if store_path is None:
        status = status or check_vector_store_status()
        reason = vector_table_fallback_reason(status, table_name)
        if reason:
            return {
                "status": reason,
                "results": [],
                "vector_store_status": {
                    "available": bool(status.get("available")),
                    "stale": bool(status.get("stale")),
                    "reason": status.get("reason"),
                    "freshness": status.get("freshness") or {},
                    "requested_table": table_name,
                    "requested_table_reason": reason,
                },
            }
    db = open_vector_store(store_path)
    if table_name not in _table_names(db):
        return {"status": "vector_table_missing", "results": []}
    model = local_embedding_service._load_model({})
    query_vector = local_embedding_service._encode_text(model, query)
    table = db.open_table(table_name)
    results = table.search(query_vector).limit(max(1, min(limit, 50))).to_list()
    return {"status": "ok", "results": [_json_safe(item) for item in results]}


def _passage_source_rows(
    limit: int | None = None,
    source_ids: list[str] | None = None,
) -> list[tuple[Document, KnowledgeChunk]]:
    requested = set(source_ids or [])
    scoped_ids = [_parse_passage_source_id(source_id) for source_id in requested]
    with SessionLocal() as session:
        statement = (
            select(Document, KnowledgeChunk)
            .options(undefer(Document.object_import_mode), undefer(KnowledgeChunk.chapter_id))
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .where(Document.read_status.in_(READ_LIBRARY_STATUSES))
        )
        if scoped_ids:
            statement = statement.where(KnowledgeChunk.id.in_([chunk_id for _document_id, chunk_id in scoped_ids]))
            statement = statement.where(Document.id.in_([document_id for document_id, _chunk_id in scoped_ids]))
        if limit is not None:
            statement = statement.limit(max(0, int(limit)))
        rows = session.execute(statement).all()
        chapter_ids = sorted({
            int(chunk.chapter_id)
            for _document, chunk in rows
            if chunk.chapter_id is not None
        })
        chapter_titles: dict[int, str] = {}
        if chapter_ids:
            chapter_rows = session.execute(
                select(BookChapter.id, BookChapter.title).where(BookChapter.id.in_(chapter_ids))
            ).all()
            chapter_titles = {int(chapter_id): str(title or "") for chapter_id, title in chapter_rows}
        for _document, chunk in rows:
            if chunk.chapter_id is not None:
                setattr(chunk, "_vector_chapter_title", chapter_titles.get(int(chunk.chapter_id), ""))
    filtered = [
        (document, chunk)
        for document, chunk in rows
        if _compact_text(chunk.chunk_text)
        and not is_metadata_chunk_text(chunk.chunk_text)
        and (not requested or make_passage_source_id(document.id, chunk.id) in requested)
    ]
    filtered.sort(key=lambda item: (int(item[0].id), int(item[1].chunk_index), int(item[1].id)))
    if limit is not None:
        return filtered[: max(0, int(limit))]
    return filtered


def _passage_source_rows_from_sqlite(
    db_path: str | Path,
    *,
    source_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[tuple[SimpleNamespace, SimpleNamespace]]:
    database = Path(db_path).resolve(strict=False)
    if not database.is_file():
        raise ValueError("source_db_path must identify an existing SQLite database")
    requested = set(source_ids or [])
    scoped_ids = sorted(
        _parse_passage_source_id(source_id) for source_id in requested
    )
    status_placeholders = ",".join("?" for _ in READ_LIBRARY_STATUSES)
    scoped_batches: list[list[tuple[int, int]] | None]
    if scoped_ids:
        scoped_batches = [
            scoped_ids[offset : offset + 400]
            for offset in range(0, len(scoped_ids), 400)
        ]
    else:
        scoped_batches = [None]
    rows: list[sqlite3.Row] = []
    with connect_readonly_sqlite(
        database,
        resolve_strict=True,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    ) as connection:
        for scoped_batch in scoped_batches:
            scope_clause = ""
            params: list[Any] = [*READ_LIBRARY_STATUSES]
            if scoped_batch:
                scope_clause = " AND (" + " OR ".join(
                    "(documents.id = ? AND chunks.id = ?)"
                    for _ in scoped_batch
                ) + ")"
                for document_id, chunk_id in scoped_batch:
                    params.extend((document_id, chunk_id))
            query = f"""
                SELECT
                    documents.id AS document_id,
                    documents.title AS document_title,
                    documents.document_type,
                    documents.object_import_mode,
                    documents.read_status,
                    chunks.id AS chunk_id,
                    chunks.document_id AS chunk_document_id,
                    chunks.chunk_index,
                    chunks.heading_path,
                    chunks.chunk_text,
                    chunks.content_hash,
                    chunks.pdf_page_start,
                    chunks.pdf_page_end,
                    chunks.chapter_id,
                    chunks.updated_at,
                    chapters.title AS chapter_title
                FROM documents
                JOIN knowledge_chunks AS chunks
                  ON chunks.document_id = documents.id
                LEFT JOIN book_chapters AS chapters
                  ON chapters.id = chunks.chapter_id
                WHERE documents.read_status IN ({status_placeholders})
                {scope_clause}
                ORDER BY documents.id, chunks.chunk_index, chunks.id
            """
            rows.extend(connection.execute(query, tuple(params)).fetchall())
    rows.sort(
        key=lambda row: (
            int(row["document_id"]),
            int(row["chunk_index"]),
            int(row["chunk_id"]),
        )
    )

    result: list[tuple[SimpleNamespace, SimpleNamespace]] = []
    for row in rows:
        data = dict(row)
        chunk_text = _compact_text(data.get("chunk_text"))
        if not chunk_text or is_metadata_chunk_text(data.get("chunk_text")):
            continue
        source_id = make_passage_source_id(
            int(data["document_id"]),
            int(data["chunk_id"]),
        )
        if requested and source_id not in requested:
            continue
        document = SimpleNamespace(
            id=int(data["document_id"]),
            title=str(data.get("document_title") or ""),
            document_type=str(data.get("document_type") or ""),
            object_import_mode=data.get("object_import_mode"),
            read_status=data.get("read_status"),
        )
        chunk = SimpleNamespace(
            id=int(data["chunk_id"]),
            document_id=int(data["chunk_document_id"]),
            chunk_index=int(data.get("chunk_index") or 0),
            heading_path=str(data.get("heading_path") or ""),
            chunk_text=data.get("chunk_text"),
            content_hash=data.get("content_hash"),
            pdf_page_start=data.get("pdf_page_start"),
            pdf_page_end=data.get("pdf_page_end"),
            chapter_id=data.get("chapter_id"),
            updated_at=data.get("updated_at"),
            _vector_chapter_title=str(data.get("chapter_title") or ""),
        )
        result.append((document, chunk))
    if limit is not None:
        return result[: max(0, int(limit))]
    return result


def _validated_passage_source_ids(source_ids: list[str]) -> list[str]:
    requested = []
    seen = set()
    for source_id in source_ids:
        _parse_passage_source_id(source_id)
        if source_id not in seen:
            requested.append(source_id)
            seen.add(source_id)
    if not requested:
        raise ValueError("affected passage sync requires at least one --source-id")
    return requested


def _parse_passage_source_id(source_id: str) -> tuple[int, int]:
    parts = str(source_id).split(":")
    if len(parts) != 3 or parts[0] != "chunk":
        raise ValueError(f"invalid passage source id: {source_id}")
    try:
        document_id, chunk_id = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"invalid passage source id: {source_id}") from exc
    if document_id < 1 or chunk_id < 1:
        raise ValueError(f"invalid passage source id: {source_id}")
    return document_id, chunk_id


def _write_table(table_name: str, records: list[dict[str, Any]], *, reset: bool, store_path: Path | None = None) -> None:
    db = open_vector_store(store_path)
    if reset and table_name in _table_names(db):
        db.drop_table(table_name)
    if not records:
        return
    mode = "overwrite" if reset or table_name not in _table_names(db) else "append"
    db.create_table(table_name, data=records, mode=mode)


def inspect_vector_store_schema(
    *,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    db = open_vector_store(store_path)
    table_names = _table_names(db)
    passage_sample = _schema_sample_record(
        collect_passage_sources(limit=1),
        lambda source: build_passage_schema_record(source["document"], source["chunk"]),
    )
    object_sample = _schema_sample_record(
        collect_object_sources(limit=1),
        lambda source: build_object_schema_record(source["object"]),
    )
    return {
        "store_path": str(Path(store_path or LANCEDB_DIR)),
        "manifest_path": str(Path(manifest_path or MANIFEST_PATH)),
        "table_names": table_names,
        "passages": _schema_inspection_for_table(
            db,
            PASSAGE_TABLE,
            set(passage_sample) if passage_sample else PASSAGE_EXPECTED_RECORD_FIELDS,
        ),
        "objects": _schema_inspection_for_table(
            db,
            OBJECT_TABLE,
            set(object_sample) if object_sample else OBJECT_EXPECTED_RECORD_FIELDS,
        ),
    }


def backup_vector_store(
    *,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
    backup_root: Path | None = None,
) -> Path:
    actual_store_path = Path(store_path or LANCEDB_DIR)
    actual_manifest_path = Path(manifest_path or MANIFEST_PATH)
    target = _unique_backup_path(planned_schema_backup_path(store_path=actual_store_path, backup_root=backup_root))
    target.mkdir(parents=True, exist_ok=False)
    try:
        if actual_store_path.exists():
            shutil.copytree(actual_store_path, target / actual_store_path.name)
        if actual_manifest_path.exists():
            shutil.copy2(actual_manifest_path, target / actual_manifest_path.name)
    except Exception:
        raise
    return target


def planned_schema_backup_path(*, store_path: Path | None = None, backup_root: Path | None = None) -> Path:
    actual_store_path = Path(store_path or LANCEDB_DIR)
    root = Path(backup_root) if backup_root is not None else actual_store_path.parent / "backups"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / f"lancedb_before_schema_upgrade_{timestamp}"


def _sync_records(
    *,
    kind: str,
    table_name: str,
    sources: list[dict[str, Any]],
    dry_run: bool,
    delete_orphans: bool,
    rebuild_if_schema_mismatch: bool = True,
    store_path: Path | None = None,
    manifest_path: Path | None = None,
    record_builder: Any = None,
    schema_record_builder: Any = None,
    expected_record_fields: set[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    db = open_vector_store(store_path)
    actual_store_path = Path(store_path or LANCEDB_DIR)
    existing = _existing_records(db, table_name)
    existing_by_id = {str(record.get("source_id") or record.get("vector_id")): record for record in existing}
    source_by_id = {source["source_id"]: source for source in sources}
    table_fields = _table_schema_fields(db, table_name)
    sample_record = _schema_sample_record(sources, schema_record_builder) if schema_record_builder else None
    expected_fields = set(sample_record) if sample_record else set(expected_record_fields or table_fields)
    schema_missing_fields = sorted(expected_fields - table_fields) if table_fields else []
    schema_extra_fields = sorted(table_fields - expected_fields) if table_fields else []
    legacy_schema_needs_upgrade = bool(existing) and not LIFECYCLE_FIELDS.issubset(set(existing[0]))
    schema_needs_upgrade = bool(schema_missing_fields) or legacy_schema_needs_upgrade

    insert_ids: list[str] = []
    update_ids: list[str] = []
    skipped_ids: list[str] = []
    for source_id, source in source_by_id.items():
        current = existing_by_id.get(source_id)
        if current is None:
            insert_ids.append(source_id)
        elif schema_needs_upgrade or _record_stale(current, source):
            update_ids.append(source_id)
        else:
            skipped_ids.append(source_id)

    orphan_ids = sorted(set(existing_by_id) - set(source_by_id))
    stats = {
        "kind": kind,
        "table_name": table_name,
        "store_path": str(actual_store_path),
        "dry_run": dry_run,
        "delete_orphans": delete_orphans,
        "scanned_count": len(sources),
        "inserted_count": 0 if dry_run else len(insert_ids),
        "updated_count": 0 if dry_run else len(update_ids),
        "skipped_count": len(skipped_ids),
        "orphan_count": len(orphan_ids),
        "deleted_orphan_count": 0 if dry_run or not delete_orphans else len(orphan_ids),
        "would_insert": len(insert_ids),
        "would_update": len(update_ids),
        "would_delete": len(orphan_ids) if delete_orphans else 0,
        "schema_needs_upgrade": schema_needs_upgrade,
        "schema_upgrade": schema_needs_upgrade,
        "schema_missing_fields": schema_missing_fields,
        "schema_extra_fields": schema_extra_fields,
        "legacy_schema_needs_upgrade": legacy_schema_needs_upgrade,
        "table_schema_fields": sorted(table_fields),
        "expected_record_fields": sorted(expected_fields),
        "planned_backup_path": str(planned_schema_backup_path(store_path=actual_store_path)),
        "backup_path": None,
        "rebuilt_table": False,
        "elapsed_ms": 0.0,
    }

    if dry_run:
        stats["elapsed_ms"] = round(_elapsed_ms(started), 2)
        return stats

    changed_ids = insert_ids + update_ids
    if schema_needs_upgrade:
        if not rebuild_if_schema_mismatch:
            raise VectorStoreSchemaMismatch(
                f"{table_name} schema is missing fields: {', '.join(schema_missing_fields)}"
            )
        backup_path = backup_vector_store(store_path=actual_store_path, manifest_path=manifest_path)
        stats["backup_path"] = str(backup_path)
        model = local_embedding_service._load_model({}) if sources else None
        records = [record for source in sources for record in [record_builder(source, model)] if record]
        if table_name in _table_names(db):
            db.drop_table(table_name)
        if records:
            db.create_table(table_name, data=records, mode="overwrite")
        stats["inserted_count"] = len(records)
        stats["updated_count"] = 0
        stats["rebuilt_table"] = True
    else:
        model = local_embedding_service._load_model({}) if changed_ids else None
        records = [record for source_id in changed_ids for record in [record_builder(source_by_id[source_id], model)] if record]
        add_missing_fields = _records_missing_table_fields(records, table_fields)
        if add_missing_fields:
            raise VectorStoreSchemaMismatch(
                f"{table_name} schema is missing fields before add: {', '.join(add_missing_fields)}"
            )
        if changed_ids and table_name in _table_names(db):
            _delete_vector_ids(db.open_table(table_name), changed_ids)
        if delete_orphans and orphan_ids and table_name in _table_names(db):
            _delete_vector_ids(db.open_table(table_name), orphan_ids)
        if records:
            if table_name not in _table_names(db):
                db.create_table(table_name, data=records, mode="overwrite")
            else:
                db.open_table(table_name).add(records)

    final_count = _table_status(db, table_name)["count"] if table_name in _table_names(db) else 0
    embedding_dim = _table_embedding_dim(records)
    if not embedding_dim:
        embedding_dim = 1024
    if kind == "passages":
        _updated_manifest(manifest_path=manifest_path, embedding_dim=embedding_dim, passage_count=final_count)
    elif kind == "objects":
        _updated_manifest(manifest_path=manifest_path, embedding_dim=embedding_dim, object_count=final_count)
    stats["elapsed_ms"] = round(_elapsed_ms(started), 2)
    return stats


def _record_stale(record: dict[str, Any], source: dict[str, Any]) -> bool:
    return (
        record.get("source_hash") != source.get("source_hash")
        or record.get("profile_version") != source.get("profile_version")
        or record.get("embedding_model") != source.get("embedding_model")
        or not _same_model_path(
            record.get("embedding_model_path"),
            _active_embedding_model_path(),
        )
    )


def _schema_sample_record(sources: list[dict[str, Any]], schema_record_builder: Any) -> dict[str, Any] | None:
    for source in sources[:1]:
        record = schema_record_builder(source)
        if record:
            return record
    return None


def _schema_inspection_for_table(db: Any, table_name: str, expected_fields: set[str]) -> dict[str, Any]:
    existing_fields = _table_schema_fields(db, table_name)
    return {
        "exists": table_name in _table_names(db),
        "existing_schema_fields": sorted(existing_fields),
        "expected_record_fields": sorted(expected_fields),
        "missing_fields": sorted(expected_fields - existing_fields) if existing_fields else [],
        "extra_fields": sorted(existing_fields - expected_fields) if existing_fields else [],
        "schema_upgrade": bool(existing_fields and (expected_fields - existing_fields)),
    }


def _table_schema_fields(db: Any, table_name: str) -> set[str]:
    if table_name not in _table_names(db):
        return set()
    table = db.open_table(table_name)
    schema = getattr(table, "schema", None)
    if callable(schema):
        schema = schema()
    if schema is None and hasattr(table, "to_arrow"):
        schema = table.to_arrow().schema
    if schema is None and hasattr(table, "to_pandas"):
        return set(table.to_pandas().columns)
    return {field.name for field in schema}


def _records_missing_table_fields(records: list[dict[str, Any]], table_fields: set[str]) -> list[str]:
    if not records or not table_fields:
        return []
    return sorted(set(records[0]) - table_fields)


def _unique_backup_path(base_path: Path) -> Path:
    candidate = base_path
    suffix = 1
    while candidate.exists():
        candidate = base_path.with_name(f"{base_path.name}_{suffix}")
        suffix += 1
    return candidate


def _existing_records(db: Any, table_name: str) -> list[dict[str, Any]]:
    if table_name not in _table_names(db):
        return []
    table = db.open_table(table_name)
    if hasattr(table, "to_list"):
        return table.to_list()
    if hasattr(table, "to_arrow"):
        return table.to_arrow().to_pylist()
    if hasattr(table, "to_pandas"):
        return table.to_pandas().to_dict(orient="records")
    raise VectorStoreUnavailable(f"cannot read LanceDB table records: {table_name}")


def _connect_existing_vector_store(path: Path) -> Any:
    if not path.exists():
        raise VectorStoreUnavailable(f"vector store path missing: {path}")
    return _import_lancedb().connect(str(path))


def _existing_records_by_source_ids(db: Any, table_name: str, source_ids: list[str]) -> dict[str, dict[str, Any]]:
    if table_name not in _table_names(db):
        return {}
    table = db.open_table(table_name)
    quoted = ", ".join(_sql_quote(value) for value in source_ids)
    where_clause = f"source_id IN ({quoted})"
    try:
        records = table.search().where(where_clause).limit(len(source_ids)).to_list()
    except Exception as exc:
        raise VectorStoreUnavailable(f"affected-only source_id lookup failed: {exc}") from exc
    return {str(record.get("source_id") or record.get("vector_id")): record for record in records}


def _delete_vector_ids(table: Any, vector_ids: list[str]) -> None:
    if not vector_ids:
        return
    quoted = ", ".join(_sql_quote(value) for value in vector_ids)
    table.delete(f"vector_id IN ({quoted})")


def _sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sync_status(table_name: str, sources: list[dict[str, Any]], db: Any) -> dict[str, int]:
    existing = _existing_records(db, table_name)
    existing_by_id = {str(record.get("source_id") or record.get("vector_id")): record for record in existing}
    source_by_id = {source["source_id"]: source for source in sources}
    stale_count = 0
    for source_id, source in source_by_id.items():
        current = existing_by_id.get(source_id)
        if current is not None and _record_stale(current, source):
            stale_count += 1
    return {
        "source_count": len(source_by_id),
        "indexed_count": len(existing_by_id),
        "stale_count": stale_count,
        "missing_count": len(set(source_by_id) - set(existing_by_id)),
        "orphan_count": len(set(existing_by_id) - set(source_by_id)),
    }


def _updated_manifest(
    *,
    manifest_path: Path | None,
    embedding_dim: int,
    passage_count: int | None = None,
    object_count: int | None = None,
    note_count: int | None = None,
) -> dict[str, Any]:
    current = get_vector_manifest(manifest_path) or {}
    manifest = {
        "backend": BACKEND,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_path": _active_embedding_model_path(),
        "embedding_dim": embedding_dim or current.get("embedding_dim"),
        "passage_profile_version": PASSAGE_PROFILE_VERSION,
        "object_profile_version": OBJECT_PROFILE_VERSION,
        "note_profile_version": NOTE_PROFILE_VERSION,
        "passage_count": current.get("passage_count", 0),
        "object_count": current.get("object_count", 0),
        "note_count": current.get("note_count", 0),
        "created_at": current.get("created_at") or _utc_now(),
    }
    if passage_count is not None:
        manifest["passage_count"] = passage_count
    if object_count is not None:
        manifest["object_count"] = object_count
    if note_count is not None:
        manifest["note_count"] = note_count
    return write_vector_manifest(manifest, manifest_path)


def evaluate_vector_store_freshness(
    sync: dict[str, Any] | None,
    *,
    available: bool,
    manifest_reason: str | None = None,
) -> dict[str, Any]:
    table_summaries: dict[str, dict[str, int]] = {}
    totals = {
        "source_count": 0,
        "indexed_count": 0,
        "missing_count": 0,
        "stale_count": 0,
        "orphan_count": 0,
    }
    for kind in ("passages", "objects"):
        raw = dict((sync or {}).get(kind) or {})
        summary = {
            key: int(_safe_int(raw.get(key)) or 0)
            for key in totals
        }
        table_summaries[kind] = summary
        for key, value in summary.items():
            totals[key] += value

    count_mismatch = any(
        table["source_count"] != table["indexed_count"]
        for table in table_summaries.values()
    )
    source_drift = count_mismatch or any(
        totals[key] > 0
        for key in ("missing_count", "stale_count", "orphan_count")
    )
    if not available:
        state = "unavailable"
        reason = None
    elif manifest_reason:
        state = "manifest_mismatch"
        reason = manifest_reason
    elif source_drift:
        state = "source_drift"
        reason = "vector_store_source_drift"
    else:
        state = "current"
        reason = None

    return {
        "state": state,
        "complete": state == "current",
        "reason": reason,
        "manifest_compatible": available and manifest_reason is None,
        "source_drift": source_drift,
        "count_mismatch": count_mismatch,
        "drift_count": (
            totals["missing_count"]
            + totals["stale_count"]
            + totals["orphan_count"]
        ),
        **totals,
        "tables": table_summaries,
    }


def _status_payload(
    *,
    available: bool,
    reason: str | None,
    stale: bool,
    manifest: dict[str, Any] | None,
    tables: dict[str, Any],
    sync: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sync_payload = sync or {}
    freshness_payload = freshness or evaluate_vector_store_freshness(
        sync_payload,
        available=available,
        manifest_reason=reason if available and reason == "vector_store_stale" else None,
    )
    if not available and reason and freshness_payload.get("reason") is None:
        freshness_payload = {**freshness_payload, "reason": reason}
    return {
        "backend": BACKEND,
        "available": available,
        "manifest": manifest,
        "tables": tables,
        "stale": stale,
        "reason": reason,
        "sync": sync_payload,
        "freshness": freshness_payload,
    }


def _table_status(db: Any, table_name: str) -> dict[str, Any]:
    if table_name not in _table_names(db):
        return {"exists": False, "count": 0}
    table = db.open_table(table_name)
    return {"exists": True, "count": int(table.count_rows())}


def _stale_reason(manifest: dict[str, Any]) -> str | None:
    expected = {
        "backend": BACKEND,
        "embedding_model": EMBEDDING_MODEL,
        "passage_profile_version": PASSAGE_PROFILE_VERSION,
        "object_profile_version": OBJECT_PROFILE_VERSION,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            return "vector_store_stale"
    if not manifest.get("embedding_dim"):
        return "vector_store_stale"
    return None


def _table_names(db: Any) -> list[str]:
    if hasattr(db, "list_tables"):
        names = db.list_tables()
        if hasattr(names, "tables"):
            return list(names.tables)
        return list(names() if callable(names) else names)
    names = db.table_names()
    return list(names() if callable(names) else names)


def _import_lancedb() -> Any:
    try:
        import lancedb
    except Exception as exc:  # pragma: no cover - depends on optional dependency
        raise VectorStoreUnavailable(f"lancedb_unavailable: {exc}") from exc
    return lancedb


def _embedding_dim(records: list[dict[str, Any]]) -> int:
    if records:
        return int(records[0].get("embedding_dim") or len(records[0].get("vector") or []))
    manifest = get_vector_manifest() or {}
    return int(manifest.get("embedding_dim") or 0)


def _table_embedding_dim(records: list[dict[str, Any]]) -> int:
    if records:
        return _embedding_dim(records)
    manifest = get_vector_manifest() or {}
    return int(manifest.get("embedding_dim") or 0)


def _expected_embedding_dim() -> int:
    return _table_embedding_dim([]) or 1024


def _hash_text(text: str) -> str:
    return hashlib.sha256(_compact_text(text).encode("utf-8")).hexdigest()


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _format_datetime(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _json_safe(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in item.items() if key != "vector"}


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, dict):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_value(child) for child in value]
    return value
