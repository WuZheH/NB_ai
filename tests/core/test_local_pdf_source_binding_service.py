from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.db.session import Base
from app.services import commit_book_service
from app.services import document_integrity_report_service
from app.services import local_pdf_source_binding_service as service
from app.services import commit_paper_service
from app.services import chat_pdf_production_import_service


def _one_page_pdf(path: Path) -> bytes:
    payload = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                chunk_index INTEGER
            );
            CREATE TABLE document_sources (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_trace_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO documents VALUES (
                41,
                'CREAD-A11-SMOKE-TEST',
                'paper'
            );
            INSERT INTO knowledge_chunks VALUES (51, 41, 0);
            """
        )


def _binding(tmp_path: Path) -> service.LocalPdfSourceBinding:
    relative = "pdfs/chat_imports/CREAD-A11-SMOKE-TEST.pdf"
    payload = _one_page_pdf(tmp_path / "data" / relative)
    digest = hashlib.sha256(payload).hexdigest()
    revision = "a" * 64
    return service.LocalPdfSourceBinding(
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
                "body_import_started",
                "source_binding_recorded",
            ],
        },
    )


def test_local_pdf_happy_path_records_one_safe_source_and_audit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.db"
    _database(db_path)
    binding = _binding(tmp_path)

    created = service.record_document_source(
        db_path=db_path,
        document_id=41,
        binding=binding,
    )
    replayed = service.record_document_source(
        db_path=db_path,
        document_id=41,
        binding=binding,
    )
    verified = service.verify_document_source(
        db_path=db_path,
        data_dir=tmp_path / "data",
        document_id=41,
        binding=binding,
    )

    assert created["status"] == "recorded"
    assert replayed["status"] == "already_recorded"
    assert replayed["write_performed"] is False
    assert verified["source_binding_count"] == 1
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        count = connection.execute(
            "SELECT COUNT(*) FROM document_sources"
        ).fetchone()[0]
        source = document_integrity_report_service._source_row(
            connection,
            41,
        )
        trace_text = connection.execute(
            "SELECT source_trace_json FROM document_sources"
        ).fetchone()[0]
    history = document_integrity_report_service._history_from_source(
        source
    )
    pdf_sha, pdf_warning = (
        document_integrity_report_service._resolve_pdf_sha256(source)
    )

    assert count == 1
    assert source["recorded"] is True
    assert source["source_type"] == "local_pdf"
    assert pdf_sha == binding.pdf_sha256
    assert pdf_warning is None
    assert set(history.values()) != {"not_recorded"}
    assert history["confirmation_token_fingerprint"] == "c" * 64
    assert history["source_revision_fingerprint"] == "a" * 64
    assert "CREAD-A11-SMOKE-TEST.pdf" in trace_text
    assert str(tmp_path) not in trace_text
    assert "confirmation_token" not in json.loads(trace_text)


def test_local_pdf_source_trace_never_contains_raw_confirmation_token(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.db"
    _database(db_path)
    binding = _binding(tmp_path)
    raw_token = "raw-confirmation-token-must-not-be-stored"
    assert raw_token not in json.dumps(
        binding.source_trace(),
        sort_keys=True,
    )
    service.record_document_source(
        db_path=db_path,
        document_id=41,
        binding=binding,
    )
    assert raw_token.encode("utf-8") not in db_path.read_bytes()


def test_paper_commit_routes_local_binding_without_disguising_it_as_zotero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(tmp_path)
    local_calls = []
    zotero_calls = []
    target_db = tmp_path / "explicit-target.db"
    monkeypatch.setattr(
        commit_paper_service.local_pdf_source_binding_service,
        "record_document_source",
        lambda **kwargs: local_calls.append(kwargs),
    )
    monkeypatch.setattr(
        commit_paper_service.zotero_source_cache_service,
        "record_document_source",
        lambda *args: zotero_calls.append(args),
    )
    commit_paper_service._record_staging_document_source(
        db_path=target_db,
        document_id=41,
        source_trace={"source_type": "local_pdf"},
        local_pdf_source_binding=binding,
    )
    assert len(local_calls) == 1
    assert local_calls[0]["binding"] is binding
    assert local_calls[0]["db_path"] == target_db
    assert zotero_calls == []


def test_paper_commit_preserves_existing_zotero_source_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_calls = []
    zotero_calls = []
    monkeypatch.setattr(
        commit_paper_service.local_pdf_source_binding_service,
        "record_document_source",
        lambda **kwargs: local_calls.append(kwargs),
    )
    monkeypatch.setattr(
        commit_paper_service.zotero_source_cache_service,
        "record_document_source",
        lambda *args: zotero_calls.append(args),
    )
    trace = {
        "source_type": "zotero_pdf",
        "zotero_item_key": "ITEM1",
        "zotero_attachment_key": "ATT1",
        "source_pdf_sha256": "f" * 64,
    }
    commit_paper_service._record_staging_document_source(
        db_path=tmp_path / "explicit-target.db",
        document_id=42,
        source_trace=trace,
        local_pdf_source_binding=None,
    )
    assert local_calls == []
    assert zotero_calls == [(42, trace)]


def test_book_commit_uses_explicit_database_and_outer_rollback_backup_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "book-job"
    job_dir.mkdir()
    for name in ("paper.md", "import_manifest.json", "source_trace.json"):
        (job_dir / name).write_text("{}", encoding="utf-8")
    pdf_path = tmp_path / "fixture.pdf"
    _one_page_pdf(pdf_path)
    target_db = tmp_path / "explicit-book.db"
    binding = _binding(tmp_path)
    calls: dict[str, object] = {}

    monkeypatch.setattr(commit_book_service, "_existing_job_dir", lambda _job: job_dir)
    monkeypatch.setattr(
        commit_book_service,
        "_read_json",
        lambda path: (
            {"title_hint": "Fixture"}
            if path.name == "import_manifest.json"
            else {"source_pdf_path": str(pdf_path)}
        ),
    )
    monkeypatch.setattr(commit_book_service, "_resolve_source_pdf_path", lambda _trace: pdf_path)
    monkeypatch.setattr(commit_book_service, "_preflight_document_sources_table", lambda path: calls.setdefault("preflight_db", path))
    monkeypatch.setattr(commit_book_service, "_detect_chapters_from_staging", lambda **_kwargs: ([SimpleNamespace(chapter_index=1, title="One", heading_path="One", pdf_page_start=1, pdf_page_end=1)], "fixture", []))
    monkeypatch.setattr(commit_book_service, "parse_markdown", lambda *_args, **_kwargs: SimpleNamespace(nodes=[object()]))
    chunk = SimpleNamespace(heading_path="One", chunk_text="text", char_count=4, overlap_before="", overlap_after="", pdf_page_start=1, pdf_page_end=1)
    monkeypatch.setattr(commit_book_service, "split_nodes", lambda _nodes: [chunk])
    monkeypatch.setattr(commit_book_service, "bind_chunks_to_chapters", lambda *_args: ([1], []))

    def fake_apply(_prepared, *, db_path, backup, **_kwargs):
        calls["apply_db"] = db_path
        calls["backup"] = backup
        return {"document_id": 41, "inserted_chunks": 1}

    monkeypatch.setattr(commit_book_service, "apply_prepared_book_import", fake_apply)
    def fake_record_source(**kwargs):
        calls["source"] = kwargs
        return {"status": "recorded"}

    monkeypatch.setattr(
        commit_book_service.local_pdf_source_binding_service,
        "record_document_source",
        fake_record_source,
    )
    monkeypatch.setattr(commit_book_service, "_record_document_zotero_key", lambda path, *_args: calls.setdefault("zotero_db", path))
    monkeypatch.setattr(commit_book_service, "_write_json", lambda *_args, **_kwargs: None)

    commit_book_service.commit_book_from_staging(
        "job-1",
        db_path=target_db,
        backup=False,
        local_pdf_source_binding=binding,
    )

    assert calls["preflight_db"] == target_db
    assert calls["apply_db"] == target_db
    assert calls["backup"] is False
    assert calls["source"]["db_path"] == target_db
    assert calls["zotero_db"] == target_db


def test_paper_commit_writes_only_the_explicit_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_db = tmp_path / "explicit-paper.db"
    engine = create_engine(f"sqlite:///{target_db.as_posix()}")
    try:
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()

    job_dir = tmp_path / "paper-job"
    job_dir.mkdir()
    (job_dir / "paper.md").write_text("# Fixture\n\nBody.", encoding="utf-8")
    (job_dir / "import_manifest.json").write_text("{}", encoding="utf-8")
    (job_dir / "source_trace.json").write_text("{}", encoding="utf-8")
    binding = _binding(tmp_path)
    downstream: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(commit_paper_service, "_existing_job_dir", lambda _job: job_dir)
    monkeypatch.setattr(commit_paper_service, "DATA_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(commit_paper_service, "COMMIT_BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(
        commit_paper_service,
        "_read_json",
        lambda path: (
            {"title_hint": "Fixture"}
            if path.name == "import_manifest.json"
            else {"source_type": "local_pdf"}
        ),
    )
    monkeypatch.setattr(commit_paper_service, "parse_markdown", lambda *_args, **_kwargs: SimpleNamespace(nodes=[]))
    monkeypatch.setattr(commit_paper_service.import_service, "split_nodes", lambda _nodes: [])
    monkeypatch.setattr(
        commit_paper_service.import_service,
        "_upsert_nodes_and_chunks",
        lambda *_args, **_kwargs: SimpleNamespace(nodes_created=0, chunks_created=0),
    )
    def fake_note_sync(**kwargs):
        downstream["note_sync"] = kwargs
        return {"status": "skipped"}

    def fake_alignment(**kwargs):
        downstream["alignment"] = kwargs
        return {"status": "skipped"}

    monkeypatch.setattr(
        commit_paper_service,
        "_sync_zotero_native_notes_for_paper",
        fake_note_sync,
    )
    monkeypatch.setattr(
        commit_paper_service,
        "_run_zotero_note_alignment_hook",
        fake_alignment,
    )

    result = commit_paper_service.commit_paper_from_staging(
        "job-1",
        db_path=target_db,
        rebuild_legacy_vector_index=False,
        local_pdf_source_binding=binding,
    )

    with sqlite3.connect(target_db) as connection:
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        sources = connection.execute("SELECT COUNT(*) FROM document_sources").fetchone()[0]
    assert result["document_id"] > 0
    assert documents == 1
    assert sources == 1
    assert downstream["note_sync"]["db_path"] == target_db
    assert downstream["alignment"]["db_path"] == target_db


@pytest.mark.parametrize(
    ("document_type", "service_name"),
    [
        ("paper", "commit_paper_service"),
        ("book", "commit_book_service"),
    ],
)
def test_production_body_commit_explicitly_passes_local_source_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document_type: str,
    service_name: str,
) -> None:
    binding = _binding(tmp_path)
    calls = []
    target = getattr(
        chat_pdf_production_import_service,
        service_name,
    )

    def fake_commit(job_id, **kwargs):
        calls.append((job_id, kwargs))
        return {"document_id": 41, "chunk_count": 1}

    function_name = (
        "commit_book_from_staging"
        if document_type == "book"
        else "commit_paper_from_staging"
    )
    monkeypatch.setattr(target, function_name, fake_commit)
    runtime = chat_pdf_production_import_service.ChatPdfImportRuntime.production()
    runtime.body_commit("job-1", document_type, binding)

    assert len(calls) == 1
    assert calls[0][0] == "job-1"
    assert calls[0][1]["local_pdf_source_binding"] is binding
    assert (
        calls[0][1]["db_path"]
        == chat_pdf_production_import_service.DEFAULT_DB_PATH
    )
    if document_type == "book":
        assert calls[0][1]["backup"] is False


def test_local_pdf_managed_pdf_sha_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    managed = tmp_path / "data" / binding.managed_pdf_relative_path
    managed.write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(
        service.LocalPdfSourceBindingError,
        match="local_pdf_managed_pdf_sha_mismatch",
    ):
        service.verify_managed_pdf(
            data_dir=tmp_path / "data",
            binding=binding,
        )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "D:/private/inbox.pdf",
        "../private/inbox.pdf",
        "/private/inbox.pdf",
    ],
)
def test_local_pdf_source_trace_rejects_absolute_or_escaping_path(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    binding = _binding(tmp_path)
    unsafe = service.LocalPdfSourceBinding(
        source_identity=binding.source_identity,
        pdf_sha256=binding.pdf_sha256,
        source_revision_fingerprint=(
            binding.source_revision_fingerprint
        ),
        managed_pdf_relative_path=unsafe_path,
        import_history=binding.import_history,
    )
    with pytest.raises(
        service.LocalPdfSourceBindingError,
        match="local_pdf_managed_pdf_path_unsafe",
    ):
        unsafe.source_trace()
