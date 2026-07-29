from __future__ import annotations

import json

import pytest

from app.core.paths import DEFAULT_DB_PATH, ZOTERO_SNAPSHOT_PATH
from app.domains.retrieval.fragment_repository import list_notebook_fragments
from app.services.retrieval.evidence_export_service import export_evidence


def _require_live_repository() -> None:
    if not DEFAULT_DB_PATH.is_file() or not ZOTERO_SNAPSHOT_PATH.is_file():
        pytest.skip("production database and Zotero snapshot are intentionally absent")


def test_live_repository_keeps_note_and_selected_text_separate() -> None:
    _require_live_repository()
    comments = list_notebook_fragments(source_types=["zotero_annotation_comment"])
    inspirations = list_notebook_fragments(source_types=["zotero_inspiration_note"])

    assert comments
    assert all(item.note_text for item in comments)
    assert all(item.selected_text for item in comments)
    assert all(item.text is None for item in comments)
    assert inspirations
    assert any(item.note_text and item.selected_text for item in inspirations)
    assert all(item.source_type == "zotero_inspiration_note" for item in inspirations)


def test_notebook_markdown_jsonl_and_json_export_are_read_only_and_ordered() -> None:
    _require_live_repository()
    note = None
    pdf = None
    comments = list_notebook_fragments(
        source_types=["zotero_annotation_comment"]
    )
    for candidate in comments:
        if candidate.document_id is None:
            continue
        pdf_candidates = list_notebook_fragments(
            source_types=["pdf_chunk"],
            document_ids=[candidate.document_id],
        )
        if pdf_candidates:
            note = candidate
            pdf = pdf_candidates[0]
            break

    if note is None or pdf is None:
        pytest.skip(
            "live repository has no mapped Zotero annotation + PDF pair"
        )

    ids = [note.fragment_id, pdf.fragment_id]

    markdown = export_evidence(
        {"fragment_ids": ids, "format": "markdown", "query": "EDSR", "save_to_file": False}
    )
    assert markdown["output_path"] is None
    assert markdown["production_db_write_performed"] is False
    assert "### User note" in markdown["content"]
    assert "### Selected source text" in markdown["content"]
    pdf_section = markdown["content"].split("## Evidence 2", 1)[1]
    assert "### PDF text" in pdf_section
    assert "### User note" not in pdf_section

    jsonl = export_evidence({"fragment_ids": ids, "format": "jsonl", "save_to_file": False})
    rows = [json.loads(line) for line in jsonl["content"].splitlines()]
    assert [row["fragment_id"] for row in rows] == ids
    assert rows[0]["user_note"] == note.note_text
    assert rows[0]["selected_source_text"] == note.selected_text
    assert "content_hash" not in rows[0]
    assert "chunk_id" not in rows[0]
    assert "production_db" not in jsonl["content"]

    payload = export_evidence({"fragment_ids": ids, "format": "json", "save_to_file": False})
    assert [item["fragment_id"] for item in json.loads(payload["content"])["results"]] == ids
