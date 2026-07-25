from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from app.services import (
    zotero_selected_book_preview_service,
)
from app.services.retrieval.fragment_normalizer import (
    normalize_text,
)


class DirectionBCommitError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


PERSONAL_NOTE_REQUIRED_COLUMNS = {
    "id",
    "document_id",
    "note_type",
    "scope_type",
    "scope_path",
    "source_path",
    "content_hash",
    "title",
    "content",
    "summary",
    "created_at",
    "updated_at",
    "source_system",
    "source_library_id",
    "source_item_key",
    "source_parent_item_key",
    "source_attachment_key",
    "source_annotation_key",
    "source_note_key",
    "source_record_kind",
    "source_identity",
    "selected_text",
    "source_comment",
    "pdf_page",
    "page_label",
    "position_json",
    "source_uri",
    "source_created_at",
    "source_updated_at",
    "source_version",
    "source_content_hash",
    "source_missing",
}

EVIDENCE_REQUIRED_COLUMNS = {
    "id",
    "note_id",
    "document_id",
    "chunk_id",
    "link_type",
    "evidence_role",
    "quote_text",
    "confidence",
    "created_by",
    "created_at",
    "pdf_page",
    "page_label",
    "source_locator_json",
    "alignment_status",
    "alignment_method",
    "alignment_warnings_json",
    "source_quote_hash",
}

KNOWLEDGE_CHUNK_REQUIRED_COLUMNS = {
    "id",
    "document_id",
    "chunk_text",
    "pdf_page_start",
    "pdf_page_end",
}

SOURCE_OWNED_COLUMNS = (
    "document_id",
    "note_type",
    "scope_type",
    "scope_path",
    "source_path",
    "content_hash",
    "title",
    "content",
    "source_system",
    "source_library_id",
    "source_item_key",
    "source_parent_item_key",
    "source_attachment_key",
    "source_annotation_key",
    "source_note_key",
    "source_record_kind",
    "source_identity",
    "selected_text",
    "source_comment",
    "pdf_page",
    "page_label",
    "position_json",
    "source_uri",
    "source_created_at",
    "source_updated_at",
    "source_version",
    "source_content_hash",
    "source_missing",
)


def commit_selected_book_preview_to_temp_db(
    *,
    preview_token: str,
    document_id: int,
    db_path: str | Path,
    now_ts: float | None = None,
) -> dict[str, Any]:
    path = Path(db_path).resolve(strict=False)

    # B3 safety boundary:
    # production is rejected before preview resolution
    # and before sqlite3.connect().
    if path == Path(DEFAULT_DB_PATH).resolve(
        strict=False
    ):
        raise DirectionBCommitError(
            code="production_db_blocked",
            message=(
                "B3 is temp-database only. "
                "Production persistence is blocked."
            ),
        )

    if not path.is_file():
        raise DirectionBCommitError(
            code="database_missing",
            message=(
                f"Target database does not exist: "
                f"{path}"
            ),
        )

    preview = (
        zotero_selected_book_preview_service
        .resolve_selected_book_preview_token(
            preview_token,
            now_ts=now_ts,
        )
    )

    if preview.get("status") != "ready":
        raise DirectionBCommitError(
            code="preview_not_ready",
            message=(
                "The selected-book preview "
                "is not ready to commit."
            ),
        )

    target_document_id = int(document_id)
    timestamp = _now_iso(now_ts)

    with _open_temp_rw(path) as connection:
        _assert_direction_b_schema(connection)
        _assert_document_exists(
            connection,
            target_document_id,
        )

        desired_records = (
            _build_desired_records(
                preview,
                document_id=target_document_id,
            )
        )

        inserted = 0
        updated = 0
        unchanged = 0
        evidence_created = 0
        missing_marked = 0
        touched_note_ids: list[int] = []

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            current_identities = {
                record["source_identity"]
                for record in desired_records
            }

            for record in desired_records:
                outcome = _upsert_personal_note(
                    connection,
                    record,
                    timestamp=timestamp,
                )

                note_id = int(
                    outcome["note_id"]
                )
                state = str(
                    outcome["state"]
                )

                if state == "inserted":
                    inserted += 1
                elif state == "updated":
                    updated += 1
                else:
                    unchanged += 1

                # Only new or genuinely changed
                # source records get evidence rebuilt.
                if state in {
                    "inserted",
                    "updated",
                }:
                    touched_note_ids.append(
                        note_id
                    )

                    connection.execute(
                        """
                        DELETE
                        FROM note_evidence_links
                        WHERE note_id = ?
                        """,
                        (note_id,),
                    )

                    evidence_rows = (
                        _build_evidence_rows(
                            connection,
                            note_id=note_id,
                            document_id=(
                                target_document_id
                            ),
                            record=record,
                            timestamp=timestamp,
                        )
                    )

                    for evidence in (
                        evidence_rows
                    ):
                        _insert_evidence_link(
                            connection,
                            evidence,
                        )

                    evidence_created += len(
                        evidence_rows
                    )

            missing_marked = (
                _mark_missing_sources(
                    connection,
                    source_library_id=int(
                        preview[
                            "zotero_item"
                        ][
                            "library_id"
                        ]
                        or 0
                    ),
                    document_id=(
                        target_document_id
                    ),
                    source_item_key=str(
                        preview[
                            "zotero_item"
                        ][
                            "zotero_item_key"
                        ]
                    ),
                    selected_attachment_key=str(
                        preview[
                            "selected_attachment"
                        ][
                            "zotero_attachment_key"
                        ]
                    ),
                    current_identities=(
                        current_identities
                    ),
                    timestamp=timestamp,
                )
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

    write_performed = any(
        (
            inserted,
            updated,
            evidence_created,
            missing_marked,
        )
    )

    return {
        "status": "committed",
        "persistence_scope": "tempdb",
        "database": str(path),
        "document_id": target_document_id,
        "zotero_item_key": (
            preview["zotero_item"][
                "zotero_item_key"
            ]
        ),
        "zotero_attachment_key": (
            preview[
                "selected_attachment"
            ][
                "zotero_attachment_key"
            ]
        ),
        "source_count": len(
            desired_records
        ),
        "inserted_count": inserted,
        "updated_count": updated,
        "unchanged_count": unchanged,
        "missing_marked_count": (
            missing_marked
        ),
        "evidence_link_count_created": (
            evidence_created
        ),
        "touched_note_ids": (
            touched_note_ids
        ),
        "db_write_performed": bool(
            write_performed
        ),
        "production_data_modified": False,
        "production_schema_migrated": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "fts_write_performed": False,
        "external_llm_called": False,
    }


def _open_temp_rw(
    path: Path,
) -> sqlite3.Connection:
    resolved = path.resolve(
        strict=False
    )

    if resolved == Path(
        DEFAULT_DB_PATH
    ).resolve(strict=False):
        raise DirectionBCommitError(
            code="production_db_blocked",
            message=(
                "Production database opening "
                "is blocked in B3."
            ),
        )

    connection = sqlite3.connect(
        (
            f"file:"
            f"{resolved.as_posix()}"
            f"?mode=rw"
        ),
        uri=True,
    )
    connection.row_factory = (
        sqlite3.Row
    )
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def _assert_direction_b_schema(
    connection: sqlite3.Connection,
) -> None:
    required_tables = (
        "documents",
        "knowledge_chunks",
        "personal_notes",
        "note_evidence_links",
    )

    missing_tables = [
        table
        for table in required_tables
        if not _table_exists(
            connection,
            table,
        )
    ]

    if missing_tables:
        raise DirectionBCommitError(
            code=(
                "direction_b_schema_not_ready"
            ),
            message=(
                "Required tables are missing: "
                + ", ".join(
                    missing_tables
                )
            ),
        )

    personal = _columns(
        connection,
        "personal_notes",
    )
    evidence = _columns(
        connection,
        "note_evidence_links",
    )
    chunks = _columns(
        connection,
        "knowledge_chunks",
    )

    missing_personal = sorted(
        PERSONAL_NOTE_REQUIRED_COLUMNS
        - set(personal)
    )
    missing_evidence = sorted(
        EVIDENCE_REQUIRED_COLUMNS
        - set(evidence)
    )
    missing_chunks = sorted(
        KNOWLEDGE_CHUNK_REQUIRED_COLUMNS
        - set(chunks)
    )

    if (
        missing_personal
        or missing_evidence
        or missing_chunks
    ):
        raise DirectionBCommitError(
            code=(
                "direction_b_schema_not_ready"
            ),
            message=(
                "Direction-B schema "
                "is incomplete."
            ),
            details={
                "missing_personal_notes": (
                    missing_personal
                ),
                "missing_note_evidence_links": (
                    missing_evidence
                ),
                "missing_knowledge_chunks": (
                    missing_chunks
                ),
            },
        )

    if bool(
        evidence["chunk_id"][
            "notnull"
        ]
    ):
        raise DirectionBCommitError(
            code=(
                "direction_b_schema_not_ready"
            ),
            message=(
                "note_evidence_links."
                "chunk_id must be nullable."
            ),
        )

    indexes = {
        str(row[1])
        for row in connection.execute(
            """
            PRAGMA index_list(
                "personal_notes"
            )
            """
        ).fetchall()
    }

    if (
        "ux_personal_notes_source_identity"
        not in indexes
    ):
        raise DirectionBCommitError(
            code=(
                "direction_b_schema_not_ready"
            ),
            message=(
                "Direction-B source identity "
                "unique index is missing."
            ),
        )


def _assert_document_exists(
    connection: sqlite3.Connection,
    document_id: int,
) -> None:
    row = connection.execute(
        """
        SELECT id
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    if row is None:
        raise DirectionBCommitError(
            code="document_not_found",
            message=(
                "Target document "
                f"does not exist: "
                f"{document_id}"
            ),
        )


def _build_desired_records(
    preview: dict[str, Any],
    *,
    document_id: int,
) -> list[dict[str, Any]]:
    parent = preview[
        "zotero_item"
    ]
    selected_attachment = preview[
        "selected_attachment"
    ]

    item_key = str(
        parent["zotero_item_key"]
    )

    attachment_key = str(
        selected_attachment[
            "zotero_attachment_key"
        ]
    )

    attachment_uri = str(
        selected_attachment.get(
            "zotero_open_pdf_uri"
        )
        or (
            "zotero://open-pdf/"
            "library/items/"
            + attachment_key
        )
    )

    records: list[
        dict[str, Any]
    ] = []

    for annotation in (
        preview.get(
            "annotations"
        )
        or []
    ):
        source_identity = str(
            annotation[
                "source_identity"
            ]
        )

        comment = str(
            annotation.get(
                "source_comment"
            )
            or ""
        )

        selected_text = str(
            annotation.get(
                "selected_text"
            )
            or ""
        )

        pdf_page = annotation.get(
            "pdf_page"
        )
        page_label = annotation.get(
            "page_label"
        )

        page_display = (
            str(page_label)
            if page_label not in (
                None,
                "",
            )
            else str(pdf_page)
            if pdf_page is not None
            else ""
        )

        title = (
            "Zotero annotation"
            + (
                f" · p.{page_display}"
                if page_display
                else ""
            )
        )

        records.append(
            {
                "document_id": (
                    document_id
                ),
                "note_type": (
                    "zotero_annotation"
                ),
                "scope_type": (
                    "pdf_page"
                    if pdf_page
                    is not None
                    else "document"
                ),
                "scope_path": None,
                "source_path": None,
                "content_hash": (
                    _sha256_text(
                        comment
                    )
                ),
                "title": title,
                # Critical semantic boundary:
                # user comment is content.
                # selected_text is never
                # substituted for content.
                "content": comment,
                "source_system": (
                    "zotero"
                ),
                "source_library_id": int(
                    annotation.get(
                        "library_id"
                    )
                    or parent.get(
                        "library_id"
                    )
                    or 0
                ),
                "source_item_key": (
                    item_key
                ),
                "source_parent_item_key": (
                    attachment_key
                ),
                "source_attachment_key": (
                    attachment_key
                ),
                "source_annotation_key": (
                    annotation.get(
                        "zotero_annotation_key"
                    )
                ),
                "source_note_key": None,
                "source_record_kind": (
                    "zotero_annotation"
                ),
                "source_identity": (
                    source_identity
                ),
                "selected_text": (
                    selected_text
                ),
                "source_comment": (
                    comment
                ),
                "pdf_page": (
                    pdf_page
                ),
                "page_label": (
                    page_label
                ),
                "position_json": (
                    annotation.get(
                        "position_json"
                    )
                ),
                "source_uri": (
                    attachment_uri
                ),
                "source_created_at": (
                    annotation.get(
                        "source_created_at"
                    )
                ),
                "source_updated_at": (
                    annotation.get(
                        "source_updated_at"
                    )
                ),
                "source_version": (
                    annotation.get(
                        "source_version"
                    )
                ),
                "source_content_hash": (
                    annotation.get(
                        "source_content_hash"
                    )
                ),
                "source_missing": 0,
            }
        )

    for child_note in (
        preview.get(
            "child_notes"
        )
        or []
    ):
        source_identity = str(
            child_note[
                "source_identity"
            ]
        )

        note_text = str(
            child_note.get(
                "note_text"
            )
            or ""
        )

        note_key = str(
            child_note.get(
                "zotero_note_key"
            )
            or ""
        )

        parent_kind = str(
            child_note.get(
                "parent_kind"
            )
            or "regular_item"
        )

        immediate_parent_key = (
            attachment_key
            if parent_kind
            == "pdf_attachment"
            else item_key
        )

        records.append(
            {
                "document_id": (
                    document_id
                ),
                "note_type": (
                    "zotero_child_note"
                ),
                "scope_type": (
                    "document"
                ),
                "scope_path": None,
                "source_path": None,
                "content_hash": (
                    _sha256_text(
                        note_text
                    )
                ),
                "title": (
                    str(
                        child_note.get(
                            "title"
                        )
                        or ""
                    ).strip()
                    or (
                        "Zotero child note"
                    )
                ),
                "content": note_text,
                "source_system": (
                    "zotero"
                ),
                "source_library_id": int(
                    child_note.get(
                        "library_id"
                    )
                    or parent.get(
                        "library_id"
                    )
                    or 0
                ),
                "source_item_key": (
                    item_key
                ),
                "source_parent_item_key": (
                    immediate_parent_key
                ),
                # Preserve the actual Zotero
                # parent semantics. A child note
                # under the regular item has no
                # attachment key; a PDF child note
                # carries its attachment key.
                "source_attachment_key": (
                    child_note.get(
                        "zotero_attachment_key"
                    )
                ),
                "source_annotation_key": (
                    None
                ),
                "source_note_key": (
                    note_key
                ),
                "source_record_kind": (
                    "zotero_child_note"
                ),
                "source_identity": (
                    source_identity
                ),
                "selected_text": None,
                "source_comment": None,
                "pdf_page": None,
                "page_label": None,
                "position_json": None,
                "source_uri": (
                    "zotero://select/"
                    "library/items/"
                    + note_key
                ),
                "source_created_at": (
                    child_note.get(
                        "source_created_at"
                    )
                ),
                "source_updated_at": (
                    child_note.get(
                        "source_updated_at"
                    )
                ),
                "source_version": (
                    child_note.get(
                        "source_version"
                    )
                ),
                "source_content_hash": (
                    child_note.get(
                        "source_content_hash"
                    )
                ),
                "source_missing": 0,
            }
        )

    return records


def _upsert_personal_note(
    connection: sqlite3.Connection,
    record: dict[str, Any],
    *,
    timestamp: str,
) -> dict[str, Any]:
    source_identity = str(
        record["source_identity"]
    )

    existing = connection.execute(
        """
        SELECT *
        FROM personal_notes
        WHERE source_identity = ?
        """,
        (source_identity,),
    ).fetchone()

    if existing is None:
        insert_values = {
            **record,
            "summary": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        columns = list(
            insert_values
        )

        placeholders = ", ".join(
            "?"
            for _ in columns
        )

        connection.execute(
            f"""
            INSERT INTO personal_notes
                ({", ".join(columns)})
            VALUES
                ({placeholders})
            """,
            [
                insert_values[
                    column
                ]
                for column
                in columns
            ],
        )

        row = connection.execute(
            """
            SELECT id
            FROM personal_notes
            WHERE source_identity = ?
            """,
            (source_identity,),
        ).fetchone()

        return {
            "state": "inserted",
            "note_id": int(
                row["id"]
            ),
        }

    existing_document_id = (
        existing["document_id"]
    )

    if (
        existing_document_id
        is not None
        and int(
            existing_document_id
        )
        != int(
            record["document_id"]
        )
    ):
        raise DirectionBCommitError(
            code=(
                "source_identity_"
                "document_conflict"
            ),
            message=(
                "The Zotero source identity "
                "is already bound to a "
                "different document."
            ),
            details={
                "source_identity": (
                    source_identity
                ),
                "existing_document_id": int(
                    existing_document_id
                ),
                "requested_document_id": int(
                    record["document_id"]
                ),
            },
        )

    changed = any(
        existing[column]
        != record[column]
        for column
        in SOURCE_OWNED_COLUMNS
    )

    note_id = int(
        existing["id"]
    )

    if not changed:
        return {
            "state": "unchanged",
            "note_id": note_id,
        }

    assignments = ", ".join(
        f"{column} = ?"
        for column
        in SOURCE_OWNED_COLUMNS
    )

    connection.execute(
        f"""
        UPDATE personal_notes
        SET
            {assignments},
            updated_at = ?
        WHERE id = ?
        """,
        [
            record[column]
            for column
            in SOURCE_OWNED_COLUMNS
        ]
        + [
            timestamp,
            note_id,
        ],
    )

    return {
        "state": "updated",
        "note_id": note_id,
    }


def _build_evidence_rows(
    connection: sqlite3.Connection,
    *,
    note_id: int,
    document_id: int,
    record: dict[str, Any],
    timestamp: str,
) -> list[dict[str, Any]]:
    if (
        record[
            "source_record_kind"
        ]
        == "zotero_child_note"
    ):
        return [
            _document_only_evidence(
                note_id=note_id,
                document_id=document_id,
                record=record,
                timestamp=timestamp,
                method=(
                    "child_note_"
                    "document_scope"
                ),
                warnings=[],
                role=(
                    "document_context"
                ),
            )
        ]

    selected_text = str(
        record.get(
            "selected_text"
        )
        or ""
    )

    normalized_quote = (
        _normalized_quote(
            selected_text
        )
    )

    if not normalized_quote:
        return [
            _document_only_evidence(
                note_id=note_id,
                document_id=document_id,
                record=record,
                timestamp=timestamp,
                method=(
                    "annotation_"
                    "document_scope"
                ),
                warnings=[
                    "selected_text_empty"
                ],
                role="source_quote",
            )
        ]

    chunks = (
        _load_document_chunks(
            connection,
            document_id=document_id,
        )
    )

    pdf_page = record.get(
        "pdf_page"
    )

    page_matches: list[
        sqlite3.Row
    ] = []

    if pdf_page is not None:
        page_candidates = [
            chunk
            for chunk in chunks
            if _chunk_contains_page(
                chunk,
                int(pdf_page),
            )
        ]

        page_matches = [
            chunk
            for chunk
            in page_candidates
            if normalized_quote
            in _normalized_quote(
                chunk[
                    "chunk_text"
                ]
            )
        ]

    if page_matches:
        warnings = (
            [
                (
                    "multiple_page_"
                    "exact_quote_matches:"
                    + str(
                        len(
                            page_matches
                        )
                    )
                )
            ]
            if len(
                page_matches
            ) > 1
            else []
        )

        return [
            _matched_evidence(
                note_id=note_id,
                document_id=(
                    document_id
                ),
                chunk_id=int(
                    chunk["id"]
                ),
                record=record,
                timestamp=timestamp,
                method=(
                    "page_and_"
                    "exact_quote"
                ),
                warnings=warnings,
            )
            for chunk
            in page_matches
        ]

    document_matches = [
        chunk
        for chunk in chunks
        if normalized_quote
        in _normalized_quote(
            chunk[
                "chunk_text"
            ]
        )
    ]

    if (
        pdf_page is None
        and len(
            document_matches
        ) == 1
    ):
        return [
            _matched_evidence(
                note_id=note_id,
                document_id=(
                    document_id
                ),
                chunk_id=int(
                    document_matches[
                        0
                    ]["id"]
                ),
                record=record,
                timestamp=timestamp,
                method=(
                    "document_"
                    "exact_quote"
                ),
                warnings=(
                    [
                        "pdf_page_"
                        "unavailable"
                    ]
                    if pdf_page
                    is None
                    else [
                        (
                            "page_exact_"
                            "quote_not_found"
                        ),
                        (
                            "used_unique_"
                            "document_"
                            "exact_quote"
                        ),
                    ]
                ),
            )
        ]

    warnings: list[str] = []

    if pdf_page is None:
        warnings.append(
            "pdf_page_unavailable"
        )
    else:
        warnings.append(
            "page_exact_quote_not_found"
        )

    if len(
        document_matches
    ) > 1:
        warnings.append(
            (
                "ambiguous_document_"
                "exact_quote_matches:"
                + str(
                    len(
                        document_matches
                    )
                )
            )
        )
    elif (
        pdf_page is not None
        and len(
            document_matches
        ) == 1
    ):
        warnings.append(
            "exact_quote_only_found_outside_page"
        )
    else:
        warnings.append(
            "exact_quote_not_found"
        )

    return [
        _document_only_evidence(
            note_id=note_id,
            document_id=document_id,
            record=record,
            timestamp=timestamp,
            method=(
                "annotation_unaligned"
            ),
            warnings=warnings,
            role="source_quote",
        )
    ]


def _matched_evidence(
    *,
    note_id: int,
    document_id: int,
    chunk_id: int,
    record: dict[str, Any],
    timestamp: str,
    method: str,
    warnings: list[str],
) -> dict[str, Any]:
    quote = str(
        record.get(
            "selected_text"
        )
        or ""
    )

    return {
        "note_id": note_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "link_type": (
            "zotero_source"
        ),
        "evidence_role": (
            "source_quote"
        ),
        "quote_text": quote,
        "confidence": 1.0,
        "created_by": (
            "zotero_direction_b_commit"
        ),
        "created_at": timestamp,
        "pdf_page": record.get(
            "pdf_page"
        ),
        "page_label": record.get(
            "page_label"
        ),
        "source_locator_json": (
            _locator_json(
                record
            )
        ),
        "alignment_status": (
            "matched"
        ),
        "alignment_method": (
            method
        ),
        "alignment_warnings_json": (
            json.dumps(
                warnings,
                ensure_ascii=False,
            )
        ),
        "source_quote_hash": (
            _sha256_text(
                quote
            )
        ),
    }


def _document_only_evidence(
    *,
    note_id: int,
    document_id: int,
    record: dict[str, Any],
    timestamp: str,
    method: str,
    warnings: list[str],
    role: str,
) -> dict[str, Any]:
    quote = (
        str(
            record.get(
                "selected_text"
            )
            or ""
        )
        if record[
            "source_record_kind"
        ]
        == "zotero_annotation"
        else None
    )

    return {
        "note_id": note_id,
        "document_id": document_id,
        "chunk_id": None,
        "link_type": (
            "zotero_source"
        ),
        "evidence_role": role,
        "quote_text": quote,
        "confidence": 0.0,
        "created_by": (
            "zotero_direction_b_commit"
        ),
        "created_at": timestamp,
        "pdf_page": record.get(
            "pdf_page"
        ),
        "page_label": record.get(
            "page_label"
        ),
        "source_locator_json": (
            _locator_json(
                record
            )
        ),
        "alignment_status": (
            "document_only"
        ),
        "alignment_method": (
            method
        ),
        "alignment_warnings_json": (
            json.dumps(
                warnings,
                ensure_ascii=False,
            )
        ),
        "source_quote_hash": (
            _sha256_text(
                quote
            )
            if quote
            else None
        ),
    }


def _insert_evidence_link(
    connection: sqlite3.Connection,
    evidence: dict[str, Any],
) -> None:
    columns = list(
        evidence
    )

    placeholders = ", ".join(
        "?"
        for _ in columns
    )

    connection.execute(
        f"""
        INSERT INTO note_evidence_links
            ({", ".join(columns)})
        VALUES
            ({placeholders})
        """,
        [
            evidence[column]
            for column
            in columns
        ],
    )


def _mark_missing_sources(
    connection: sqlite3.Connection,
    *,
    source_library_id: int,
    document_id: int,
    source_item_key: str,
    selected_attachment_key: str,
    current_identities: set[str],
    timestamp: str,
) -> int:
    # The preview covers:
    #   1. annotations on the selected PDF,
    #   2. child notes on the selected PDF,
    #   3. child notes directly on the regular item.
    #
    # It does NOT cover sibling PDF attachments.
    # Therefore missing detection must use the same
    # source boundary and must also be library/document
    # scoped.
    rows = connection.execute(
        """
        SELECT
            id,
            source_identity
        FROM personal_notes
        WHERE source_system = 'zotero'
          AND source_library_id = ?
          AND document_id = ?
          AND source_item_key = ?
          AND source_missing = 0
          AND (
              (
                  source_record_kind =
                      'zotero_annotation'
                  AND source_attachment_key = ?
              )
              OR
              (
                  source_record_kind =
                      'zotero_child_note'
                  AND source_parent_item_key
                      IN (?, ?)
              )
          )
        """,
        (
            int(source_library_id),
            int(document_id),
            source_item_key,
            selected_attachment_key,
            source_item_key,
            selected_attachment_key,
        ),
    ).fetchall()

    missing = [
        row
        for row in rows
        if str(
            row[
                "source_identity"
            ]
        )
        not in current_identities
    ]

    for row in missing:
        # Missing from the current Zotero source:
        # preserve the note and its evidence.
        connection.execute(
            """
            UPDATE personal_notes
            SET
                source_missing = 1,
                updated_at = ?
            WHERE id = ?
            """,
            (
                timestamp,
                int(row["id"]),
            ),
        )

    return len(missing)


def _load_document_chunks(
    connection: sqlite3.Connection,
    *,
    document_id: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            id,
            document_id,
            chunk_text,
            pdf_page_start,
            pdf_page_end
        FROM knowledge_chunks
        WHERE document_id = ?
        ORDER BY id
        """,
        (document_id,),
    ).fetchall()


def _chunk_contains_page(
    chunk: sqlite3.Row,
    page: int,
) -> bool:
    start = chunk[
        "pdf_page_start"
    ]

    if start is None:
        return False

    end = (
        chunk[
            "pdf_page_end"
        ]
        if chunk[
            "pdf_page_end"
        ]
        is not None
        else start
    )

    return (
        int(start)
        <= page
        <= int(end)
    )


def _locator_json(
    record: dict[str, Any],
) -> str:
    payload = {
        "source_identity": (
            record.get(
                "source_identity"
            )
        ),
        "source_item_key": (
            record.get(
                "source_item_key"
            )
        ),
        "source_parent_item_key": (
            record.get(
                "source_parent_item_key"
            )
        ),
        "source_attachment_key": (
            record.get(
                "source_attachment_key"
            )
        ),
        "source_annotation_key": (
            record.get(
                "source_annotation_key"
            )
        ),
        "source_note_key": (
            record.get(
                "source_note_key"
            )
        ),
        "pdf_page": record.get(
            "pdf_page"
        ),
        "page_label": record.get(
            "page_label"
        ),
        "position_json": (
            record.get(
                "position_json"
            )
        ),
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalized_quote(
    value: Any,
) -> str:
    return normalize_text(
        value,
        preserve_paragraphs=False,
    ).casefold()


def _sha256_text(
    value: Any,
) -> str:
    return hashlib.sha256(
        str(value or "").encode(
            "utf-8"
        )
    ).hexdigest()


def _now_iso(
    now_ts: float | None,
) -> str:
    if now_ts is None:
        return datetime.now(
            timezone.utc
        ).isoformat()

    return datetime.fromtimestamp(
        float(now_ts),
        tz=timezone.utc,
    ).isoformat()


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone() is not None


def _columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, sqlite3.Row]:
    return {
        str(row["name"]): row
        for row
        in connection.execute(
            f"""
            PRAGMA table_info(
                "{table_name}"
            )
            """
        ).fetchall()
    }
