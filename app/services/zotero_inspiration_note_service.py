from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import DATA_DIR
from app.services.production_write_surface_guard import (
    require_proven_legacy_for_legacy_write_surface,
)
from app.services.unit_note_object_processing_service import (
    apply_note_processing_fields,
    note_processing_fields,
    note_processing_summary,
)


TABLE_NAME = "zotero_inspiration_notes"
REQUIRED_INDEX_NAMES = {
    "ix_zotero_inspiration_notes_client_note_id",
    "ix_zotero_inspiration_notes_annotation_key",
    "ix_zotero_inspiration_notes_attachment_key",
    "ix_zotero_inspiration_notes_selected_text_hash",
    "ix_zotero_inspiration_notes_document_id",
    "ix_zotero_inspiration_notes_chunk_id",
    "ix_zotero_inspiration_notes_review_status",
    "ix_zotero_inspiration_notes_mechanism_status",
}
ALLOWED_SOURCES = {"zotero_plugin", "zotero_annotation", "zotero_native_annotation", "manual"}
ALLOWED_SELECTION_TYPES = {"sentence", "paragraph", "section_title", "chapter_title", "manual"}
PAYLOAD_FIELDS = {
    "client_note_id",
    "source",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_annotation_key",
    "pdf_page",
    "page_label",
    "selected_text",
    "selected_text_hash",
    "note_text",
    "user_tags",
    "selection_type",
    "context_before",
    "context_after",
    "bbox",
    "created_at",
    "updated_at",
    "sync_status",
}
RAW_UPDATE_COLUMNS = (
    "source",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_annotation_key",
    "pdf_page",
    "page_label",
    "selected_text",
    "selected_text_hash",
    "note_text",
    "user_tags_json",
    "selection_type",
    "context_before",
    "context_after",
    "bbox_json",
    "created_at",
    "updated_at",
)


class InspirationPayloadError(ValueError):
    pass


class InspirationSchemaUnavailable(RuntimeError):
    pass


def _main_database_path(conn: sqlite3.Connection) -> Path | None:
    for _sequence, name, path in conn.execute("PRAGMA database_list").fetchall():
        if name == "main" and path:
            return Path(path).resolve(strict=False)
    return None


def _require_legacy_inspiration_write(conn: sqlite3.Connection) -> None:
    database = _main_database_path(conn)
    if database is None:
        return
    require_proven_legacy_for_legacy_write_surface(
        error_code="zotero_inspiration_note_write_versioned_frozen",
        message=(
            "Zotero inspiration-note 写入在 versioned production 中已冻结；"
            "本次请求未执行任何数据库写入。"
        ),
        db_path=database,
        data_dir=DATA_DIR,
    )


def ensure_zotero_inspiration_note_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the candidate-note table only on the explicit connection supplied by the caller."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS zotero_inspiration_notes (
            id INTEGER PRIMARY KEY,
            server_note_id TEXT NOT NULL UNIQUE,
            client_note_id TEXT NOT NULL,
            source TEXT NOT NULL,
            zotero_item_key TEXT,
            zotero_attachment_key TEXT,
            zotero_annotation_key TEXT,
            pdf_page INTEGER,
            page_label TEXT,
            selected_text TEXT NOT NULL,
            selected_text_hash TEXT NOT NULL,
            note_text TEXT NOT NULL,
            user_tags_json TEXT NOT NULL,
            selection_type TEXT NOT NULL,
            context_before TEXT,
            context_after TEXT,
            bbox_json TEXT,
            matched_document_id INTEGER,
            matched_chunk_id INTEGER,
            matched_object_ids_json TEXT NOT NULL DEFAULT '[]',
            sync_status TEXT NOT NULL,
            match_status TEXT NOT NULL DEFAULT 'unmatched',
            review_status TEXT NOT NULL DEFAULT 'imported',
            mechanism_status TEXT NOT NULL DEFAULT 'not_generated',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_client_note_id
            ON zotero_inspiration_notes (client_note_id);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_annotation_key
            ON zotero_inspiration_notes (zotero_annotation_key);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_attachment_key
            ON zotero_inspiration_notes (zotero_attachment_key);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_selected_text_hash
            ON zotero_inspiration_notes (selected_text_hash);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_document_id
            ON zotero_inspiration_notes (matched_document_id);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_chunk_id
            ON zotero_inspiration_notes (matched_chunk_id);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_review_status
            ON zotero_inspiration_notes (review_status);
        CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_mechanism_status
            ON zotero_inspiration_notes (mechanism_status);
        """
    )
    conn.commit()
    return {
        "table_name": TABLE_NAME,
        "schema_present": True,
        "db_write_performed": True,
        "production_write_enabled": False,
    }


def validate_inspiration_payload(payload: Mapping[str, Any]) -> list[str]:
    extra_fields = set(payload) - PAYLOAD_FIELDS
    if extra_fields:
        raise InspirationPayloadError(
            f"Unsupported inspiration fields: {', '.join(sorted(extra_fields))}"
        )
    if not str(payload.get("client_note_id") or ""):
        raise InspirationPayloadError("client_note_id is required.")
    if payload.get("source") not in ALLOWED_SOURCES:
        raise InspirationPayloadError("source is not an allowed inspiration source.")
    if payload.get("selection_type") not in ALLOWED_SELECTION_TYPES:
        raise InspirationPayloadError("selection_type is not allowed.")

    selected_text = payload.get("selected_text")
    note_text = payload.get("note_text")
    if not isinstance(selected_text, str) or not isinstance(note_text, str):
        raise InspirationPayloadError("selected_text and note_text must be strings.")
    if selected_text == "" and payload.get("selection_type") != "manual":
        raise InspirationPayloadError("Only manual notes may omit selected_text.")
    if note_text == "" and selected_text == "":
        raise InspirationPayloadError("A manual note without selected_text must contain note_text.")
    if not isinstance(payload.get("user_tags"), list) or not all(
        isinstance(tag, str) for tag in payload.get("user_tags", [])
    ):
        raise InspirationPayloadError("user_tags must be a list of original strings.")
    if not isinstance(payload.get("selected_text_hash"), str) or not payload["selected_text_hash"]:
        raise InspirationPayloadError("selected_text_hash is required.")
    if payload.get("pdf_page") is not None and int(payload["pdf_page"]) < 1:
        raise InspirationPayloadError("pdf_page must be a physical 1-based page.")

    warnings = []
    if note_text == "" and selected_text:
        warnings.append("empty_note_text_with_selected_text")
    return warnings


def normalized_selected_text_hash(selected_text: str) -> str:
    normalized = unicodedata.normalize("NFC", selected_text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_server_note_id(payload: Mapping[str, Any]) -> str:
    if payload.get("zotero_annotation_key"):
        identity = f"annotation:{payload['zotero_annotation_key']}"
    elif payload.get("client_note_id"):
        identity = f"client:{payload['client_note_id']}"
    elif payload.get("zotero_attachment_key") and payload.get("selected_text_hash"):
        identity = f"attachment_hash:{payload['zotero_attachment_key']}:{payload['selected_text_hash']}"
    else:
        identity = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
    return "zinsp_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def upsert_inspiration_note(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    _require_legacy_inspiration_write(conn)
    _require_schema(conn)
    warnings = validate_inspiration_payload(payload)
    received_at = datetime.now(timezone.utc).isoformat()
    record = _storage_record(payload, received_at)
    existing, matched_by = _find_duplicate(conn, payload)

    if existing is not None and _is_client_selection_conflict(existing, payload):
        return _result(
            existing,
            payload,
            status="CONFLICT",
            sync_status="conflict",
            dedup_action="conflict",
            db_write_performed=False,
            warnings=["client_note_id_selected_text_conflict", *warnings],
        )

    if existing is None:
        record["server_note_id"] = compute_server_note_id(payload)
        columns = tuple(record)
        placeholders = ", ".join(f":{column}" for column in columns)
        conn.execute(
            f"INSERT INTO {TABLE_NAME} ({', '.join(columns)}) VALUES ({placeholders})",
            record,
        )
        row = _fetch_by_server_id(conn, record["server_note_id"])
        action = "inserted"
        written = True
    elif _same_captured_values(existing, record):
        row = existing
        action = "unchanged"
        written = False
    else:
        assignments = ", ".join(f"{column} = :{column}" for column in RAW_UPDATE_COLUMNS)
        values = {column: record[column] for column in RAW_UPDATE_COLUMNS}
        values.update(
            {
                "id": existing["id"],
                "sync_status": "synced",
                "received_at": received_at,
            }
        )
        conn.execute(
            f"UPDATE {TABLE_NAME} SET {assignments}, "
            "sync_status = :sync_status, received_at = :received_at WHERE id = :id",
            values,
        )
        row = _fetch_by_id(conn, int(existing["id"]))
        action = "updated"
        written = True

    if commit:
        conn.commit()
    return _result(
        row,
        payload,
        status="OK",
        sync_status="synced",
        dedup_action=action,
        db_write_performed=written,
        warnings=warnings + ([f"deduplicated_by_{matched_by}"] if matched_by and action != "inserted" else []),
    )


def batch_upsert_inspiration_notes(
    conn: sqlite3.Connection,
    payloads: list[Mapping[str, Any]],
) -> dict[str, Any]:
    _require_legacy_inspiration_write(conn)
    results = [upsert_inspiration_note(conn, payload, commit=False) for payload in payloads]
    conn.commit()
    return {
        "status": "OK",
        "mode": "legacy_zotero_plugin_capture",
        "production_write_endpoint": True,
        "recommended_primary_flow": "zotero_native_annotation_import",
        "results": results,
        "count": len(results),
        "db_write_performed": any(item["db_write_performed"] for item in results),
        "mechanism_generated": False,
        "llm_called": False,
    }


def list_inspiration_notes_by_attachment(
    conn: sqlite3.Connection,
    attachment_key: str,
) -> dict[str, Any]:
    _require_schema(conn)
    rows = _fetchall(
        conn,
        f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE zotero_attachment_key = ?
        ORDER BY CASE WHEN pdf_page IS NULL THEN 1 ELSE 0 END,
                 pdf_page,
                 created_at,
                 id
        """,
        (attachment_key,),
    )
    items = [_public_note(row) for row in rows]
    return {
        "status": "OK",
        "zotero_attachment_key": attachment_key,
        "items": items,
        "count": len(items),
        "summary": note_processing_summary(items),
        **note_processing_summary(items),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def list_inspiration_notes_by_document(
    conn: sqlite3.Connection,
    document_id: int,
    tag: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _require_schema(conn)
    normalized_limit = max(1, min(int(limit), 500))
    rows = _fetchall(
        conn,
        f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE matched_document_id = ?
        ORDER BY CASE WHEN pdf_page IS NULL THEN 1 ELSE 0 END,
                 pdf_page,
                 page_label,
                 created_at,
                 id
        LIMIT ?
        """,
        (document_id, normalized_limit),
    )
    normalized_tag = str(tag or "").strip()
    if normalized_tag:
        rows = [
            row for row in rows
            if normalized_tag in _json_list_value(row, "user_tags_json")
        ]
    items = [_public_note(row) for row in rows]
    return {
        "status": "OK",
        "document_id": document_id,
        "count": len(items),
        "items": items,
        "summary": note_processing_summary(items),
        **note_processing_summary(items),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def get_sync_status(
    conn: sqlite3.Connection | None,
    *,
    production_persistence_enabled: bool = False,
    integrity_check_ok: bool | None = None,
) -> dict[str, Any]:
    if conn is None:
        return {
            "status": "OK",
            "available": False,
            "persistence_configured": False,
            "schema_present": False,
            "schema_ready": False,
            "missing_tables": [TABLE_NAME],
            "missing_indexes": sorted(REQUIRED_INDEX_NAMES),
            "integrity_check_ok": integrity_check_ok,
            "production_persistence_enabled": production_persistence_enabled,
            "write_available": False,
            "table_name": TABLE_NAME,
            "mode": "k_c_apply_required",
            "db_write_performed": False,
            "mechanism_generated": False,
            "llm_called": False,
        }
    schema_present = _schema_present(conn)
    missing_indexes = _missing_indexes(conn) if schema_present else sorted(REQUIRED_INDEX_NAMES)
    missing_tables = [] if schema_present else [TABLE_NAME]
    schema_ready = schema_present and not missing_indexes
    available = schema_ready and integrity_check_ok is not False
    return {
        "status": "OK",
        "available": available,
        "persistence_configured": True,
        "schema_present": schema_present,
        "schema_ready": schema_ready,
        "missing_tables": missing_tables,
        "missing_indexes": missing_indexes,
        "integrity_check_ok": integrity_check_ok,
        "production_persistence_enabled": production_persistence_enabled,
        "write_available": bool(production_persistence_enabled and available),
        "table_name": TABLE_NAME,
        "mode": "explicit_connection_only",
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def _storage_record(payload: Mapping[str, Any], received_at: str) -> dict[str, Any]:
    return {
        "client_note_id": payload["client_note_id"],
        "source": payload["source"],
        "zotero_item_key": payload.get("zotero_item_key"),
        "zotero_attachment_key": payload.get("zotero_attachment_key"),
        "zotero_annotation_key": payload.get("zotero_annotation_key"),
        "pdf_page": payload.get("pdf_page"),
        "page_label": payload.get("page_label"),
        "selected_text": payload["selected_text"],
        "selected_text_hash": payload["selected_text_hash"],
        "note_text": payload["note_text"],
        "user_tags_json": json.dumps(payload["user_tags"], ensure_ascii=False),
        "selection_type": payload["selection_type"],
        "context_before": payload.get("context_before"),
        "context_after": payload.get("context_after"),
        "bbox_json": (
            json.dumps(payload["bbox"], ensure_ascii=False)
            if payload.get("bbox") is not None
            else None
        ),
        "matched_document_id": None,
        "matched_chunk_id": None,
        "matched_object_ids_json": "[]",
        "sync_status": "synced",
        "match_status": "unmatched",
        "review_status": "imported",
        "mechanism_status": "not_generated",
        "created_at": payload["created_at"],
        "updated_at": payload["updated_at"],
        "received_at": received_at,
    }


def _find_duplicate(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    searches: list[tuple[str, tuple[Any, ...], str]] = []
    if payload.get("zotero_annotation_key"):
        searches.append(("zotero_annotation_key = ?", (payload["zotero_annotation_key"],), "annotation_key"))
    if payload.get("client_note_id"):
        searches.append(("client_note_id = ?", (payload["client_note_id"],), "client_note_id"))
    if payload.get("zotero_attachment_key") and payload.get("selected_text_hash") and payload.get("pdf_page") is not None:
        searches.append(
            (
                "zotero_attachment_key = ? AND selected_text_hash = ? AND pdf_page = ?",
                (
                    payload["zotero_attachment_key"],
                    payload["selected_text_hash"],
                    payload["pdf_page"],
                ),
                "attachment_hash_page",
            )
        )
    if payload.get("zotero_attachment_key") and payload.get("selected_text_hash"):
        searches.append(
            (
                "zotero_attachment_key = ? AND selected_text_hash = ?",
                (payload["zotero_attachment_key"], payload["selected_text_hash"]),
                "attachment_hash",
            )
        )
    for condition, values, match_name in searches:
        row = _fetchone(conn, f"SELECT * FROM {TABLE_NAME} WHERE {condition} ORDER BY id LIMIT 1", values)
        if row is not None:
            return row, match_name
    return None, None


def _same_captured_values(existing: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    return all(existing[column] == record[column] for column in RAW_UPDATE_COLUMNS)


def _is_client_selection_conflict(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return (
        existing["client_note_id"] == payload["client_note_id"]
        and existing["selected_text"] != payload["selected_text"]
    )


def _result(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    status: str,
    sync_status: str,
    dedup_action: str,
    db_write_performed: bool,
    warnings: list[str],
) -> dict[str, Any]:
    computed_hash = normalized_selected_text_hash(payload["selected_text"])
    return {
        "status": status,
        "mode": "legacy_zotero_plugin_capture" if payload.get("source") == "zotero_plugin" else "inspiration_note_upsert",
        "production_write_endpoint": True,
        "recommended_primary_flow": "zotero_native_annotation_import",
        "server_note_id": _row_value(row, "server_note_id"),
        "client_note_id": payload["client_note_id"],
        "sync_status": sync_status,
        "matched_document_id": _row_value(row, "matched_document_id"),
        "matched_chunk_id": _row_value(row, "matched_chunk_id"),
        "matched_object_ids": _json_list_value(row, "matched_object_ids_json"),
        "dedup_action": dedup_action,
        "db_write_performed": db_write_performed,
        "mechanism_generated": False,
        "llm_called": False,
        "match_status": _row_value(row, "match_status"),
        "warnings": warnings,
        "selected_text_hash_diagnostic": {
            "received": payload["selected_text_hash"],
            "computed": computed_hash,
            "matches": payload["selected_text_hash"] == computed_hash,
        },
    }


def _require_schema(conn: sqlite3.Connection) -> None:
    if not _schema_present(conn):
        raise InspirationSchemaUnavailable(
            "zotero_inspiration_notes is not initialized; K-C does not apply production schema automatically."
        )


def _schema_present(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE_NAME,),
    ).fetchone()
    return row is not None


def _missing_indexes(conn: sqlite3.Connection) -> list[str]:
    placeholders = ", ".join(["?"] * len(REQUIRED_INDEX_NAMES))
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'index' AND name IN ({placeholders})",
        tuple(REQUIRED_INDEX_NAMES),
    ).fetchall()
    present = {str(row[0]) for row in rows}
    return sorted(REQUIRED_INDEX_NAMES - present)


def _fetch_by_server_id(conn: sqlite3.Connection, server_note_id: str) -> dict[str, Any]:
    row = _fetchone(conn, f"SELECT * FROM {TABLE_NAME} WHERE server_note_id = ?", (server_note_id,))
    assert row is not None
    return row


def _fetch_by_id(conn: sqlite3.Connection, note_id: int) -> dict[str, Any]:
    row = _fetchone(conn, f"SELECT * FROM {TABLE_NAME} WHERE id = ?", (note_id,))
    assert row is not None
    return row


def _fetchone(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    cursor = conn.execute(query, parameters)
    result = cursor.fetchone()
    if result is None:
        return None
    return dict(zip((column[0] for column in cursor.description), result, strict=True))


def _fetchall(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _public_note(row: Mapping[str, Any]) -> dict[str, Any]:
    matched_chunk_ids = _json_list_value(row, "matched_chunk_ids_json")
    matched_object_ids = _json_list_value(row, "matched_object_ids_json")
    processing_fields = note_processing_fields(row)
    alignment_warnings = _ordered_unique(
        [*_json_list_value(row, "alignment_warnings_json"), *processing_fields["note_processing_warnings"]]
    )
    return apply_note_processing_fields({
        "id": _row_value(row, "id"),
        "server_note_id": _row_value(row, "server_note_id"),
        "client_note_id": _row_value(row, "client_note_id"),
        "source": _row_value(row, "source"),
        "zotero_item_key": _row_value(row, "zotero_item_key"),
        "zotero_attachment_key": _row_value(row, "zotero_attachment_key"),
        "zotero_annotation_key": _row_value(row, "zotero_annotation_key"),
        "pdf_page": _row_value(row, "pdf_page"),
        "page_label": _row_value(row, "page_label"),
        "selected_text": _row_value(row, "selected_text", ""),
        "selected_text_hash": _row_value(row, "selected_text_hash"),
        "note_text": _row_value(row, "note_text", ""),
        "user_tags": _json_list_value(row, "user_tags_json"),
        "selection_type": _row_value(row, "selection_type"),
        "context_before": _row_value(row, "context_before"),
        "context_after": _row_value(row, "context_after"),
        "bbox": _json_value(row, "bbox_json"),
        "matched_document_id": _row_value(row, "matched_document_id"),
        "matched_chunk_id": _row_value(row, "matched_chunk_id"),
        "matched_chunk_ids_json": matched_chunk_ids,
        "matched_chunk_ids": matched_chunk_ids,
        "matched_object_ids_json": matched_object_ids,
        "matched_object_ids": matched_object_ids,
        "evidence_alignment_status": _row_value(row, "evidence_alignment_status"),
        "alignment_confidence": _row_value(row, "alignment_confidence"),
        "alignment_method": _row_value(row, "alignment_method"),
        "alignment_warnings_json": alignment_warnings,
        "alignment_warnings": alignment_warnings,
        "sync_status": _row_value(row, "sync_status"),
        "match_status": _row_value(row, "match_status"),
        "review_status": _row_value(row, "review_status"),
        "mechanism_status": _row_value(row, "mechanism_status"),
        "created_at": _row_value(row, "created_at"),
        "updated_at": _row_value(row, "updated_at"),
        "received_at": _row_value(row, "received_at"),
    })


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)  # type: ignore[union-attr]
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _json_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = _row_value(row, key)
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _json_list_value(row: Mapping[str, Any], key: str) -> list[Any]:
    value = _json_value(row, key, default=[])
    return value if isinstance(value, list) else []


def _ordered_unique(items: list[Any]) -> list[Any]:
    unique: list[Any] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique
