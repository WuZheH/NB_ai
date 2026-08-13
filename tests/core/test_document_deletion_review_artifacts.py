from __future__ import annotations

import sqlite3

from app.services.library import document_deletion_service as deletion


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE note_correction_reviews (
            review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL
        );
        CREATE TABLE note_correction_review_items (
            id INTEGER PRIMARY KEY,
            review_id TEXT NOT NULL
                REFERENCES note_correction_reviews(review_id)
                ON DELETE NO ACTION,
            payload TEXT
        );

        CREATE TABLE note_classification_reviews (
            review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL
        );
        CREATE TABLE note_classification_review_items (
            id INTEGER PRIMARY KEY,
            review_id TEXT NOT NULL
                REFERENCES note_classification_reviews(review_id)
                ON DELETE NO ACTION,
            payload TEXT
        );

        CREATE TABLE object_candidate_draft_reviews (
            review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL
        );
        CREATE TABLE object_candidate_draft_review_items (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            review_id TEXT NOT NULL
                REFERENCES object_candidate_draft_reviews(review_id)
                ON DELETE NO ACTION
        );

        CREATE TABLE object_candidate_human_reviews (
            human_review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL,
            source_draft_review_id TEXT
                REFERENCES object_candidate_draft_reviews(review_id)
                ON DELETE NO ACTION
        );
        CREATE TABLE object_candidate_human_review_items (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            human_review_id TEXT NOT NULL
                REFERENCES object_candidate_human_reviews(human_review_id)
                ON DELETE NO ACTION
        );
        """
    )

    for document_id in (10, 11):
        suffix = str(document_id)

        connection.execute(
            "INSERT INTO note_correction_reviews VALUES (?, ?)",
            (f"nc-{suffix}", document_id),
        )
        connection.execute(
            "INSERT INTO note_correction_review_items"
            "(review_id, payload) VALUES (?, ?)",
            (f"nc-{suffix}", "correction"),
        )

        connection.execute(
            "INSERT INTO note_classification_reviews VALUES (?, ?)",
            (f"ncl-{suffix}", document_id),
        )
        connection.execute(
            "INSERT INTO note_classification_review_items"
            "(review_id, payload) VALUES (?, ?)",
            (f"ncl-{suffix}", "classification"),
        )

        connection.execute(
            "INSERT INTO object_candidate_draft_reviews VALUES (?, ?)",
            (f"draft-{suffix}", document_id),
        )
        connection.execute(
            "INSERT INTO object_candidate_draft_review_items"
            "(document_id, review_id) VALUES (?, ?)",
            (document_id, f"draft-{suffix}"),
        )

        connection.execute(
            "INSERT INTO object_candidate_human_reviews VALUES (?, ?, ?)",
            (f"human-{suffix}", document_id, f"draft-{suffix}"),
        )
        connection.execute(
            "INSERT INTO object_candidate_human_review_items"
            "(document_id, human_review_id) VALUES (?, ?)",
            (document_id, f"human-{suffix}"),
        )

    connection.commit()
    return connection


def test_search_review_artifacts_are_not_a_delete_blocker() -> None:
    blockers = deletion._deletion_blockers(
        options={
            "preserve_external_pdf": True,
            "delete_managed_pdf": False,
            "preserve_personal_notes": True,
            "preserve_zotero_notes": True,
            "preserve_shared_objects": True,
        },
        review_count=84,
        user_object_comment_count=0,
        cross_document_references=0,
        unknown_references=[],
        vector_warning="",
        fts_warning="",
        managed_pdf_shared=False,
    )

    assert blockers == []


def test_review_artifact_rows_include_fk_only_note_children() -> None:
    connection = _connection()

    try:
        target = deletion._review_artifact_rows(connection, 10)

        assert len(target["note_correction_reviews"]) == 1
        assert len(target["note_correction_review_items"]) == 1
        assert len(target["note_classification_reviews"]) == 1
        assert len(target["note_classification_review_items"]) == 1
        assert sum(map(len, target.values())) == 8
    finally:
        connection.close()


def test_review_artifacts_delete_fk_safe_and_document_scoped() -> None:
    connection = _connection()

    try:
        before_target = deletion._review_artifact_rows(connection, 10)
        before_other = deletion._review_artifact_rows(connection, 11)

        assert sum(map(len, before_target.values())) == 8
        assert sum(map(len, before_other.values())) == 8

        counts = deletion._delete_review_artifacts(connection, 10)

        assert sum(counts.values()) == 8

        after_target = deletion._review_artifact_rows(connection, 10)
        after_other = deletion._review_artifact_rows(connection, 11)

        assert sum(map(len, after_target.values())) == 0
        assert sum(map(len, after_other.values())) == 8

        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    finally:
        connection.close()
