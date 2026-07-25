from __future__ import annotations

import sqlite3

import pytest

from scripts.migrations import (
    migrate_zotero_personal_notes_schema
    as migration,
)


def make_legacy_database(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            );

            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                content TEXT NOT NULL,
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

            CREATE INDEX
                ix_personal_notes_id
                ON personal_notes(id);

            CREATE INDEX
                ix_personal_notes_document_id
                ON personal_notes(document_id);

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

            CREATE INDEX
                ix_note_evidence_links_id
                ON note_evidence_links(id);

            CREATE INDEX
                ix_note_evidence_links_note_id
                ON note_evidence_links(note_id);

            CREATE INDEX
                ix_note_evidence_links_chunk_id
                ON note_evidence_links(chunk_id);

            CREATE TABLE zotero_inspiration_notes (
                id INTEGER PRIMARY KEY,
                marker TEXT NOT NULL
            );
            """
        )

        connection.execute(
            """
            INSERT INTO documents(
                id,
                title
            )
            VALUES (
                1,
                'Legacy book'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO knowledge_chunks(
                id,
                document_id,
                content
            )
            VALUES (
                10,
                1,
                'legacy chunk'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO personal_notes(
                id,
                document_id,
                note_type,
                title,
                content,
                created_at,
                updated_at
            )
            VALUES (
                100,
                1,
                'manual',
                'Legacy note',
                'Legacy note content',
                '2026-01-01 00:00:00',
                '2026-01-01 00:00:00'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO note_evidence_links(
                id,
                note_id,
                chunk_id,
                link_type,
                evidence_role,
                quote_text,
                confidence,
                created_by,
                created_at
            )
            VALUES (
                1000,
                100,
                10,
                'supports',
                'evidence',
                'legacy quote',
                0.8,
                'manual',
                '2026-01-01 00:00:00'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO zotero_inspiration_notes(
                id,
                marker
            )
            VALUES (
                1,
                'must remain untouched'
            )
            """
        )

        connection.commit()


def get_columns(
    connection,
    table_name,
):
    connection.row_factory = sqlite3.Row

    return {
        row["name"]: row
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def test_dry_run_has_zero_writes(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    with sqlite3.connect(db_path) as connection:
        before = set(
            get_columns(
                connection,
                "personal_notes",
            )
        )

    result = migration.migrate_database(
        db_path,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result[
        "production_write_performed"
    ] is False

    assert (
        "rebuild_table:note_evidence_links"
        in result["operations"]
    )

    with sqlite3.connect(db_path) as connection:
        after = set(
            get_columns(
                connection,
                "personal_notes",
            )
        )

    assert after == before
    assert "source_identity" not in after


def test_apply_preserves_legacy_rows(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    result = migration.migrate_database(
        db_path,
        dry_run=False,
    )

    assert result["status"] == "applied"
    assert result["remaining_operations"] == []

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        personal = get_columns(
            connection,
            "personal_notes",
        )

        expected = {
            name
            for name, _definition
            in migration.PERSONAL_NOTE_COLUMNS
        }

        assert expected.issubset(personal)

        evidence = get_columns(
            connection,
            "note_evidence_links",
        )

        assert evidence[
            "chunk_id"
        ]["notnull"] == 0

        legacy_note = connection.execute(
            """
            SELECT *
            FROM personal_notes
            WHERE id = 100
            """
        ).fetchone()

        assert legacy_note["title"] == "Legacy note"
        assert (
            legacy_note["content"]
            == "Legacy note content"
        )

        assert legacy_note[
            "source_identity"
        ] is None

        assert legacy_note[
            "source_missing"
        ] == 0

        legacy_link = connection.execute(
            """
            SELECT *
            FROM note_evidence_links
            WHERE id = 1000
            """
        ).fetchone()

        assert legacy_link["note_id"] == 100
        assert legacy_link["chunk_id"] == 10
        assert legacy_link["document_id"] == 1

        assert (
            legacy_link["alignment_status"]
            == "matched"
        )

        assert (
            legacy_link["alignment_method"]
            == "legacy_existing_chunk_id"
        )

        old_route = connection.execute(
            """
            SELECT marker
            FROM zotero_inspiration_notes
            WHERE id = 1
            """
        ).fetchone()

        assert (
            old_route["marker"]
            == "must remain untouched"
        )

        assert (
            connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            == "ok"
        )

        assert (
            connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            == []
        )


def test_document_level_child_note_link_is_valid(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    migration.migrate_database(
        db_path,
        dry_run=False,
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO note_evidence_links(
                note_id,
                document_id,
                chunk_id,
                link_type,
                evidence_role,
                created_by,
                created_at,
                alignment_status,
                alignment_method
            )
            VALUES (
                100,
                1,
                NULL,
                'document_context',
                'context',
                'zotero_import',
                '2026-01-02 00:00:00',
                'document_only',
                'zotero_child_note_parent'
            )
            """
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT
                document_id,
                chunk_id
            FROM note_evidence_links
            WHERE link_type =
                'document_context'
            """
        ).fetchone()

        assert row == (1, None)


def test_link_without_document_or_chunk_is_rejected(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    migration.migrate_database(
        db_path,
        dry_run=False,
    )

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(
            sqlite3.IntegrityError
        ):
            connection.execute(
                """
                INSERT INTO note_evidence_links(
                    note_id,
                    document_id,
                    chunk_id,
                    link_type,
                    created_by,
                    created_at
                )
                VALUES (
                    100,
                    NULL,
                    NULL,
                    'invalid',
                    'test',
                    '2026-01-02 00:00:00'
                )
                """
            )


def test_source_identity_is_unique_when_present(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    migration.migrate_database(
        db_path,
        dry_run=False,
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE personal_notes
            SET
                source_system = 'zotero',
                source_record_kind = 'annotation',
                source_library_id = 1,
                source_annotation_key = 'ABC123',
                source_identity =
                    'zotero:1:annotation:ABC123'
            WHERE id = 100
            """
        )

        connection.execute(
            """
            INSERT INTO personal_notes(
                id,
                document_id,
                note_type,
                title,
                content,
                created_at,
                updated_at
            )
            VALUES (
                101,
                1,
                'manual',
                'Second note',
                'content',
                '2026-01-02 00:00:00',
                '2026-01-02 00:00:00'
            )
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError
        ):
            connection.execute(
                """
                UPDATE personal_notes
                SET source_identity =
                    'zotero:1:annotation:ABC123'
                WHERE id = 101
                """
            )


def test_migration_is_idempotent(
    tmp_path,
):
    db_path = tmp_path / "legacy.db"
    make_legacy_database(db_path)

    first = migration.migrate_database(
        db_path,
        dry_run=False,
    )

    second = migration.migrate_database(
        db_path,
        dry_run=False,
    )

    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert second["operations"] == []
    assert second["applied"] == []
    assert second["applied_count"] == 0
    assert second["remaining_operations"] == []


def test_production_apply_is_hard_blocked():
    with pytest.raises(
        migration.MigrationSafetyError,
        match="Production schema migration",
    ):
        migration.migrate_database(
            migration.DEFAULT_DB_PATH,
            dry_run=False,
        )
