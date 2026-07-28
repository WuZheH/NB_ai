from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.chat_tools import ImportPreviewRequest
from app.services import chat_tool_service, pdf_import_classifier_service
from app.services import chat_pdf_production_import_service
from app.services.pdf_backend_service import load_fitz_backend
from app.services.library import document_deletion_service


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
        return {"status": "committed", "document_id": 9, "title": record.title, "chunk_count": 6}

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


def test_import_rejects_same_token_while_operation_in_progress(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    pdf = inbox / "fixture.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\nfixture"
    )

    runtime = chat_tool_service.ChatToolRuntime(
        db_path=tmp_path / "data" / "db.sqlite",
        data_dir=tmp_path / "data",
        inbox_root=inbox,
        classify_pdf=lambda _path, **_kwargs: {
            "title": "Fixture",
            "document_type": "paper",
            "object_import_mode": "full_document",
            "duplicate": False,
            "signals": {
                "page_count": 1,
            },
        },
        commit_import=lambda **_kwargs: {
            "status": "committed",
            "document_id": 1,
            "chunk_count": 1,
        },
    )

    preview = chat_tool_service.import_preview(
        runtime=runtime
    )
    token = preview["confirmation_token"]
    digest = chat_tool_service._token_digest(
        token
    )

    chat_tool_service._IMPORT_IN_PROGRESS.add(
        digest
    )

    try:
        with pytest.raises(
            chat_tool_service.ChatToolError
        ) as blocked:
            chat_tool_service.import_document(
                confirmation_token=token,
                confirmed=True,
                runtime=runtime,
            )

        assert (
            blocked.value.error_code
            == "chat_import_operation_in_progress"
        )
    finally:
        chat_tool_service._IMPORT_IN_PROGRESS.discard(
            digest
        )


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
    assert result["status"] == "completed"

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
