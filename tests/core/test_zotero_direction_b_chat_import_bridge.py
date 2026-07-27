from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.services import (
    chat_tool_service,
    vector_store_service,
    zotero_direction_b_import_service,
    zotero_selected_book_preview_service,
)
from app.services.retrieval import fts_index_service
from scripts.migrations import (
    migrate_zotero_personal_notes_schema
    as migration,
)


@pytest.fixture(autouse=True)
def reset_chat_state():
    chat_tool_service.reset_chat_tool_state_for_tests()
    yield
    chat_tool_service.reset_chat_tool_state_for_tests()


@pytest.fixture(autouse=True)
def isolate_b4_derived_primitives(monkeypatch):
    monkeypatch.setattr(
        fts_index_service,
        "upsert_document_retrieval_fts",
        lambda **_kwargs: {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        },
    )
    monkeypatch.setattr(
        vector_store_service,
        "sync_affected_passage_embeddings",
        lambda *_args, **_kwargs: {
            "scope": "affected_source_ids_only",
            "full_rebuild_allowed": False,
            "delete_orphans_allowed": False,
            "lancedb_writes_performed": False,
        },
    )
    monkeypatch.setattr(
        vector_store_service,
        "sync_document_note_embeddings",
        lambda *_args, **_kwargs: {
            "scope": "document_only",
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
            "lancedb_writes_performed": False,
        },
    )


def make_temp_data_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    search_index = root / "search_index"
    search_index.mkdir(parents=True, exist_ok=True)
    fts_index_service._build_database(
        search_index / "retrieval_fts_v1.db",
        [],
    )
    (search_index / "retrieval_fts_v1_manifest.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    return root


def make_temp_db(
    root: Path,
) -> Path:
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = root / "research.db"

    with sqlite3.connect(
        path
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
                zotero_key TEXT,
                created_at TEXT,
                read_status TEXT
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

            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                zotero_item_key TEXT,
                zotero_attachment_key TEXT,
                zotero_source_id TEXT,
                zotero_select_uri TEXT,
                zotero_open_pdf_uri TEXT,
                source_trace_json TEXT,
                pdf_path TEXT,
                created_at TEXT,
                FOREIGN KEY(document_id)
                    REFERENCES documents(id)
            );

            INSERT INTO zotero_inspiration_notes(
                id,
                marker
            )
            VALUES (
                1,
                'must remain untouched'
            );
            """
        )

        connection.commit()

    result = migration.migrate_database(
        path,
        dry_run=False,
    )

    assert result[
        "status"
    ] == "applied"

    return path


def preview_payload():
    return {
        "status": "ready",
        "zotero_item": {
            "zotero_item_key": (
                "BOOKKEY1"
            ),
            "library_id": 1,
            "title": "Selected Book",
        },
        "selected_attachment": {
            "zotero_attachment_key": (
                "PDFKEY1"
            ),
            "pdf_sha256": "a" * 64,
            "page_count": 120,
            "zotero_open_pdf_uri": (
                "zotero://open-pdf/"
                "library/items/PDFKEY1"
            ),
        },
        "annotation_count": 1,
        "child_note_count": 1,
        "annotations": [
            {
                "source_identity": (
                    "zotero:1:"
                    "annotation:ANNKEY1"
                ),
                "library_id": 1,
                "zotero_annotation_key": (
                    "ANNKEY1"
                ),
                "selected_text": (
                    "Original selected text"
                ),
                "source_comment": (
                    "My annotation comment"
                ),
                "pdf_page": 12,
                "page_label": "12",
                "position_json": (
                    '{"pageIndex":11}'
                ),
                "source_created_at": (
                    "2026-07-01 00:00:00"
                ),
                "source_updated_at": (
                    "2026-07-02 00:00:00"
                ),
                "source_version": 4,
                "source_content_hash": (
                    "annotation-hash"
                ),
            }
        ],
        "child_notes": [
            {
                "source_identity": (
                    "zotero:1:"
                    "child_note:NOTEKEY1"
                ),
                "library_id": 1,
                "zotero_note_key": (
                    "NOTEKEY1"
                ),
                "parent_kind": (
                    "regular_item"
                ),
                "zotero_attachment_key": (
                    None
                ),
                "title": "Reading note",
                "note_text": (
                    "Parent child note"
                ),
                "source_created_at": (
                    "2026-07-03 00:00:00"
                ),
                "source_updated_at": (
                    "2026-07-04 00:00:00"
                ),
                "source_version": 2,
                "source_content_hash": (
                    "child-hash"
                ),
            }
        ],
        "duplicate_check": {
            "duplicate_found": False,
        },
        "warnings": [],
    }


def body_importer(
    *,
    preview,
    db_path,
):
    with sqlite3.connect(
        db_path
    ) as connection:
        connection.execute(
            """
            INSERT INTO documents(
                id,
                title,
                document_type,
                content_layer,
                created_at,
                read_status,
                zotero_key
            )
            VALUES (
                1,
                ?,
                'book',
                'body',
                '2026-07-26',
                'unread',
                'BOOKKEY1'
            )
            """,
            (
                preview[
                    "zotero_item"
                ]["title"],
            ),
        )

        connection.execute(
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
            VALUES (
                101,
                1,
                0,
                'chapter',
                'Context. Original selected text. End.',
                'chunk-hash',
                12,
                12
            )
            """
        )

        connection.commit()

    return {
        "status": "committed",
        "document_id": 1,
        "title": (
            preview[
                "zotero_item"
            ]["title"]
        ),
        "document_type": "book",
        "chunk_count": 1,
    }


def install_constant_preview(
    monkeypatch,
):
    payload = preview_payload()

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )

    # Synthetic tests do not create a real B2
    # cache entry. Bypass only the internal
    # private-source lookup; runtime override
    # body importers in these tests do not use
    # the PDF path.
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_source",
        lambda *_args, **_kwargs: (
            payload,
            Path(__file__).resolve(),
        ),
    )

    return payload


def test_chat_import_document_runs_full_direction_b_temp_chain(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    install_constant_preview(
        monkeypatch
    )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=make_temp_data_dir(tmp_path / "data"),
            zotero_body_importer=(
                body_importer
            ),
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime,
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as missing:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=False,
            runtime=runtime,
        )

    assert missing.value.error_code == (
        "chat_import_confirmation_required"
    )

    result = (
        chat_tool_service
        .import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )
    )

    assert result == {
        "status": "committed",
        "document_id": 1,
        "title": "Selected Book",
        "document_type": "book",
        "chunk_count": 1,
        "duplicate_status": (
            "not_detected"
        ),
        "error_code": None,
    }

    with sqlite3.connect(
        db_path
    ) as connection:
        connection.row_factory = (
            sqlite3.Row
        )

        notes = connection.execute(
            """
            SELECT
                source_identity,
                content,
                selected_text,
                source_comment,
                source_attachment_key,
                source_version
            FROM personal_notes
            ORDER BY source_identity
            """
        ).fetchall()

        assert len(notes) == 2

        annotation = next(
            row
            for row in notes
            if row[
                "source_identity"
            ].endswith(
                "ANNKEY1"
            )
        )

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
            "source_version"
        ] == 4

        child = next(
            row
            for row in notes
            if row[
                "source_identity"
            ].endswith(
                "NOTEKEY1"
            )
        )

        assert child[
            "source_attachment_key"
        ] is None

        evidence = connection.execute(
            """
            SELECT
                chunk_id,
                alignment_status,
                alignment_method
            FROM note_evidence_links
            ORDER BY id
            """
        ).fetchall()

        assert len(evidence) == 2

        assert any(
            row["chunk_id"] == 101
            and row[
                "alignment_method"
            ] == "page_and_exact_quote"
            for row in evidence
        )

        assert any(
            row["chunk_id"] is None
            and row[
                "alignment_status"
            ] == "document_only"
            for row in evidence
        )

        sentinel = connection.execute(
            """
            SELECT marker
            FROM zotero_inspiration_notes
            WHERE id = 1
            """
        ).fetchone()[0]

        assert sentinel == (
            "must remain untouched"
        )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as replay:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )

    assert replay.value.error_code == (
        "chat_import_confirmation_"
        "invalid_or_expired"
    )


def test_zotero_bridge_output_is_compact(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    install_constant_preview(
        monkeypatch
    )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=tmp_path / "data",
        )
    )

    result = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="secret-preview-token",
            runtime=runtime,
        )
    )

    assert result[
        "source_type"
    ] == "zotero_selected_book"

    assert result[
        "annotation_count"
    ] == 1

    assert result[
        "child_note_count"
    ] == 1

    serialized = str(result)

    assert "secret-preview-token" not in serialized
    assert "annotations" not in result
    assert "child_notes" not in result
    assert "source_revision" not in result
    assert "pdf_path" not in result
    assert "zotero_item_key" not in result
    assert "zotero_attachment_key" not in result


def test_source_drift_is_rejected_before_body_import(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    calls = {
        "resolve": 0,
        "body": 0,
    }

    def resolver(*_args, **_kwargs):
        calls["resolve"] += 1

        if calls["resolve"] == 1:
            return preview_payload()

        raise (
            zotero_selected_book_preview_service
            .ZoteroSelectedBookPreviewError(
                status_code=409,
                code="preview_source_drift",
                message="source drift",
            )
        )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        resolver,
    )

    def forbidden_body(**_kwargs):
        calls["body"] += 1
        raise AssertionError(
            "body importer must not run"
        )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=make_temp_data_dir(tmp_path / "data"),
            zotero_body_importer=(
                forbidden_body
            ),
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime,
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as error:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )

    assert error.value.error_code == (
        "preview_source_drift"
    )
    assert calls["body"] == 0

    with sqlite3.connect(
        db_path
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_body_failure_restores_temp_database(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    install_constant_preview(
        monkeypatch
    )

    before = db_path.read_bytes()

    def failing_body(
        *,
        preview,
        db_path,
    ):
        with sqlite3.connect(
            db_path
        ) as connection:
            connection.execute(
                """
                INSERT INTO documents(
                    id,
                    title
                )
                VALUES (
                    77,
                    'partial body'
                )
                """
            )
            connection.commit()

        raise RuntimeError(
            "fixture body failure"
        )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=make_temp_data_dir(tmp_path / "data"),
            zotero_body_importer=(
                failing_body
            ),
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime,
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as error:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )

    assert error.value.error_code == (
        "zotero_direction_b_"
        "body_import_failed"
    )

    assert db_path.read_bytes() == before

    with sqlite3.connect(
        db_path
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_confirmation_is_bound_to_target_database(
    tmp_path,
    monkeypatch,
):
    db_one = make_temp_db(
        tmp_path / "one"
    )
    db_two = make_temp_db(
        tmp_path / "two"
    )

    install_constant_preview(
        monkeypatch
    )

    runtime_one = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_one,
            data_dir=tmp_path / "data-one",
        )
    )

    runtime_two = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_two,
            data_dir=tmp_path / "data-two",
            zotero_body_importer=(
                body_importer
            ),
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime_one,
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as error:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime_two,
        )

    assert error.value.error_code == (
        "zotero_import_target_changed"
    )

    with sqlite3.connect(
        db_two
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_production_is_blocked_before_preview_or_body(
    monkeypatch,
):
    calls = {
        "preview": 0,
        "body": 0,
    }

    def forbidden_preview(
        *_args,
        **_kwargs,
    ):
        calls["preview"] += 1
        raise AssertionError(
            "preview must not run"
        )

    def forbidden_body(
        **_kwargs,
    ):
        calls["body"] += 1
        raise AssertionError(
            "body must not run"
        )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        forbidden_preview,
    )

    with pytest.raises(
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_temp_db(
                preview_token="unused",
                db_path=DEFAULT_DB_PATH,
                data_dir=DATA_DIR,
                body_importer=forbidden_body,
            )
        )

    assert error.value.code == (
        "zotero_direction_b_"
        "production_not_enabled"
    )

    assert calls == {
        "preview": 0,
        "body": 0,
    }



def test_duplicate_preview_is_blocked_without_confirmation(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    payload = preview_payload()

    payload["duplicate_check"] = {
        "duplicate_found": True,
        "duplicate_confidence": "high",
        "existing_documents": [
            {
                "document_id": 5,
                "duplicate_reasons": [
                    "same_zotero_attachment_key",
                ],
            }
        ],
    }

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=tmp_path / "data",
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as error:
        (
            chat_tool_service
            .register_zotero_selected_book_import_preview(
                preview_token="p" * 40,
                runtime=runtime,
            )
        )

    assert error.value.error_code == (
        "zotero_import_duplicate_"
        "requires_review"
    )


def test_duplicate_appearing_after_confirmation_blocks_body(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )

    calls = {
        "resolve": 0,
        "body": 0,
    }

    def resolver(
        *_args,
        **_kwargs,
    ):
        calls["resolve"] += 1

        payload = preview_payload()

        if calls["resolve"] >= 2:
            payload[
                "duplicate_check"
            ] = {
                "duplicate_found": True,
                "duplicate_confidence": "high",
                "existing_documents": [
                    {
                        "document_id": 8,
                        "duplicate_reasons": [
                            "same_zotero_item_key",
                        ],
                    }
                ],
            }

        return payload

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        resolver,
    )

    def source_resolver(
        *_args,
        **_kwargs,
    ):
        return (
            resolver(
                *_args,
                **_kwargs,
            ),
            Path(__file__).resolve(),
        )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_source",
        source_resolver,
    )

    def forbidden_body(
        **_kwargs,
    ):
        calls["body"] += 1
        raise AssertionError(
            "body importer must not run"
        )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=make_temp_data_dir(tmp_path / "data"),
            zotero_body_importer=(
                forbidden_body
            ),
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime,
        )
    )

    with pytest.raises(
        chat_tool_service.ChatToolError
    ) as error:
        chat_tool_service.import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )

    assert error.value.error_code == (
        "zotero_import_duplicate_"
        "requires_review"
    )

    assert calls["body"] == 0

    with sqlite3.connect(
        db_path
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_chat_bridge_allows_production_preview_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_db = tmp_path / "production.db"
    production_db.write_bytes(b"fixture")

    # Treat the isolated fixture as the canonical production DB for
    # this test. No real production data is touched.
    monkeypatch.setattr(
        chat_tool_service,
        "DEFAULT_DB_PATH",
        production_db,
    )

    calls = {
        "preview": 0,
    }

    def ready_preview(
        preview_token,
        *,
        expected_db_path=None,
        **_kwargs,
    ):
        calls["preview"] += 1

        assert preview_token == "production-preview"
        assert Path(expected_db_path).resolve(
            strict=False
        ) == production_db.resolve(
            strict=False
        )

        return {
            "status": "ready",
            "zotero_item": {
                "zotero_item_key": "BOOKKEY1",
                "title": "Production Preview Book",
                "item_type": "book",
            },
            "selected_attachment": {
                "zotero_attachment_key": "PDFKEY1",
                "page_count": 12,
            },
            "annotation_count": 1,
            "child_note_count": 2,
            "duplicate_check": {
                "duplicate_found": False,
            },
        }

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        ready_preview,
    )

    result = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="production-preview",
            runtime=chat_tool_service.ChatToolRuntime(
                db_path=production_db,
                data_dir=tmp_path / "production-data",
            ),
        )
    )

    assert calls["preview"] == 1
    assert result["status"] == "ok"
    assert result["document_type"] == "book"
    assert result["duplicate_status"] == "not_detected"
    assert result["confirmation_token"]
    assert (
        result["confirmation_expires_in_seconds"]
        == chat_tool_service.IMPORT_CONFIRMATION_TTL_SECONDS
    )


def test_preview_token_is_bound_to_target_database(
    tmp_path,
):
    (
        zotero_selected_book_preview_service
        ._clear_preview_cache_for_tests()
    )

    first = tmp_path / "one.db"
    second = tmp_path / "two.db"

    first.write_bytes(b"")
    second.write_bytes(b"")

    token = "target-bound-preview"

    (
        zotero_selected_book_preview_service
        ._store_preview(
            token,
            {
                "created_at": 1000.0,
                "expires_at": 2000.0,
                "source_revision_fingerprint": (
                    "x" * 64
                ),
                "zotero_item_key": "BOOKKEY1",
                "zotero_attachment_key": (
                    "PDFKEY1"
                ),
                "snapshot_path": str(
                    tmp_path
                    / "unused.sqlite"
                ),
                "db_path": str(first),
                "config": {},
            },
            now_ts=1000.0,
        )
    )

    try:
        with pytest.raises(
            zotero_selected_book_preview_service
            .ZoteroSelectedBookPreviewError
        ) as error:
            (
                zotero_selected_book_preview_service
                .resolve_selected_book_preview_token(
                    token,
                    now_ts=1001.0,
                    expected_db_path=second,
                )
            )

        assert error.value.code == (
            "preview_target_db_mismatch"
        )

    finally:
        (
            zotero_selected_book_preview_service
            ._clear_preview_cache_for_tests()
        )


def _public_chat_zotero_ready_preview(
    *,
    duplicate: bool = False,
    existing_documents: list[dict] | None = None,
) -> dict:
    return {
        "status": "ready",
        "zotero_item": {"title": "Selected Zotero Book"},
        "attachment_choices": [
            {
                "zotero_attachment_key": "EFGH5678",
                "file_name": "selected.pdf",
                "path_exists": True,
                "path_status": "available",
                "content_type": "application/pdf",
                "date_modified": "2026-07-26",
                "version": 2,
            }
        ],
        "selected_attachment": {
            "zotero_attachment_key": "EFGH5678",
            "file_name": "selected.pdf",
            "path_exists": True,
            "path_status": "available",
            "content_type": "application/pdf",
            "date_modified": "2026-07-26",
            "version": 2,
            "pdf_sha256": "a" * 64,
            "page_count": 12,
        },
        "annotation_count": 4,
        "child_note_count": 2,
        "duplicate_check": {
            "duplicate_found": duplicate,
            "existing_documents": existing_documents or [],
        },
        "warnings": [],
        "preview_token": "internal-b2-token",
    }


def test_public_chat_zotero_preview_forwards_keys_and_sanitizes_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")
    calls: list[dict] = []

    def build(**kwargs):
        calls.append(kwargs)
        return {
            "status": "attachment_choice_required",
            "zotero_item": {"title": "Choose a PDF"},
            "attachment_choices": [
                {
                    "zotero_attachment_key": "ATTACH01",
                    "file_name": "choice.pdf",
                    "path_exists": True,
                    "path_status": "available",
                    "content_type": "application/pdf",
                    "date_modified": "2026-07-26",
                    "version": 1,
                    "resolved_pdf_path": r"C:\private\choice.pdf",
                }
            ],
            "annotation_count": None,
            "child_note_count": None,
            "warnings": [],
        }

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        build,
    )
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=database,
        data_dir=tmp_path / "data",
    )
    result = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        zotero_attachment_key="ATTACH01",
        runtime=runtime,
    )
    assert calls == [
        {
            "zotero_item_key": "ABCD1234",
            "zotero_attachment_key": "ATTACH01",
            "db_path": database,
            "issue_token": True,
        }
    ]
    assert result["status"] == "ok"
    assert result["duplicate_status"] == "not_evaluated"
    assert result["confirmation_token"] is None
    assert result["attachment_choices"][0] == {
        "zotero_attachment_key": "ATTACH01",
        "file_name": "choice.pdf",
        "path_exists": True,
        "path_status": "available",
        "content_type": "application/pdf",
        "date_modified": "2026-07-26",
        "version": 1,
    }
    assert "C:\\" not in str(result)


def test_public_chat_zotero_temp_preview_registers_chat_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")
    registered: list[dict] = []
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        lambda **_kwargs: _public_chat_zotero_ready_preview(),
    )

    def register(**kwargs):
        registered.append(kwargs)
        return {
            "status": "ok",
            "source_type": "zotero_selected_book",
            "duplicate_status": "not_detected",
            "confirmation_token": "chat-confirmation-token",
            "confirmation_expires_in_seconds": 600,
        }

    monkeypatch.setattr(
        chat_tool_service,
        "register_zotero_selected_book_import_preview",
        register,
    )
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=database,
        data_dir=tmp_path / "data",
    )
    result = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=runtime,
    )
    assert registered == [
        {
            "preview_token": "internal-b2-token",
            "runtime": runtime,
        }
    ]
    assert result["confirmation_token"] == "chat-confirmation-token"
    assert result["confirmation_expires_in_seconds"] == 600
    assert result["estimated_pages"] == 12
    assert result["estimated_chunks"] == 36
    assert "internal-b2-token" not in str(result)


def test_public_chat_zotero_production_registers_and_duplicate_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations: list[dict] = []

    previews = [
        _public_chat_zotero_ready_preview(),
        _public_chat_zotero_ready_preview(
            duplicate=True,
            existing_documents=[
                {
                    "document_id": 17,
                    "pdf_path": r"C:\private.pdf",
                }
            ],
        ),
    ]

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        lambda **_kwargs: previews.pop(0),
    )

    def register_preview(**kwargs):
        registrations.append(kwargs)
        return {
            "status": "ok",
            "source_type": "zotero_selected_book",
            "title": "Selected Test Book",
            "document_type": "book",
            "estimated_pages": 12,
            "annotation_count": 1,
            "child_note_count": 1,
            "duplicate_status": "not_detected",
            "confirmation_token": "chat-confirmation-token",
            "confirmation_expires_in_seconds": 600,
        }

    monkeypatch.setattr(
        chat_tool_service,
        "register_zotero_selected_book_import_preview",
        register_preview,
    )

    production_runtime = chat_tool_service.ChatToolRuntime(
        db_path=DEFAULT_DB_PATH,
        data_dir=tmp_path / "production-data",
    )

    production = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=production_runtime,
    )

    assert production["confirmation_token"] == (
        "chat-confirmation-token"
    )
    assert production[
        "confirmation_expires_in_seconds"
    ] == 600
    assert (
        "zotero_direction_b_production_not_enabled"
        not in production["warnings"]
    )

    assert len(registrations) == 1
    assert registrations[0]["preview_token"] == (
        "internal-b2-token"
    )
    assert registrations[0]["runtime"] is (
        production_runtime
    )

    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")

    duplicate = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=chat_tool_service.ChatToolRuntime(
            db_path=database,
            data_dir=tmp_path / "data",
        ),
    )

    assert duplicate["duplicate_status"] == "duplicate"
    assert duplicate["existing_document_id"] == 17
    assert duplicate["confirmation_token"] is None

    # Duplicate protection returns before confirmation registration.
    assert len(registrations) == 1
    assert "private.pdf" not in str(duplicate)


def test_public_chat_zotero_preview_filters_private_error_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")

    def fail(**_kwargs):
        raise zotero_selected_book_preview_service.ZoteroSelectedBookPreviewError(
            status_code=422,
            code="pdf_file_missing",
            message="The selected PDF is missing.",
            details={
                "zotero_item_key": "ABCD1234",
                "resolved_pdf_path": r"C:\Users\ROG\private.pdf",
                "snapshot_path": r"D:\private\zotero.sqlite",
                "db_path": r"D:\private\research.db",
                "zotero_data_dir": r"D:\private\Zotero",
                "zotero_storage_root": r"D:\private\storage",
            },
        )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        fail,
    )
    with pytest.raises(chat_tool_service.ChatToolError) as caught:
        chat_tool_service.import_preview(
            source_type="zotero_selected_book",
            zotero_item_key="ABCD1234",
            runtime=chat_tool_service.ChatToolRuntime(
                db_path=database,
                data_dir=tmp_path / "data",
            ),
        )
    assert caught.value.error_code == "pdf_file_missing"
    assert caught.value.details == {"zotero_item_key": "ABCD1234"}
    assert "private" not in str(caught.value.details)



def test_default_core_body_importer_is_used_without_runtime_override(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from app.services import (
        book_import_service,
    )
    from app.services.pdf_parser_backends import (
        PYMUPDF_BACKEND,
    )

    db_path = make_temp_db(
        tmp_path / "db"
    )

    pdf_path = (
        tmp_path
        / "selected-book.pdf"
    )

    pdf_path.write_bytes(
        b"%PDF-1.4\nB4 core body fixture\n"
    )

    payload = preview_payload()

    payload[
        "selected_attachment"
    ][
        "pdf_sha256"
    ] = hashlib.sha256(
        pdf_path.read_bytes()
    ).hexdigest()

    payload["source_revision"] = {
        "fingerprint": "r" * 64,
    }

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_source",
        lambda *_args, **_kwargs: (
            payload,
            pdf_path,
        ),
    )

    calls = {
        "prepare": 0,
        "apply": 0,
    }

    def fake_prepare(
        source,
        *,
        title,
        backend,
        **_kwargs,
    ):
        calls["prepare"] += 1

        assert Path(source) == pdf_path
        assert title == "Selected Book"
        assert backend == PYMUPDF_BACKEND

        return SimpleNamespace(
            backend=backend,
        )

    def fake_apply(
        prepared,
        *,
        db_path,
        backup,
    ):
        calls["apply"] += 1

        assert prepared.backend == (
            PYMUPDF_BACKEND
        )

        assert backup is False

        with sqlite3.connect(
            db_path
        ) as connection:
            connection.execute(
                """
                INSERT INTO documents(
                    id,
                    title,
                    document_type,
                    content_layer,
                    created_at,
                    read_status
                )
                VALUES (
                    6,
                    'Selected Book',
                    'book',
                    'evidence',
                    '2026-07-26',
                    'read'
                )
                """
            )

            connection.execute(
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
                VALUES (
                    601,
                    6,
                    0,
                    'chapter',
                    'Original selected text',
                    'core-body-chunk',
                    12,
                    12
                )
                """
            )

            connection.commit()

        return {
            "status": "APPLIED",
            "document_id": 6,
            "inserted_chunks": 1,
            "inserted_chapters": 1,
            "book_safety_decision": (
                "allowed"
            ),
        }

    monkeypatch.setattr(
        book_import_service,
        "prepare_book_import",
        fake_prepare,
    )

    monkeypatch.setattr(
        book_import_service,
        "apply_prepared_book_import",
        fake_apply,
    )

    runtime = (
        chat_tool_service
        .ChatToolRuntime(
            db_path=db_path,
            data_dir=make_temp_data_dir(tmp_path / "data"),
            # Intentionally NO
            # zotero_body_importer override.
        )
    )

    bridge = (
        chat_tool_service
        .register_zotero_selected_book_import_preview(
            preview_token="p" * 40,
            runtime=runtime,
        )
    )

    result = (
        chat_tool_service
        .import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )
    )

    assert result[
        "status"
    ] == "committed"

    assert result[
        "document_id"
    ] == 6

    assert result[
        "chunk_count"
    ] == 1

    assert calls == {
        "prepare": 1,
        "apply": 1,
    }

    with sqlite3.connect(
        db_path
    ) as connection:
        connection.row_factory = (
            sqlite3.Row
        )

        document = connection.execute(
            """
            SELECT
                zotero_key
            FROM documents
            WHERE id = 6
            """
        ).fetchone()

        assert document[
            "zotero_key"
        ] == "BOOKKEY1"

        source = connection.execute(
            """
            SELECT
                source_type,
                zotero_item_key,
                zotero_attachment_key,
                source_trace_json
            FROM document_sources
            WHERE document_id = 6
            """
        ).fetchone()

        assert source[
            "source_type"
        ] == "zotero_pdf"

        assert source[
            "zotero_item_key"
        ] == "BOOKKEY1"

        assert source[
            "zotero_attachment_key"
        ] == "PDFKEY1"

        assert (
            '"zotero_library_id": 1'
            in source[
                "source_trace_json"
            ]
        )

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM personal_notes
            WHERE document_id = 6
            """
        ).fetchone()[0] == 2


def test_internal_pdf_source_hash_guard(
    tmp_path,
    monkeypatch,
):
    (
        zotero_selected_book_preview_service
        ._clear_preview_cache_for_tests()
    )

    db_path = tmp_path / "research.db"
    db_path.write_bytes(b"")

    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(
        b"%PDF-1.4\noriginal\n"
    )

    payload = preview_payload()

    payload[
        "selected_attachment"
    ][
        "pdf_sha256"
    ] = hashlib.sha256(
        pdf_path.read_bytes()
    ).hexdigest()

    token = "private-source-token"

    (
        zotero_selected_book_preview_service
        ._store_preview(
            token,
            {
                "created_at": 1000.0,
                "expires_at": 2000.0,
                "source_revision_fingerprint": (
                    "x" * 64
                ),
                "zotero_item_key": "BOOKKEY1",
                "zotero_attachment_key": (
                    "PDFKEY1"
                ),
                "snapshot_path": str(
                    tmp_path
                    / "unused.sqlite"
                ),
                "db_path": str(db_path),
                "resolved_pdf_path": str(
                    pdf_path
                ),
                "config": {},
            },
            now_ts=1000.0,
        )
    )

    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )

    try:
        (
            resolved_preview,
            resolved_path,
        ) = (
            zotero_selected_book_preview_service
            .resolve_selected_book_preview_source(
                token,
                now_ts=1001.0,
                expected_db_path=db_path,
            )
        )

        assert resolved_preview is payload
        assert resolved_path == (
            pdf_path.resolve(
                strict=False
            )
        )

        pdf_path.write_bytes(
            b"%PDF-1.4\nchanged\n"
        )

        with pytest.raises(
            zotero_selected_book_preview_service
            .ZoteroSelectedBookPreviewError
        ) as error:
            (
                zotero_selected_book_preview_service
                .resolve_selected_book_preview_source(
                    token,
                    now_ts=1002.0,
                    expected_db_path=db_path,
                )
            )

        assert error.value.code == (
            "preview_source_drift"
        )

        assert error.value.details[
            "cause_code"
        ] == (
            "resolved_pdf_hash_changed"
        )

    finally:
        (
            zotero_selected_book_preview_service
            ._clear_preview_cache_for_tests()
        )
