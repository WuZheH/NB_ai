from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services import document_integrity_report_service as service


def _runtime(tmp_path: Path) -> service.IntegrityReportRuntime:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                read_status TEXT,
                created_at TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER
            );
            CREATE TABLE chapters (
                id INTEGER PRIMARY KEY,
                document_id INTEGER
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER
            );
            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                document_id INTEGER
            );
            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                source_type TEXT,
                source_trace_json TEXT
            );
            INSERT INTO documents VALUES (1, 'Fixture', 'book', 'active', '2026-07-29T00:00:00+00:00');
            INSERT INTO knowledge_chunks VALUES (11, 1);
            INSERT INTO knowledge_chunks VALUES (12, 1);
            INSERT INTO chapters VALUES (21, 1);
            INSERT INTO personal_notes VALUES (31, 1);
            INSERT INTO note_evidence_links VALUES (41, 1);
            """
        )
        connection.execute(
            "INSERT INTO document_sources VALUES (1, 1, 'zotero_pdf', ?)",
            (
                json.dumps(
                    {
                        "zotero_item_key": "ITEM1",
                        "zotero_attachment_key": "ATT1",
                        "source_sha256": "a" * 64,
                        "source_path": "D:/private/source.pdf",
                    }
                ),
            ),
        )
    fts_path = tmp_path / "fts.db"
    with sqlite3.connect(fts_path) as connection:
        connection.execute(
            "CREATE TABLE retrieval_fragments (document_id INTEGER, source_type TEXT)"
        )
        connection.executemany(
            "INSERT INTO retrieval_fragments VALUES (?, ?)",
            [(1, "pdf_chunk"), (1, "zotero_annotation_comment")],
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


def test_integrity_report_is_read_only_path_free_and_marks_unrecorded_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.db_path.read_bytes()
    monkeypatch.setattr(
        service.fts_status_service,
        "get_index_status",
        lambda **_kwargs: {"status": "ready", "ready": True, "reasons": []},
    )
    monkeypatch.setattr(
        service.vector_store_service,
        "inspect_document_vector_impact",
        lambda **_kwargs: {"passage_vector_count": 2},
    )
    monkeypatch.setattr(
        service.vector_store_service,
        "inspect_note_vector_impact",
        lambda **_kwargs: {"note_vector_count": 1},
    )

    result = service.build_integrity_report(document_id=1, runtime=runtime)

    assert result["read_only"] is True
    assert result["database"]["chunk_count"] == 2
    assert result["fts"]["fragment_count"] == 2
    assert result["vectors"]["passage_indexed_count"] == 2
    assert result["vectors"]["note_indexed_count"] == 1
    assert set(result["writes_performed"].values()) == {False}
    assert set(result["history"].values()) == {"not_recorded"}
    serialized = json.dumps(result)
    assert "D:/private" not in serialized
    assert "confirmation_token" not in serialized.replace(
        "confirmation_token_fingerprint",
        "",
    )
    assert runtime.db_path.read_bytes() == before


def test_integrity_report_requires_exact_existing_document_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with pytest.raises(service.IntegrityReportError) as invalid:
        service.build_integrity_report(document_id=0, runtime=runtime)
    assert invalid.value.error_code == "integrity_report_document_id_invalid"
    with pytest.raises(service.IntegrityReportError) as missing:
        service.build_integrity_report(document_id=2, runtime=runtime)
    assert missing.value.error_code == "integrity_report_document_not_found"
