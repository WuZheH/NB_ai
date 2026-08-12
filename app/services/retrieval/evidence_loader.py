from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.services.retrieval.evidence_errors import EvidenceWorkflowError
from app.services.retrieval.fragment_id import canonical_source_locator, fragment_uuid
from app.services.retrieval.fts_schema import ORDINARY_TABLE
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_NOTES_ROOT,
    DEFAULT_QUERY_ALIASES_PATH,
    DEFAULT_ZOTERO_SNAPSHOT_PATH,
    connect_readonly_index,
    get_index_status,
)
from app.core.paths import DEFAULT_DB_PATH


@dataclass(frozen=True)
class EvidenceRecord:
    fragment_id: str
    display_id: str
    source_type: str
    origin_kind: str
    source_record_id: str
    canonical_source_locator: str
    document_id: int | None
    zotero_library_id: int | None
    zotero_item_key: str | None
    zotero_attachment_key: str | None
    zotero_annotation_key: str | None
    title: str | None
    authors: list[str]
    year: int | None
    collections: list[str]
    tags: list[str]
    page_number: int | None
    page_label: str | None
    section: str | None
    heading_path: list[str]
    text: str
    note_comment: str | None
    context_before: str | None
    context_after: str | None
    original_file_path: str | None
    zotero_uri: str | None
    content_hash: str
    source_order: int | None
    duplicate_count: int
    duplicate_fragment_ids: list[str]
    duplicate_source_types: list[str]
    provenance: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    match_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceLoadResult:
    records: list[EvidenceRecord]
    index_status: dict[str, Any]


def require_ready_index(
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    if index_path is None and manifest_path is None:
        from app.services.retrieval_generation_service import (
            current_retrieval_generation,
        )

        generation = current_retrieval_generation()
        index = generation.fts_index_path
        manifest = generation.fts_manifest_path
    else:
        index = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
        manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    status = _cached_index_status(
        _status_signature(index, manifest),
        str(index.resolve(strict=False)),
        str(manifest.resolve(strict=False)),
    )
    if status.get("status") != "ready":
        raise EvidenceWorkflowError(
            "retrieval_index_unavailable",
            f"Retrieval index is not ready: {status.get('status')}",
            status_code=503,
            details={"index_status": status},
        )
    return index, manifest, status


def load_evidence_records(
    fragment_ids: Iterable[str],
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> EvidenceLoadResult:
    ordered_ids = [str(value).strip() for value in fragment_ids if str(value).strip()]
    index, _, status = require_ready_index(
        index_path=index_path,
        manifest_path=manifest_path,
    )
    if not ordered_ids:
        return EvidenceLoadResult(records=[], index_status=status)

    with closing(connect_readonly_index(index)) as connection:
        rows = _load_rows(connection, ordered_ids)
        missing = [fragment_id for fragment_id in ordered_ids if fragment_id not in rows]
        if missing:
            raise EvidenceWorkflowError(
                "evidence_fragment_not_found",
                "One or more fragment IDs are absent from the ready retrieval index.",
                status_code=404,
                details={"missing_fragment_ids": missing},
            )
        duplicate_members = _load_duplicate_members(connection, rows.values())
        records = [
            _row_to_record(rows[fragment_id], duplicate_members)
            for fragment_id in ordered_ids
        ]
    return EvidenceLoadResult(records=records, index_status=status)


def load_document_fragment_ids(
    document_id: int,
    source_types: list[str],
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    fetch_limit: int = 1001,
) -> tuple[list[str], int, dict[str, Any]]:
    index, _, status = require_ready_index(
        index_path=index_path,
        manifest_path=manifest_path,
    )
    clauses = ["document_id = ?"]
    params: list[Any] = [document_id]
    if source_types:
        clauses.append(f"source_type IN ({','.join('?' for _ in source_types)})")
        params.extend(source_types)
    where = " AND ".join(clauses)
    with closing(connect_readonly_index(index)) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {ORDINARY_TABLE} WHERE {where}",
                params,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT fragment_id
            FROM {ORDINARY_TABLE}
            WHERE {where}
            ORDER BY COALESCE(source_order, 2147483647), row_id
            LIMIT ?
            """,
            [*params, fetch_limit],
        ).fetchall()
    return [str(row[0]) for row in rows], total, status


def _load_rows(
    connection: sqlite3.Connection,
    fragment_ids: list[str],
) -> dict[str, sqlite3.Row]:
    result: dict[str, sqlite3.Row] = {}
    for start in range(0, len(fragment_ids), 400):
        batch = list(dict.fromkeys(fragment_ids[start : start + 400]))
        placeholders = ",".join("?" for _ in batch)
        for row in connection.execute(
            f"SELECT * FROM {ORDINARY_TABLE} WHERE fragment_id IN ({placeholders})",
            batch,
        ).fetchall():
            result[str(row["fragment_id"])] = row
    return result


def _load_duplicate_members(
    connection: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
) -> dict[str, list[dict[str, Any]]]:
    group_ids = sorted(
        {
            str(row["duplicate_group_id"])
            for row in rows
            if bool(row["duplicate_candidate"]) and row["duplicate_group_id"]
        }
    )
    if not group_ids:
        return {}
    placeholders = ",".join("?" for _ in group_ids)
    result: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(
        f"""
        SELECT duplicate_group_id, fragment_id, source_type, provenance_json
        FROM {ORDINARY_TABLE}
        WHERE duplicate_candidate = 1
          AND duplicate_group_id IN ({placeholders})
        ORDER BY duplicate_group_id, fragment_id
        """,
        group_ids,
    ).fetchall():
        result.setdefault(str(row["duplicate_group_id"]), []).append(
            {
                "fragment_id": str(row["fragment_id"]),
                "source_type": str(row["source_type"]),
                "provenance": _json_list(row["provenance_json"]),
            }
        )
    return result


def _row_to_record(
    row: sqlite3.Row,
    duplicate_members: dict[str, list[dict[str, Any]]],
) -> EvidenceRecord:
    provenance = _json_list(row["provenance_json"])
    warnings = [str(value) for value in _json_list(row["warnings_json"])]
    raw_metadata = _json_dict(row["raw_metadata_json"])
    heading_path = [str(value) for value in _json_list(row["heading_path_json"])]
    source_record_id, locator = _restore_source_identity(
        row,
        provenance=provenance,
        raw_metadata=raw_metadata,
        heading_path=heading_path,
    )
    if fragment_uuid(locator) != str(row["fragment_id"]):
        raise EvidenceWorkflowError(
            "retrieval_fragment_identity_mismatch",
            "A retrieval index row does not match its reconstructed UUIDv5 identity.",
            status_code=500,
            details={"fragment_id": str(row["fragment_id"])},
        )

    members = (
        duplicate_members.get(str(row["duplicate_group_id"]), [])
        if bool(row["duplicate_candidate"]) and row["duplicate_group_id"]
        else []
    )
    if members:
        duplicate_ids = [str(item["fragment_id"]) for item in members]
        duplicate_types = sorted({str(item["source_type"]) for item in members})
    else:
        duplicate_ids = [str(row["fragment_id"])]
        duplicate_types = [str(row["source_type"])]

    return EvidenceRecord(
        fragment_id=str(row["fragment_id"]),
        display_id=str(row["display_id"]),
        source_type=str(row["source_type"]),
        origin_kind=str(row["origin_kind"]),
        source_record_id=source_record_id,
        canonical_source_locator=locator,
        document_id=_int_or_none(row["document_id"]),
        zotero_library_id=_int_or_none(row["zotero_library_id"]),
        zotero_item_key=_string_or_none(row["zotero_item_key"]),
        zotero_attachment_key=_string_or_none(row["zotero_attachment_key"]),
        zotero_annotation_key=_string_or_none(row["zotero_annotation_key"]),
        title=_string_or_none(row["title"]),
        authors=_split_lines(row["authors_text"]),
        year=_int_or_none(row["year"]),
        collections=_split_lines(row["collections_text"]),
        tags=_split_lines(row["tags_text"]),
        page_number=_int_or_none(row["page_number"]),
        page_label=_string_or_none(row["page_label"]),
        section=_string_or_none(row["section"]),
        heading_path=heading_path,
        text=str(row["text"]),
        note_comment=_string_or_none(row["note_comment"]),
        context_before=_string_or_none(row["context_before"]),
        context_after=_string_or_none(row["context_after"]),
        original_file_path=_string_or_none(row["original_file_path"]),
        zotero_uri=_string_or_none(row["zotero_uri"]),
        content_hash=str(row["content_hash"]),
        source_order=_int_or_none(row["source_order"]),
        duplicate_count=max(1, len(duplicate_ids)),
        duplicate_fragment_ids=duplicate_ids,
        duplicate_source_types=duplicate_types,
        provenance=provenance,
        warnings=warnings,
        raw_metadata=raw_metadata,
        match_fields={
            "title": str(row["title"] or ""),
            "section": str(row["section"] or ""),
            "tags_text": str(row["tags_text"] or ""),
            "text": str(row["text"] or ""),
            "note_comment": str(row["note_comment"] or ""),
            "context_text": str(row["context_text"] or ""),
            "source_type": str(row["source_type"]),
        },
    )


def _restore_source_identity(
    row: sqlite3.Row,
    *,
    provenance: list[dict[str, Any]],
    raw_metadata: dict[str, Any],
    heading_path: list[str],
) -> tuple[str, str]:
    source_type = str(row["source_type"])
    library_id = _int_or_none(row["zotero_library_id"])
    if source_type == "pdf_chunk":
        source_id = str(_provenance_value(provenance, "knowledge_chunks", "row_id"))
        return source_id, canonical_source_locator(
            "pdf_chunk",
            document_id=int(row["document_id"]),
            chunk_id=source_id,
        )
    if source_type in {"zotero_highlight", "zotero_annotation_comment"}:
        source_id = str(row["zotero_annotation_key"] or "")
        return source_id, canonical_source_locator(
            source_type,
            library_id=library_id,
            annotation_key=source_id,
        )
    if source_type == "zotero_child_note":
        source_id = str(_provenance_value(provenance, "itemNotes", "item_key"))
        return source_id, canonical_source_locator(
            "zotero_child_note",
            library_id=library_id,
            item_key=source_id,
        )
    if source_type == "zotero_inspiration_note":
        entry = _provenance_entry(provenance, "zotero_inspiration_notes")
        server_note_id = _string_or_none(entry.get("server_note_id"))
        row_id = entry.get("row_id")
        source_id = server_note_id or f"row-{row_id}"
        return source_id, canonical_source_locator(
            "zotero_inspiration_note",
            server_note_id=server_note_id,
            row_id=row_id,
        )
    if source_type == "personal_note":
        row_id = _provenance_value(provenance, "personal_notes", "row_id")
        return str(row_id), canonical_source_locator("personal_note", row_id=row_id)
    if source_type == "markdown_note":
        relative_path = str(raw_metadata.get("relative_path") or "")
        block_ordinal = raw_metadata.get("block_ordinal")
        source_id = f"{relative_path}#{block_ordinal}"
        return source_id, canonical_source_locator(
            "markdown_note",
            relative_path=relative_path,
            heading_path=" > ".join(heading_path) or "(root)",
            block_ordinal=block_ordinal,
        )
    raise EvidenceWorkflowError(
        "unsupported_retrieval_source_type",
        f"Unsupported retrieval source type: {source_type}",
        status_code=500,
    )


def _provenance_entry(
    provenance: list[dict[str, Any]],
    table: str,
) -> dict[str, Any]:
    for entry in provenance:
        if entry.get("table") == table:
            return entry
    raise EvidenceWorkflowError(
        "retrieval_provenance_incomplete",
        f"Required provenance entry is missing: {table}",
        status_code=500,
    )


def _provenance_value(
    provenance: list[dict[str, Any]],
    table: str,
    key: str,
) -> Any:
    entry = _provenance_entry(provenance, table)
    value = entry.get(key)
    if value is None or str(value).strip() == "":
        raise EvidenceWorkflowError(
            "retrieval_provenance_incomplete",
            f"Required provenance value is missing: {table}.{key}",
            status_code=500,
        )
    return value


def _json_list(value: object) -> list[Any]:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _json_dict(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _split_lines(value: object) -> list[str]:
    return [line for line in str(value or "").splitlines() if line]


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _status_signature(
    index_path: Path,
    manifest_path: Path,
) -> tuple[tuple[str, int, int], ...]:
    paths = [
        index_path,
        manifest_path,
        DEFAULT_QUERY_ALIASES_PATH,
        DEFAULT_DB_PATH,
        DEFAULT_ZOTERO_SNAPSHOT_PATH,
        *(
            sorted(DEFAULT_NOTES_ROOT.rglob("*.md"))
            if DEFAULT_NOTES_ROOT.is_dir()
            else []
        ),
    ]
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved.is_file():
            stat = resolved.stat()
            signature.append((str(resolved), stat.st_size, stat.st_mtime_ns))
        else:
            signature.append((str(resolved), -1, -1))
    return tuple(signature)


@lru_cache(maxsize=8)
def _cached_index_status(
    signature: tuple[tuple[str, int, int], ...],
    index_path: str,
    manifest_path: str,
) -> dict[str, Any]:
    del signature
    return get_index_status(index_path=index_path, manifest_path=manifest_path)

