from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from app.services import zotero_source_cache_service


_SPACE_RE = re.compile(r"\s+")
_SEARCH_RE = re.compile(r"[^\w]+", re.UNICODE)


def list_parent_items(
    *,
    query: str | None = None,
    document_type: str | None = None,
    limit: int = 20,
    db_path: str | Path | None = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Return one candidate per top-level Zotero bibliographic item.

    Zotero annotations belong to attachments, and attachments belong to the
    bibliographic parent. Notes can belong directly to either level. This
    function deliberately resolves that graph before filtering or searching.
    """

    config = zotero_source_cache_service._load_config()
    snapshot = zotero_source_cache_service._project_path(
        config["zotero_db_snapshot"]
    ).resolve(strict=False)
    if not snapshot.is_file():
        raise RuntimeError("zotero_snapshot_missing")

    query_text = _normalize_search(query)
    requested_type = str(document_type or "").strip().casefold()
    requested_limit = max(1, min(int(limit), 50))
    imported = _load_imported_documents(db_path)

    with sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        parent_rows = connection.execute(
            """
            SELECT p.itemID AS item_id,
                   p.key AS parent_key,
                   COALESCE(t.typeName, 'unknown') AS item_type,
                   COALESCE(title_value.value, '') AS title,
                   COALESCE(date_value.value, '') AS publication_date,
                   COALESCE(p.dateModified, '') AS date_modified
              FROM items AS p
              JOIN itemTypes AS t ON t.itemTypeID = p.itemTypeID
         LEFT JOIN fields AS title_field ON title_field.fieldName = 'title'
         LEFT JOIN itemData AS title_data
                ON title_data.itemID = p.itemID
               AND title_data.fieldID = title_field.fieldID
         LEFT JOIN itemDataValues AS title_value
                ON title_value.valueID = title_data.valueID
         LEFT JOIN fields AS date_field ON date_field.fieldName = 'date'
         LEFT JOIN itemData AS date_data
                ON date_data.itemID = p.itemID
               AND date_data.fieldID = date_field.fieldID
         LEFT JOIN itemDataValues AS date_value
                ON date_value.valueID = date_data.valueID
             WHERE NOT EXISTS (
                       SELECT 1 FROM itemAttachments a WHERE a.itemID = p.itemID
                   )
               AND NOT EXISTS (
                       SELECT 1 FROM itemAnnotations a WHERE a.itemID = p.itemID
                   )
               AND NOT EXISTS (
                       SELECT 1 FROM itemNotes n WHERE n.itemID = p.itemID
                   )
          ORDER BY lower(COALESCE(title_value.value, '')), p.key
            """
        ).fetchall()

        candidates: list[dict[str, Any]] = []
        for parent in parent_rows:
            item_type = str(parent["item_type"] or "unknown")
            if requested_type and item_type.casefold() != requested_type:
                continue

            parent_id = int(parent["item_id"])
            attachments = _attachment_rows(connection, parent_id, config)
            attachment_ids = [int(item["_item_id"]) for item in attachments]
            authors = _author_rows(connection, parent_id)
            metadata_values = _metadata_values(connection, parent_id)
            searchable = [
                str(parent["title"] or ""),
                item_type,
                *authors,
                *metadata_values,
            ]
            if query_text and not _matches_query(query_text, searchable):
                continue

            annotation_count = _count_children(
                connection, "itemAnnotations", attachment_ids
            )
            child_note_count = _count_notes(
                connection, parent_id, attachment_ids
            )
            attachment_choices = [
                {key: value for key, value in item.items() if key != "_item_id"}
                for item in attachments
            ]
            pdf_choices = [
                item
                for item in attachment_choices
                if _is_pdf_attachment(item)
            ]
            recent_activity_at = _recent_activity_at(
                connection, parent_id, attachment_ids, str(parent["date_modified"] or "")
            )
            imported_document_id = imported.get(str(parent["parent_key"]))

            candidates.append(
                {
                    "kind": "zotero",
                    "source": "zotero_library",
                    "document_id": imported_document_id,
                    "parent_key": str(parent["parent_key"]),
                    "zotero_item_key": str(parent["parent_key"]),
                    "title": str(parent["title"] or ""),
                    "item_type": item_type,
                    "authors": authors,
                    "date": str(parent["publication_date"] or ""),
                    "attachment_keys": [
                        str(item["zotero_attachment_key"]) for item in attachment_choices
                    ],
                    "primary_pdf_attachment_key": (
                        str(pdf_choices[0]["zotero_attachment_key"])
                        if pdf_choices
                        else None
                    ),
                    "has_pdf": bool(pdf_choices),
                    "attachment_count": len(attachment_choices),
                    "attachment_choices": attachment_choices,
                    "annotation_count": annotation_count,
                    "child_note_count": child_note_count,
                    "date_modified": str(parent["date_modified"] or ""),
                    "recent_activity_at": recent_activity_at,
                    "already_imported": imported_document_id is not None,
                    "imported_document_id": imported_document_id,
                    "duplicate_status": (
                        "already_imported"
                        if imported_document_id is not None
                        else "not_evaluated"
                    ),
                    "status": "available",
                }
            )
            if len(candidates) >= requested_limit:
                break

    return {
        "status": "ok",
        "scope": "zotero",
        "count": len(candidates),
        "items": candidates,
        "truncated": len(candidates) >= requested_limit,
    }


def _attachment_rows(
    connection: sqlite3.Connection,
    parent_id: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT i.itemID AS item_id,
               i.key AS attachment_key,
               a.path,
               a.contentType
          FROM itemAttachments AS a
          JOIN items AS i ON i.itemID = a.itemID
         WHERE a.parentItemID = ?
      ORDER BY CASE
                 WHEN lower(COALESCE(a.contentType, '')) = 'application/pdf' THEN 0
                 WHEN lower(COALESCE(a.path, '')) LIKE '%.pdf' THEN 0
                 ELSE 1
               END,
               i.key
        """,
        (parent_id,),
    ).fetchall()
    choices: list[dict[str, Any]] = []
    for row in rows:
        raw = str(row["path"] or "")
        resolved, supported = zotero_source_cache_service._resolve_attachment_path(
            str(row["attachment_key"]),
            raw,
            Path(config["zotero_data_dir"]),
            Path(config["zotero_storage_root"]),
        )
        choices.append(
            {
                "_item_id": int(row["item_id"]),
                "zotero_attachment_key": str(row["attachment_key"]),
                "file_name": (
                    Path(raw.removeprefix("storage:")).name if raw else None
                ),
                "path_exists": bool(
                    supported and resolved and Path(resolved).is_file()
                ),
                "content_type": row["contentType"],
            }
        )
    return choices


def _author_rows(connection: sqlite3.Connection, parent_id: int) -> list[str]:
    if not _table_exists(connection, "itemCreators"):
        return []
    rows = connection.execute(
        """
        SELECT c.firstName, c.lastName, c.fieldMode
          FROM itemCreators AS ic
          JOIN creators AS c ON c.creatorID = ic.creatorID
         WHERE ic.itemID = ?
      ORDER BY ic.orderIndex
        """,
        (parent_id,),
    ).fetchall()
    authors = []
    for row in rows:
        if int(row["fieldMode"] or 0) == 1:
            name = str(row["lastName"] or row["firstName"] or "")
        else:
            name = " ".join(
                part for part in (str(row["firstName"] or ""), str(row["lastName"] or "")) if part
            )
        if name:
            authors.append(name)
    return authors


def _metadata_values(connection: sqlite3.Connection, parent_id: int) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT v.value
              FROM itemData d
              JOIN itemDataValues v ON v.valueID = d.valueID
             WHERE d.itemID = ?
            """,
            (parent_id,),
        ).fetchall()
        if row[0]
    ]


def _count_children(
    connection: sqlite3.Connection, table: str, parent_ids: list[int]
) -> int:
    if not parent_ids:
        return 0
    placeholders = ",".join("?" for _ in parent_ids)
    row = connection.execute(
        f"SELECT COUNT(DISTINCT itemID) FROM {table} WHERE parentItemID IN ({placeholders})",
        parent_ids,
    ).fetchone()
    return int(row[0] or 0)


def _count_notes(
    connection: sqlite3.Connection, parent_id: int, attachment_ids: list[int]
) -> int:
    ids = [parent_id, *attachment_ids]
    placeholders = ",".join("?" for _ in ids)
    row = connection.execute(
        f"SELECT COUNT(DISTINCT itemID) FROM itemNotes WHERE parentItemID IN ({placeholders})",
        ids,
    ).fetchone()
    return int(row[0] or 0)


def _recent_activity_at(
    connection: sqlite3.Connection,
    parent_id: int,
    attachment_ids: list[int],
    parent_date_modified: str,
) -> str:
    ids = [parent_id, *attachment_ids]
    placeholders = ",".join("?" for _ in ids)
    values = [parent_date_modified]
    values.extend(
        str(row[0] or "")
        for row in connection.execute(
            f"""
            SELECT dateModified
              FROM items
             WHERE itemID IN ({placeholders})
                OR itemID IN (
                     SELECT itemID FROM itemAnnotations
                      WHERE parentItemID IN ({placeholders})
                )
                OR itemID IN (
                     SELECT itemID FROM itemNotes
                      WHERE parentItemID IN ({placeholders})
                )
            """,
            [*ids, *ids, *ids],
        ).fetchall()
    )
    return max((value for value in values if value), default="")


def _load_imported_documents(db_path: str | Path | None) -> dict[str, int]:
    if db_path is None:
        return {}
    path = Path(db_path)
    if not path.is_file():
        return {}
    result: dict[str, int] = {}
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            if _table_exists(connection, "documents"):
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(documents)")
                }
                if "zotero_key" in columns:
                    for document_id, key in connection.execute(
                        "SELECT id, zotero_key FROM documents WHERE zotero_key IS NOT NULL"
                    ):
                        if key:
                            result[str(key)] = int(document_id)
            if _table_exists(connection, "document_sources"):
                for document_id, key in connection.execute(
                    """
                    SELECT document_id, zotero_item_key
                      FROM document_sources
                     WHERE zotero_item_key IS NOT NULL
                    """
                ):
                    if key:
                        result[str(key)] = int(document_id)
    except sqlite3.Error:
        return {}
    return result


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _is_pdf_attachment(item: dict[str, Any]) -> bool:
    return (
        str(item.get("content_type") or "").casefold() == "application/pdf"
        or str(item.get("file_name") or "").casefold().endswith(".pdf")
    )


def _normalize_search(value: str | None) -> str:
    cleaned = _SPACE_RE.sub(" ", str(value or "").strip().casefold())
    return _SEARCH_RE.sub("", cleaned)


def _matches_query(query: str, values: list[str]) -> bool:
    return any(query in _normalize_search(value) for value in values)
