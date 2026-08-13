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


def _boundary_db(
    path: Path,
    *,
    current_text: str,
    previous_text: str,
    following_text: str = "",
    previous_chapter: int = 7,
    current_chapter: int = 7,
) -> None:
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
                    previous_text,
                    None,
                    None,
                    10,
                    10,
                    previous_chapter,
                ),
                (
                    2,
                    1,
                    1,
                    "Chapter A",
                    current_text,
                    None,
                    None,
                    11,
                    11,
                    current_chapter,
                ),
                (
                    3,
                    1,
                    2,
                    "Chapter A",
                    following_text,
                    None,
                    None,
                    12,
                    12,
                    current_chapter,
                ),
                (
                    4,
                    2,
                    0,
                    "Chapter A",
                    "p(secret)=forbidden. (99.99)",
                    None,
                    None,
                    11,
                    11,
                    current_chapter,
                ),
            ],
        )


def test_caption_dependent_clause_recovers_numbered_equation(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _boundary_db(
        db_path,
        previous_text=(
            "Using Equation (3.38), the predictive distribution is Gaussian:\n"
            "p(y|x,D)=N(y|m,σ̂²(x))\n"
            "(11.124)\n"
            "“Probabilistic Machine Learning: An Introduction”. "
            "Online version. April 18, 2025"
        ),
        current_text=(
            "Figure 11.20: Posterior predictive samples. "
            "Generated by demo.ipynb. "
            "where σ̂²(x)=σ²+xᵀΣx is the predictive variance. "
            "The predicted variance contains observation noise and parameter uncertainty, "
            "representing increased uncertainty."
        ),
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert result.text.startswith("Using Equation (3.38)")
    assert "p(y|x,D)=N(y|m,σ̂²(x)) (11.124) where σ̂²(x)=σ²+xᵀΣx" in result.text
    assert "demo.ipynb. where" not in result.text
    assert result.page_label == "10–11"


def test_real_caption_boundary_recovers_formula_without_rewriting_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as connection:
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
                    "D",
                    (
                        "Using Equation (3.38), we can show that the posterior "
                        "predictive distribution at a test point x is also Gaussian:\n"
                        "p(y|x, D, σ2) = Z N(y|xTw, σ2)N(w|⌢µ,⌢Σ)dw (11.123)\n"
                        "= N(y|⌢µT x,⌢σ2(x)) (11.124)\n"
                        "“Example Book”. Online version. April 18, 2025"
                    ),
                    None,
                    None,
                    436,
                    436,
                    11,
                ),
                (
                    2,
                    1,
                    1,
                    "D",
                    (
                        "The blue circles in column 3 are the observed data points.\n"
                        "Adapted from Figure 3.7. Generated by "
                        "linreg_2d_bayes_demo.ipynb."
                    ),
                    None,
                    "where",
                    437,
                    437,
                    11,
                ),
                (
                    3,
                    1,
                    2,
                    "D",
                    (
                        "where ⌢σ2(x) ≜ σ2 + xT⌢Σx is the variance of the posterior "
                        "predictive distribution. The predicted variance depends on "
                        "observation noise and parameter uncertainty. This is illustrated in"
                    ),
                    None,
                    "Figure 11.21(b), where the error bars ",
                    437,
                    437,
                    11,
                ),
                (
                    4,
                    1,
                    3,
                    "D",
                    (
                        "Figure 11.21(b), where the error bars get larger away from "
                        "the training points, representing increased uncertainty. "
                        "The next independent sentence is context."
                    ),
                    None,
                    None,
                    437,
                    437,
                    11,
                ),
            ],
        )

    result = build_coherent_pdf_evidence(
        _fragment(chunk_id=3),
        db_path=db_path,
    )

    assert result.text.startswith("Using Equation (3.38)")
    assert "linreg_2d_bayes_demo.ipynb. where" not in result.text
    assert "observation noise" in result.text
    assert "parameter uncertainty" in result.text
    assert "representing increased uncertainty." in result.text
    assert result.text.endswith("representing increased uncertainty.")
    assert "⌢σ2(x) ≜ σ2 + xT⌢Σx" in result.text
    assert result.page_label == "436–437"


def test_caption_followed_by_independent_sentence_is_preserved(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _boundary_db(
        db_path,
        previous_text="Previous complete sentence.",
        current_text=(
            "Figure 2: Posterior samples. Generated by demo.ipynb. "
            "The predicted variance is a complete independent sentence."
        ),
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert result.text == (
        "Figure 2: Posterior samples. Generated by demo.ipynb. "
        "The predicted variance is a complete independent sentence."
    )


def test_equation_in_previous_block_completes_leading_where_clause(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _boundary_db(
        db_path,
        previous_text="This gives p(y|x)=N(y|m,v). (11.9)",
        current_text=(
            "where v=σ²+xᵀΣx includes both uncertainty terms. "
            "The result is well defined."
        ),
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert result.text.startswith("This gives p(y|x)=N(y|m,v). (11.9) where v=σ²+xᵀΣx")


def test_missing_equation_drops_dependent_clause_at_next_independent_sentence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.db"
    _boundary_db(
        db_path,
        previous_text="Previous prose without a display formula.",
        current_text=(
            "Figure 3: Caption. Generated by demo.ipynb. "
            "where the missing symbol depends on omitted material. "
            "The next independent sentence is safe evidence."
        ),
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert "where the missing symbol" not in result.text
    assert result.text.endswith("The next independent sentence is safe evidence.")


def test_page_header_and_footer_are_excluded_without_rewriting_text(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.db"
    original = "The symbol σ̂²(x) remains exact."
    _boundary_db(
        db_path,
        previous_text="Previous complete sentence.",
        current_text=(
            "11.7. Bayesian linear regression *\n"
            "407\n"
            f"{original}\n"
            "Author: Kevin P. Murphy. (C) MIT Press. CC-BY-NC-ND license"
        ),
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert result.text == original
    assert "407" not in result.text
    assert "Author:" not in result.text


def test_equation_antecedent_does_not_cross_chapter_or_document(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _boundary_db(
        db_path,
        previous_text="This gives p(y|x)=N(y|m,v). (11.9)",
        current_text=(
            "where v depends on a different chapter. "
            "The independent sentence remains."
        ),
        previous_chapter=6,
        current_chapter=7,
    )

    result = build_coherent_pdf_evidence(_fragment(), db_path=db_path)

    assert "p(secret)" not in result.text
    assert "where v depends" not in result.text
    assert result.text == "The independent sentence remains."


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
