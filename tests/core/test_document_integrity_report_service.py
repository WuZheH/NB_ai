from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.services import document_integrity_report_service as service
from app.services import local_pdf_source_binding_service
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
        "inspect_document_vector_impact",
        lambda **kwargs: {
            "passage_source_ids": list(
                kwargs["passage_source_ids"]
            ),
        },
    )
    monkeypatch.setattr(
        service.vector_store_service,
        "inspect_note_vector_impact",
        lambda **kwargs: {
            "note_source_ids": list(
                kwargs["note_source_ids"]
            ),
        },
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
    assert (
        result["vectors"]["passage_orphan_count"]
        == "not_available"
    )
    assert result["vectors"]["note_expected_count"] == 2
    assert result["vectors"]["note_indexed_count"] == 2
    assert result["vectors"]["note_missing_count"] == 0
    assert result["vectors"]["note_orphan_count"] == "not_available"
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
        "inspect_document_vector_impact",
        lambda **_kwargs: {
            "passage_source_ids": [],
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
    }
    assert "historical_events_not_recorded" not in result["warnings"]
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
    assert all(
        value != "not_recorded"
        for value in result["history"].values()
    )
    assert result["verdict"] == "warn", result["warnings"]
    assert "document_source_binding_missing" not in result["warnings"]
    assert "historical_events_not_recorded" not in result["warnings"]


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
