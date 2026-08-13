from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat_tools import ImportStatusRequest
from app.services import chat_tool_service
from app.services.import_operation_journal import ImportOperationJournalStore


@pytest.fixture(autouse=True)
def _reset_chat_tool_state() -> None:
    chat_tool_service.reset_chat_tool_state_for_tests()


def _local_case(
    tmp_path: Path,
    *,
    importer=None,
) -> tuple[chat_tool_service.ChatToolRuntime, dict[str, object]]:
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\nimport status fixture")
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Import Status Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {"page_count": 1},
        },
        commit_import=importer,
    )
    return runtime, chat_tool_service.import_preview(runtime=runtime)


def _accepted_journal(
    runtime: chat_tool_service.ChatToolRuntime,
    preview: dict[str, object],
):
    token = str(preview["confirmation_token"])
    digest = chat_tool_service._token_digest(token)
    record = chat_tool_service._IMPORT_CONFIRMATIONS[digest]
    journal, _audit = chat_tool_service._new_import_journal(
        record=record,
        token_digest=digest,
    )
    store = ImportOperationJournalStore(runtime.resolved_import_journal_dir())
    chat_tool_service._IMPORT_IN_PROGRESS.add(digest)
    return store, store.create(journal)


def _assert_status_shape(
    result: dict[str, object],
    *,
    operation_id: str,
    status: str,
    terminal: bool,
) -> None:
    assert result == {
        "status": status,
        "operation_id": operation_id,
        "document_id": result["document_id"],
        "title": result["title"],
        "document_type": result["document_type"],
        "chunk_count": result["chunk_count"],
        "terminal": terminal,
        "operation_in_progress": not terminal,
        "writes_performed": result["writes_performed"],
        "token_consumed": True,
        "safe_to_retry": False,
        "replayed_receipt": result["replayed_receipt"],
        "error_code": result["error_code"],
        "error_stage": result["error_stage"],
        "rollback_attempted": result["rollback_attempted"],
        "rollback_completed": result["rollback_completed"],
    }


def test_import_status_request_requires_opaque_operation_id() -> None:
    operation_id = "a" * 32
    assert ImportStatusRequest(operation_id=operation_id).operation_id == operation_id
    for invalid in ("A" * 32, "a" * 31, "g" * 32, "../journal", "a" * 64):
        with pytest.raises(Exception):
            ImportStatusRequest(operation_id=invalid)


def test_local_preview_preallocates_operation_id_and_journal_reuses_it(
    tmp_path: Path,
) -> None:
    runtime, preview = _local_case(tmp_path)
    operation_id = str(preview["operation_id"])
    assert re.fullmatch(r"[0-9a-f]{32}", operation_id)
    store, journal = _accepted_journal(runtime, preview)
    assert journal.operation_id == operation_id
    assert store.read(operation_id) == journal


def test_zotero_preview_registration_preallocates_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    db_path.write_bytes(b"fixture")
    pdf_sha = "b" * 64
    monkeypatch.setattr(
        chat_tool_service.zotero_selected_book_preview_service,
        "resolve_selected_book_preview_token",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "zotero_item": {
                "zotero_item_key": "BOOK1",
                "title": "Zotero Status Fixture",
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
        },
    )
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=db_path,
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
    )
    preview = chat_tool_service.register_zotero_selected_book_import_preview(
        preview_token="fixture-preview",
        runtime=runtime,
    )
    operation_id = str(preview["operation_id"])
    assert re.fullmatch(r"[0-9a-f]{32}", operation_id)
    _store, journal = _accepted_journal(runtime, preview)
    assert journal.operation_id == operation_id


def test_import_status_accepted_is_read_only_and_api_exposes_safe_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, preview = _local_case(tmp_path)
    store, journal = _accepted_journal(runtime, preview)
    path = runtime.resolved_import_journal_dir() / f"{journal.operation_id}.json"
    before_bytes = path.read_bytes()
    before_revision = journal.revision

    result = chat_tool_service.import_status(journal.operation_id, runtime=runtime)

    _assert_status_shape(
        result,
        operation_id=journal.operation_id,
        status="accepted",
        terminal=False,
    )
    assert result["writes_performed"] is None
    assert result["replayed_receipt"] is False
    assert path.read_bytes() == before_bytes
    assert store.read(journal.operation_id).revision == before_revision

    monkeypatch.setattr(chat_tool_service, "import_status", lambda operation_id: result)
    monkeypatch.setenv("SEARCH_CHAT_GATEWAY_TOKEN", "t" * 32)
    client = TestClient(app, client=("127.0.0.1", 50100))
    response = client.post(
        "/api/v1/chat-tools/import-status",
        headers={
            "Authorization": f"Bearer {'t' * 32}",
            "X-Search-Chat-Adapter": "mcp",
        },
        json={"operation_id": journal.operation_id},
    )
    assert response.status_code == 200
    assert response.json() == result


def test_import_status_api_authenticates_before_reading_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.delenv("SEARCH_CHAT_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        chat_tool_service,
        "import_status",
        lambda operation_id: calls.append(operation_id),
    )
    client = TestClient(app, client=("127.0.0.1", 50100))

    response = client.post(
        "/api/v1/chat-tools/import-status",
        json={"operation_id": "a" * 32},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "chat_gateway_not_configured"
    assert calls == []


def test_import_status_tracks_running_then_committed_receipt(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def finish(*, record, **_kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return {
            "status": "committed",
            "document_id": 41,
            "title": record.title,
            "document_type": record.document_type,
            "chunk_count": 3,
        }

    runtime, preview = _local_case(
        tmp_path,
        importer=finish,
    )
    result: dict[str, object] = {}

    def invoke() -> None:
        result.update(
            chat_tool_service.import_document(
                confirmation_token=str(preview["confirmation_token"]),
                confirmed=True,
                runtime=runtime,
            )
        )

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    assert entered.wait(timeout=10)
    operation_id = str(preview["operation_id"])
    store = ImportOperationJournalStore(runtime.resolved_import_journal_dir())
    running = store.read(operation_id)
    assert running is not None
    assert running.status == "running"
    status = chat_tool_service.import_status(operation_id, runtime=runtime)
    _assert_status_shape(
        status,
        operation_id=operation_id,
        status="running",
        terminal=False,
    )
    assert status["safe_to_retry"] is False

    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert result["operation_id"] == operation_id
    assert result["terminal"] is True

    journal_path = runtime.resolved_import_journal_dir() / f"{operation_id}.json"
    before = journal_path.read_bytes()
    committed = store.read(operation_id)
    assert committed is not None
    final = chat_tool_service.import_status(operation_id, runtime=runtime)
    _assert_status_shape(
        final,
        operation_id=operation_id,
        status="committed",
        terminal=True,
    )
    assert final["document_id"] == 41
    assert final["chunk_count"] == 3
    assert final["writes_performed"] is True
    assert final["replayed_receipt"] is True
    assert journal_path.read_bytes() == before
    assert store.read(operation_id).revision == committed.revision


def test_import_status_tracks_running_then_failed_receipt(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def fail(**_kwargs):
        entered.set()
        assert release.wait(timeout=10)
        raise chat_tool_service.ChatToolError(
            "fixture_late_failure",
            "Import failed safely.",
            details={
                "writes_performed": True,
                "error_stage": "generation_validate",
                "rollback_attempted": True,
                "rollback_completed": True,
            },
        )

    runtime, preview = _local_case(tmp_path, importer=fail)
    caught: list[chat_tool_service.ChatToolError] = []

    def invoke() -> None:
        try:
            chat_tool_service.import_document(
                confirmation_token=str(preview["confirmation_token"]),
                confirmed=True,
                runtime=runtime,
            )
        except chat_tool_service.ChatToolError as exc:
            caught.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    assert entered.wait(timeout=10)
    operation_id = str(preview["operation_id"])
    running = chat_tool_service.import_status(operation_id, runtime=runtime)
    _assert_status_shape(
        running,
        operation_id=operation_id,
        status="running",
        terminal=False,
    )
    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert [exc.error_code for exc in caught] == ["fixture_late_failure"]

    store = ImportOperationJournalStore(runtime.resolved_import_journal_dir())
    failed = store.read(operation_id)
    assert failed is not None
    journal_path = runtime.resolved_import_journal_dir() / f"{operation_id}.json"
    before = journal_path.read_bytes()
    final = chat_tool_service.import_status(operation_id, runtime=runtime)
    _assert_status_shape(
        final,
        operation_id=operation_id,
        status="failed",
        terminal=True,
    )
    assert final["error_code"] == "fixture_late_failure"
    assert final["error_stage"] == "generation_validate"
    assert final["rollback_attempted"] is True
    assert final["rollback_completed"] is True
    assert final["writes_performed"] is True
    assert final["safe_to_retry"] is False
    assert journal_path.read_bytes() == before
    assert store.read(operation_id).revision == failed.revision


def test_import_status_tracks_failed_receipt_without_leaking_secrets(
    tmp_path: Path,
) -> None:
    secret = "Bearer abcdefghijklmnopqrstuvwxyz012345"

    def fail(**_kwargs):
        raise chat_tool_service.ChatToolError(
            "fixture_failed",
            "Import failed safely.",
            details={
                "writes_performed": True,
                "error_stage": "vector_store_retire",
                "rollback_attempted": True,
                "rollback_completed": False,
                "cause_filename": "D:\\private\\secret.pdf",
                "Authorization": secret,
            },
        )

    runtime, preview = _local_case(tmp_path, importer=fail)
    with pytest.raises(chat_tool_service.ChatToolError) as failure:
        chat_tool_service.import_document(
            confirmation_token=str(preview["confirmation_token"]),
            confirmed=True,
            runtime=runtime,
        )
    assert failure.value.details["status"] == "failed"
    assert failure.value.details["operation_id"] == preview["operation_id"]
    assert failure.value.details["terminal"] is True
    result = chat_tool_service.import_status(str(preview["operation_id"]), runtime=runtime)
    _assert_status_shape(
        result,
        operation_id=str(preview["operation_id"]),
        status="failed",
        terminal=True,
    )
    assert result["error_code"] == "fixture_failed"
    assert result["error_stage"] == "vector_store_retire"
    assert result["rollback_attempted"] is True
    assert result["rollback_completed"] is False
    assert result["writes_performed"] is True
    serialized = json.dumps(result)
    assert secret not in serialized
    assert "private" not in serialized.lower()
    assert "confirmation" not in serialized.lower()


def test_import_status_owner_abort_is_orphaned_and_never_retryable(
    tmp_path: Path,
) -> None:
    def abort(**_kwargs):
        raise KeyboardInterrupt("fixture owner abort")

    runtime, preview = _local_case(tmp_path, importer=abort)
    with pytest.raises(KeyboardInterrupt, match="owner abort"):
        chat_tool_service.import_document(
            confirmation_token=str(preview["confirmation_token"]),
            confirmed=True,
            runtime=runtime,
        )
    operation_id = str(preview["operation_id"])
    result = chat_tool_service.import_status(operation_id, runtime=runtime)
    _assert_status_shape(
        result,
        operation_id=operation_id,
        status="orphaned",
        terminal=True,
    )
    assert result["error_code"] == "import_owner_aborted"
    assert result["safe_to_retry"] is False


def test_import_status_restart_derives_orphaned_without_mutating_journal(
    tmp_path: Path,
) -> None:
    runtime, preview = _local_case(tmp_path)
    store, journal = _accepted_journal(runtime, preview)
    path = runtime.resolved_import_journal_dir() / f"{journal.operation_id}.json"
    before = path.read_bytes()
    chat_tool_service.reset_chat_tool_state_for_tests()

    result = chat_tool_service.import_status(journal.operation_id, runtime=runtime)

    assert result["status"] == "orphaned"
    assert result["terminal"] is True
    assert result["operation_in_progress"] is False
    assert result["error_code"] == "import_owner_not_active"
    assert result["safe_to_retry"] is False
    assert path.read_bytes() == before
    assert store.read(journal.operation_id).status == "accepted"


def test_import_status_unknown_operation_fails_closed_without_creating_journal(
    tmp_path: Path,
) -> None:
    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        import_journal_dir=tmp_path / "journals",
    )
    with pytest.raises(chat_tool_service.ChatToolError) as error:
        chat_tool_service.import_status("a" * 32, runtime=runtime)
    assert error.value.error_code == "chat_import_operation_not_found"
    assert error.value.status_code == 404
    assert error.value.details == {"safe_to_retry": False}
    assert not runtime.resolved_import_journal_dir().exists()
