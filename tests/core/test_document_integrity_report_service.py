from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import document_integrity_report_service as service
from app.services import local_pdf_source_binding_service
from app.services.import_operation_journal import (
    ImportOperationJournal,
    ImportOperationJournalStore,
)
from app.services.retrieval.source_registry import (
    RetrievalSourceRegistry,
)
from app.services.retrieval.sources.personal_note_adapter import (
    personal_note_exclusion_reason,
)


def _runtime(tmp_path: Path) -> service.IntegrityReportRuntime:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                read_status TEXT,
                created_at TEXT,
                pdf_path TEXT,
                source_path TEXT,
                zotero_key TEXT,
                object_import_mode TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES documents(id),
                node_id INTEGER,
                chunk_index INTEGER,
                heading_path TEXT,
                chunk_text TEXT,
                overlap_before TEXT,
                overlap_after TEXT,
                content_hash TEXT,
                pdf_path TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER,
                zotero_open_url TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE markdown_nodes (
                id INTEGER PRIMARY KEY,
                order_index INTEGER
            );
            CREATE TABLE book_chapters (
                id INTEGER PRIMARY KEY,
                chapter_index INTEGER,
                title TEXT
            );
            CREATE TABLE chapters (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id)
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id),
                note_type TEXT,
                title TEXT,
                content TEXT,
                summary TEXT,
                content_hash TEXT,
                selected_text TEXT,
                source_comment TEXT,
                source_record_kind TEXT,
                source_identity TEXT,
                source_content_hash TEXT,
                source_missing INTEGER,
                pdf_page INTEGER,
                page_label TEXT,
                scope_path TEXT,
                scope_type TEXT,
                source_path TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id)
            );
            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES documents(id),
                source_type TEXT,
                zotero_item_key TEXT,
                zotero_attachment_key TEXT,
                source_revision_fingerprint TEXT,
                source_trace_json TEXT
            );
            INSERT INTO documents VALUES (
                1, 'Fixture', 'book', 'read',
                '2026-07-29T00:00:00+00:00',
                'D:/private/source.pdf', NULL, 'ITEM1', NULL
            );
            INSERT INTO knowledge_chunks VALUES (
                11, 1, NULL, 0, '[]', 'first chunk',
                NULL, NULL, 'chunk-hash-1', 'D:/private/source.pdf',
                1, 1, NULL, NULL, NULL, NULL
            );
            INSERT INTO knowledge_chunks VALUES (
                12, 1, NULL, 1, '[]', 'second chunk',
                NULL, NULL, 'chunk-hash-2', 'D:/private/source.pdf',
                2, 2, NULL, NULL, NULL, NULL
            );
            INSERT INTO chapters VALUES (21, 1);
            INSERT INTO personal_notes VALUES (
                31, 1, 'zotero_annotation', 'Comment', 'eligible body',
                NULL, NULL, '', 'eligible body', 'zotero_annotation',
                'note-31', NULL, 0, 1, '1', NULL, NULL,
                'D:/private/note', NULL, NULL
            );
            INSERT INTO personal_notes VALUES (
                32, 1, 'zotero_annotation', 'Highlight only', '',
                NULL, NULL, 'selected evidence', '', 'zotero_annotation',
                'note-32', NULL, 0, 2, '2', NULL, NULL,
                'D:/private/note', NULL, NULL
            );
            INSERT INTO note_evidence_links VALUES (41, 1);
            """
        )
        connection.execute(
            """
            INSERT INTO document_sources VALUES (
                1, 1, 'zotero_pdf', 'ITEM1', 'ATT1', ?, ?
            )
            """,
            (
                "b" * 64,
                json.dumps(
                    {
                        "zotero_item_key": "ITEM1",
                        "zotero_attachment_key": "ATT1",
                        "source_pdf_sha256": "a" * 64,
                        "source_revision_fingerprint": "b" * 64,
                        "source_pdf_path": "D:/private/source.pdf",
                    }
                ),
            ),
        )

    registry = RetrievalSourceRegistry(
        research_db_path=db_path,
        zotero_snapshot_path=tmp_path / "missing-zotero.db",
        notes_root=tmp_path / "missing-notes",
        project_root=tmp_path,
    )
    fragments = registry.read(
        source_types=("pdf_chunk", "personal_note"),
        document_ids=(1,),
    ).fragments
    fts_path = tmp_path / "fts.db"
    with sqlite3.connect(fts_path) as connection:
        connection.execute(
            """
            CREATE TABLE retrieval_fragments (
                document_id INTEGER,
                source_type TEXT,
                fragment_id TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO retrieval_fragments VALUES (?, ?, ?)",
            [
                (
                    fragment.document_id,
                    fragment.source_type,
                    fragment.fragment_id,
                )
                for fragment in fragments
            ],
        )
    manifest = tmp_path / "fts.json"
    manifest.write_text("{}", encoding="utf-8")
    vector_store = tmp_path / "vectors"
    vector_store.mkdir()
    vector_manifest = tmp_path / "vectors.json"
    vector_manifest.write_text("{}", encoding="utf-8")
    return service.IntegrityReportRuntime(
        db_path=db_path,
        fts_index_path=fts_path,
        fts_manifest_path=manifest,
        vector_store_path=vector_store,
        vector_manifest_path=vector_manifest,
    )


def _patch_ready_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.fts_status_service,
        "get_index_status",
        lambda **_kwargs: {
            "status": "ready",
            "ready": True,
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        service.vector_store_service,
        "inspect_document_vector_state",
        lambda **kwargs: {
            "status": "ok",
            "read_only": True,
            "passage": {
                "status": "ok",
                "reason": None,
                "actual_source_ids": list(
                    kwargs["expected_passage_source_ids"]
                ),
                "missing_count": 0,
                "orphan_count": 0,
            },
            "note": {
                "status": "ok",
                "reason": None,
                "actual_source_ids": list(
                    kwargs["expected_note_source_ids"]
                ),
                "missing_count": 0,
                "orphan_count": 0,
            },
        },
    )


def _write_terminal_journal(
    runtime: service.IntegrityReportRuntime,
    *,
    operation_id: str = "d" * 32,
    document_id: int = 1,
    transaction_fingerprint: str = "e" * 64,
    source_revision_fingerprint: str = "b" * 64,
    source_pdf_sha256: str = "a" * 64,
    status: str = "committed",
    stage: str = "receipt_persisted",
    chunk_count: int = 2,
    revision: int = 7,
    updated_at: str = "2026-08-02T01:01:00+00:00",
    completion_receipt: dict[str, object] | None = None,
) -> ImportOperationJournal:
    assert runtime.import_journal_dir is not None
    store = ImportOperationJournalStore(runtime.import_journal_dir)
    store._ensure_dir()
    receipt = (
        {
            "kind": "success",
            "response": {
                "document_id": document_id,
                "chunk_count": chunk_count,
            },
        }
        if completion_receipt is None
        else completion_receipt
    )
    record = ImportOperationJournal(
        operation_id=operation_id,
        confirmation_token_digest="f" * 64,
        transaction_fingerprint=transaction_fingerprint,
        source_revision_fingerprint=source_revision_fingerprint,
        title="Fixture",
        zotero_item_key="ITEM1",
        zotero_attachment_key="ATT1",
        source_pdf_sha256=source_pdf_sha256,
        owner_process_id=os.getpid(),
        owner_process_started_at="2026-08-02T01:00:00+00:00",
        owner_thread_id=1,
        started_at="2026-08-02T01:00:00+00:00",
        updated_at=updated_at,
        heartbeat_at=updated_at,
        revision=revision,
        status=status,
        stage=stage,
        writes_performed=status == "committed",
        document_id=document_id,
        chunk_count=chunk_count,
        error=(
            {"error_code": "fixture_failure"}
            if status in {"failed", "orphaned"}
            else None
        ),
        completion_receipt=receipt,
    )
    store._write_atomic(record.to_dict(), store._journal_path(operation_id))
    return record


def _set_source_transaction_fingerprint(
    runtime: service.IntegrityReportRuntime,
    transaction_fingerprint: str | None,
    *,
    document_id: int = 1,
) -> None:
    with sqlite3.connect(runtime.db_path) as connection:
        row = connection.execute(
            """
            SELECT source_trace_json
            FROM document_sources
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
        assert row is not None
        trace = json.loads(str(row[0]))
        history = dict(trace.get("import_history") or {})
        if transaction_fingerprint is None:
            history.pop("transaction_fingerprint", None)
        else:
            history["transaction_fingerprint"] = transaction_fingerprint
        if history:
            trace["import_history"] = history
        else:
            trace.pop("import_history", None)
        connection.execute(
            """
            UPDATE document_sources
            SET source_trace_json = ?
            WHERE document_id = ?
            """,
            (json.dumps(trace), document_id),
        )


def _reassign_fixture_document_id(
    runtime: service.IntegrityReportRuntime,
    document_id: int,
) -> None:
    with sqlite3.connect(runtime.db_path) as connection:
        for table in (
            "knowledge_chunks",
            "chapters",
            "personal_notes",
            "note_evidence_links",
            "document_sources",
        ):
            connection.execute(
                f"UPDATE {table} SET document_id = ? WHERE document_id = 1",
                (document_id,),
            )
        connection.execute(
            "UPDATE documents SET id = ? WHERE id = 1",
            (document_id,),
        )
    with sqlite3.connect(runtime.fts_index_path) as connection:
        connection.execute(
            """
            UPDATE retrieval_fragments
            SET document_id = ?
            WHERE document_id = 1
            """,
            (document_id,),
        )


def test_integrity_report_explains_note_eligibility_and_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.db_path.read_bytes()
    _patch_ready_dependencies(monkeypatch)

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["read_only"] is True
    assert result["verdict"] == "warn"
    assert result["pdf_sha256"] == "a" * 64
    assert result["database"]["integrity_check"] == "ok"
    assert result["database"]["foreign_key_issue_count"] == 0
    assert result["database"]["chunk_count"] == 2
    assert result["database"]["personal_note_count"] == 2
    assert result["fts"] == {
        "status": "ready",
        "ready": True,
        "expected_pdf_chunk_count": 2,
        "indexed_pdf_chunk_count": 2,
        "missing_pdf_chunk_count": 0,
        "orphan_pdf_chunk_count": 0,
        "eligible_personal_note_count": 1,
        "indexed_personal_note_count": 1,
        "missing_personal_note_count": 0,
        "orphan_personal_note_count": 0,
        "excluded_personal_note_count": 1,
        "exclusion_reasons": {
            "empty_content": 1,
        },
        "fragment_count": 3,
        "source_type_counts": {
            "pdf_chunk": 2,
            "personal_note": 1,
        },
        "reasons": [],
    }
    assert result["vectors"]["passage_expected_count"] == 2
    assert result["vectors"]["passage_indexed_count"] == 2
    assert result["vectors"]["passage_missing_count"] == 0
    assert result["vectors"]["passage_orphan_count"] == 0
    assert result["vectors"]["note_expected_count"] == 2
    assert result["vectors"]["note_indexed_count"] == 2
    assert result["vectors"]["note_missing_count"] == 0
    assert result["vectors"]["note_orphan_count"] == 0
    assert result["vectors"]["reasons"] == []
    assert set(result["writes_performed"].values()) == {False}
    assert set(result["history"].values()) == {"not_recorded"}
    assert "personal_notes_excluded_from_fts:1" in result["warnings"]
    serialized = json.dumps(result)
    assert "D:/private" not in serialized
    assert "confirmation_token" not in serialized.replace(
        "confirmation_token_fingerprint",
        "",
    )
    assert runtime.db_path.read_bytes() == before


def test_integrity_report_detects_fts_missing_and_orphan_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    with sqlite3.connect(runtime.fts_index_path) as connection:
        expected = connection.execute(
            """
            SELECT fragment_id
            FROM retrieval_fragments
            WHERE source_type = 'pdf_chunk'
            ORDER BY fragment_id
            LIMIT 1
            """
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM retrieval_fragments WHERE fragment_id = ?",
            (expected,),
        )
        connection.execute(
            "INSERT INTO retrieval_fragments VALUES (1, 'pdf_chunk', 'orphan')"
        )

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["verdict"] == "fail"
    assert result["fts"]["missing_pdf_chunk_count"] == 1
    assert result["fts"]["orphan_pdf_chunk_count"] == 1
    assert "fts_missing_pdf_chunk_count" in result["warnings"]
    assert "fts_orphan_pdf_chunk_count" in result["warnings"]


def test_integrity_report_detects_vector_missing_without_faking_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    monkeypatch.setattr(
        service.vector_store_service,
        "inspect_document_vector_state",
        lambda **_kwargs: {
            "status": "capability_unavailable",
            "read_only": True,
            "passage": {
                "status": "capability_unavailable",
                "reason": "passage_schema_document_id_unavailable",
                "actual_source_ids": [],
                "missing_count": 2,
                "orphan_count": "not_available",
            },
            "note": {
                "status": "ok",
                "reason": None,
                "actual_source_ids": ["note:31", "note:32"],
                "missing_count": 0,
                "orphan_count": 0,
            },
        },
    )

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["verdict"] == "fail"
    assert result["vectors"]["passage_missing_count"] == 2
    assert (
        result["vectors"]["passage_orphan_count"]
        == "not_available"
    )
    assert "vector_passage_missing_count" in result["warnings"]
    assert (
        "vector_inspection:passage_schema_document_id_unavailable"
        in result["warnings"]
    )


def test_integrity_report_pdf_sha_is_not_revision_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute(
            """
            UPDATE document_sources
            SET source_trace_json = ?
            WHERE document_id = 1
            """,
            (
                json.dumps(
                    {
                        "source_revision_fingerprint": "b" * 64,
                    }
                ),
            ),
        )

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["pdf_sha256"] == "not_recorded"
    assert (
        result["source"]["source_revision_fingerprint"]
        == "b" * 64
    )
    assert "pdf_sha256_not_recorded" in result["warnings"]


def test_integrity_report_reads_safe_import_history_from_source_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    history = {
        "confirmation_token_fingerprint": "c" * 64,
        "previewed_at": "2026-07-30T01:00:00+00:00",
        "confirmed_at": "2026-07-30T01:01:00+00:00",
        "transaction_fingerprint": "t" * 64,
        "source_revision_fingerprint": "r" * 64,
        "lifecycle_events": [
            "previewed",
            "confirmed",
            "transaction_started",
        ],
    }
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute(
            """
            UPDATE document_sources
            SET source_trace_json = ?
            WHERE document_id = 1
            """,
            (json.dumps({"import_history": history}),),
        )

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["history"] == {
        **{key: value for key, value in history.items() if key != "lifecycle_events"},
        "lifecycle_events": "previewed,confirmed,transaction_started",
        "terminal_status": "not_recorded",
        "terminal_stage": "not_recorded",
        "journal_operation_id": "not_recorded",
        "journal_revision": "not_recorded",
        "receipt_recorded": "not_recorded",
        "journal_updated_at": "not_recorded",
        "journal_terminal_events": "not_recorded",
    }
    assert "historical_events_not_recorded" in result["warnings"]
    assert result["writes_performed"] == {
        "production_db": False,
        "fts": False,
        "vector_store": False,
        "zotero": False,
    }


def test_personal_note_fts_eligibility_uses_nonempty_content() -> None:
    assert (
        personal_note_exclusion_reason(
            {
                "content": "",
                "selected_text": "highlight",
            }
        )
        == "empty_content"
    )
    assert (
        personal_note_exclusion_reason(
            {
                "content": "comment",
                "selected_text": "",
            }
        )
        is None
    )


def test_integrity_verdict_pass_warn_and_fail_rules() -> None:
    base = {
        "source": {
            "recorded": True,
        },
        "database": {
            "integrity_check": "ok",
            "foreign_key_issue_count": 0,
        },
        "fts": {
            "ready": True,
            "missing_pdf_chunk_count": 0,
            "orphan_pdf_chunk_count": 0,
            "missing_personal_note_count": 0,
            "orphan_personal_note_count": 0,
            "excluded_personal_note_count": 0,
        },
        "vectors": {
            "status": "ready",
            "passage_missing_count": 0,
            "passage_orphan_count": 0,
            "note_missing_count": 0,
            "note_orphan_count": 0,
        },
        "history": {
            "confirmation_token_fingerprint": "recorded",
            "previewed_at": "recorded",
            "confirmed_at": "recorded",
            "transaction_fingerprint": "recorded",
            "source_revision_fingerprint": "recorded",
            "lifecycle_events": "recorded",
        },
        "writes_performed": {
            "production_db": False,
            "fts": False,
            "vector_store": False,
            "zotero": False,
        },
        "pdf_warning": None,
    }
    assert service._evaluate_verdict(**base) == ("pass", [])
    warning_case = {
        **base,
        "pdf_warning": "pdf_sha256_not_recorded",
    }
    assert service._evaluate_verdict(**warning_case)[0] == "warn"
    failure_case = {
        **base,
        "database": {
            **base["database"],
            "foreign_key_issue_count": 1,
        },
    }
    assert service._evaluate_verdict(**failure_case)[0] == "fail"


def test_integrity_report_accepts_complete_local_pdf_source_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    relative = "pdfs/chat_imports/CREAD-A11-SMOKE-TEST.pdf"
    managed = tmp_path / "data" / relative
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"%PDF-1.4\nlocal integrity fixture")
    digest = hashlib.sha256(managed.read_bytes()).hexdigest()
    revision = "a" * 64
    binding = local_pdf_source_binding_service.LocalPdfSourceBinding(
        source_identity=f"local_pdf:sha256:{digest}",
        pdf_sha256=digest,
        source_revision_fingerprint=revision,
        managed_pdf_relative_path=relative,
        import_history={
            "previewed_at": "2026-08-01T12:00:00+00:00",
            "confirmed_at": "2026-08-01T12:01:00+00:00",
            "transaction_fingerprint": "b" * 64,
            "confirmation_token_fingerprint": "c" * 64,
            "source_revision_fingerprint": revision,
            "lifecycle_events": [
                "previewed",
                "confirmed",
                "transaction_started",
                "source_binding_recorded",
            ],
        },
    )
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute(
            "DELETE FROM document_sources WHERE document_id = 1"
        )
        connection.commit()
    local_pdf_source_binding_service.record_document_source(
        db_path=runtime.db_path,
        document_id=1,
        binding=binding,
    )

    result = service.build_integrity_report(
        document_id=1,
        runtime=runtime,
    )

    assert result["source"]["recorded"] is True
    assert result["source"]["source_type"] == "local_pdf"
    assert result["pdf_sha256"] == digest
    assert result["database"]["source_binding_count"] == 1
    for key in (
        "confirmation_token_fingerprint",
        "previewed_at",
        "confirmed_at",
        "transaction_fingerprint",
        "source_revision_fingerprint",
        "lifecycle_events",
    ):
        assert result["history"][key] != "not_recorded"
    assert result["history"]["terminal_status"] == "not_recorded"
    assert result["verdict"] == "warn", result["warnings"]
    assert "document_source_binding_missing" not in result["warnings"]
    assert "historical_events_not_recorded" in result["warnings"]


def test_integrity_report_projects_matching_committed_journal_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute(
            """
            UPDATE document_sources
            SET source_trace_json = ?
            WHERE document_id = 1
            """,
            (
                json.dumps(
                    {
                        "source_pdf_sha256": "a" * 64,
                        "source_revision_fingerprint": "b" * 64,
                        "import_history": {
                            "previewed_at": "2026-08-02T00:59:00+00:00",
                            "confirmed_at": "2026-08-02T01:00:00+00:00",
                            "transaction_fingerprint": "e" * 64,
                            "source_revision_fingerprint": "b" * 64,
                            "lifecycle_events": [
                                "previewed",
                                "confirmed",
                                "transaction_started",
                            ],
                        },
                    }
                ),
            ),
        )
    record = _write_terminal_journal(runtime)
    before_db = runtime.db_path.read_bytes()
    before_journal = {
        path.name: path.read_bytes()
        for path in runtime.import_journal_dir.iterdir()
    }

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "committed"
    assert result["history"]["terminal_stage"] == "receipt_persisted"
    assert result["history"]["journal_operation_id"] == record.operation_id
    assert result["history"]["journal_revision"] == 7
    assert result["history"]["receipt_recorded"] is True
    assert result["history"]["journal_terminal_events"] == (
        "final_verification_completed,receipt_persisted"
    )
    serialized = json.dumps(result)
    assert record.confirmation_token_digest not in serialized
    assert str(runtime.import_journal_dir) not in serialized
    assert runtime.db_path.read_bytes() == before_db
    assert {
        path.name: path.read_bytes()
        for path in runtime.import_journal_dir.iterdir()
    } == before_journal


@pytest.mark.parametrize("transaction_fingerprint", (None, "not-a-sha256"))
def test_single_journal_requires_current_document_transaction_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction_fingerprint: str | None,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _set_source_transaction_fingerprint(runtime, transaction_fingerprint)
    _write_terminal_journal(runtime)

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert (
        "import_journal_transaction_fingerprint_mismatch"
        in result["warnings"]
    )


def test_integrity_report_warns_for_multiple_matching_journals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _write_terminal_journal(runtime, operation_id="d" * 32)
    _write_terminal_journal(runtime, operation_id="e" * 32)

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["verdict"] == "fail"
    assert "import_journal_multiple_matches" in result["warnings"]
    assert result["history"]["terminal_status"] == "not_recorded"


@pytest.mark.parametrize(
    ("old_operation_id", "new_operation_id"),
    (
        ("1" * 32, "2" * 32),
        ("f" * 32, "0" * 32),
    ),
)
def test_integrity_report_disambiguates_reused_document_id_by_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_operation_id: str,
    new_operation_id: str,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    current_transaction = "2" * 64
    _set_source_transaction_fingerprint(runtime, current_transaction)
    _write_terminal_journal(
        runtime,
        operation_id=old_operation_id,
        transaction_fingerprint="1" * 64,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    selected = _write_terminal_journal(
        runtime,
        operation_id=new_operation_id,
        transaction_fingerprint=current_transaction,
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "committed"
    assert result["history"]["journal_operation_id"] == selected.operation_id
    assert "import_journal_multiple_matches" not in result["warnings"]


def test_integrity_report_fails_closed_when_no_journal_transaction_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _set_source_transaction_fingerprint(runtime, "3" * 64)
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint="1" * 64,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint="2" * 64,
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_transaction_fingerprint_mismatch" in result["warnings"]
    assert "import_journal_multiple_matches" not in result["warnings"]


def test_integrity_report_keeps_multiple_matches_without_current_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _set_source_transaction_fingerprint(runtime, None)
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint="1" * 64,
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint="2" * 64,
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_multiple_matches" in result["warnings"]


def test_integrity_report_rejects_duplicate_exact_transaction_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    current_transaction = "2" * 64
    _set_source_transaction_fingerprint(runtime, current_transaction)
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint=current_transaction,
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint=current_transaction,
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_multiple_matches" in result["warnings"]


def test_integrity_report_validates_pdf_after_transaction_disambiguation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    current_transaction = "2" * 64
    _set_source_transaction_fingerprint(runtime, current_transaction)
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint="1" * 64,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint=current_transaction,
        source_pdf_sha256="3" * 64,
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_pdf_sha256_mismatch" in result["warnings"]


def test_integrity_report_validates_receipt_after_transaction_disambiguation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    current_transaction = "2" * 64
    _set_source_transaction_fingerprint(runtime, current_transaction)
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint="1" * 64,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint=current_transaction,
        completion_receipt={"kind": "success"},
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_receipt_response_invalid" in result["warnings"]


def test_integrity_report_projects_production_shaped_reused_document_four(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _reassign_fixture_document_id(runtime, 4)
    current_transaction = "2" * 64
    _set_source_transaction_fingerprint(
        runtime,
        current_transaction,
        document_id=4,
    )
    _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        document_id=4,
        transaction_fingerprint="1" * 64,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    selected = _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        document_id=4,
        transaction_fingerprint=current_transaction,
    )

    result = service.build_integrity_report(document_id=4, runtime=runtime)

    assert result["history"]["terminal_status"] == "committed"
    assert result["history"]["journal_operation_id"] == selected.operation_id
    assert "import_journal_multiple_matches" not in result["warnings"]


def test_integrity_report_does_not_prefer_committed_or_latest_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    current_transaction = "1" * 64
    _set_source_transaction_fingerprint(runtime, current_transaction)
    selected = _write_terminal_journal(
        runtime,
        operation_id="1" * 32,
        transaction_fingerprint=current_transaction,
        status="failed",
        revision=1,
        updated_at="2026-08-02T01:00:00+00:00",
        completion_receipt={"kind": "failure"},
    )
    _write_terminal_journal(
        runtime,
        operation_id="2" * 32,
        transaction_fingerprint="2" * 64,
        revision=999,
        updated_at="2026-08-03T00:00:00+00:00",
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert selected.status == "failed"
    assert result["history"]["terminal_status"] == "not_recorded"
    assert "import_journal_terminal_not_committed" in result["warnings"]
    assert "import_journal_multiple_matches" not in result["warnings"]


@pytest.mark.parametrize(
    ("field", "journal_value", "warning"),
    (
        (
            "transaction_fingerprint",
            "1" * 64,
            "import_journal_transaction_fingerprint_mismatch",
        ),
        (
            "source_revision_fingerprint",
            "2" * 64,
            "import_journal_source_revision_mismatch",
        ),
        (
            "source_pdf_sha256",
            "3" * 64,
            "import_journal_pdf_sha256_mismatch",
        ),
    ),
)
def test_integrity_report_rejects_journal_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    journal_value: str,
    warning: str,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute(
            """
            UPDATE document_sources
            SET source_trace_json = ?
            WHERE document_id = 1
            """,
            (
                json.dumps(
                    {
                        "source_pdf_sha256": "a" * 64,
                        "source_revision_fingerprint": "b" * 64,
                        "import_history": {
                            "transaction_fingerprint": "e" * 64,
                            "source_revision_fingerprint": "b" * 64,
                        },
                    }
                ),
            ),
        )
    kwargs = {field: journal_value}
    _write_terminal_journal(runtime, **kwargs)

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["verdict"] == "fail"
    assert warning in result["warnings"]
    for key in (
        "terminal_status",
        "terminal_stage",
        "journal_operation_id",
        "journal_revision",
        "receipt_recorded",
        "journal_updated_at",
        "journal_terminal_events",
    ):
        assert result["history"][key] == "not_recorded"


def test_integrity_report_rejects_failed_or_invalid_committed_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _set_source_transaction_fingerprint(runtime, "e" * 64)
    _write_terminal_journal(
        runtime,
        status="failed",
        completion_receipt={"kind": "failure"},
    )
    failed = service.build_integrity_report(document_id=1, runtime=runtime)
    assert "import_journal_terminal_not_committed" in failed["warnings"]
    assert failed["history"]["receipt_recorded"] == "not_recorded"

    for entry in runtime.import_journal_dir.iterdir():
        entry.unlink()
    _write_terminal_journal(
        runtime,
        completion_receipt={"kind": "failure"},
    )
    invalid = service.build_integrity_report(document_id=1, runtime=runtime)
    assert invalid["verdict"] == "fail"
    assert "import_journal_committed_receipt_invalid" in invalid["warnings"]
    assert invalid["history"]["terminal_status"] == "not_recorded"


@pytest.mark.parametrize(
    ("receipt", "warning"),
    (
        (
            {"kind": "success"},
            "import_journal_receipt_response_invalid",
        ),
        (
            {"kind": "success", "response": None},
            "import_journal_receipt_response_invalid",
        ),
        (
            {"kind": "success", "response": "not-a-mapping"},
            "import_journal_receipt_response_invalid",
        ),
        (
            {"kind": "success", "response": {"chunk_count": 2}},
            "import_journal_receipt_document_invalid",
        ),
        (
            {
                "kind": "success",
                "response": {"document_id": 2, "chunk_count": 2},
            },
            "import_journal_receipt_document_mismatch",
        ),
        (
            {"kind": "success", "response": {"document_id": 1}},
            "import_journal_receipt_chunk_count_invalid",
        ),
        (
            {
                "kind": "success",
                "response": {"document_id": 1, "chunk_count": 3},
            },
            "import_journal_receipt_chunk_count_mismatch",
        ),
    ),
)
def test_committed_journal_requires_complete_matching_success_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt: dict[str, object],
    warning: str,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _write_terminal_journal(runtime, completion_receipt=receipt)

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["verdict"] == "fail"
    assert warning in result["warnings"]
    for key in (
        "terminal_status",
        "terminal_stage",
        "journal_operation_id",
        "journal_revision",
        "receipt_recorded",
        "journal_updated_at",
        "journal_terminal_events",
    ):
        assert result["history"][key] == "not_recorded"


@pytest.mark.parametrize(
    ("journal_chunks", "receipt_chunks", "projects"),
    (
        (999, 999, False),
        (2, 999, False),
        (999, 2, False),
        (2, 2, True),
    ),
)
def test_terminal_journal_cross_checks_database_chunk_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_chunks: int,
    receipt_chunks: int,
    projects: bool,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    _set_source_transaction_fingerprint(runtime, "e" * 64)
    _write_terminal_journal(
        runtime,
        chunk_count=journal_chunks,
        completion_receipt={
            "kind": "success",
            "response": {
                "document_id": 1,
                "chunk_count": receipt_chunks,
            },
        },
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    if projects:
        assert result["history"]["terminal_status"] == "committed"
        assert result["history"]["receipt_recorded"] is True
    else:
        assert result["verdict"] == "fail"
        assert (
            "import_journal_database_chunk_count_mismatch"
            in result["warnings"]
        )
        for key in (
            "terminal_status",
            "terminal_stage",
            "journal_operation_id",
            "receipt_recorded",
        ):
            assert result["history"][key] == "not_recorded"


def test_integrity_report_allows_document_with_zero_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _patch_ready_dependencies(monkeypatch)
    with sqlite3.connect(runtime.db_path) as connection:
        connection.execute("DELETE FROM knowledge_chunks WHERE document_id=1")
    with sqlite3.connect(runtime.fts_index_path) as connection:
        connection.execute(
            "DELETE FROM retrieval_fragments WHERE source_type='pdf_chunk'"
        )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["database"]["chunk_count"] == 0
    assert result["vectors"]["passage_expected_count"] == 0
    assert result["vectors"]["passage_missing_count"] == 0


def test_integrity_report_fails_closed_for_malformed_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = replace(
        _runtime(tmp_path),
        import_journal_dir=tmp_path / "operation_journal",
    )
    _patch_ready_dependencies(monkeypatch)
    runtime.import_journal_dir.mkdir()
    (runtime.import_journal_dir / ("a" * 32 + ".json")).write_text(
        "{not-json",
        encoding="utf-8",
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["verdict"] == "fail"
    assert "import_journal_invalid" in result["warnings"]


def test_integrity_report_requires_exact_existing_document_id(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    with pytest.raises(service.IntegrityReportError) as invalid:
        service.build_integrity_report(
            document_id=0,
            runtime=runtime,
        )
    assert (
        invalid.value.error_code
        == "integrity_report_document_id_invalid"
    )
    with pytest.raises(service.IntegrityReportError) as missing:
        service.build_integrity_report(
            document_id=2,
            runtime=runtime,
        )
    assert (
        missing.value.error_code
        == "integrity_report_document_not_found"
    )
