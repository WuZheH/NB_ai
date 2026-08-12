from __future__ import annotations

import sqlite3
from typing import Final


INDEX_SCHEMA_VERSION: Final = "retrieval_fts_v1"
UNICODE_FTS_TABLE: Final = "retrieval_fts_unicode"
TRIGRAM_FTS_TABLE: Final = "retrieval_fts_trigram"
ORDINARY_TABLE: Final = "retrieval_fragments"

TOKENIZER_CONFIG: Final = {
    UNICODE_FTS_TABLE: "unicode61 remove_diacritics 2",
    TRIGRAM_FTS_TABLE: "trigram",
}

REQUIRED_TABLES: Final = {
    ORDINARY_TABLE,
    UNICODE_FTS_TABLE,
    TRIGRAM_FTS_TABLE,
    "index_metadata",
}

SCHEMA_SQL: Final = f"""
CREATE TABLE index_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE {ORDINARY_TABLE} (
    row_id INTEGER PRIMARY KEY,
    fragment_id TEXT NOT NULL UNIQUE,
    display_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    origin_kind TEXT NOT NULL,
    document_id INTEGER,
    zotero_library_id INTEGER,
    zotero_item_key TEXT,
    zotero_attachment_key TEXT,
    zotero_annotation_key TEXT,
    parent_fragment_id TEXT,
    duplicate_group_id TEXT,
    duplicate_candidate INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    authors_text TEXT NOT NULL,
    year INTEGER,
    collections_text TEXT NOT NULL,
    tags_text TEXT NOT NULL,
    page_number INTEGER,
    page_label TEXT,
    section TEXT,
    heading_path_json TEXT NOT NULL,
    text TEXT NOT NULL,
    note_comment TEXT,
    context_before TEXT,
    context_after TEXT,
    context_text TEXT NOT NULL,
    original_file_path TEXT,
    zotero_uri TEXT,
    content_hash TEXT NOT NULL,
    source_order INTEGER,
    has_note_comment INTEGER NOT NULL,
    has_zotero_uri INTEGER NOT NULL,
    normalized_search_text TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    raw_metadata_json TEXT NOT NULL,
    adapter_version TEXT NOT NULL
);

CREATE UNIQUE INDEX ix_retrieval_fragments_fragment_id
    ON {ORDINARY_TABLE}(fragment_id);
CREATE INDEX ix_retrieval_fragments_source_type
    ON {ORDINARY_TABLE}(source_type);
CREATE INDEX ix_retrieval_fragments_origin_kind
    ON {ORDINARY_TABLE}(origin_kind);
CREATE INDEX ix_retrieval_fragments_document_id
    ON {ORDINARY_TABLE}(document_id);
CREATE INDEX ix_retrieval_fragments_zotero_item_key
    ON {ORDINARY_TABLE}(zotero_item_key);
CREATE INDEX ix_retrieval_fragments_zotero_attachment_key
    ON {ORDINARY_TABLE}(zotero_attachment_key);
CREATE INDEX ix_retrieval_fragments_zotero_annotation_key
    ON {ORDINARY_TABLE}(zotero_annotation_key);
CREATE INDEX ix_retrieval_fragments_year
    ON {ORDINARY_TABLE}(year);
CREATE INDEX ix_retrieval_fragments_duplicate_group
    ON {ORDINARY_TABLE}(duplicate_group_id);

CREATE VIRTUAL TABLE {UNICODE_FTS_TABLE} USING fts5(
    title,
    section,
    tags,
    text,
    note_comment,
    context,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE {TRIGRAM_FTS_TABLE} USING fts5(
    title,
    section,
    tags,
    text,
    note_comment,
    context,
    tokenize = 'trigram'
);
"""


def initialize_index_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
        ("index_schema_version", INDEX_SCHEMA_VERSION),
    )


def validate_index_database(
    connection: sqlite3.Connection,
    *,
    expected_fragment_count: int | None = None,
) -> dict[str, object]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    integrity_status = str(integrity[0]) if integrity else "missing"
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    missing_tables = sorted(REQUIRED_TABLES.difference(tables))
    ordinary_count = _table_count(connection, ORDINARY_TABLE) if not missing_tables else 0
    unicode_count = _table_count(connection, UNICODE_FTS_TABLE) if not missing_tables else 0
    trigram_count = _table_count(connection, TRIGRAM_FTS_TABLE) if not missing_tables else 0
    duplicate_ids = (
        int(
            connection.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT fragment_id
                    FROM {ORDINARY_TABLE}
                    GROUP BY fragment_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        if not missing_tables
        else 0
    )
    count_match = (
        expected_fragment_count is None
        or (
            ordinary_count == expected_fragment_count
            and unicode_count == expected_fragment_count
            and trigram_count == expected_fragment_count
        )
    )
    valid = (
        integrity_status == "ok"
        and not missing_tables
        and count_match
        and duplicate_ids == 0
    )
    return {
        "valid": valid,
        "integrity_check": integrity_status,
        "missing_tables": missing_tables,
        "ordinary_count": ordinary_count,
        "unicode_fts_count": unicode_count,
        "trigram_fts_count": trigram_count,
        "fragment_count_match": count_match,
        "duplicate_fragment_ids": duplicate_ids,
    }


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
