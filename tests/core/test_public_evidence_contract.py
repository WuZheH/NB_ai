from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.domains.retrieval import evidence_export_adapter
from app.domains.retrieval.public_evidence import (
    build_coherent_pdf_evidence,
    serialize_public_evidence,
)
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget


FORBIDDEN_PUBLIC_FIELDS = {
    "production_db",
    "zotero_snapshot",
    "documents",
    "document_sources",
    "zotero_pdf_sources",
    "knowledge_chunks",
    "itemAttachments",
    "itemAnnotations",
    "row_id",
    "chunk_id",
    "content_hash",
    "source_path",
    "pdf_path",
}


def _fragment(*, source_type: str = "pdf_chunk", chunk_id: int | None = 2) -> NotebookFragment:
    return NotebookFragment(
        fragment_id="11111111-1111-5111-8111-111111111111",
        source_type=source_type,
        zotero_item_key="ITEM1",
        zotero_attachment_key="ATT1",
        zotero_annotation_key="ANN1" if source_type != "pdf_chunk" else None,
        document_id=1,
        document_title="Public document",
        document_type="book",
        chunk_id=chunk_id,
        pdf_page=2,
        heading="Chapter A",
        section="Chapter A",
        text="Complete fallback sentence." if source_type == "pdf_chunk" else None,
        note_text="Relevant user note." if source_type != "pdf_chunk" else None,
        selected_text="Selected source text." if source_type != "pdf_chunk" else None,
        context_before="Before context.",
        context_after="After context.",
        tags=["bayes"],
        content_hash="a" * 64,
        provenance=[
            {"store": "production_db", "table": "knowledge_chunks", "row_id": 2}
        ],
        open_target=OpenTarget(
            pdf_url="/api/v1/library/documents/1/pdf#page=2",
            zotero_url="zotero://open-pdf/library/items/ATT1",
            can_open_pdf=True,
            can_open_zotero=True,
        ),
    )


def _coherent_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT,
                chunk_text TEXT NOT NULL,
                overlap_before TEXT,
                overlap_after TEXT,
                pdf_page_start INTEGER,
                pdf_page_end INTEGER,
                chapter_id INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    1,
                    0,
                    "Chapter A",
                    "Prior sentence. The observed values from a stale duplicate.",
                    None,
                    None,
                    1,
                    1,
                    7,
                ),
                (
                    2,
                    1,
                    1,
                    "Chapter A",
                    "rved values show observation noise and parameter uncertainty. This is illustrated in",
                    "obse",
                    "Figure 1, where the explanation",
                    2,
                    2,
                    7,
                ),
                (
                    3,
                    1,
                    2,
                    "Chapter A",
                    "Earlier overlap. Figure 1, where the explanation becomes complete. Next sentence.",
                    None,
                    None,
                    3,
                    3,
                    7,
                ),
                (4, 2, 0, "Other", "SECRET OTHER DOCUMENT.", None, None, 2, 2, 7),
                (5, 1, 3, "Chapter B", "SECRET OTHER CHAPTER.", None, None, 3, 3, 8),
            ],
        )


def test_coherent_builder_repairs_boundaries_overlap_and_page_range(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _coherent_db(db_path)

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert not result.text.startswith("rved")
    assert result.text.startswith("The observed values")
    assert result.text.endswith("becomes complete.")
    assert "obseobserved" not in result.text
    assert "stale duplicate" not in result.text
    assert "SECRET OTHER DOCUMENT" not in result.text
    assert "SECRET OTHER CHAPTER" not in result.text
    assert result.page_label == "1–3"


def test_coherent_builder_obeys_maximum_and_safely_falls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _coherent_db(db_path)
    limited = build_coherent_pdf_evidence(
        _fragment(), db_path=db_path, maximum_chars=72
    )
    assert len(limited.text) <= 72
    assert limited.text.endswith(".")

    missing = build_coherent_pdf_evidence(
        _fragment(chunk_id=999), db_path=db_path
    )
    assert missing.text == "Complete fallback sentence."


def test_public_serializer_is_a_recursive_whitelist(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _coherent_db(db_path)
    payload = serialize_public_evidence(
        _fragment(), selection_rank=1, db_path=db_path
    ).model_dump(mode="json")
    encoded = json.dumps(payload)

    assert payload["coherent_text"].startswith("The observed values")
    assert payload["provenance"] == {
        "source": "pdf",
        "document_title": "Public document",
        "page": 2,
        "zotero_item_key": "ITEM1",
        "zotero_attachment_key": "ATT1",
        "fragment_id": payload["fragment_id"],
    }
    assert FORBIDDEN_PUBLIC_FIELDS.isdisjoint(payload)
    assert set(payload["provenance"]).isdisjoint(FORBIDDEN_PUBLIC_FIELDS)
    assert "production_db" not in encoded
    assert "knowledge_chunks" not in encoded
    assert ":\\" not in encoded


def test_export_formats_share_complete_sanitized_records(monkeypatch) -> None:
    fragments = [_fragment(chunk_id=None), _fragment(source_type="zotero_annotation_comment", chunk_id=None)]
    fragments[1] = fragments[1].model_copy(
        update={"fragment_id": "22222222-2222-5222-8222-222222222222"}
    )
    monkeypatch.setattr(
        evidence_export_adapter,
        "get_notebook_fragments",
        lambda _ids: fragments,
    )

    markdown = evidence_export_adapter.render_notebook_evidence(
        [item.fragment_id for item in fragments], format="markdown", query="Bayes"
    )["content"]
    jsonl = evidence_export_adapter.render_notebook_evidence(
        [item.fragment_id for item in fragments], format="jsonl", query="Bayes"
    )["content"]
    json_text = evidence_export_adapter.render_notebook_evidence(
        [item.fragment_id for item in fragments], format="json", query="Bayes"
    )["content"]
    rows = [json.loads(line) for line in jsonl.splitlines()]
    json_rows = json.loads(json_text)["results"]

    assert markdown.count("## Evidence ") == len(rows) == len(json_rows) == 2
    assert rows == json_rows
    for row in rows:
        assert row["fragment_id"]
        assert row["document_title"] == "Public document"
        assert row["pdf_page"] == 2
        assert row["source_type"]
        assert row["coherent_text"] or row["user_note"]
    for row in rows:
        assert FORBIDDEN_PUBLIC_FIELDS.isdisjoint(row)
        assert set(row["provenance"]).isdisjoint(FORBIDDEN_PUBLIC_FIELDS)
        assert not any(
            value in {"documents", "document_sources", "knowledge_chunks"}
            for value in row["provenance"].values()
        )
    assert "Reranker score" not in markdown
    assert "```json" not in markdown
