from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.services.retrieval.fragment_normalizer import (
    normalize_string_list,
    normalize_text,
    year_or_none,
)


@dataclass(frozen=True)
class ResolvedSourceMetadata:
    document_id: int | None = None
    candidate_document_ids: tuple[int, ...] = ()
    mapping_status: str = "unmapped"
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    collections: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    original_file_path: str | None = None
    zotero_library_id: int | None = None
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_uri: str | None = None
    warnings: tuple[str, ...] = ()
    provenance: tuple[dict[str, Any], ...] = ()


class RetrievalMetadataResolver:
    """Resolve source metadata without mutating either SQLite database."""

    def __init__(
        self,
        research_conn: sqlite3.Connection,
        zotero_conn: sqlite3.Connection | None = None,
    ) -> None:
        self.research_conn = research_conn
        self.zotero_conn = zotero_conn
        self._document_cache: dict[int, ResolvedSourceMetadata] = {}
        self._attachment_cache: dict[tuple[str, str | None], ResolvedSourceMetadata] = {}
        self._item_cache: dict[str, ResolvedSourceMetadata] = {}
        self._documents: list[dict[str, Any]] | None = None
        self._pdf_sources: list[dict[str, Any]] | None = None
        self._document_sources: list[dict[str, Any]] | None = None

    def for_document(self, document_id: int) -> ResolvedSourceMetadata:
        cached = self._document_cache.get(document_id)
        if cached is not None:
            return cached
        document = next((row for row in self._all_documents() if int(row["id"]) == int(document_id)), None)
        if document is None:
            result = ResolvedSourceMetadata(
                document_id=document_id,
                candidate_document_ids=(document_id,),
                mapping_status="document_not_found",
                warnings=("document_not_found",),
            )
            self._document_cache[document_id] = result
            return result

        sources = [
            row for row in self._all_document_sources()
            if int(row.get("document_id") or 0) == int(document_id)
        ]
        pdf_source, inferred_method = self._pdf_source_for_document(document, sources)
        item_key = _clean(
            (sources[0].get("zotero_item_key") if sources else None)
            or document.get("zotero_key")
            or (pdf_source or {}).get("zotero_item_key")
        )
        attachment_key = _clean(
            (sources[0].get("zotero_attachment_key") if sources else None)
            or (pdf_source or {}).get("zotero_attachment_key")
        )
        snapshot = self._snapshot_item_metadata(item_key)
        authors = normalize_string_list((pdf_source or {}).get("creators_json")) or list(snapshot.get("authors") or [])
        title = _clean((pdf_source or {}).get("title") or document.get("title") or snapshot.get("title"))
        year = year_or_none((pdf_source or {}).get("year") or snapshot.get("date"))
        path = _clean((pdf_source or {}).get("resolved_pdf_path") or document.get("pdf_path") or document.get("source_path"))
        uri = _clean(
            (sources[0].get("zotero_open_pdf_uri") if sources else None)
            or (pdf_source or {}).get("zotero_open_pdf_uri")
            or (f"zotero://open-pdf/library/items/{attachment_key}" if attachment_key else None)
        )

        warnings: list[str] = []
        mapping_status = "document_only"
        candidates = (document_id,)
        if attachment_key:
            attachment_resolution = self.resolve_attachment(attachment_key, item_key=item_key)
            candidates = attachment_resolution.candidate_document_ids or (document_id,)
            if len(candidates) > 1:
                mapping_status = "ambiguous_attachment_mapping"
                warnings.append("ambiguous_attachment_mapping")
            elif sources:
                mapping_status = "resolved_document_source"
            elif inferred_method:
                mapping_status = inferred_method
        elif not uri:
            warnings.append("zotero_uri_unavailable")

        result = ResolvedSourceMetadata(
            document_id=document_id,
            candidate_document_ids=tuple(sorted(set(candidates))),
            mapping_status=mapping_status,
            title=title,
            authors=tuple(authors),
            year=year,
            collections=tuple(snapshot.get("collections") or []),
            tags=tuple(snapshot.get("tags") or []),
            original_file_path=path,
            zotero_library_id=_int_or_none(snapshot.get("library_id")),
            zotero_item_key=item_key,
            zotero_attachment_key=attachment_key,
            zotero_uri=uri,
            warnings=tuple(dict.fromkeys(warnings)),
            provenance=tuple(
                [
                    {"store": "production_db", "table": "documents", "row_id": document_id},
                    *(
                        [{"store": "production_db", "table": "document_sources", "row_id": sources[0].get("id")}]
                        if sources else []
                    ),
                    *(
                        [{"store": "production_db", "table": "zotero_pdf_sources", "row_id": pdf_source.get("id")}]
                        if pdf_source else []
                    ),
                ]
            ),
        )
        self._document_cache[document_id] = result
        return result

    def resolve_attachment(
        self,
        attachment_key: str,
        *,
        item_key: str | None = None,
    ) -> ResolvedSourceMetadata:
        clean_attachment = _clean(attachment_key)
        clean_item = _clean(item_key)
        if not clean_attachment:
            return ResolvedSourceMetadata(mapping_status="attachment_key_missing", warnings=("attachment_key_missing",))
        cache_key = (clean_attachment, clean_item)
        cached = self._attachment_cache.get(cache_key)
        if cached is not None:
            return cached

        pdf_source = next(
            (
                row for row in self._all_pdf_sources()
                if _clean(row.get("zotero_attachment_key")) == clean_attachment
            ),
            None,
        )
        if clean_item is None:
            clean_item = _clean((pdf_source or {}).get("zotero_item_key"))
        snapshot = self._snapshot_item_metadata(clean_item)
        candidates, method = self._document_candidates(
            attachment_key=clean_attachment,
            item_key=clean_item,
            title=_clean((pdf_source or {}).get("title") or snapshot.get("title")),
            path=_clean((pdf_source or {}).get("resolved_pdf_path")),
        )
        document_id = candidates[0] if len(candidates) == 1 else None
        document = next((row for row in self._all_documents() if int(row["id"]) == int(document_id or -1)), None)

        warnings: list[str] = []
        if len(candidates) > 1:
            mapping_status = "ambiguous_attachment_mapping"
            warnings.append("ambiguous_attachment_mapping")
        elif document_id is not None:
            mapping_status = method
        else:
            mapping_status = "attachment_unmapped"
            warnings.append("document_mapping_unavailable")

        authors = normalize_string_list((pdf_source or {}).get("creators_json")) or list(snapshot.get("authors") or [])
        title = _clean((pdf_source or {}).get("title") or snapshot.get("title") or (document or {}).get("title"))
        uri = _clean(
            (pdf_source or {}).get("zotero_open_pdf_uri")
            or f"zotero://open-pdf/library/items/{clean_attachment}"
        )
        result = ResolvedSourceMetadata(
            document_id=document_id,
            candidate_document_ids=tuple(candidates),
            mapping_status=mapping_status,
            title=title,
            authors=tuple(authors),
            year=year_or_none((pdf_source or {}).get("year") or snapshot.get("date")),
            collections=tuple(snapshot.get("collections") or []),
            tags=tuple(snapshot.get("tags") or []),
            original_file_path=_clean(
                (pdf_source or {}).get("resolved_pdf_path")
                or (document or {}).get("pdf_path")
                or (document or {}).get("source_path")
            ),
            zotero_library_id=_int_or_none(snapshot.get("library_id")),
            zotero_item_key=clean_item,
            zotero_attachment_key=clean_attachment,
            zotero_uri=uri,
            warnings=tuple(warnings),
            provenance=tuple(
                [
                    *(
                        [{"store": "production_db", "table": "zotero_pdf_sources", "row_id": pdf_source.get("id")}]
                        if pdf_source else []
                    ),
                    *(
                        [{"store": "production_db", "table": "documents", "row_id": document_id}]
                        if document_id else []
                    ),
                    {"store": "zotero_snapshot", "table": "itemAttachments", "key": clean_attachment},
                ]
            ),
        )
        self._attachment_cache[cache_key] = result
        return result

    def resolve_item(self, item_key: str) -> ResolvedSourceMetadata:
        clean_item = _clean(item_key)
        if not clean_item:
            return ResolvedSourceMetadata(mapping_status="item_key_missing", warnings=("item_key_missing",))
        cached = self._item_cache.get(clean_item)
        if cached is not None:
            return cached

        attachment_keys = self._snapshot_pdf_attachments(clean_item)
        if len(attachment_keys) == 1:
            result = self.resolve_attachment(attachment_keys[0], item_key=clean_item)
        elif len(attachment_keys) > 1:
            snapshot = self._snapshot_item_metadata(clean_item)
            result = ResolvedSourceMetadata(
                mapping_status="multiple_pdf_attachments",
                title=_clean(snapshot.get("title")),
                authors=tuple(snapshot.get("authors") or []),
                year=year_or_none(snapshot.get("date")),
                collections=tuple(snapshot.get("collections") or []),
                tags=tuple(snapshot.get("tags") or []),
                zotero_library_id=_int_or_none(snapshot.get("library_id")),
                zotero_item_key=clean_item,
                warnings=("multiple_pdf_attachments_found",),
                provenance=(
                    {"store": "zotero_snapshot", "table": "items", "key": clean_item},
                ),
            )
        else:
            candidates, method = self._document_candidates(
                attachment_key=None,
                item_key=clean_item,
                title=_clean(self._snapshot_item_metadata(clean_item).get("title")),
                path=None,
            )
            document_id = candidates[0] if len(candidates) == 1 else None
            base = self.for_document(document_id) if document_id is not None else ResolvedSourceMetadata()
            result = replace(
                base,
                candidate_document_ids=tuple(candidates),
                mapping_status=method if len(candidates) == 1 else (
                    "ambiguous_item_mapping" if len(candidates) > 1 else "item_unmapped"
                ),
                zotero_item_key=clean_item,
                warnings=tuple(
                    dict.fromkeys(
                        [
                            *base.warnings,
                            *(["ambiguous_item_mapping"] if len(candidates) > 1 else []),
                            *(["pdf_attachment_not_found"] if not candidates else []),
                        ]
                    )
                ),
            )
        self._item_cache[clean_item] = result
        return result

    def _document_candidates(
        self,
        *,
        attachment_key: str | None,
        item_key: str | None,
        title: str | None,
        path: str | None,
    ) -> tuple[tuple[int, ...], str]:
        if attachment_key:
            ids = _ids(
                row.get("document_id")
                for row in self._all_document_sources()
                if _clean(row.get("zotero_attachment_key")) == attachment_key
            )
            if ids:
                return ids, "resolved_document_source"

        if item_key:
            ids = _ids(
                row.get("id")
                for row in self._all_documents()
                if _clean(row.get("zotero_key")) == item_key
            )
            if ids:
                return ids, "resolved_document_item_key"

        normalized_path = _normalize_path(path)
        if normalized_path:
            ids = _ids(
                row.get("id")
                for row in self._all_documents()
                if _normalize_path(row.get("pdf_path") or row.get("source_path")) == normalized_path
            )
            if ids:
                return ids, "inferred_exact_path"

        normalized_title = _normalize_title(title)
        if normalized_title:
            ids = _ids(
                row.get("id")
                for row in self._all_documents()
                if _normalize_title(row.get("title")) == normalized_title
            )
            if ids:
                return ids, "inferred_exact_title"
        return (), "unmapped"

    def _pdf_source_for_document(
        self,
        document: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        attachment = _clean(sources[0].get("zotero_attachment_key")) if sources else None
        if attachment:
            row = next(
                (item for item in self._all_pdf_sources() if _clean(item.get("zotero_attachment_key")) == attachment),
                None,
            )
            if row:
                return row, "resolved_document_source"

        item_key = _clean(document.get("zotero_key"))
        if item_key:
            matches = [
                row for row in self._all_pdf_sources()
                if _clean(row.get("zotero_item_key")) == item_key
            ]
            if len(matches) == 1:
                return matches[0], "resolved_document_item_key"

        path = _normalize_path(document.get("pdf_path") or document.get("source_path"))
        matches = [
            row for row in self._all_pdf_sources()
            if path and _normalize_path(row.get("resolved_pdf_path")) == path
        ]
        if len(matches) == 1:
            return matches[0], "inferred_exact_path"

        title = _normalize_title(document.get("title"))
        matches = [
            row for row in self._all_pdf_sources()
            if title and _normalize_title(row.get("title")) == title
        ]
        if len(matches) == 1:
            return matches[0], "inferred_exact_title"
        return None, None

    def _snapshot_pdf_attachments(self, item_key: str) -> list[str]:
        if self.zotero_conn is None or not _table_exists(self.zotero_conn, "itemAttachments"):
            return []
        rows = self.zotero_conn.execute(
            """
            SELECT attachment.key
            FROM items AS parent
            JOIN itemAttachments AS ia ON ia.parentItemID = parent.itemID
            JOIN items AS attachment ON attachment.itemID = ia.itemID
            LEFT JOIN deletedItems AS deleted ON deleted.itemID = attachment.itemID
            WHERE parent.key = ?
              AND deleted.itemID IS NULL
              AND LOWER(COALESCE(ia.contentType, '')) = 'application/pdf'
            ORDER BY attachment.key
            """,
            (item_key,),
        ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _snapshot_item_metadata(self, item_key: str | None) -> dict[str, Any]:
        if not item_key or self.zotero_conn is None:
            return {}
        conn = self.zotero_conn
        item = conn.execute(
            "SELECT itemID, libraryID, key FROM items WHERE key = ? LIMIT 1",
            (item_key,),
        ).fetchone()
        if item is None:
            return {}
        item_id = int(item["itemID"])
        fields = {
            str(row["fieldName"]): row["value"]
            for row in conn.execute(
                """
                SELECT fields.fieldName, itemDataValues.value
                FROM itemData
                JOIN fields ON fields.fieldID = itemData.fieldID
                JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
                WHERE itemData.itemID = ?
                """,
                (item_id,),
            ).fetchall()
        }
        authors = [
            " ".join(part for part in (row["firstName"], row["lastName"]) if part).strip()
            for row in conn.execute(
                """
                SELECT creators.firstName, creators.lastName
                FROM itemCreators
                JOIN creators ON creators.creatorID = itemCreators.creatorID
                WHERE itemCreators.itemID = ?
                ORDER BY itemCreators.orderIndex
                """,
                (item_id,),
            ).fetchall()
        ]
        tags = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT tags.name FROM itemTags
                JOIN tags ON tags.tagID = itemTags.tagID
                WHERE itemTags.itemID = ? ORDER BY tags.name
                """,
                (item_id,),
            ).fetchall()
            if row[0]
        ]
        collections = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT collections.collectionName FROM collectionItems
                JOIN collections ON collections.collectionID = collectionItems.collectionID
                WHERE collectionItems.itemID = ? ORDER BY collections.collectionName
                """,
                (item_id,),
            ).fetchall()
            if row[0]
        ]
        return {
            "library_id": int(item["libraryID"]),
            "title": fields.get("title"),
            "date": fields.get("date"),
            "authors": [author for author in authors if author],
            "tags": tags,
            "collections": collections,
        }

    def _all_documents(self) -> list[dict[str, Any]]:
        if self._documents is None:
            self._documents = _rows(self.research_conn, "documents")
        return self._documents

    def _all_pdf_sources(self) -> list[dict[str, Any]]:
        if self._pdf_sources is None:
            self._pdf_sources = _rows(self.research_conn, "zotero_pdf_sources")
        return self._pdf_sources

    def _all_document_sources(self) -> list[dict[str, Any]]:
        if self._document_sources is None:
            self._document_sources = _rows(self.research_conn, "document_sources")
        return self._document_sources


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone() is not None


def _clean(value: Any) -> str | None:
    text = normalize_text(value, preserve_paragraphs=False)
    return text or None


def _ids(values: Any) -> tuple[int, ...]:
    return tuple(sorted({int(value) for value in values if value is not None}))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_path(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    return str(Path(text)).replace("\\", "/").casefold()


def _normalize_title(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())
