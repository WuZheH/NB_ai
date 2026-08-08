from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.domains.retrieval import note_vector_index
from app.domains.retrieval.result_contracts import (
    NotebookFragment,
    OpenTarget,
)
from app.services import (
    chat_tool_service,
    vector_store_service,
    zotero_direction_b_import_service,
    zotero_selected_book_preview_service,
)
from app.services.import_operation_journal import (
    ImportOperationJournalStore,
    JournalConflictError,
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
    def fts_sync(**kwargs):
        source_db_path = Path(kwargs["research_db_path"])
        index_path = Path(kwargs["index_path"])
        manifest_path = Path(kwargs["manifest_path"])
        manifest_path.write_text(
            "{\n"
            f'  "production_db_sha256": "{hashlib.sha256(source_db_path.read_bytes()).hexdigest()}",\n'
            f'  "index_content_hash": "{hashlib.sha256(index_path.read_bytes()).hexdigest()}"\n'
            "}\n",
            encoding="utf-8",
        )
        return {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        }

    monkeypatch.setattr(
        fts_index_service,
        "upsert_document_retrieval_fts",
        fts_sync,
    )
    monkeypatch.setattr(
        vector_store_service,
        "sync_affected_passage_embeddings",
        lambda *_args, **kwargs: _fixture_passage_sync(**kwargs),
    )
    monkeypatch.setattr(
        vector_store_service,
        "inspect_document_vector_impact",
        lambda *, passage_source_ids, **_kwargs: {
            "status": "ok",
            "passage_vector_count": len(passage_source_ids),
            "object_vector_count": 0,
        },
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service.fts_status_service,
        "get_index_status",
        lambda **_kwargs: {
            "status": "ready",
            "ready": True,
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


def _fixture_passage_sync(
    *,
    store_path,
    manifest_path,
    **_kwargs,
):
    Path(store_path).mkdir(parents=True, exist_ok=True)
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text("{}\n", encoding="utf-8")
    return {
        "scope": "affected_source_ids_only",
        "full_rebuild_allowed": False,
        "delete_orphans_allowed": False,
        "lancedb_writes_performed": True,
    }


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
            "item_type": "book",
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
        "source_revision": {
            "fingerprint": "f" * 64,
        },
        "extractor_strategy": "native_text",
        "estimated_pages": 120,
        "estimated_chunks": 12,
        "extraction_ready": True,
        "blockers": [],
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
        "already_completed": False,
        "replayed_receipt": False,
        "operation_in_progress": False,
        "token_consumed": True,
        "writes_performed": True,
        "safe_to_retry": False,
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

    replay = (
        chat_tool_service
        .import_document(
            confirmation_token=bridge[
                "confirmation_token"
            ],
            confirmed=True,
            runtime=runtime,
        )
    )

    assert replay == {
        "status": "committed",
        "document_id": 1,
        "title": "Selected Book",
        "document_type": "book",
        "chunk_count": 1,
        "duplicate_status": (
            "not_detected"
        ),
        "error_code": None,
        "already_completed": True,
        "replayed_receipt": True,
        "operation_in_progress": False,
        "token_consumed": True,
            "writes_performed": True,
        "safe_to_retry": False,
    }

    with sqlite3.connect(
        db_path
    ) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM documents
            WHERE id = 1
            """
        ).fetchone()[0] == 1

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM knowledge_chunks
            WHERE document_id = 1
            """
        ).fetchone()[0] == 1

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM personal_notes
            WHERE document_id = 1
            """
        ).fetchone()[0] == 2

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM note_evidence_links
            """
        ).fetchone()[0] == 2


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


def test_confirmation_preserves_supported_journal_article_type(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(tmp_path / "db")
    payload = preview_payload()
    payload["zotero_item"]["item_type"] = "journalArticle"
    payload["_preview_audit"] = {
        "previewed_at": "2026-07-30T01:00:00+00:00",
    }
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: payload,
    )
    result = chat_tool_service.register_zotero_selected_book_import_preview(
        preview_token="article-preview-token",
        runtime=chat_tool_service.ChatToolRuntime(
            db_path=db_path,
            data_dir=tmp_path / "data",
        ),
    )
    assert result["item_type"] == "journalArticle"
    assert result["document_type"] == "journalArticle"
    assert result["confirmation_token"]
    assert "zotero_attachment_key" not in result
    record = next(iter(chat_tool_service._IMPORT_CONFIRMATIONS.values()))
    assert record.previewed_at == "2026-07-30T01:00:00+00:00"
    assert len(record.confirmation_token_fingerprint) == 64


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
    production_data = tmp_path / "production-data"

    monkeypatch.setattr(
        chat_tool_service,
        "DEFAULT_DB_PATH",
        production_db,
    )
    monkeypatch.setattr(
        chat_tool_service,
        "DATA_DIR",
        production_data,
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
                "pdf_sha256": "a" * 64,
                "page_count": 12,
            },
            "annotation_count": 1,
            "child_note_count": 2,
            "duplicate_check": {
                "duplicate_found": False,
            },
                "source_revision": {
                    "fingerprint": "d" * 64,
                },
                "extractor_strategy": "native_text",
                "estimated_chunks": 4,
                "extraction_ready": True,
                "blockers": [],
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
                data_dir=production_data,
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
        "zotero_item": {
            "zotero_item_key": "ABCD1234",
            "title": "Selected Zotero Book",
            "item_type": "book",
        },
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
        "extractor_strategy": "native_text",
        "estimated_pages": 12,
        "estimated_chunks": 4,
        "chapter_count": 3,
        "page_marker_count": 12,
        "detection_method": "pdf_outline",
        "binding_rate": 1.0,
        "extraction_ready": True,
        "blockers": [],
        "source_revision": {
            "fingerprint": "e" * 64,
        },
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
            "zotero_item": {
                "zotero_item_key": "ABCD1234",
                "title": "Choose a PDF",
                "item_type": "book",
            },
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
    assert result["estimated_chunks"] == 4
    assert "chunk_count_not_precomputed_by_preview" not in result["warnings"]


def test_public_chat_journal_article_preview_uses_real_document_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")
    payload = _public_chat_zotero_ready_preview()
    payload["zotero_item"]["item_type"] = "journalArticle"
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        lambda **_kwargs: payload,
    )
    monkeypatch.setattr(
        chat_tool_service,
        "register_zotero_selected_book_import_preview",
        lambda **_kwargs: {
            "duplicate_status": "not_detected",
            "confirmation_token": "article-confirmation-token",
            "confirmation_expires_in_seconds": 600,
        },
    )
    result = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=chat_tool_service.ChatToolRuntime(
            db_path=database,
            data_dir=tmp_path / "data",
        ),
    )
    assert result["item_type"] == "journalArticle"
    assert result["document_type"] == "journalArticle"
    assert result["parent_key"] == "ABCD1234"
    assert result["zotero_item_key"] == "ABCD1234"
    assert result["zotero_attachment_key"] == "EFGH5678"
    assert result["estimated_chunks"] == 4
    assert result["chapter_count"] == 3
    assert result["page_marker_count"] == 12
    assert result["detection_method"] == "pdf_outline"
    assert result["binding_rate"] == 1.0
    assert result["confirmation_token"] == "article-confirmation-token"


def test_public_chat_extraction_blocker_never_registers_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    database.write_bytes(b"fixture")
    payload = _public_chat_zotero_ready_preview()
    payload.update(
        {
            "extractor_strategy": "high_quality_pdf_to_markdown",
            "estimated_pages": 0,
            "estimated_chunks": 0,
            "extraction_ready": False,
            "blockers": [{"code": "required_extraction_models_missing"}],
            "preview_token": None,
        }
    )
    monkeypatch.setattr(
        zotero_selected_book_preview_service,
        "build_selected_book_preview",
        lambda **_kwargs: payload,
    )
    monkeypatch.setattr(
        chat_tool_service,
        "register_zotero_selected_book_import_preview",
        lambda **_kwargs: pytest.fail("blocked preview must not register"),
    )
    result = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=chat_tool_service.ChatToolRuntime(
            db_path=database,
            data_dir=tmp_path / "data",
        ),
    )
    assert result["extraction_ready"] is False
    assert result["estimated_chunks"] == 0
    assert result["blockers"] == [
        {"code": "required_extraction_models_missing"}
    ]
    assert result["confirmation_token"] is None
    assert "internal-b2-token" not in str(result)


def test_public_chat_zotero_production_registers_and_duplicate_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_db = tmp_path / "production.db"
    production_db.write_bytes(b"fixture")
    production_data = tmp_path / "production-data"

    monkeypatch.setattr(
        chat_tool_service,
        "DEFAULT_DB_PATH",
        production_db,
    )
    monkeypatch.setattr(
        chat_tool_service,
        "DATA_DIR",
        production_data,
    )

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
            "title": "Selected Zotero Book",
            "item_type": "book",
            "document_type": "book",
            "estimated_pages": 12,
            "annotation_count": 4,
            "child_note_count": 2,
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
        db_path=production_db,
        data_dir=production_data,
    )

    production = chat_tool_service.import_preview(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
        runtime=production_runtime,
    )

    assert production["item_type"] == "book"
    assert production["document_type"] == "book"
    assert production["confirmation_token"] == (
        "chat-confirmation-token"
    )
    assert len(registrations) == 1

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
        document_type,
    ):
        calls["apply"] += 1

        assert prepared.backend == (
            PYMUPDF_BACKEND
        )

        assert backup is False
        assert document_type == "book"

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

def test_confirmation_is_bound_to_target_data_dir(
    tmp_path,
    monkeypatch,
):
    db_path = make_temp_db(
        tmp_path / "db"
    )
    install_constant_preview(monkeypatch)

    runtime_one = chat_tool_service.ChatToolRuntime(
        db_path=db_path,
        data_dir=tmp_path / "data-one",
    )
    runtime_two = chat_tool_service.ChatToolRuntime(
        db_path=db_path,
        data_dir=tmp_path / "data-two",
        zotero_body_importer=body_importer,
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

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def _install_production_shaped_runtime(
    tmp_path,
    monkeypatch,
    *,
    fts_ready: bool,
):
    db_path = make_temp_db(
        tmp_path / "production-db"
    )
    data_dir = make_temp_data_dir(
        tmp_path / "production-data"
    )

    install_constant_preview(monkeypatch)

    fts_path = (
        data_dir
        / "search_index"
        / "retrieval_fts_v1.db"
    )
    fts_manifest = (
        data_dir
        / "search_index"
        / "retrieval_fts_v1_manifest.json"
    )
    vector_store = (
        data_dir
        / "vector_store"
        / "lancedb"
    )
    vector_manifest = (
        data_dir
        / "vector_store"
        / "vector_manifest.json"
    )
    note_vector_dir = (
        data_dir
        / "vector_store"
        / "zotero_user_notes_v1"
    )
    note_vector_index.build_zotero_note_vectors(
        index_dir=note_vector_dir,
        fragments=[],
        encode_text=lambda _text: [1.0, 0.0],
    )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "DEFAULT_DB_PATH",
        db_path,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "DATA_DIR",
        data_dir,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "DEFAULT_INDEX_PATH",
        fts_path,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "DEFAULT_MANIFEST_PATH",
        fts_manifest,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "LANCEDB_DIR",
        vector_store,
    )
    monkeypatch.setattr(
        vector_store_service,
        "MANIFEST_PATH",
        vector_manifest,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service
        .zotero_direction_b_commit_service,
        "DEFAULT_DB_PATH",
        db_path,
    )

    observed = {
        "fts_source": None,
        "passage_source": None,
        "note_source": None,
    }

    def fts_sync(
        *,
        research_db_path,
        index_path,
        manifest_path,
        **_kwargs,
    ):
        source = Path(
            research_db_path
        ).resolve(strict=False)
        observed["fts_source"] = source

        target_index = Path(index_path)
        target_index.write_bytes(
            target_index.read_bytes()
            + b"\nC4-production-staged"
        )

        digest = hashlib.sha256(
            source.read_bytes()
        ).hexdigest()

        Path(manifest_path).write_text(
            '{"production_db_sha256":"'
            + digest
            + '"}\n',
            encoding="utf-8",
        )

        return {
            "status": "ready",
            "full_rebuild_performed": False,
            "production_db_write_performed": False,
        }

    def passage_sync(
        *_args,
        source_db_path,
        store_path,
        manifest_path,
        **_kwargs,
    ):
        observed["passage_source"] = Path(
            source_db_path
        ).resolve(strict=False)
        Path(store_path).mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text("{}\n", encoding="utf-8")
        return {
            "scope": "affected_source_ids_only",
            "full_rebuild_allowed": False,
            "delete_orphans_allowed": False,
            "lancedb_writes_performed": True,
        }

    def note_sync(
        *_args,
        source_db_path,
        **_kwargs,
    ):
        observed["note_source"] = Path(
            source_db_path
        ).resolve(strict=False)
        return {
            "scope": "document_only",
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
            "lancedb_writes_performed": False,
        }

    monkeypatch.setattr(
        fts_index_service,
        "upsert_document_retrieval_fts",
        fts_sync,
    )
    monkeypatch.setattr(
        vector_store_service,
        "sync_affected_passage_embeddings",
        passage_sync,
    )
    monkeypatch.setattr(
        vector_store_service,
        "sync_document_note_embeddings",
        note_sync,
    )
    def fts_status(**kwargs):
        target = Path(kwargs["index_path"]).resolve(strict=False)
        if target != fts_path.resolve(strict=False):
            return {"status": "ready", "ready": True}
        return {
            "status": "ready" if fts_ready else "source_drift",
            "ready": fts_ready,
        }

    monkeypatch.setattr(
        zotero_direction_b_import_service.fts_status_service,
        "get_index_status",
        fts_status,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_native_note_fragments_for_document",
        lambda **_kwargs: [],
    )

    return {
        "db_path": db_path,
        "data_dir": data_dir,
        "fts_path": fts_path,
        "fts_manifest": fts_manifest,
        "note_vector_dir": note_vector_dir,
        "observed": observed,
    }


def test_production_shaped_import_uses_post_write_snapshot(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )

    result = (
        zotero_direction_b_import_service
        .commit_selected_book_import_to_production(
            preview_token="p" * 40,
            body_importer=body_importer,
        )
    )

    db_path = fixture["db_path"].resolve(
        strict=False
    )
    observed = fixture["observed"]

    assert result["status"] == "committed"
    assert result["persistence_scope"] == "production"
    assert result["production_data_modified"] is True

    assert observed["fts_source"] != db_path
    assert observed["passage_source"] != db_path
    assert observed["note_source"] != db_path

    assert observed["fts_source"] == (
        observed["passage_source"]
    )
    assert observed["fts_source"] == (
        observed["note_source"]
    )

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 1


def _native_annotation_fragment(
    document_id: int | None,
    *,
    fragment_id: str = "native-comment-1",
) -> NotebookFragment:
    return NotebookFragment(
        fragment_id=fragment_id,
        source_type="zotero_annotation_comment",
        zotero_item_key="BOOKKEY1",
        zotero_attachment_key="PDFKEY1",
        zotero_annotation_key="ANN1",
        document_id=document_id,
        document_title="Fixture Book",
        document_type=(
            "book"
            if document_id is not None
            else None
        ),
        note_text="这个可以作为对数推入",
        selected_text="Original selected text.",
        content_hash="native-comment-hash",
        provenance=[
            {
                "store": "zotero_snapshot",
                "table": "itemAnnotations",
                "row_id": 1,
            }
        ],
        open_target=OpenTarget(),
    )


def test_production_import_attaches_only_native_note_vector_scope(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )
    note_vector_dir = fixture["note_vector_dir"]
    unrelated = _native_annotation_fragment(
        22,
        fragment_id="unrelated-native-comment",
    ).model_copy(
        update={
            "zotero_annotation_key": "UNRELATED1",
            "note_text": "unrelated note",
            "content_hash": "unrelated-note-hash",
        }
    )
    note_vector_index.build_zotero_note_vectors(
        index_dir=note_vector_dir,
        fragments=[
            _native_annotation_fragment(None),
            unrelated,
        ],
        encode_text=lambda _text: [1.0, 0.0],
    )
    _manifest, before_entries = (
        note_vector_index._load_existing(
            note_vector_dir,
            required=True,
        )
    )
    unrelated_before = next(
        entry
        for entry in before_entries
        if entry["fragment_id"]
        == "unrelated-native-comment"
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_native_note_fragments_for_document",
        lambda **_kwargs: [
            _native_annotation_fragment(1)
        ],
    )
    monkeypatch.setattr(
        note_vector_index,
        "_default_encoder",
        lambda: (
            lambda _text: (
                (_ for _ in ()).throw(
                    AssertionError(
                        "metadata-only attach must reuse embedding"
                    )
                )
            )
        ),
    )

    result = (
        zotero_direction_b_import_service
        .commit_selected_book_import_to_production(
            preview_token="p" * 40,
            body_importer=body_importer,
        )
    )

    native_sync = result["native_note_vector_sync"]
    assert native_sync["scope"] == "affected_fragment_ids_only"
    assert native_sync["scoped_entry_count_after"] == 1
    assert native_sync["recomputed_count"] == 0
    assert native_sync["full_rebuild_performed"] is False
    assert native_sync["orphan_delete_performed"] is False

    impact = (
        note_vector_index
        .inspect_zotero_note_vector_document_impact(
            1,
            index_dir=note_vector_dir,
        )
    )
    assert impact["document_entry_count"] == 1
    _manifest, after_entries = (
        note_vector_index._load_existing(
            note_vector_dir,
            required=True,
        )
    )
    unrelated_after = next(
        entry
        for entry in after_entries
        if entry["fragment_id"]
        == "unrelated-native-comment"
    )
    assert unrelated_after == unrelated_before


def test_production_final_verify_failure_restores_db_and_derived_exactly(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=False,
    )

    db_path = fixture["db_path"]
    fts_path = fixture["fts_path"]
    manifest_path = fixture["fts_manifest"]

    before_db = db_path.read_bytes()
    before_fts = fts_path.read_bytes()
    before_manifest = manifest_path.read_bytes()

    with pytest.raises(
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert error.value.code == (
        "zotero_direction_b_"
        "production_final_verify_failed"
    )

    assert db_path.read_bytes() == before_db
    assert fts_path.read_bytes() == before_fts
    assert manifest_path.read_bytes() == before_manifest


def test_production_db_rollback_failure_has_priority_and_retains_backup(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=False,
    )
    db_path = fixture["db_path"]

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_rollback_copy",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                RuntimeError("forced db restore failure")
            )
        ),
    )

    with pytest.raises(
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert error.value.code == (
        "zotero_direction_b_"
        "production_db_rollback_failed"
    )

    retained = list(
        db_path.parent.glob(
            f".{db_path.name}."
            "direction-b-rollback-*.sqlite"
        )
    )
    assert retained

    for item in retained:
        item.unlink()


_DIRECTION_B_FORWARD_STAGES = [
    "body_import_started",
    "body_import_completed",
    "staging_snapshot_created",
    "staging_fts_started",
    "staging_fts_completed",
    "staging_vector_started",
    "staging_vector_completed",
    "derived_backup_started",
    "derived_backup_completed",
    "publish_started",
    "publish_completed",
    "final_verification_started",
    "final_verification_completed",
]


def _direction_temp_case(tmp_path, monkeypatch):
    db_path = make_temp_db(tmp_path / "db")
    data_dir = make_temp_data_dir(tmp_path / "data")
    install_constant_preview(monkeypatch)
    return db_path, data_dir


def _direction_commit(
    db_path,
    data_dir,
    *,
    callback=None,
    importer=body_importer,
):
    return (
        zotero_direction_b_import_service
        .commit_selected_book_import_to_temp_db(
            preview_token="p" * 40,
            db_path=db_path,
            data_dir=data_dir,
            body_importer=importer,
            stage_callback=callback,
        )
    )


def test_direction_b_stage_callback_success_order(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    stages = []
    result = _direction_commit(
        db_path,
        data_dir,
        callback=lambda stage, metadata: stages.append((stage, metadata)),
    )
    assert result["status"] == "committed"
    assert result["production_data_modified"] is False
    assert result["writes_performed"] is True
    assert [stage for stage, _metadata in stages] == (
        _DIRECTION_B_FORWARD_STAGES
    )
    assert all(
        not any(
            forbidden in metadata
            for forbidden in (
                "preview_token",
                "confirmation_token",
                "pdf_path",
                "db_path",
                "staging_path",
                "traceback",
            )
        )
        for _stage, metadata in stages
    )


def test_direction_b_failure_emits_rollback_stages(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    stages = []

    def failing_importer(**_kwargs):
        raise RuntimeError("fixture body failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ):
        _direction_commit(
            db_path,
            data_dir,
            callback=lambda stage, _metadata: stages.append(stage),
            importer=failing_importer,
        )
    assert stages == [
        "body_import_started",
        "rollback_started",
        "rollback_completed",
    ]


def test_stage_callback_failure_before_body_prevents_body_write(
    tmp_path,
    monkeypatch,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    calls = 0

    def importer(**kwargs):
        nonlocal calls
        calls += 1
        return body_importer(**kwargs)

    def callback(stage, _metadata):
        if stage == "body_import_started":
            raise RuntimeError("fixture callback failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ):
        _direction_commit(
            db_path,
            data_dir,
            callback=callback,
            importer=importer,
        )
    assert calls == 0
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def test_stage_callback_failure_after_body_restores_database(
    tmp_path,
    monkeypatch,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)

    def callback(stage, _metadata):
        if stage == "body_import_completed":
            raise RuntimeError("fixture callback failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir, callback=callback)
    assert error.value.details["rollback_completed"] is True
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


def _install_fts_mutation(monkeypatch, mutation):
    original = fts_index_service.upsert_document_retrieval_fts

    def wrapper(**kwargs):
        result = original(**kwargs)
        mutation(kwargs)
        return result

    monkeypatch.setattr(
        fts_index_service,
        "upsert_document_retrieval_fts",
        wrapper,
    )


def _assert_staging_fts_not_ready_blocks_publish(
    tmp_path,
    monkeypatch,
    *,
    status,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    before_db = db_path.read_bytes()
    publish_calls = []
    derived_restore_calls = []
    monkeypatch.setattr(
        zotero_direction_b_import_service.fts_status_service,
        "get_index_status",
        lambda **_kwargs: dict(status),
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: publish_calls.append(True),
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_derived_artifacts",
        lambda **_kwargs: derived_restore_calls.append(True),
    )

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)

    assert error.value.code == "zotero_direction_b_staging_validation_failed"
    assert (
        "staging_fts_not_ready"
        in error.value.details["missing_components"]
    )
    assert publish_calls == []
    assert derived_restore_calls == []
    assert db_path.read_bytes() == before_db


def test_staging_fts_stale_blocks_publish(tmp_path, monkeypatch):
    _assert_staging_fts_not_ready_blocks_publish(
        tmp_path,
        monkeypatch,
        status={"status": "stale", "ready": False},
    )


def test_staging_fts_ready_false_blocks_publish(tmp_path, monkeypatch):
    _assert_staging_fts_not_ready_blocks_publish(
        tmp_path,
        monkeypatch,
        status={"status": "ready", "ready": False},
    )


def test_missing_staging_fts_directory_blocks_publish(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)

    def remove_staging_fts(kwargs):
        Path(kwargs["index_path"]).unlink()
        Path(kwargs["manifest_path"]).unlink()
        Path(kwargs["index_path"]).parent.rmdir()

    _install_fts_mutation(monkeypatch, remove_staging_fts)
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)
    assert error.value.code == "zotero_direction_b_staging_validation_failed"
    assert "staging_fts_index" in error.value.details["missing_components"]


def test_missing_staging_fts_manifest_blocks_publish(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    _install_fts_mutation(
        monkeypatch,
        lambda kwargs: Path(kwargs["manifest_path"]).unlink(),
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)
    assert "staging_fts_manifest" in error.value.details["missing_components"]


def test_invalid_staging_fts_manifest_blocks_publish(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    _install_fts_mutation(
        monkeypatch,
        lambda kwargs: Path(kwargs["manifest_path"]).write_text(
            "not-json", encoding="utf-8"
        ),
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)
    assert (
        "staging_fts_manifest_invalid"
        in error.value.details["missing_components"]
    )


def test_missing_staging_vector_manifest_blocks_publish(
    tmp_path,
    monkeypatch,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    original = vector_store_service.sync_affected_passage_embeddings

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        Path(kwargs["manifest_path"]).unlink()
        return result

    monkeypatch.setattr(
        vector_store_service,
        "sync_affected_passage_embeddings",
        wrapper,
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)
    assert (
        "staging_vector_manifest"
        in error.value.details["missing_components"]
    )


def test_missing_required_staging_zotero_note_vector_directory_blocks_publish(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )
    before_db = fixture["db_path"].read_bytes()
    publish_calls = []
    original_verify = (
        zotero_direction_b_import_service._verify_staging_final_state
    )

    def remove_required_directory(**kwargs):
        zotero_direction_b_import_service._remove_generated_tree(
            kwargs["staging_zotero_note_vector_path"]
        )
        return original_verify(**kwargs)

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_verify_staging_final_state",
        remove_required_directory,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: publish_calls.append(True),
    )

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert error.value.code == "zotero_direction_b_staging_validation_failed"
    assert (
        "staging_zotero_note_vectors"
        in error.value.details["missing_components"]
    )
    assert publish_calls == []
    assert fixture["db_path"].read_bytes() == before_db


def test_missing_required_staging_zotero_note_manifest_blocks_publish(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )
    note_vector_index.build_zotero_note_vectors(
        index_dir=fixture["note_vector_dir"],
        fragments=[_native_annotation_fragment(None)],
        encode_text=lambda _text: [1.0, 0.0],
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_native_note_fragments_for_document",
        lambda **_kwargs: [_native_annotation_fragment(1)],
    )
    publish_calls = []
    observed_expected_counts = []
    original_verify = (
        zotero_direction_b_import_service._verify_staging_final_state
    )

    def remove_required_manifest(**kwargs):
        observed_expected_counts.append(
            kwargs["expected_native_note_vector_count"]
        )
        (
            kwargs["staging_zotero_note_vector_path"]
            / note_vector_index.MANIFEST_NAME
        ).unlink()
        return original_verify(**kwargs)

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_verify_staging_final_state",
        remove_required_manifest,
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: publish_calls.append(True),
    )

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert observed_expected_counts == [1]
    assert (
        "staging_zotero_note_manifest"
        in error.value.details["missing_components"]
    )
    assert publish_calls == []


def test_staging_db_sha_mismatch_blocks_publish(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)

    def mutate_snapshot(kwargs):
        with sqlite3.connect(kwargs["research_db_path"]) as connection:
            connection.execute(
                "UPDATE documents SET title = title || ' changed'"
            )
            connection.commit()

    _install_fts_mutation(monkeypatch, mutate_snapshot)
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(db_path, data_dir)
    assert (
        "staging_database_revision"
        in error.value.details["missing_components"]
    )


def test_staging_validation_failure_restores_db(tmp_path, monkeypatch):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    before = db_path.read_bytes()
    _install_fts_mutation(
        monkeypatch,
        lambda kwargs: Path(kwargs["manifest_path"]).unlink(),
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ):
        _direction_commit(db_path, data_dir)
    assert db_path.read_bytes() == before


def test_staging_validation_failure_does_not_touch_production_indexes(
    tmp_path,
    monkeypatch,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    production_fts = data_dir / "search_index" / "retrieval_fts_v1.db"
    production_manifest = (
        data_dir / "search_index" / "retrieval_fts_v1_manifest.json"
    )
    before = (production_fts.read_bytes(), production_manifest.read_bytes())
    _install_fts_mutation(
        monkeypatch,
        lambda kwargs: Path(kwargs["manifest_path"]).unlink(),
    )
    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ):
        _direction_commit(db_path, data_dir)
    assert (production_fts.read_bytes(), production_manifest.read_bytes()) == before


def _fixture_derived_fingerprints(fixture):
    data_dir = fixture["data_dir"]
    paths = {
        "fts_index": fixture["fts_path"],
        "fts_manifest": fixture["fts_manifest"],
        "vector_store": data_dir / "vector_store" / "lancedb",
        "vector_manifest": (
            data_dir / "vector_store" / "vector_manifest.json"
        ),
        "zotero_note_vectors": fixture["note_vector_dir"],
    }
    result = {}
    for name, path in paths.items():
        if path.is_file():
            result[name] = (
                "file",
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        elif path.is_dir():
            result[name] = (
                "dir",
                zotero_direction_b_import_service._tree_fingerprint(path),
            )
        else:
            result[name] = ("missing", None)
    return result


def test_publish_started_callback_failure_does_not_restore_or_touch_derived(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )
    before_db = fixture["db_path"].read_bytes()
    before_derived = _fixture_derived_fingerprints(fixture)
    publish_calls = []
    derived_restore_calls = []
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: publish_calls.append(True),
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_derived_artifacts",
        lambda **_kwargs: derived_restore_calls.append(True),
    )

    def callback(stage, _metadata):
        if stage == "publish_started":
            raise RuntimeError("fixture publish-start callback failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
                stage_callback=callback,
            )
        )

    assert publish_calls == []
    assert derived_restore_calls == []
    assert fixture["db_path"].read_bytes() == before_db
    assert _fixture_derived_fingerprints(fixture) == before_derived
    assert error.value.details["publish_attempted"] is False
    assert error.value.details["publish_substage"] is None
    assert error.value.details["rollback_attempted"] is True
    assert error.value.details["rollback_completed"] is True
    # callback failure must not produce fake cause_filename / os.replace substage
    assert error.value.details.get("cause_filename") is None
    assert error.value.details.get("cause_filename2") is None
    assert error.value.details.get("cause_winerror") is None
    assert error.value.details.get("cause_errno") is None


def _chat_direction_case(
    tmp_path,
    monkeypatch,
    *,
    importer=body_importer,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=db_path,
        data_dir=data_dir,
        import_journal_dir=tmp_path / "journals",
        zotero_body_importer=importer,
    )
    bridge = chat_tool_service.register_zotero_selected_book_import_preview(
        preview_token="p" * 40,
        runtime=runtime,
    )
    return runtime, bridge["confirmation_token"]


def _chat_journal(runtime, token):
    return ImportOperationJournalStore(
        runtime.resolved_import_journal_dir()
    ).resolve_by_token_digest(chat_tool_service._token_digest(token))


def test_publish_failure_is_persisted_as_failed_receipt(tmp_path, monkeypatch):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fixture publish failure")
        ),
    )
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"
    assert journal.completion_receipt["kind"] == "failure"
    assert journal.error["error_stage"] == "publish_started"
    assert journal.rollback["attempted"] is True
    assert journal.rollback["completed"] is True


def test_failure_receipt_preserves_original_stage_after_successful_rollback(
    tmp_path,
    monkeypatch,
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fixture publish failure")
        ),
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    journal = _chat_journal(runtime, token)
    failure_receipt = journal.completion_receipt
    assert journal.error["error_stage"] == "publish_started"
    assert failure_receipt["details"]["error_stage"] == "publish_started"
    assert journal.rollback["attempted"] is True
    assert journal.rollback["completed"] is True
    chat_tool_service.reset_chat_tool_state_for_tests()

    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert replay.value.details["error_stage"] == "publish_started"
    assert replay.value.details["rollback_attempted"] is True
    assert replay.value.details["rollback_completed"] is True


def test_final_verification_failure_is_persisted_as_failed_receipt(
    tmp_path,
    monkeypatch,
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    original = ImportOperationJournalStore.update

    def fail_final(self, operation_id, **kwargs):
        if kwargs.get("stage") == "final_verification_started":
            raise RuntimeError("fixture final verification journal failure")
        return original(self, operation_id, **kwargs)

    monkeypatch.setattr(ImportOperationJournalStore, "update", fail_final)
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert _chat_journal(runtime, token).status == "failed"


def test_rollback_failure_details_are_replayable(tmp_path, monkeypatch):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fixture publish failure")
        ),
    )
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_rollback_copy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fixture rollback failure")
        ),
    )
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    chat_tool_service.reset_chat_tool_state_for_tests()
    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert replay.value.details["rollback_completed"] is False
    assert replay.value.details["replayed_receipt"] is True


def test_cleanup_failure_does_not_change_committed_receipt(
    tmp_path,
    monkeypatch,
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_remove_generated_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("fixture cleanup failure")
        ),
    )
    first = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    replay = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert first["status"] == "committed"
    assert replay["status"] == "committed"
    assert _chat_journal(runtime, token).status == "committed"


@pytest.mark.parametrize("fault_stage", _DIRECTION_B_FORWARD_STAGES)
def test_direction_b_forward_fault_is_failed_and_replayed_without_reimport(
    tmp_path,
    monkeypatch,
    fault_stage,
):
    importer_calls = 0

    def importer(**kwargs):
        nonlocal importer_calls
        importer_calls += 1
        return body_importer(**kwargs)

    runtime, token = _chat_direction_case(
        tmp_path,
        monkeypatch,
        importer=importer,
    )
    original_emit = zotero_direction_b_import_service._emit_stage

    def fault_emit(callback, stage, **metadata):
        original_emit(callback, stage, **metadata)
        if stage == fault_stage:
            raise RuntimeError("fixture forward stage fault")

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_emit_stage",
        fault_emit,
    )
    with pytest.raises(chat_tool_service.ChatToolError) as first:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"
    assert journal.completion_receipt["kind"] == "failure"
    calls_after_first = importer_calls
    chat_tool_service.reset_chat_tool_state_for_tests()
    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    assert replay.value.error_code == first.value.error_code
    assert replay.value.details["replayed_receipt"] is True
    assert importer_calls == calls_after_first


@pytest.mark.parametrize("fault_stage", _DIRECTION_B_FORWARD_STAGES)
def test_direction_b_forward_stage_fault_injection(
    tmp_path,
    monkeypatch,
    fault_stage,
):
    db_path, data_dir = _direction_temp_case(tmp_path, monkeypatch)
    before_db = db_path.read_bytes()
    publish_calls = 0
    real_publish = (
        zotero_direction_b_import_service._publish_staged_derived_indexes
    )

    def publish(**kwargs):
        nonlocal publish_calls
        publish_calls += 1
        return real_publish(**kwargs)

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        publish,
    )

    def callback(stage, _metadata):
        if stage == fault_stage:
            raise RuntimeError("fixture stage failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        _direction_commit(
            db_path,
            data_dir,
            callback=callback,
        )
    assert error.value.details["rollback_attempted"] is True
    assert db_path.read_bytes() == before_db
    if _DIRECTION_B_FORWARD_STAGES.index(fault_stage) <= (
        _DIRECTION_B_FORWARD_STAGES.index("publish_started")
    ):
        assert publish_calls == 0

def test_production_derived_rollback_failure_restores_db_and_retains_backup(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=False,
    )
    db_path = fixture["db_path"]
    before_db = db_path.read_bytes()

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_derived_artifacts",
        lambda **_kwargs: (
            (_ for _ in ()).throw(
                RuntimeError(
                    "forced derived restore failure"
                )
            )
        ),
    )

    with pytest.raises(
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert error.value.code == (
        "zotero_direction_b_"
        "production_derived_rollback_failed"
    )
    assert db_path.read_bytes() == before_db

    rollback_root = (
        fixture["data_dir"]
        / ".direction_b_index_rollback"
    )
    assert rollback_root.is_dir()

    for child in rollback_root.iterdir():
        zotero_direction_b_import_service._remove_generated_tree(
            child
        )

def test_production_cleanup_failure_does_not_mask_success(
    tmp_path,
    monkeypatch,
):
    fixture = _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=True,
    )

    real_remove = (
        zotero_direction_b_import_service
        ._remove_generated_tree
    )

    def flaky_remove(path):
        candidate = Path(path)

        if ".direction_b_index_staging" in str(candidate):
            raise PermissionError(
                "forced staging cleanup failure"
            )

        return real_remove(candidate)

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_remove_generated_tree",
        flaky_remove,
    )

    result = (
        zotero_direction_b_import_service
        .commit_selected_book_import_to_production(
            preview_token="p" * 40,
            body_importer=body_importer,
        )
    )

    assert result["status"] == "committed"

    with sqlite3.connect(
        fixture["db_path"]
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 1


def test_cleanup_failure_does_not_mask_production_db_rollback_failure(
    tmp_path,
    monkeypatch,
):
    _install_production_shaped_runtime(
        tmp_path,
        monkeypatch,
        fts_ready=False,
    )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_restore_rollback_copy",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                RuntimeError(
                    "forced database rollback failure"
                )
            )
        ),
    )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_remove_generated_tree",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                PermissionError(
                    "forced cleanup failure"
                )
            )
        ),
    )

    with pytest.raises(
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as error:
        (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_production(
                preview_token="p" * 40,
                body_importer=body_importer,
            )
        )

    assert error.value.code == (
        "zotero_direction_b_"
        "production_db_rollback_failed"
    )


# ============================================================================
# P0-FIX1: publish substage / cause metadata / fault injection tests
# ============================================================================


_PUBLISH_SUBSTAGES = [
    "fts_index_replace",
    "fts_manifest_replace",
    "vector_store_retire",
    "vector_store_publish",
    "vector_manifest_replace",
    "native_note_vector_retire",
    "native_note_vector_publish",
    "native_note_vector_cache_invalidate",
]


_ERROR_BEFORE_SUBSTAGE: dict[str, set[str]] = {
    # substage -> set of substage names that must have run before it fails
    "fts_index_replace": set(),
    "fts_manifest_replace": {"fts_index_replace"},
    "vector_store_retire": {"fts_index_replace", "fts_manifest_replace"},
    "vector_store_publish": {"fts_index_replace", "fts_manifest_replace"},
    "vector_manifest_replace": {
        "fts_index_replace", "fts_manifest_replace", "vector_store_publish",
    },
    "native_note_vector_retire": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
    },
    "native_note_vector_publish": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
    },
    "native_note_vector_cache_invalidate": {
        "fts_index_replace", "fts_manifest_replace",
        "vector_store_publish", "vector_manifest_replace",
        "native_note_vector_publish",
    },
}


def _raise_at_substage(substage: str):
    """Return a callable that simulates a publish failure at *substage*."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    def fail(**kwargs):
        _fk = kwargs
        if "fts_index_replace" in _ERROR_BEFORE_SUBSTAGE[substage]:
            os.replace(_fk["staging_fts_index"], _fk["fts_index_path"])
        if "fts_manifest_replace" in _ERROR_BEFORE_SUBSTAGE[substage]:
            os.replace(_fk["staging_fts_manifest"], _fk["fts_manifest_path"])
        if "vector_store_retire" in _ERROR_BEFORE_SUBSTAGE[substage]:
            _fk["vector_store_path"].parent.mkdir(parents=True, exist_ok=True)
            if _fk["vector_store_path"].exists():
                retired = _fk["staging_vector_store"].parent / ".retired-lancedb"
                os.replace(_fk["vector_store_path"], retired)
        if "vector_store_publish" in _ERROR_BEFORE_SUBSTAGE[substage]:
            _fk["vector_store_path"].parent.mkdir(parents=True, exist_ok=True)
            if _fk["vector_store_path"].exists():
                retired = _fk["staging_vector_store"].parent / ".retired-lancedb"
                os.replace(_fk["vector_store_path"], retired)
            os.replace(_fk["staging_vector_store"], _fk["vector_store_path"])
        if "vector_manifest_replace" in _ERROR_BEFORE_SUBSTAGE[substage]:
            if _fk["staging_vector_manifest"].is_file():
                os.replace(
                    _fk["staging_vector_manifest"],
                    _fk["vector_manifest_path"],
                )
        if "native_note_vector_retire" in _ERROR_BEFORE_SUBSTAGE[substage]:
            if _fk["staging_zotero_note_vector_path"].is_dir():
                retired_note = (
                    _fk["staging_zotero_note_vector_path"].parent
                    / ".retired-zotero-user-notes"
                )
                if _fk["zotero_note_vector_path"].exists():
                    os.replace(_fk["zotero_note_vector_path"], retired_note)
        if "native_note_vector_publish" in _ERROR_BEFORE_SUBSTAGE[substage]:
            if _fk["staging_zotero_note_vector_path"].is_dir():
                retired_note = (
                    _fk["staging_zotero_note_vector_path"].parent
                    / ".retired-zotero-user-notes"
                )
                if _fk["zotero_note_vector_path"].exists():
                    os.replace(_fk["zotero_note_vector_path"], retired_note)
                os.replace(
                    _fk["staging_zotero_note_vector_path"],
                    _fk["zotero_note_vector_path"],
                )

        raise DirectionBDerivedPublishError(
            publish_substage=substage,
            original_exception=PermissionError(f"fixture {substage} denied"),
        )

    return fail


@pytest.mark.parametrize("substage", _PUBLISH_SUBSTAGES)
def test_publish_substage_failure_persists_receipt_with_substage_and_cause(
    tmp_path, monkeypatch, substage
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        _raise_at_substage(substage),
    )
    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"
    assert journal.completion_receipt["kind"] == "failure"

    details = journal.completion_receipt["details"]
    assert details["error_stage"] == "publish_started"
    assert details["publish_substage"] == substage
    assert details["cause_type"] is not None
    assert details["cause_message"] is not None
    assert details["rollback_attempted"] is True
    assert details["rollback_completed"] is True
    assert details["writes_performed"] is True
    assert details["safe_to_retry"] is False

    pr = journal.completion_receipt["public_response"]
    assert pr["token_consumed"] is True
    assert pr["writes_performed"] is True
    assert pr["safe_to_retry"] is False
    assert pr["error_stage"] == "publish_started"
    assert pr["publish_substage"] == substage
    assert pr.get("cause_type") is not None

    assert journal.error["error_stage"] == "publish_started"
    assert journal.error.get("publish_substage") == substage

    assert exc.value.details["error_stage"] == "publish_started"
    assert exc.value.details.get("publish_substage") == substage
    assert exc.value.details["rollback_attempted"] is True
    assert exc.value.details["rollback_completed"] is True
    assert exc.value.details["writes_performed"] is True
    # token_consumed is in public_response and receipt, not leaked to service-layer details
    assert pr["token_consumed"] is True


def test_publish_substage_failure_receipt_replay_preserves_fields(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        _raise_at_substage("fts_manifest_replace"),
    )
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"
    details = journal.completion_receipt["details"]
    assert details["publish_substage"] == "fts_manifest_replace"
    assert details["cause_type"] == "PermissionError"

    chat_tool_service.reset_chat_tool_state_for_tests()

    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    assert replay.value.details["replayed_receipt"] is True
    assert replay.value.details["publish_substage"] == "fts_manifest_replace"
    assert replay.value.details.get("cause_type") == "PermissionError"
    assert replay.value.details["rollback_attempted"] is True
    assert replay.value.details["rollback_completed"] is True
    assert replay.value.details["writes_performed"] is True
    # token_consumed verified via journal — not leaked to service-layer ChatToolError


def test_publish_failure_windows_style_exception_preserved_in_journal(
    tmp_path, monkeypatch
):
    """PermissionError with winerror=32 preserved in journal / receipt / replay."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    exc = PermissionError("fixture win32 sharing violation")
    exc.errno = 13
    exc.winerror = 32
    exc.filename = r"C:\staging\retrieval_fts_v1.db"
    exc.filename2 = r"C:\production\retrieval_fts_v1.db"

    runtime, token = _chat_direction_case(tmp_path, monkeypatch)

    def windows_fail(**kwargs):
        raise DirectionBDerivedPublishError(
            publish_substage="fts_index_replace",
            original_exception=exc,
        )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        windows_fail,
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"

    assert journal.error["publish_substage"] == "fts_index_replace"

    details = journal.completion_receipt["details"]
    assert details["publish_substage"] == "fts_index_replace"
    assert details["cause_type"] == "PermissionError"
    assert details["cause_errno"] == 13
    assert details["cause_winerror"] == 32
    assert details["cause_filename"] is not None
    assert details["cause_filename2"] is not None

    pr = journal.completion_receipt["public_response"]
    assert pr["publish_substage"] == "fts_index_replace"
    assert pr["cause_type"] == "PermissionError"
    assert pr["cause_errno"] == 13
    assert pr["cause_winerror"] == 32

    journal_raw = (
        runtime.resolved_import_journal_dir()
        / f"{journal.operation_id}.json"
    ).read_text(encoding="utf-8")
    assert "Traceback" not in journal_raw

    chat_tool_service.reset_chat_tool_state_for_tests()
    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    assert replay.value.details["publish_substage"] == "fts_index_replace"
    assert replay.value.details["cause_type"] == "PermissionError"
    assert replay.value.details["cause_winerror"] == 32
    assert replay.value.details["replayed_receipt"] is True


def test_publish_oserror_fields_extracted_correctly(tmp_path, monkeypatch):
    """OSError with errno but no winerror — null fields stay null."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    exc = OSError("fixture os error")
    exc.errno = 5
    exc.winerror = None
    exc.filename = "/tmp/source.db"
    exc.filename2 = None

    runtime, token = _chat_direction_case(tmp_path, monkeypatch)

    def oserror_fail(**kwargs):
        raise DirectionBDerivedPublishError(
            publish_substage="vector_manifest_replace",
            original_exception=exc,
        )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        oserror_fail,
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    details = journal.completion_receipt["details"]
    assert details["publish_substage"] == "vector_manifest_replace"
    assert details["cause_type"] == "OSError"
    assert details["cause_errno"] == 5
    assert details["cause_winerror"] is None
    assert details["cause_filename"] is not None
    assert details["cause_filename2"] is None


def test_non_oserror_exception_cause_metadata(tmp_path, monkeypatch):
    """RuntimeError has cause_type/message but null OS fields."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    runtime, token = _chat_direction_case(tmp_path, monkeypatch)

    def runtime_fail(**kwargs):
        raise DirectionBDerivedPublishError(
            publish_substage="native_note_vector_cache_invalidate",
            original_exception=RuntimeError("cache lock contention"),
        )

    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        runtime_fail,
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    details = journal.completion_receipt["details"]
    assert details["publish_substage"] == "native_note_vector_cache_invalidate"
    assert details["cause_type"] == "RuntimeError"
    assert details["cause_message"] is not None
    assert details["cause_errno"] is None
    assert details["cause_winerror"] is None
    assert details["cause_filename"] is None
    assert details["cause_filename2"] is None


def test_callback_failure_replay_does_not_reinvoke_importer(
    tmp_path, monkeypatch
):
    """Callback failure at publish_started must not re-execute on retry."""
    fixture = _install_production_shaped_runtime(
        tmp_path, monkeypatch, fts_ready=True
    )
    importer_calls = []
    callback_calls = []

    def counting_importer(*, preview, db_path):
        importer_calls.append(True)
        return body_importer(preview=preview, db_path=db_path)

    def counting_callback(stage, _metadata):
        callback_calls.append(stage)
        if stage == "publish_started":
            raise RuntimeError("fixture callback failure")

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ):
        zotero_direction_b_import_service.commit_selected_book_import_to_production(
            preview_token="p" * 40,
            body_importer=counting_importer,
            stage_callback=counting_callback,
        )

    first_count = len(importer_calls)
    assert first_count == 1
    assert "publish_started" in callback_calls

    with pytest.raises(
        zotero_direction_b_import_service.DirectionBSelectedBookImportError
    ) as error:
        zotero_direction_b_import_service.commit_selected_book_import_to_production(
            preview_token="p" * 40,
            body_importer=counting_importer,
            stage_callback=counting_callback,
        )

    assert error.value.details["error_stage"] == "publish_started"
    assert error.value.details["publish_attempted"] is False
    assert error.value.details["publish_substage"] is None


# ============================================================================
# P0-FIX1-CLOSURE1: cause_message redaction tests
# ============================================================================


class TestCauseMessageRedaction:
    def test_confirmation_token_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("import failed confirmation_token=abc123def456ghi789 secret message")
        msg = _safe_exception_message(exc)
        assert "abc123" not in msg
        assert "[REDACTED]" in msg
        assert "import failed" in msg

    def test_authorization_bearer_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("request failed Authorization: Bearer sk-12345abcdef67890 endpoint")
        msg = _safe_exception_message(exc)
        assert "sk-12345" not in msg
        assert "[REDACTED]" in msg
        assert "request failed" in msg
        assert "endpoint" in msg

    def test_api_key_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("api_key=sk-proj-12345 call failed")
        msg = _safe_exception_message(exc)
        assert "sk-proj-12345" not in msg
        assert "[REDACTED]" in msg

    def test_access_token_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("auth error access_token=ya29.abcdef123456")
        msg = _safe_exception_message(exc)
        assert "ya29.abcdef123456" not in msg
        assert "[REDACTED]" in msg

    def test_secret_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("secret=my-super-secret-value-here")
        msg = _safe_exception_message(exc)
        assert "my-super-secret-value-here" not in msg
        assert "[REDACTED]" in msg

    def test_password_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("connection failed password=admin123 host=localhost")
        msg = _safe_exception_message(exc)
        assert "admin123" not in msg
        assert "[REDACTED]" in msg
        assert "host=localhost" in msg

    def test_case_insensitive_match(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("CONFIRMATION_TOKEN=sEcReT123 AND Access_Token=XyZ789")
        msg = _safe_exception_message(exc)
        assert "sEcReT123" not in msg
        assert "XyZ789" not in msg
        assert "[REDACTED]" in msg

    def test_token_followed_by_comma_semicolon_paren(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = RuntimeError("api_key=abc123, next_field; api_key=def456) end")
        msg = _safe_exception_message(exc)
        assert "abc123" not in msg
        assert "def456" not in msg
        assert "[REDACTED]" in msg
        assert "next_field" in msg

    def test_normal_winerror_message_not_destroyed(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = OSError("[WinError 32] The process cannot access the file because it is being used by another process")
        msg = _safe_exception_message(exc)
        assert "WinError 32" in msg
        assert "process cannot access" in msg
        assert "[REDACTED]" not in msg

    def test_normal_path_in_message_preserved(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        exc = OSError("[Errno 13] Permission denied: '/tmp/staging/file.db'")
        msg = _safe_exception_message(exc)
        assert "/tmp/staging/file.db" in msg
        assert "Permission denied" in msg
        assert "[REDACTED]" not in msg

    def test_long_message_redacted_then_truncated(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        long_secret = "confirmation_token=" + ("x" * 600)
        exc = RuntimeError(long_secret)
        msg = _safe_exception_message(exc)
        assert msg is not None
        assert len(msg) <= 512
        assert "[REDACTED]" in msg

    def test_secrets_not_in_journal_or_replay(self, tmp_path, monkeypatch):
        """End-to-end: secret in cause_message must not reach journal."""
        from app.services.zotero_direction_b_import_service import (
            DirectionBDerivedPublishError,
        )
        bearer_secret = "AbCdEf.gh_IJ~kl+MN/op=QR-stuvwxyz123456"
        exc_with_secret = PermissionError(
            "os.replace failed confirmation_token=TOP_SECRET_12345 "
            f"Bearer {bearer_secret} file in use"
        )
        exc_with_secret.errno = 13
        exc_with_secret.winerror = 32

        runtime, token = _chat_direction_case(tmp_path, monkeypatch)

        def secret_fail(**kwargs):
            raise DirectionBDerivedPublishError(
                publish_substage="fts_index_replace",
                original_exception=exc_with_secret,
            )

        monkeypatch.setattr(
            zotero_direction_b_import_service,
            "_publish_staged_derived_indexes",
            secret_fail,
        )

        with pytest.raises(chat_tool_service.ChatToolError):
            chat_tool_service.import_document(
                confirmation_token=token, confirmed=True, runtime=runtime
            )

        journal = _chat_journal(runtime, token)
        details = journal.completion_receipt["details"]
        cause_msg = details.get("cause_message") or ""

        # Secret must not appear in journal
        assert "TOP_SECRET_12345" not in cause_msg
        assert bearer_secret not in cause_msg
        assert "[REDACTED]" in cause_msg
        # Diagnostic info must remain
        assert "os.replace" in cause_msg or "in use" in cause_msg

        # raw journal file must not contain the secret
        journal_raw = (
            runtime.resolved_import_journal_dir()
            / f"{journal.operation_id}.json"
        ).read_text(encoding="utf-8")
        assert "TOP_SECRET_12345" not in journal_raw
        assert bearer_secret not in journal_raw

        # Replay must also not leak
        chat_tool_service.reset_chat_tool_state_for_tests()
        with pytest.raises(chat_tool_service.ChatToolError) as replay:
            chat_tool_service.import_document(
                confirmation_token=token, confirmed=True, runtime=runtime
            )
        replay_msg = replay.value.details.get("cause_message") or ""
        assert "TOP_SECRET_12345" not in replay_msg
        assert bearer_secret not in replay_msg

    def test_confirmation_token_digest_not_redacted(self):
        """SHA256 digests for auditing must not be redacted."""
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        digest = "a" * 64
        for label in (
            "confirmation_token_digest",
            "confirmation-token-digest",
            "confirmation token digest",
        ):
            msg = _safe_exception_message(
                RuntimeError(f"digest verification failed {label}={digest}")
            )
            assert digest in msg
            assert "[REDACTED]" not in msg

    @pytest.mark.parametrize(
        "label",
        (
            "confirmation_token",
            "confirmation-token",
            "confirmation token",
        ),
    )
    def test_confirmation_token_spellings_redacted(self, label):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        secret = "token-value.A_B~C+D/E=F-123456789"
        msg = _safe_exception_message(RuntimeError(f"{label}={secret}, failed"))
        assert secret not in msg
        assert "[REDACTED]" in msg
        assert msg.endswith(", failed")

    def test_digest_and_real_token_in_same_message(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        digest = "b" * 64
        secret = "real-token.A_B~C+D/E=F-123456789"
        msg = _safe_exception_message(
            RuntimeError(
                f"confirmation_token_digest={digest}; "
                f"confirmation_token={secret})"
            )
        )
        assert digest in msg
        assert secret not in msg
        assert "[REDACTED]" in msg

    def test_plain_bearer_extended_jwt_charset_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        secret = "AbCdEf.gh_IJ~kl+MN/op=QR-stuvwxyz123456"
        msg = _safe_exception_message(RuntimeError(f"proxy Bearer {secret}; denied"))
        assert secret not in msg
        assert "bearer [redacted]" in msg.casefold()
        assert msg.endswith("; denied")

    def test_mixed_case_authorization_bearer_redacted(self):
        from app.services.zotero_direction_b_import_service import _safe_exception_message

        secret = "AbCdEf.gh_IJ~kl+MN/op=QR-stuvwxyz123456"
        msg = _safe_exception_message(
            RuntimeError(f"AUTHORIZATION: bEaReR {secret}) request failed")
        )
        assert secret not in msg
        assert "[REDACTED]" in msg
        assert msg.endswith(") request failed")


# ============================================================================
# P0-FIX1-CLOSURE1: token_consumed semantic tests
# ============================================================================


def test_token_consumed_present_in_failed_receipt_details(
    tmp_path, monkeypatch
):
    """token_consumed=True must be in receipt details and public_response."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(
            DirectionBDerivedPublishError(
                publish_substage="fts_index_replace",
                original_exception=PermissionError("fixture"),
            )
        ),
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    journal = _chat_journal(runtime, token)
    assert journal.status == "failed"
    pr = journal.completion_receipt["public_response"]
    assert pr["token_consumed"] is True
    assert pr["writes_performed"] is True


def test_token_consumed_survives_replay_for_failed_receipt(
    tmp_path, monkeypatch
):
    """Replayed failure receipt must report token_consumed=True."""
    from app.services.zotero_direction_b_import_service import (
        DirectionBDerivedPublishError,
    )

    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(
            DirectionBDerivedPublishError(
                publish_substage="fts_index_replace",
                original_exception=PermissionError("fixture"),
            )
        ),
    )

    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    chat_tool_service.reset_chat_tool_state_for_tests()

    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )

    assert replay.value.details["replayed_receipt"] is True
    assert replay.value.details["token_consumed"] is True
    assert replay.value.details["writes_performed"] is True


def test_invalid_token_does_not_set_token_consumed_true(tmp_path, monkeypatch):
    """Invalid/expired token raises ChatToolError before token is consumed."""
    chat_tool_service.reset_chat_tool_state_for_tests()
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "nonexistent.db",
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token="invalid_token_that_does_not_exist_12345678",
            confirmed=True,
            runtime=runtime,
        )

    # The error should be about invalid/expired confirmation, not about token_consumed
    details = exc.value.details
    assert details.get("token_consumed") is not True
    # safe_to_retry indicates the token was NOT consumed
    assert details.get("safe_to_retry") is not True


def test_confirmed_false_does_not_consume_token(tmp_path, monkeypatch):
    """confirmed=False raises before token consumption."""
    chat_tool_service.reset_chat_tool_state_for_tests()
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "nonexistent.db",
        data_dir=tmp_path / "data",
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token="any_token_value_12345678901234567890",
            confirmed=False,
            runtime=runtime,
        )

    # confirmed=False should raise before token is consumed
    assert exc.value.error_code == "chat_import_confirmation_required"
    assert exc.value.details.get("token_consumed") is not True


def test_successful_owner_claim_reports_token_consumed(tmp_path, monkeypatch):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)

    result = chat_tool_service.import_document(
        confirmation_token=token,
        confirmed=True,
        runtime=runtime,
    )

    assert result["token_consumed"] is True
    assert result["writes_performed"] is True
    assert chat_tool_service._token_digest(token) not in (
        chat_tool_service._IMPORT_CONFIRMATIONS
    )


def test_new_import_journal_failure_after_claim_consumes_token(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chat_tool_service,
        "_new_import_journal",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture")),
    )

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert exc.value.error_code == "chat_import_post_claim_failed"
    assert exc.value.details["token_consumed"] is True
    assert exc.value.details["writes_performed"] is False
    assert chat_tool_service._token_digest(token) not in (
        chat_tool_service._IMPORT_CONFIRMATIONS
    )


def test_journal_store_create_failure_after_claim_consumes_token(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ImportOperationJournalStore,
        "create",
        lambda self, record: (_ for _ in ()).throw(OSError("fixture")),
    )

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert exc.value.error_code == "chat_import_journal_create_failed"
    assert exc.value.details["token_consumed"] is True
    assert exc.value.details["writes_performed"] is False


def test_journal_conflict_without_replay_after_claim_consumes_token(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ImportOperationJournalStore,
        "create",
        lambda self, record: (_ for _ in ()).throw(
            JournalConflictError("fixture conflict")
        ),
    )

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert exc.value.error_code == "chat_import_journal_conflict"
    assert exc.value.details["token_consumed"] is True
    assert exc.value.details.get("writes_performed") is not True


def _create_nonterminal_chat_journal(runtime, token):
    digest = chat_tool_service._token_digest(token)
    record = chat_tool_service._IMPORT_CONFIRMATIONS[digest]
    journal, _audit = chat_tool_service._new_import_journal(
        record=record,
        token_digest=digest,
    )
    store = ImportOperationJournalStore(runtime.resolved_import_journal_dir())
    return store, store.create(journal)


def test_running_journal_reports_token_consumed(tmp_path, monkeypatch):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    store, journal = _create_nonterminal_chat_journal(runtime, token)
    store.update(
        journal.operation_id,
        expected_revision=journal.revision,
        expected_status=journal.status,
        status="running",
        stage="body_import_started",
    )

    result = chat_tool_service.import_document(
        confirmation_token=token,
        confirmed=True,
        runtime=runtime,
    )

    assert result["operation_in_progress"] is True
    assert result["token_consumed"] is True


def test_committed_receipt_replay_reports_token_consumed(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    first = chat_tool_service.import_document(
        confirmation_token=token,
        confirmed=True,
        runtime=runtime,
    )
    assert first["token_consumed"] is True
    chat_tool_service.reset_chat_tool_state_for_tests()

    replay = chat_tool_service.import_document(
        confirmation_token=token,
        confirmed=True,
        runtime=runtime,
    )

    assert replay["replayed_receipt"] is True
    assert replay["token_consumed"] is True
    assert replay["writes_performed"] is True


def test_orphaned_journal_reports_token_consumed(tmp_path, monkeypatch):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    store, journal = _create_nonterminal_chat_journal(runtime, token)
    store.update(
        journal.operation_id,
        expected_revision=journal.revision,
        expected_status=journal.status,
        status="orphaned",
        stage="body_import_started",
        writes_performed=None,
        error={"error_code": "import_owner_aborted"},
    )

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert exc.value.error_code == "chat_import_operation_orphaned"
    assert exc.value.details["token_consumed"] is True


def test_failed_receipt_replay_only_merges_whitelisted_contract_fields(
    tmp_path, monkeypatch
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        zotero_direction_b_import_service,
        "_publish_staged_derived_indexes",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture")),
    )
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    store = ImportOperationJournalStore(runtime.resolved_import_journal_dir())
    journal = store.resolve_by_token_digest(chat_tool_service._token_digest(token))
    receipt = dict(journal.completion_receipt)
    receipt["details"] = {}
    receipt["public_response"] = {
        "token_consumed": False,
        "writes_performed": True,
        "safe_to_retry": True,
        "error_code": "attacker_override",
        "cause_message": "attacker override",
    }
    tampered = replace(journal, completion_receipt=receipt)

    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service._resolve_import_journal_outcome(tampered)

    assert replay.value.details["token_consumed"] is True
    assert replay.value.details["writes_performed"] is True
    assert replay.value.details["safe_to_retry"] is False
    assert replay.value.error_code != "attacker_override"
    assert replay.value.details.get("cause_message") != "attacker override"


@pytest.mark.parametrize("exception_kind", ("chat_tool", "runtime"))
def test_failed_receipt_persistence_call_sites_require_successful_owner_claim(
    tmp_path, monkeypatch, exception_kind
):
    runtime, token = _chat_direction_case(tmp_path, monkeypatch)
    digest = chat_tool_service._token_digest(token)

    def fail_commit(**_kwargs):
        if exception_kind == "chat_tool":
            raise chat_tool_service.ChatToolError(
                "fixture_failure",
                "fixture failure",
                status_code=500,
                details={"writes_performed": False},
            )
        raise RuntimeError("fixture runtime failure")

    runtime = replace(runtime, commit_zotero_import=fail_commit)
    original = chat_tool_service._persist_failed_import_receipt
    observations = []

    def verify_claim_then_persist(**kwargs):
        observations.append(
            (
                digest in chat_tool_service._IMPORT_IN_PROGRESS,
                digest in chat_tool_service._IMPORT_CONFIRMATIONS,
            )
        )
        return original(**kwargs)

    monkeypatch.setattr(
        chat_tool_service,
        "_persist_failed_import_receipt",
        verify_claim_then_persist,
    )

    with pytest.raises(chat_tool_service.ChatToolError) as exc:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )

    assert observations == [(True, True)]
    assert exc.value.details["token_consumed"] is True
