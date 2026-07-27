from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services import (
    import_duplicate_check_service,
    zotero_direction_b_commit_service as service,
    zotero_selected_book_preview_service as preview_service,
)
from scripts.migrations import (
    migrate_zotero_personal_notes_schema as migration,
)


def make_research_db(
    tmp_path,
    *,
    migrated: bool,
) -> Path:
    db_path = (
        tmp_path
        / (
            "research_migrated.db"
            if migrated
            else "research_legacy.db"
        )
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                content_layer TEXT,
                source_path TEXT,
                pdf_path TEXT,
                zotero_key TEXT
            );

            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                FOREIGN KEY(document_id)
                    REFERENCES documents(id)
            );

            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                note_type VARCHAR(64) NOT NULL,
                scope_type VARCHAR(64),
                scope_path TEXT,
                source_path TEXT,
                content_hash VARCHAR(64),
                title VARCHAR(512) NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(document_id)
                    REFERENCES documents(id)
            );

            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL,
                link_type VARCHAR(64) NOT NULL,
                evidence_role VARCHAR(64),
                quote_text TEXT,
                confidence FLOAT,
                created_by VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(note_id)
                    REFERENCES personal_notes(id),
                FOREIGN KEY(chunk_id)
                    REFERENCES knowledge_chunks(id)
            );

            CREATE TABLE zotero_inspiration_notes (
                id INTEGER PRIMARY KEY,
                marker TEXT NOT NULL
            );
            """
        )

        connection.executemany(
            """
            INSERT INTO documents(
                id,
                title,
                document_type,
                content_layer
            )
            VALUES (?, ?, 'book', 'body')
            """,
            [
                (
                    1,
                    "Target Book",
                ),
                (
                    2,
                    "Other Book",
                ),
            ],
        )

        connection.executemany(
            """
            INSERT INTO knowledge_chunks(
                id,
                document_id,
                chunk_index,
                heading_path,
                chunk_text,
                content_hash,
                pdf_page_start,
                pdf_page_end
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    101,
                    1,
                    0,
                    "chapter",
                    (
                        "Context before. "
                        "Original selected text. "
                        "Evidence without comment. "
                        "Context after."
                    ),
                    "chunk-101",
                    12,
                    12,
                ),
                (
                    102,
                    1,
                    1,
                    "chapter",
                    "Different material.",
                    "chunk-102",
                    13,
                    13,
                ),
                (
                    201,
                    2,
                    0,
                    "other",
                    (
                        "Original selected text. "
                        "Evidence without comment."
                    ),
                    "chunk-201",
                    12,
                    12,
                ),
            ],
        )

        connection.execute(
            """
            INSERT INTO zotero_inspiration_notes(
                id,
                marker
            )
            VALUES (1, 'sentinel')
            """
        )

        connection.commit()

    if migrated:
        result = migration.migrate_database(
            db_path,
            dry_run=False,
        )

        assert result[
            "status"
        ] == "applied"

    return db_path


def make_zotero_environment(
    tmp_path,
) -> dict:
    zotero_root = (
        tmp_path
        / "zotero"
    )
    storage_root = (
        zotero_root
        / "storage"
    )

    storage_root.mkdir(
        parents=True
    )

    snapshot = (
        tmp_path
        / "zotero.sqlite"
    )

    config = {
        "zotero_data_dir": str(
            zotero_root
        ),
        "zotero_storage_root": str(
            storage_root
        ),
        "zotero_db_snapshot": str(
            snapshot
        ),
    }

    with sqlite3.connect(
        snapshot
    ) as connection:
        connection.executescript(
            """
            CREATE TABLE fields (
                fieldID INTEGER PRIMARY KEY,
                fieldName TEXT NOT NULL
            );

            CREATE TABLE itemDataValues (
                valueID INTEGER PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE itemData (
                itemID INTEGER,
                fieldID INTEGER,
                valueID INTEGER
            );

            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY,
                itemTypeID INTEGER,
                dateAdded TEXT,
                dateModified TEXT,
                clientDateModified TEXT,
                libraryID INTEGER,
                key TEXT,
                version INTEGER,
                synced INTEGER
            );

            CREATE TABLE itemTypes (
                itemTypeID INTEGER PRIMARY KEY,
                typeName TEXT NOT NULL
            );

            CREATE TABLE itemAttachments (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                linkMode INTEGER,
                contentType TEXT,
                charsetID INTEGER,
                path TEXT,
                syncState INTEGER,
                storageModTime INTEGER,
                storageHash TEXT,
                lastProcessedModificationTime INTEGER,
                lastRead INTEGER
            );

            CREATE TABLE itemAnnotations (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                type TEXT,
                authorName TEXT,
                text TEXT,
                comment TEXT,
                color TEXT,
                pageLabel TEXT,
                sortIndex TEXT,
                position TEXT,
                isExternal INTEGER
            );

            CREATE TABLE itemNotes (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                note TEXT,
                title TEXT
            );

            CREATE TABLE deletedItems (
                itemID INTEGER PRIMARY KEY
            );
            """
        )

        connection.execute(
            "INSERT INTO itemTypes(itemTypeID, typeName) VALUES (1, 'book'), (2, 'attachment')"
        )
        connection.execute(
            """
            INSERT INTO fields(
                fieldID,
                fieldName
            )
            VALUES (1, 'title')
            """
        )

        connection.execute(
            """
            INSERT INTO itemDataValues(
                valueID,
                value
            )
            VALUES (
                1,
                'Selected Test Book'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO items(
                itemID,
                itemTypeID,
                dateAdded,
                dateModified,
                libraryID,
                key,
                version,
                synced
            )
            VALUES (
                1,
                1,
                '2026-07-01 00:00:00',
                '2026-07-20 00:00:00',
                1,
                'BOOKKEY1',
                5,
                1
            )
            """
        )

        connection.execute(
            """
            INSERT INTO itemData(
                itemID,
                fieldID,
                valueID
            )
            VALUES (1, 1, 1)
            """
        )

        connection.execute(
            """
            INSERT INTO items(
                itemID,
                itemTypeID,
                dateAdded,
                dateModified,
                libraryID,
                key,
                version,
                synced
            )
            VALUES (
                10,
                2,
                '2026-07-01 00:00:00',
                '2026-07-21 00:00:00',
                1,
                'PDFKEY1',
                3,
                1
            )
            """
        )

        connection.execute(
            """
            INSERT INTO itemAttachments(
                itemID,
                parentItemID,
                linkMode,
                contentType,
                path
            )
            VALUES (
                10,
                1,
                0,
                'application/pdf',
                'storage:book.pdf'
            )
            """
        )

        annotation_rows = [
            (
                20,
                "ANNKEY1",
                "Original selected text",
                "My annotation comment",
            ),
            (
                21,
                "ANNKEY2",
                "Evidence without comment",
                "",
            ),
        ]

        for (
            item_id,
            key,
            text,
            comment,
        ) in annotation_rows:
            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    ?,
                    3,
                    '2026-07-02 00:00:00',
                    '2026-07-22 00:00:00',
                    1,
                    ?,
                    4,
                    1
                )
                """,
                (
                    item_id,
                    key,
                ),
            )

            connection.execute(
                """
                INSERT INTO itemAnnotations(
                    itemID,
                    parentItemID,
                    type,
                    text,
                    comment,
                    pageLabel,
                    position
                )
                VALUES (
                    ?,
                    10,
                    'highlight',
                    ?,
                    ?,
                    '12',
                    '{"pageIndex":11}'
                )
                """,
                (
                    item_id,
                    text,
                    comment,
                ),
            )

        child_rows = [
            (
                30,
                "NOTEKEY1",
                1,
                (
                    "<p>Parent child "
                    "note</p>"
                ),
                "Reading note",
            ),
            (
                31,
                "NOTEKEY2",
                10,
                (
                    "<p>PDF child "
                    "note</p>"
                ),
                "PDF note",
            ),
        ]

        for (
            item_id,
            key,
            parent_id,
            html,
            title,
        ) in child_rows:
            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    ?,
                    4,
                    '2026-07-03 00:00:00',
                    '2026-07-23 00:00:00',
                    1,
                    ?,
                    2,
                    1
                )
                """,
                (
                    item_id,
                    key,
                ),
            )

            connection.execute(
                """
                INSERT INTO itemNotes(
                    itemID,
                    parentItemID,
                    note,
                    title
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    item_id,
                    parent_id,
                    html,
                    title,
                ),
            )

        connection.commit()

    pdf_path = (
        storage_root
        / "PDFKEY1"
        / "book.pdf"
    )

    pdf_path.parent.mkdir(
        parents=True
    )

    pdf_path.write_bytes(
        b"%PDF-1.4\nB3 fixture\n"
    )

    return {
        "snapshot": snapshot,
        "config": config,
        "pdf_path": pdf_path,
    }


@pytest.fixture(autouse=True)
def reset_preview_cache():
    preview_service._clear_preview_cache_for_tests()
    yield
    preview_service._clear_preview_cache_for_tests()


@pytest.fixture(autouse=True)
def no_real_pdf_parser(
    monkeypatch,
):
    monkeypatch.setattr(
        import_duplicate_check_service,
        "first_pages_text_fingerprint",
        lambda *_args, **_kwargs: (
            None,
            {
                "status": "skipped",
                "warnings": [],
            },
        ),
    )

    monkeypatch.setattr(
        import_duplicate_check_service,
        "_file_page_size_meta",
        lambda path: {
            "file_size": (
                Path(path)
                .stat()
                .st_size
            ),
            "page_count": 200,
        },
    )


def issue_preview(
    env,
    db_path,
    *,
    now_ts=1000,
    attachment_key=None,
):
    return (
        preview_service
        .build_selected_book_preview(
            zotero_item_key="BOOKKEY1",
            zotero_attachment_key=(
                attachment_key
            ),
            snapshot_path=env[
                "snapshot"
            ],
            db_path=db_path,
            config=env["config"],
            now_ts=now_ts,
            token_ttl_seconds=300,
        )
    )


def add_second_pdf_attachment(
    env,
):
    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            INSERT INTO items(
                itemID,
                itemTypeID,
                dateAdded,
                dateModified,
                libraryID,
                key,
                version,
                synced
            )
            VALUES (
                11,
                2,
                '2026-07-05 00:00:00',
                '2026-07-25 00:00:00',
                1,
                'PDFKEY2',
                1,
                1
            )
            """
        )

        connection.execute(
            """
            INSERT INTO itemAttachments(
                itemID,
                parentItemID,
                linkMode,
                contentType,
                path
            )
            VALUES (
                11,
                1,
                0,
                'application/pdf',
                'storage:book2.pdf'
            )
            """
        )

        connection.commit()

    target = (
        Path(
            env[
                "config"
            ][
                "zotero_storage_root"
            ]
        )
        / "PDFKEY2"
        / "book2.pdf"
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    target.write_bytes(
        b"%PDF-1.4\nB3 second PDF\n"
    )


def fetch_note(
    db_path,
    identity,
):
    with sqlite3.connect(
        db_path
    ) as connection:
        connection.row_factory = (
            sqlite3.Row
        )

        row = connection.execute(
            """
            SELECT *
            FROM personal_notes
            WHERE source_identity = ?
            """,
            (identity,),
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )


def evidence_for(
    db_path,
    identity,
):
    with sqlite3.connect(
        db_path
    ) as connection:
        connection.row_factory = (
            sqlite3.Row
        )

        rows = connection.execute(
            """
            SELECT
                evidence.*
            FROM note_evidence_links
                AS evidence
            JOIN personal_notes AS note
              ON note.id =
                 evidence.note_id
            WHERE note.source_identity = ?
            ORDER BY evidence.id
            """,
            (identity,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


def test_commit_maps_sources_and_evidence(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )
    preview = issue_preview(
        env,
        db_path,
    )

    result = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=preview[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1000,
        )
    )

    assert result["status"] == "committed"
    assert result["source_count"] == 4
    assert result["inserted_count"] == 4
    assert result[
        "evidence_link_count_created"
    ] == 4

    annotation = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert annotation[
        "note_type"
    ] == "zotero_annotation"

    assert annotation[
        "content"
    ] == "My annotation comment"

    assert annotation[
        "selected_text"
    ] == "Original selected text"

    assert annotation[
        "source_comment"
    ] == "My annotation comment"

    assert annotation[
        "source_attachment_key"
    ] == "PDFKEY1"

    assert annotation[
        "source_version"
    ] == 4

    no_comment = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY2",
    )

    # Highlight text must never be
    # mislabeled as the user's comment.
    assert no_comment["content"] == ""
    assert no_comment[
        "source_comment"
    ] == ""
    assert no_comment[
        "selected_text"
    ] == "Evidence without comment"

    child = fetch_note(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    assert child[
        "note_type"
    ] == "zotero_child_note"
    assert child[
        "content"
    ] == "Parent child note"

    assert child[
        "source_version"
    ] == 2

    ann_evidence = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert len(
        ann_evidence
    ) == 1

    assert ann_evidence[
        0
    ]["document_id"] == 1

    assert ann_evidence[
        0
    ]["chunk_id"] == 101

    assert ann_evidence[
        0
    ]["alignment_method"] == (
        "page_and_exact_quote"
    )

    # Identical text in document 2
    # must never leak into evidence.
    assert ann_evidence[
        0
    ]["chunk_id"] != 201

    child_evidence = evidence_for(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    assert child_evidence[
        0
    ]["document_id"] == 1
    assert child_evidence[
        0
    ]["chunk_id"] is None
    assert child_evidence[
        0
    ]["alignment_status"] == (
        "document_only"
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        sentinel = connection.execute(
            """
            SELECT COUNT(*)
            FROM zotero_inspiration_notes
            WHERE marker = 'sentinel'
            """
        ).fetchone()[0]

    assert sentinel == 1
    assert result[
        "production_data_modified"
    ] is False
    assert result[
        "zotero_db_write_performed"
    ] is False


def test_reimport_is_idempotent(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )
    preview = issue_preview(
        env,
        db_path,
    )

    first = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=preview[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1000,
        )
    )

    before = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    second = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=preview[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1001,
        )
    )

    after = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert first[
        "inserted_count"
    ] == 4

    assert second[
        "inserted_count"
    ] == 0
    assert second[
        "updated_count"
    ] == 0
    assert second[
        "unchanged_count"
    ] == 4
    assert second[
        "evidence_link_count_created"
    ] == 0
    assert second[
        "db_write_performed"
    ] is False

    assert [
        row["id"]
        for row in before
    ] == [
        row["id"]
        for row in after
    ]


def test_changed_note_rebuilds_only_its_evidence(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )

    initial = issue_preview(
        env,
        db_path,
        now_ts=1000,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=initial[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    child_before = evidence_for(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )[0]["created_at"]

    annotation_before = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )[0]["created_at"]

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            UPDATE itemAnnotations
            SET comment = 'Changed comment'
            WHERE itemID = 20
            """
        )

        connection.execute(
            """
            UPDATE items
            SET
                dateModified =
                    '2026-07-25 00:00:00',
                version = 5
            WHERE itemID = 20
            """
        )

        connection.commit()

    changed = issue_preview(
        env,
        db_path,
        now_ts=1001,
    )

    result = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=changed[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1001,
        )
    )

    assert result[
        "updated_count"
    ] == 1
    assert result[
        "unchanged_count"
    ] == 3
    assert result[
        "evidence_link_count_created"
    ] == 1

    annotation = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert annotation[
        "content"
    ] == "Changed comment"

    annotation_after = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )[0]["created_at"]

    child_after = evidence_for(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )[0]["created_at"]

    assert annotation_before != annotation_after
    assert child_before == child_after


def test_source_version_only_change_is_persisted(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )

    env = make_zotero_environment(
        tmp_path
    )

    initial = issue_preview(
        env,
        db_path,
        now_ts=1000,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=initial[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    before = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert before[
        "source_version"
    ] == 4

    original_hash = before[
        "source_content_hash"
    ]

    # Change only Zotero's item version.
    # Content and dateModified stay unchanged.
    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            UPDATE items
            SET version = 5
            WHERE itemID = 20
            """
        )
        connection.commit()

    changed = issue_preview(
        env,
        db_path,
        now_ts=1001,
    )

    result = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=changed[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1001,
        )
    )

    assert result[
        "inserted_count"
    ] == 0

    assert result[
        "updated_count"
    ] == 1

    assert result[
        "unchanged_count"
    ] == 3

    after = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert after[
        "source_version"
    ] == 5

    # Proves this really was a revision-only change.
    assert after[
        "source_content_hash"
    ] == original_hash


def test_missing_source_is_marked_not_deleted(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )

    first = issue_preview(
        env,
        db_path,
        now_ts=1000,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=first[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    before_evidence = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    # Same Zotero keys in another library/document
    # must never be touched by this import.
    with sqlite3.connect(
        db_path
    ) as connection:
        connection.execute(
            """
            INSERT INTO personal_notes(
                document_id,
                note_type,
                title,
                content,
                created_at,
                updated_at,
                source_system,
                source_library_id,
                source_item_key,
                source_parent_item_key,
                source_attachment_key,
                source_record_kind,
                source_identity,
                source_missing
            )
            VALUES (
                2,
                'zotero_annotation',
                'Foreign library sentinel',
                'foreign',
                '2026-07-01T00:00:00+00:00',
                '2026-07-01T00:00:00+00:00',
                'zotero',
                2,
                'BOOKKEY1',
                'PDFKEY1',
                'PDFKEY1',
                'zotero_annotation',
                'zotero:2:annotation:FOREIGN',
                0
            )
            """
        )

        connection.commit()

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            DELETE FROM itemAnnotations
            WHERE itemID = 20
            """
        )

        connection.commit()

    current = issue_preview(
        env,
        db_path,
        now_ts=1001,
    )

    result = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=current[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1001,
        )
    )

    assert result[
        "missing_marked_count"
    ] == 1

    missing = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert missing is not None
    assert missing[
        "source_missing"
    ] == 1

    after_evidence = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert len(
        before_evidence
    ) == len(
        after_evidence
    ) == 1

    foreign = fetch_note(
        db_path,
        "zotero:2:annotation:FOREIGN",
    )

    assert foreign is not None
    assert foreign[
        "document_id"
    ] == 2
    assert foreign[
        "source_library_id"
    ] == 2
    assert foreign[
        "source_missing"
    ] == 0


def test_unaligned_annotation_is_document_level(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            UPDATE itemAnnotations
            SET text = 'Quote absent from chunks'
            WHERE itemID = 20
            """
        )

        connection.execute(
            """
            UPDATE items
            SET version = 9
            WHERE itemID = 20
            """
        )

        connection.commit()

    current = issue_preview(
        env,
        db_path,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=current[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    evidence = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert len(evidence) == 1

    assert evidence[
        0
    ]["document_id"] == 1
    assert evidence[
        0
    ]["chunk_id"] is None

    assert evidence[
        0
    ]["alignment_status"] == (
        "document_only"
    )

    assert evidence[
        0
    ]["quote_text"] == (
        "Quote absent from chunks"
    )


def test_known_page_never_falls_back_to_wrong_page(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )

    env = make_zotero_environment(
        tmp_path
    )

    # The annotation says page 12.
    # Remove the quote from page 12 and place
    # one unique copy on page 13.
    with sqlite3.connect(
        db_path
    ) as connection:
        connection.execute(
            """
            UPDATE knowledge_chunks
            SET chunk_text =
                'Page twelve without the quote.'
            WHERE id = 101
            """
        )

        connection.execute(
            """
            UPDATE knowledge_chunks
            SET chunk_text =
                'Original selected text only on page thirteen.'
            WHERE id = 102
            """
        )

        connection.commit()

    current = issue_preview(
        env,
        db_path,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=current[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    evidence = evidence_for(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    assert len(evidence) == 1
    assert evidence[
        0
    ]["pdf_page"] == 12

    # Never bind a known-page annotation
    # to the unique quote on another page.
    assert evidence[
        0
    ]["chunk_id"] is None

    assert evidence[
        0
    ]["alignment_status"] == (
        "document_only"
    )

    assert evidence[
        0
    ]["alignment_method"] == (
        "annotation_unaligned"
    )

    assert (
        "exact_quote_only_found_outside_page"
        in evidence[
            0
        ][
            "alignment_warnings_json"
        ]
    )


def test_regular_child_note_is_stable_across_pdf_selection(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )

    env = make_zotero_environment(
        tmp_path
    )

    add_second_pdf_attachment(
        env
    )

    first = issue_preview(
        env,
        db_path,
        attachment_key="PDFKEY1",
        now_ts=1000,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=first[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    regular_before = fetch_note(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    evidence_before = evidence_for(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    assert regular_before[
        "source_parent_item_key"
    ] == "BOOKKEY1"

    assert regular_before[
        "source_attachment_key"
    ] is None

    second = issue_preview(
        env,
        db_path,
        attachment_key="PDFKEY2",
        now_ts=1001,
    )

    result = (
        service
        .commit_selected_book_preview_to_temp_db(
            preview_token=second[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1001,
        )
    )

    # PDF2 has no own annotations/child notes;
    # only the regular-item child note is present.
    assert result[
        "source_count"
    ] == 1

    assert result[
        "inserted_count"
    ] == 0

    assert result[
        "updated_count"
    ] == 0

    assert result[
        "unchanged_count"
    ] == 1

    assert result[
        "missing_marked_count"
    ] == 0

    assert result[
        "db_write_performed"
    ] is False

    regular_after = fetch_note(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    assert regular_after[
        "source_attachment_key"
    ] is None

    assert regular_after[
        "source_parent_item_key"
    ] == "BOOKKEY1"

    # Sources belonging to PDF1 are outside the
    # PDF2 missing-detection boundary.
    annotation = fetch_note(
        db_path,
        "zotero:1:annotation:ANNKEY1",
    )

    pdf_child = fetch_note(
        db_path,
        "zotero:1:child_note:NOTEKEY2",
    )

    assert annotation[
        "source_missing"
    ] == 0

    assert pdf_child[
        "source_missing"
    ] == 0

    evidence_after = evidence_for(
        db_path,
        "zotero:1:child_note:NOTEKEY1",
    )

    assert [
        row["id"]
        for row in evidence_before
    ] == [
        row["id"]
        for row in evidence_after
    ]


def test_production_db_is_blocked_before_connect(
    monkeypatch,
):
    called = False

    def forbidden_connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError(
            "sqlite3.connect must not run"
        )

    monkeypatch.setattr(
        service.sqlite3,
        "connect",
        forbidden_connect,
    )

    with pytest.raises(
        service.DirectionBCommitError
    ) as exc_info:
        service.commit_selected_book_preview_to_temp_db(
            preview_token="unused",
            document_id=1,
            db_path=service.DEFAULT_DB_PATH,
        )

    assert exc_info.value.code == (
        "production_db_blocked"
    )
    assert called is False


def test_unmigrated_temp_db_is_rejected(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=False,
    )
    env = make_zotero_environment(
        tmp_path
    )
    current = issue_preview(
        env,
        db_path,
    )

    with pytest.raises(
        service.DirectionBCommitError
    ) as exc_info:
        service.commit_selected_book_preview_to_temp_db(
            preview_token=current[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1000,
        )

    assert exc_info.value.code == (
        "direction_b_schema_not_ready"
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM personal_notes
            """
        ).fetchone()[0]

    assert count == 0


def test_document_conflict_and_preview_drift_are_rejected(
    tmp_path,
):
    db_path = make_research_db(
        tmp_path,
        migrated=True,
    )
    env = make_zotero_environment(
        tmp_path
    )

    current = issue_preview(
        env,
        db_path,
        now_ts=1000,
    )

    service.commit_selected_book_preview_to_temp_db(
        preview_token=current[
            "preview_token"
        ],
        document_id=1,
        db_path=db_path,
        now_ts=1000,
    )

    with pytest.raises(
        service.DirectionBCommitError
    ) as conflict:
        service.commit_selected_book_preview_to_temp_db(
            preview_token=current[
                "preview_token"
            ],
            document_id=2,
            db_path=db_path,
            now_ts=1001,
        )

    assert conflict.value.code == (
        "source_identity_document_conflict"
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        moved = connection.execute(
            """
            SELECT COUNT(*)
            FROM personal_notes
            WHERE document_id = 2
            """
        ).fetchone()[0]

    assert moved == 0

    fresh = issue_preview(
        env,
        db_path,
        now_ts=1002,
    )

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            UPDATE itemAnnotations
            SET comment = 'drifted'
            WHERE itemID = 20
            """
        )
        connection.commit()

    with pytest.raises(
        preview_service.ZoteroSelectedBookPreviewError
    ) as drift:
        service.commit_selected_book_preview_to_temp_db(
            preview_token=fresh[
                "preview_token"
            ],
            document_id=1,
            db_path=db_path,
            now_ts=1003,
        )

    assert drift.value.code == (
        "preview_source_drift"
    )
