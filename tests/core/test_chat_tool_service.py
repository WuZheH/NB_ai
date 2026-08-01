from __future__ import annotations

import hashlib
import threading
import time
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.chat_tools import ImportPreviewRequest
from app.services import chat_tool_service, pdf_import_classifier_service
from app.services import chat_pdf_production_import_service
from app.services.pdf_backend_service import load_fitz_backend
from app.services.library import document_deletion_service
from app.services.import_operation_journal import ImportOperationJournalStore


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    chat_tool_service.reset_chat_tool_state_for_tests()


def _library_database(root: Path) -> Path:
    path = root / "data" / "db" / "research_memory.db"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            document_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            pdf_path TEXT,
            read_status TEXT NOT NULL
        );
        CREATE TABLE knowledge_chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "Motion Diffusion Model", "paper", "2026-01-02", None, "read"),
            (2, "Archived Motion Notes", "book", "2026-01-01", None, "archived"),
        ],
    )
    connection.executemany(
        "INSERT INTO knowledge_chunks VALUES (?, ?)",
        [(11, 1), (12, 1), (21, 2)],
    )
    connection.commit()
    connection.close()
    return path


def test_list_library_is_compact_and_filters_archived(tmp_path: Path) -> None:
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=_library_database(tmp_path),
        data_dir=tmp_path / "data",
    )
    active = chat_tool_service.list_library(query="motion", runtime=runtime)
    archived = chat_tool_service.list_library(status="archived", runtime=runtime)
    assert active["count"] == 1
    assert active["items"][0] == {
        "document_id": 1,
        "title": "Motion Diffusion Model",
        "type": "paper",
        "imported_at": "2026-01-02",
        "chunk_count": 2,
        "has_pdf": False,
            "duplicate_status": "not_evaluated",
            "status": "active",
            "source": "search_library",
    }
    assert [item["document_id"] for item in archived["items"]] == [2]
    assert "pdf_path" not in str(active)


def test_delete_tools_keep_internal_revision_and_require_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def preview(document_id, **_kwargs):
        return {
            "document_id": document_id,
            "title": "Fixture Book",
            "preview_token": "p" * 40,
            "document_revision": "r" * 64,
            "whether_safe_to_delete": True,
            "search_review_artifact_count": 2,
            "warnings": [
                "search_review_artifacts_will_be_deleted",
            ],
            "deletion_blockers": [],
        }

    def delete(**kwargs):
        calls.append(kwargs)
        return {
            "status": "completed",
            "audit_id": "delete-fixture",
            "recovery_package": {"created": True},
            "error_code": None,
        }

    monkeypatch.setattr(document_deletion_service, "create_deletion_preview", preview)
    monkeypatch.setattr(document_deletion_service, "delete_document", delete)
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
    )
    result = chat_tool_service.delete_preview(7, runtime=runtime)
    assert result["safe_to_delete"] is True
    assert result["search_review_artifact_count"] == 2
    assert result["warnings"] == [
        "search_review_artifacts_will_be_deleted"
    ]
    assert result["pdf_preserved"] is True
    assert result["notes_preserved"] is True
    assert (
        result["confirmation_expires_in_seconds"]
        == document_deletion_service.PREVIEW_TTL_SECONDS
    )
    assert "document_revision" not in result
    assert "preview_token" not in result
    with pytest.raises(chat_tool_service.ChatToolError) as missing:
        chat_tool_service.delete_document(
            confirmation_token=result["confirmation_token"],
            confirmed=False,
            runtime=runtime,
        )
    assert missing.value.error_code == "chat_delete_confirmation_required"
    deleted = chat_tool_service.delete_document(
        confirmation_token=result["confirmation_token"],
        confirmed=True,
        runtime=runtime,
    )
    assert deleted["status"] == "completed"
    assert calls[0]["document_id"] == 7
    assert calls[0]["preview_token"] == "p" * 40
    assert calls[0]["expected_document_revision"] == "r" * 64
    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.delete_document(
            confirmation_token=result["confirmation_token"],
            confirmed=True,
            runtime=runtime,
        )
    assert replay.value.error_code == "chat_delete_confirmation_invalid_or_expired"


def test_import_preview_reads_inbox_without_writing_and_commit_uses_confirmation(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\nisolated fixture")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    committed: list[chat_tool_service.ImportConfirmation] = []

    def classify(path, **kwargs):
        assert path == pdf
        assert Path(kwargs["allowed_root"]) == inbox
        return {
            "title": "Fixture PDF",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 3},
            "reasons": ["metadata_missing_type_fallback"],
        }

    def commit(*, record, runtime):
        committed.append(record)
        assert runtime.data_dir == tmp_path / "data"
        return {
            "status": "committed",
            "document_id": 9,
            "title": record.title,
            "document_type": record.document_type,
            "chunk_count": 6,
        }

    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        inbox_root=inbox,
        classify_pdf=classify,
        commit_import=commit,
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    after_preview = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after_preview == before
    assert preview["pdf_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert preview["estimated_pages"] == 3
    assert preview["estimated_chunks"] is None
    assert (
        "chunk_count_not_precomputed_by_preview"
        in preview["warnings"]
    )
    assert "pdf_path" not in preview
    with pytest.raises(chat_tool_service.ChatToolError) as missing:
        chat_tool_service.import_document(
            confirmation_token=preview["confirmation_token"],
            confirmed=False,
            runtime=runtime,
        )
    assert missing.value.error_code == "chat_import_confirmation_required"
    result = chat_tool_service.import_document(
        confirmation_token=preview["confirmation_token"],
        confirmed=True,
        runtime=runtime,
    )
    assert result["status"] == "committed"
    assert result["document_id"] == 9
    assert result["already_completed"] is False
    assert result["replayed_receipt"] is False
    assert len(committed) == 1

    replay = chat_tool_service.import_document(
        confirmation_token=preview[
            "confirmation_token"
        ],
        confirmed=True,
        runtime=runtime,
    )

    assert replay["status"] == "committed"
    assert replay["document_id"] == 9
    assert replay["chunk_count"] == 6
    assert replay["already_completed"] is True
    assert replay["replayed_receipt"] is True
    assert len(committed) == 1


def test_import_reports_same_token_as_in_progress_without_second_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nfixture")

    commit_count = 0

    def commit(**_kwargs):
        nonlocal commit_count
        commit_count += 1
        return {
            "status": "committed",
            "document_id": 1,
            "chunk_count": 1,
        }

    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 1},
        },
        commit_import=commit,
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    token = preview["confirmation_token"]
    digest = chat_tool_service._token_digest(token)

    monkeypatch.setattr(
        chat_tool_service,
        "IMPORT_CONCURRENT_WAIT_SECONDS",
        0.0,
    )
    chat_tool_service._IMPORT_IN_PROGRESS.add(digest)

    try:
        result = chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    finally:
        chat_tool_service._IMPORT_IN_PROGRESS.discard(digest)

    assert result["status"] == "in_progress"
    assert result["operation_in_progress"] is True
    assert result["token_consumed"] is True
    assert result["safe_to_retry"] is False
    assert result["writes_performed"] is None
    assert result["error_code"] is None
    assert commit_count == 0


def test_concurrent_same_token_waits_and_replays_owner_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\\nfixture")

    owner_started = threading.Event()
    release_owner = threading.Event()
    commit_calls: list[str] = []

    def commit(*, record, runtime):
        del runtime
        commit_calls.append(record.title)
        owner_started.set()
        assert release_owner.wait(timeout=5.0)
        return {
            "status": "committed",
            "document_id": 9,
            "title": record.title,
            "document_type": record.document_type,
            "chunk_count": 6,
        }

    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 1},
        },
        commit_import=commit,
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    token = preview["confirmation_token"]

    monkeypatch.setattr(
        chat_tool_service,
        "IMPORT_CONCURRENT_WAIT_SECONDS",
        3.0,
    )

    owner_result: dict[str, object] = {}
    duplicate_result: dict[str, object] = {}
    failures: list[BaseException] = []

    def owner_call() -> None:
        try:
            owner_result.update(
                chat_tool_service.import_document(
                    confirmation_token=token,
                    confirmed=True,
                    runtime=runtime,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    def duplicate_call() -> None:
        try:
            duplicate_result.update(
                chat_tool_service.import_document(
                    confirmation_token=token,
                    confirmed=True,
                    runtime=runtime,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    owner = threading.Thread(target=owner_call)
    duplicate = threading.Thread(target=duplicate_call)

    owner.start()
    assert owner_started.wait(timeout=2.0)
    duplicate.start()
    time.sleep(0.1)
    release_owner.set()

    owner.join(timeout=5.0)
    duplicate.join(timeout=5.0)

    assert not owner.is_alive()
    assert not duplicate.is_alive()
    assert failures == []
    assert owner_result["status"] == "committed"
    assert owner_result["already_completed"] is False
    assert duplicate_result["status"] == "in_progress"
    assert duplicate_result["already_completed"] is False
    assert duplicate_result["replayed_receipt"] is False
    assert duplicate_result["operation_in_progress"] is True
    assert commit_calls == ["Fixture"]


def test_import_rejects_changed_pdf_and_duplicate_without_commit_token(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfirst")
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 1},
        },
        commit_import=lambda **_kwargs: {"status": "committed"},
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    pdf.write_bytes(b"%PDF-1.4\nchanged")
    with pytest.raises(chat_tool_service.ChatToolError) as changed:
        chat_tool_service.import_document(
            confirmation_token=preview["confirmation_token"],
            confirmed=True,
            runtime=runtime,
        )
    assert changed.value.error_code == "import_source_changed"

    duplicate_runtime = chat_tool_service.ChatToolRuntime(
        db_path=runtime.db_path,
        data_dir=runtime.data_dir,
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": True,
            "existing_document_id": 3,
            "signals": {"page_count": 1},
        },
    )
    duplicate = chat_tool_service.import_preview(runtime=duplicate_runtime)
    assert duplicate["duplicate_status"] == "duplicate"
    assert duplicate["confirmation_token"] is None


def test_import_preview_request_source_contract() -> None:
    assert ImportPreviewRequest(inbox_filename="book.pdf").source_type == "local_pdf"
    assert ImportPreviewRequest(
        source_type="local_pdf",
        inbox_filename="book.pdf",
    ).inbox_filename == "book.pdf"
    with pytest.raises(ValidationError):
        ImportPreviewRequest(source_type="local_pdf", zotero_item_key="ABCD1234")
    with pytest.raises(ValidationError):
        ImportPreviewRequest(source_type="zotero_selected_book")
    with pytest.raises(ValidationError):
        ImportPreviewRequest(
            source_type="zotero_selected_book",
            inbox_filename="book.pdf",
            zotero_item_key="ABCD1234",
        )
    item_only = ImportPreviewRequest(
        source_type="zotero_selected_book",
        zotero_item_key="ABCD1234",
    )
    assert item_only.zotero_attachment_key is None
    selected = ImportPreviewRequest(
        source_type="zotero_selected_book",
        zotero_item_key=" ABCD1234 ",
        zotero_attachment_key=" EFGH5678 ",
    )
    assert selected.zotero_item_key == "ABCD1234"
    assert selected.zotero_attachment_key == "EFGH5678"
    with pytest.raises(ValidationError):
        ImportPreviewRequest(
            source_type="zotero_selected_book",
            zotero_item_key=" ",
        )


def test_import_preview_api_forwards_all_source_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def preview(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "source_type": "zotero_selected_book",
        }

    monkeypatch.setattr(chat_tool_service, "import_preview", preview)
    monkeypatch.setenv("SEARCH_CHAT_GATEWAY_TOKEN", "t" * 32)
    client = TestClient(app, client=("127.0.0.1", 50100))
    response = client.post(
        "/api/v1/chat-tools/import-preview",
        headers={
            "Authorization": f"Bearer {'t' * 32}",
            "X-Search-Chat-Adapter": "mcp",
        },
        json={
            "source_type": "zotero_selected_book",
            "zotero_item_key": "ABCD1234",
            "zotero_attachment_key": "EFGH5678",
        },
    )
    assert response.status_code == 200
    assert calls == [
        {
            "source_type": "zotero_selected_book",
            "inbox_filename": None,
            "zotero_item_key": "ABCD1234",
            "zotero_attachment_key": "EFGH5678",
        }
    ]


def test_pdf_classifier_accepts_only_the_explicit_inbox_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    outside = tmp_path / "outside"
    inbox.mkdir()
    outside.mkdir()
    fitz = load_fitz_backend()
    allowed_pdf = inbox / "allowed.pdf"
    outside_pdf = outside / "outside.pdf"
    for path in (allowed_pdf, outside_pdf):
        document = fitz.open()
        document.new_page()
        document.set_metadata({"title": "Inbox Fixture"})
        document.save(path)
        document.close()
    monkeypatch.setattr(
        pdf_import_classifier_service,
        "find_duplicate_pdf",
        lambda *_args, **_kwargs: None,
    )
    result = pdf_import_classifier_service.classify_pdf_import(
        allowed_pdf,
        allowed_root=inbox,
    )
    assert result["status"] == "ok"
    assert result["signals"]["page_count"] == 1
    with pytest.raises(FileNotFoundError):
        pdf_import_classifier_service.classify_pdf_import(
            outside_pdf,
            allowed_root=inbox,
        )


def test_chat_gateway_requires_explicit_secret_and_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app, client=("127.0.0.1", 50100))
    monkeypatch.delenv("SEARCH_CHAT_GATEWAY_TOKEN", raising=False)
    unconfigured = client.post("/api/v1/chat-tools/list-library", json={})
    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"]["error_code"] == "chat_gateway_not_configured"

    token = "t" * 40
    monkeypatch.setenv("SEARCH_CHAT_GATEWAY_TOKEN", token)
    missing_adapter = client.post(
        "/api/v1/chat-tools/list-library",
        headers={"authorization": f"Bearer {token}"},
        json={},
    )
    assert missing_adapter.status_code == 403
    wrong_token = client.post(
        "/api/v1/chat-tools/list-library",
        headers={
            "authorization": "Bearer wrong",
            "x-search-chat-adapter": "mcp",
        },
        json={},
    )
    assert wrong_token.status_code == 401


def test_forwarded_chat_gateway_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "t" * 40
    monkeypatch.setenv("SEARCH_CHAT_GATEWAY_TOKEN", token)
    client = TestClient(app, client=("127.0.0.1", 50100))
    response = client.post(
        "/api/v1/chat-tools/list-library",
        headers={
            "authorization": f"Bearer {token}",
            "x-search-chat-adapter": "actions",
            "x-forwarded-for": "127.0.0.1",
        },
        json={},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "chat_gateway_forwarded_request_forbidden"


def test_default_chat_import_routes_to_production_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-1.4 fixture")
    record = chat_tool_service.ImportConfirmation(
        source_path=source, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_size=source.stat().st_size, source_mtime_ns=source.stat().st_mtime_ns,
        title="Fixture", document_type="paper", object_import_mode="full_document", page_count=1,
        expires_at=9999999999.0,
    )
    calls = []
    monkeypatch.setattr(chat_pdf_production_import_service, "import_document_to_production", lambda **kwargs: calls.append(kwargs) or {"status": "completed", "document_id": 1, "title": "Fixture", "chunk_count": 1})
    monkeypatch.setattr(chat_tool_service, "_managed_pdf_name", lambda _record: "fixture.pdf")
    runtime = chat_tool_service.ChatToolRuntime(db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "data", inbox_root=tmp_path)
    sentinel = chat_pdf_production_import_service.ChatPdfImportRuntime.production()
    monkeypatch.setattr(chat_tool_service, "_resolve_chat_pdf_import_runtime", lambda _runtime: sentinel)
    monkeypatch.setattr(chat_tool_service.import_preview_service, "create_import_preview", lambda *_args, **_kwargs: {"import_job_id": "job-1"})
    result = chat_tool_service._commit_confirmed_import(record=record, runtime=runtime)
    assert len(calls) == 1
    assert calls[0]["allow_production"] is True
    assert calls[0]["runtime"] is sentinel
    assert result["status"] == "committed"
    assert result["writes_performed"] is True

def test_canonical_runtime_resolver_uses_real_constants():
    runtime = chat_tool_service.ChatToolRuntime(db_path=chat_tool_service.DEFAULT_DB_PATH, data_dir=chat_tool_service.DATA_DIR)
    resolved = chat_tool_service._resolve_chat_pdf_import_runtime(runtime)
    assert chat_pdf_production_import_service._is_production_runtime(resolved)


def test_noncanonical_chat_tool_runtime_rejects_default_import_route(tmp_path: Path) -> None:
    runtime = chat_tool_service.ChatToolRuntime(db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "data", inbox_root=tmp_path)
    with pytest.raises(chat_tool_service.ChatToolError, match="Production import runtime"):
        chat_tool_service._commit_confirmed_import(
            record=chat_tool_service.ImportConfirmation(
                source_path=tmp_path / "missing.pdf", source_sha256="0" * 64, source_size=0,
                source_mtime_ns=0, title="Fixture", document_type="paper", object_import_mode="full_document",
                page_count=1, expires_at=9999999999.0,
            ), runtime=runtime,
        )


def _journal_local_case(
    tmp_path: Path,
    *,
    importer,
) -> tuple[chat_tool_service.ChatToolRuntime, str, Path]:
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\njournal fixture")
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Journal Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 1},
        },
        commit_import=importer,
    )
    preview = chat_tool_service.import_preview(runtime=runtime)
    return runtime, preview["confirmation_token"], pdf


def _valid_local_result(record) -> dict[str, object]:
    return {
        "status": "committed",
        "document_id": 41,
        "title": record.title,
        "document_type": record.document_type,
        "chunk_count": 3,
    }


def _journal_for_token(
    runtime: chat_tool_service.ChatToolRuntime,
    token: str,
):
    return ImportOperationJournalStore(
        runtime.resolved_import_journal_dir()
    ).resolve_by_token_digest(chat_tool_service._token_digest(token))


def test_committed_journal_replays_after_memory_reset(tmp_path: Path) -> None:
    calls = 0

    def importer(*, record, **_kwargs):
        nonlocal calls
        calls += 1
        return _valid_local_result(record)

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    first = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    chat_tool_service.reset_chat_tool_state_for_tests()
    replay = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert first["already_completed"] is False
    assert replay["already_completed"] is True
    assert replay["replayed_receipt"] is True
    assert calls == 1


def test_failed_journal_replays_after_memory_reset(tmp_path: Path) -> None:
    calls = 0

    def importer(**_kwargs):
        nonlocal calls
        calls += 1
        raise chat_tool_service.ChatToolError(
            "fixture_import_failed",
            "Fixture import failed safely.",
            status_code=502,
            details={
                "writes_performed": False,
                "rollback_attempted": True,
                "rollback_completed": True,
            },
        )

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    with pytest.raises(chat_tool_service.ChatToolError) as first:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    chat_tool_service.reset_chat_tool_state_for_tests()
    with pytest.raises(chat_tool_service.ChatToolError) as replay:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert first.value.error_code == "fixture_import_failed"
    assert replay.value.error_code == "fixture_import_failed"
    assert replay.value.details["replayed_receipt"] is True
    assert replay.value.details["safe_to_retry"] is False
    assert calls == 1


def _create_nonterminal_journal(
    runtime: chat_tool_service.ChatToolRuntime,
    token: str,
):
    digest = chat_tool_service._token_digest(token)
    record = chat_tool_service._IMPORT_CONFIRMATIONS[digest]
    journal, _audit = chat_tool_service._new_import_journal(
        record=record,
        token_digest=digest,
    )
    store = ImportOperationJournalStore(
        runtime.resolved_import_journal_dir()
    )
    return store, store.create(journal)


def test_running_journal_never_invokes_importer_after_reset(
    tmp_path: Path,
) -> None:
    calls = 0

    def importer(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("importer must not run")

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    store, journal = _create_nonterminal_journal(runtime, token)
    store.update(
        journal.operation_id,
        expected_revision=journal.revision,
        expected_status="accepted",
        status="running",
        stage="body_import_started",
        heartbeat_at=chat_tool_service._utc_now(),
    )
    chat_tool_service.reset_chat_tool_state_for_tests()
    response = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert response["status"] == "in_progress"
    assert calls == 0


def test_orphaned_journal_never_invokes_importer(tmp_path: Path) -> None:
    calls = 0

    def importer(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("importer must not run")

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    store, journal = _create_nonterminal_journal(runtime, token)
    store.update(
        journal.operation_id,
        expected_revision=journal.revision,
        expected_status="accepted",
        status="orphaned",
        error={
            "error_code": "import_owner_aborted",
            "exception_type": "SystemExit",
            "error_stage": "confirmation_accepted",
        },
    )
    chat_tool_service.reset_chat_tool_state_for_tests()
    with pytest.raises(chat_tool_service.ChatToolError) as error:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert error.value.error_code == "chat_import_operation_orphaned"
    assert calls == 0


def test_same_token_two_threads_invokes_importer_once_with_journal(
    tmp_path: Path,
) -> None:
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def importer(*, record, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(3)
        return _valid_local_result(record)

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    results: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            results.append(
                chat_tool_service.import_document(
                    confirmation_token=token, confirmed=True, runtime=runtime
                )
            )
        except BaseException as exc:
            failures.append(exc)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert started.wait(2)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(3)
    second.join(3)
    assert failures == []
    assert {result["status"] for result in results} <= {
        "committed",
        "in_progress",
    }
    assert calls == 1


def test_base_exception_clears_in_memory_owner_and_marks_orphaned(
    tmp_path: Path,
) -> None:
    def importer(**_kwargs):
        raise KeyboardInterrupt()

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    digest = chat_tool_service._token_digest(token)
    with pytest.raises(KeyboardInterrupt):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert digest not in chat_tool_service._IMPORT_IN_PROGRESS
    assert _journal_for_token(runtime, token).status == "orphaned"


def test_successful_import_persists_receipt_before_return(
    tmp_path: Path,
) -> None:
    runtime, token, _pdf = _journal_local_case(
        tmp_path,
        importer=lambda *, record, **_kwargs: _valid_local_result(record),
    )
    response = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    journal = _journal_for_token(runtime, token)
    assert response["status"] == "committed"
    assert journal.status == "committed"
    assert journal.stage == "receipt_persisted"
    assert journal.completion_receipt["kind"] == "success"


def test_failed_import_persists_receipt_before_error(tmp_path: Path) -> None:
    def importer(**_kwargs):
        raise chat_tool_service.ChatToolError(
            "fixture_failed",
            "Fixture failed.",
            details={"writes_performed": False},
        )

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    journal = _journal_for_token(runtime, token)
    assert journal.status == "failed"
    assert journal.completion_receipt["kind"] == "failure"


def test_receipt_write_failure_after_import_never_runs_importer_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def importer(*, record, **_kwargs):
        nonlocal calls
        calls += 1
        return _valid_local_result(record)

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    original = ImportOperationJournalStore.update

    def fail_committed(self, operation_id, **kwargs):
        if kwargs.get("status") == "committed":
            raise OSError("fixture receipt write failure")
        return original(self, operation_id, **kwargs)

    monkeypatch.setattr(ImportOperationJournalStore, "update", fail_committed)
    with pytest.raises(chat_tool_service.ChatToolError) as error:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert error.value.error_code == "chat_import_receipt_persist_failed"
    replay = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert replay["status"] == "in_progress"
    assert calls == 1


def test_duplicate_digest_journal_fails_closed(tmp_path: Path) -> None:
    runtime, token, _pdf = _journal_local_case(
        tmp_path,
        importer=lambda *, record, **_kwargs: _valid_local_result(record),
    )
    _store, journal = _create_nonterminal_journal(runtime, token)
    duplicate = replace(journal, operation_id=uuid4().hex)
    duplicate_path = (
        runtime.resolved_import_journal_dir()
        / f"{duplicate.operation_id}.json"
    )
    duplicate_path.write_text(
        json.dumps(duplicate.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(chat_tool_service.ChatToolError) as error:
        chat_tool_service.import_document(
            confirmation_token=token, confirmed=True, runtime=runtime
        )
    assert error.value.error_code == "chat_import_journal_conflict"


def test_raw_confirmation_token_never_appears_in_journal(
    tmp_path: Path,
) -> None:
    runtime, token, _pdf = _journal_local_case(
        tmp_path,
        importer=lambda *, record, **_kwargs: _valid_local_result(record),
    )
    chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in runtime.resolved_import_journal_dir().glob("*.json")
    )
    assert token not in payload


def test_zotero_pdf_sha_is_bound_into_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    db_path.write_bytes(b"fixture")
    pdf_sha = "b" * 64
    preview = {
        "status": "ready",
        "zotero_item": {
            "zotero_item_key": "BOOK1",
            "title": "Zotero Journal Fixture",
            "item_type": "book",
        },
        "selected_attachment": {
            "zotero_attachment_key": "PDF1",
            "pdf_sha256": pdf_sha,
            "page_count": 10,
        },
        "source_revision": {"fingerprint": "c" * 64},
        "duplicate_check": {"duplicate_found": False},
        "extraction_ready": True,
        "estimated_chunks": 2,
        "blockers": [],
    }
    monkeypatch.setattr(
        chat_tool_service.zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: preview,
    )
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=db_path,
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
        commit_zotero_import=lambda *, record, **_kwargs: {
            "status": "committed",
            "document_id": 7,
            "title": record.title,
            "document_type": record.document_type,
            "chunk_count": 2,
            "writes_performed": True,
        },
    )
    registered = chat_tool_service.register_zotero_selected_book_import_preview(
        preview_token="fixture-preview",
        runtime=runtime,
    )
    token = registered["confirmation_token"]
    chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert _journal_for_token(runtime, token).source_pdf_sha256 == pdf_sha


def test_local_pdf_revision_fingerprint_changes_on_source_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"first")
    first = chat_tool_service.ImportConfirmation(
        source_path=source,
        source_sha256=hashlib.sha256(b"first").hexdigest(),
        source_size=5,
        source_mtime_ns=1,
        title="Fixture",
        document_type="paper",
        object_import_mode="full_document",
        page_count=1,
        expires_at=time.monotonic() + 60,
    )
    second = replace(
        first,
        source_sha256=hashlib.sha256(b"second").hexdigest(),
        source_size=6,
        source_mtime_ns=2,
    )
    assert chat_tool_service._local_source_revision_fingerprint(
        first
    ) != chat_tool_service._local_source_revision_fingerprint(second)


def test_memory_completion_without_journal_is_not_authoritative(
    tmp_path: Path,
) -> None:
    calls = 0

    def importer(*, record, **_kwargs):
        nonlocal calls
        calls += 1
        return _valid_local_result(record)

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    digest = chat_tool_service._token_digest(token)
    chat_tool_service._IMPORT_COMPLETIONS[digest] = (
        chat_tool_service.ImportCompletion(
            response={"status": "committed", "document_id": 999},
            expires_at=time.monotonic() + 60,
        )
    )
    response = chat_tool_service.import_document(
        confirmation_token=token, confirmed=True, runtime=runtime
    )
    assert response["document_id"] == 41
    assert calls == 1


def test_waiter_reads_terminal_journal_after_owner_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer_calls = 0
    owner_claimed = threading.Event()
    allow_create = threading.Event()
    original_create = ImportOperationJournalStore.create

    def importer(*, record, **_kwargs):
        nonlocal importer_calls
        importer_calls += 1
        return _valid_local_result(record)

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)

    def delayed_create(self, record):
        owner_claimed.set()
        assert allow_create.wait(3)
        return original_create(self, record)

    monkeypatch.setattr(ImportOperationJournalStore, "create", delayed_create)
    results: list[dict[str, object]] = []

    def invoke() -> None:
        results.append(
            chat_tool_service.import_document(
                confirmation_token=token, confirmed=True, runtime=runtime
            )
        )

    owner = threading.Thread(target=invoke)
    waiter = threading.Thread(target=invoke)
    owner.start()
    assert owner_claimed.wait(2)
    waiter.start()
    time.sleep(0.05)
    allow_create.set()
    owner.join(3)
    waiter.join(3)
    assert len(results) == 2
    assert sorted(result["already_completed"] for result in results) == [
        False,
        True,
    ]
    assert importer_calls == 1


def test_invalid_committed_result_is_persisted_as_failure(
    tmp_path: Path,
) -> None:
    calls = 0

    def importer(**_kwargs):
        nonlocal calls
        calls += 1
        return {
            "status": "committed",
            "document_id": True,
            "title": "",
            "document_type": "paper",
            "chunk_count": 0,
        }

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    with pytest.raises(chat_tool_service.ChatToolError) as error:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    assert error.value.error_code == "import_result_contract_invalid"
    assert _journal_for_token(runtime, token).status == "failed"
    replay_error = None
    try:
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    except chat_tool_service.ChatToolError as exc:
        replay_error = exc
    assert replay_error is not None
    assert replay_error.error_code == "import_result_contract_invalid"
    assert calls == 1


def test_failure_receipt_redacts_unsafe_details_and_paths(
    tmp_path: Path,
) -> None:
    raw_token = "raw-token-must-not-persist"

    def importer(**_kwargs):
        raise chat_tool_service.ChatToolError(
            "fixture_failed",
            "Failure at D:\\private\\fixture.pdf",
            details={
                "Authorization": raw_token,
                "error_stage": "D:\\private\\fixture.pdf",
                "warnings": [raw_token, "safe_warning_code"],
                "writes_performed": False,
            },
        )

    runtime, token, _pdf = _journal_local_case(tmp_path, importer=importer)
    with pytest.raises(chat_tool_service.ChatToolError):
        chat_tool_service.import_document(
            confirmation_token=token,
            confirmed=True,
            runtime=runtime,
        )
    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in runtime.resolved_import_journal_dir().glob("*.json")
    )
    assert raw_token not in payload
    assert "D:\\private" not in payload
    assert "safe_warning_code" in payload
