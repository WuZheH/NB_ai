from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workspace_read_service


def _book_payload() -> dict:
    return {
        "document_id": 7,
        "title": "Example Research Document",
        "chapters": [
            {
                "chapter_id": 11,
                "chapter_index": 2,
                "title": "Methods",
                "pdf_page_start": 12,
                "pdf_page_end": 24,
                "evidence_count": 9,
                "note_count": 3,
                "user_note_count": 2,
                "evidence_only_count": 1,
            }
        ],
    }


def test_workspace_state_contains_only_generic_read_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_read_service.book_chapter_service,
        "build_book_detail_payload",
        lambda _document_id: _book_payload(),
    )
    monkeypatch.setattr(
        workspace_read_service.library_service,
        "show_library_document",
        lambda _document_id: SimpleNamespace(pdf_path="papers/example.pdf", zotero_key="item-key"),
    )

    state = workspace_read_service.build_workspace_state(document_id=7, chapter_id=11)

    assert state["document"] == {"document_id": 7, "title": "Example Research Document"}
    assert state["current_chapter"]["title"] == "Methods"
    assert state["source_ingestion_status"]["chunk_count"] == 9
    assert state["notes_import_status"] == {
        "status": "available",
        "existing": 3,
        "user_notes": 2,
        "evidence_only": 1,
    }
    assert state["db_write_performed"] is False
    assert state["llm_called"] is False
    for obsolete in (
        "correction_review_status",
        "saved_review_state",
        "classification_review_status",
        "object_candidate_dry_run_summary",
        "graph_preview",
    ):
        assert obsolete not in state


def test_workspace_state_rejects_an_unknown_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_read_service.book_chapter_service,
        "build_book_detail_payload",
        lambda _document_id: _book_payload(),
    )

    with pytest.raises(workspace_read_service.WorkspaceReadError, match="chapter not found"):
        workspace_read_service.build_workspace_state(document_id=7, chapter_id=999)
