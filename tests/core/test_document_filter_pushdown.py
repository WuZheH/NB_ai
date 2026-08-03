from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services import vector_store_service as service


# ---------------------------------------------------------------------------
# Fake LanceDB surface for recording call order and simulating filters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Field:
    name: str


class _Query:
    """Record query method calls in order and simulate filtering."""

    def __init__(
        self,
        table: "_Table",
        query_vector: list[float],
    ) -> None:
        self.table = table
        self.query_vector = query_vector
        self._where_clause: str | None = None
        self._limit_value: int | None = None
        # Full chain starts at search: the query object is created by it.
        self._called: list[str] = ["search"]

    def where(self, clause: str) -> "_Query":
        self._called.append("where")
        self._where_clause = clause
        return self

    def limit(self, value: int) -> "_Query":
        self._called.append("limit")
        self._limit_value = value
        return self

    def to_list(self) -> list[dict[str, Any]]:
        self._called.append("to_list")
        return self.table._filtered(  # noqa: SLF001
            self._where_clause,
            self._limit_value,
            call_log=self._called,
        )


class _Table:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        fields: tuple[str, ...] = ("document_id", "chunk_id", "passage_text", "vector"),
        fail_filter: bool = False,
        ignore_filter: bool = False,
    ) -> None:
        self.rows = rows
        self.schema = [_Field(name) for name in fields]
        self.fail_filter = fail_filter
        self.ignore_filter = ignore_filter
        self.queries: list[tuple[str | None, int | None]] = []
        self.query_call_orders: list[list[str]] = []
        self._call_order: list[str] = []

    def search(self, query_vector: list[float] | None = None) -> _Query:
        self._call_order.append("search")
        return _Query(self, query_vector or [])

    def _filtered(
        self,
        where_clause: str | None,
        limit_value: int | None,
        *,
        call_log: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.queries.append((where_clause, limit_value))
        if call_log is not None:
            self.query_call_orders.append(list(call_log))
        if self.fail_filter:
            raise RuntimeError("filter query failed")
        rows = list(self.rows)
        if where_clause and not self.ignore_filter:
            rows = self._apply_where(rows, where_clause)
        if limit_value is not None:
            rows = rows[:limit_value]
        return rows

    @staticmethod
    def _apply_where(
        rows: list[dict[str, Any]],
        clause: str,
    ) -> list[dict[str, Any]]:
        clause = clause.strip()
        if clause.startswith("document_id = "):
            target = int(clause.split("=")[-1].strip())
            return [r for r in rows if r.get("document_id") == target]
        if clause.startswith("document_id IN ("):
            inner = clause[len("document_id IN ("):].rstrip(")")
            ids = {int(v.strip()) for v in inner.split(",")}
            return [r for r in rows if r.get("document_id") in ids]
        return rows


class _Db:
    def __init__(self, tables: dict[str, _Table]) -> None:
        self.tables = tables

    def table_names(self) -> list[str]:
        return list(self.tables)

    def open_table(self, name: str) -> _Table:
        return self.tables[name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    document_id: int,
    chunk_id: int,
    passage_text: str = "",
    *,
    score: float = 0.5,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "title": f"Document {document_id}",
        "passage_text": passage_text or f"passage {document_id}:{chunk_id}",
        "heading_path": "",
        "_distance": score,
        "vector": [0.0] * 1024,
    }


def _patch_vector_store(
    monkeypatch: pytest.MonkeyPatch,
    db: _Db,
) -> None:
    """Replace vector_store_service internals so _search_table uses our fake DB."""
    monkeypatch.setattr(service, "open_vector_store", lambda _path=None: db)
    monkeypatch.setattr(service, "check_vector_store_status", lambda: {
        "available": True, "stale": False, "reason": None,
        "manifest": {}, "tables": {}, "freshness": {"state": "current"},
    })
    monkeypatch.setattr(service, "vector_table_fallback_reason", lambda _s, _t: None)
    # minimal model mock - we only need encode_text to return a dummy vector
    monkeypatch.setattr(
        service.local_embedding_service,
        "_load_model",
        lambda _timings: object(),
    )
    monkeypatch.setattr(
        service.local_embedding_service,
        "_encode_text",
        lambda _model, _text: [0.1] * 1024,
    )


def _patch_high_quality_document_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep prefilter tests independent from the runtime document database."""

    from app.services import high_quality_search_service

    def fake_document_metadata(
        document_ids: set[Any],
    ) -> dict[Any, dict[str, Any]]:
        metadata: dict[Any, dict[str, Any]] = {}

        for raw_document_id in document_ids:
            try:
                document_id = int(raw_document_id)
            except (TypeError, ValueError):
                continue

            item = {
                "title": f"Document {document_id}",
                "document_type": "paper",
                "object_import_mode": None,
            }
            metadata[document_id] = item
            metadata[str(document_id)] = item

        return metadata

    monkeypatch.setattr(
        high_quality_search_service,
        "_document_metadata_by_id",
        fake_document_metadata,
    )


# ===================================================================
# Test A: Global competition — target ranked outside global top-k
# ===================================================================

def test_prefilter_brings_target_into_candidates_when_globally_ranked_outside_topk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct 65 distractors from document 1, target from document 2 ranked ~66.

    Without prefilter the target is excluded by top-k limit=30.
    With prefilter on document 2, it must appear in the results.
    """
    rows: list[dict[str, Any]] = []
    # 65 distractors from document 1 (all ranked higher)
    for i in range(65):
        rows.append(_make_row(1, 1000 + i, f"distractor {i}", score=0.9 - i * 0.001))
    # Target from document 2 at rank ~66 (0.835 score — lower than distractors)
    target = _make_row(2, 2001, "R-precision ground-truth description", score=0.835)
    rows.append(target)
    # 10 more from document 2 (higher ranked within doc 2)
    for i in range(10):
        rows.append(_make_row(2, 2010 + i, f"doc2 passage {i}", score=0.84 - i * 0.001))

    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "_distance", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    # Without prefilter: global recall with limit=30 would miss target
    result_all = service.search_passage_vectors("R-precision", limit=30)
    global_ids = {r["chunk_id"] for r in result_all["results"]}
    assert 2001 not in global_ids, "target should be excluded from global top-30"

    # With prefilter on document 2: target must appear
    result_filtered = service.search_passage_vectors(
        "R-precision", limit=30, document_ids=(2,),
    )
    filtered_ids = {r["chunk_id"] for r in result_filtered["results"]}
    assert 2001 in filtered_ids, "prefilter must bring target into candidates"
    # No document 1 results
    assert all(
        r.get("document_id") != 1 for r in result_filtered["results"]
    ), "document 1 must not leak"

    # document_prefilter metadata
    assert result_filtered.get("document_prefilter", {}).get("applied") is True
    assert result_filtered["document_prefilter"]["document_ids"] == [2]

    # Unfiltered path still works
    result_none = service.search_passage_vectors("R-precision", limit=30, document_ids=None)
    assert "document_prefilter" not in result_none


# ===================================================================
# Test B: Call order — search → where → limit → to_list
# ===================================================================

def test_lancedb_call_order_is_search_where_limit_tolist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert the LanceDB call order is search → where → limit → to_list."""
    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    assert not table._call_order  # noqa: SLF001

    service.search_passage_vectors("test query", limit=10, document_ids=(2,))

    assert len(table.query_call_orders) == 1, "exactly one LanceDB query executed"
    chain = table.query_call_orders[0]
    assert chain == ["search", "where", "limit", "to_list"], (
        f"expected search → where → limit → to_list, got {chain}"
    )
    # Table-level: search must be the only table method invoked
    assert table._call_order == ["search"], f"Table-level: got {table._call_order}"  # noqa: SLF001
    # Where clause and limit values
    assert len(table.queries) == 1
    where_clause, limit_value = table.queries[0]
    assert where_clause == "document_id = 2", f"where clause mismatch: {where_clause}"
    assert limit_value == 10, f"limit mismatch: {limit_value}"


def test_without_document_ids_skips_where_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When document_ids is None, no where clause should be generated."""
    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    service.search_passage_vectors("test query", limit=10)

    assert len(table.queries) == 1
    where_clause, limit_value = table.queries[0]
    assert where_clause is None, "no where clause expected without document_ids"
    assert limit_value == 10


# ===================================================================
# Test C: Variant propagation — all variants receive same document_ids
# ===================================================================

def test_all_query_variants_receive_same_document_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every query variant must receive document_ids in the vector recall call."""
    from app.services import local_reranker_service

    variant_calls: list[tuple[str, tuple[int, ...] | None]] = []

    def fake_embedding(
        variant: str,
        *,
        limit: int,
        document_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        variant_calls.append((variant, document_ids))
        return {
            "results": [_make_row(2, 101, f"result for {variant}")],
            "retrieval_backend": "lancedb",
        }

    monkeypatch.setattr(
        local_reranker_service.local_embedding_service,
        "search_embedding_sidecar",
        fake_embedding,
    )
    monkeypatch.setattr(local_reranker_service, "_load_reranker", lambda _timings: object())
    monkeypatch.setattr(
        local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.5] * len(pairs),
    )

    result = local_reranker_service.search_reranker_sidecar(
        "R-precision", recall_limit=20, limit=5, document_ids=(2,),
    )

    assert len(variant_calls) >= 1
    for variant, doc_ids in variant_calls:
        assert doc_ids == (2,), f"variant '{variant}' got {doc_ids}, expected (2,)"

    # Reranker must receive the original query "R-precision" as the first element
    # (verified by the service itself using normalized_query for pairs)
    assert result["query"] == "R-precision"


# ===================================================================
# Test D: Multi-document filter
# ===================================================================

def test_multi_document_filter_generates_safe_in_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """document_ids=(2, 5) must generate document_id IN (2, 5)."""
    rows = [
        _make_row(1, 101),
        _make_row(2, 201),
        _make_row(3, 301),
        _make_row(5, 501),
    ]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = service.search_passage_vectors("test", limit=10, document_ids=(2, 5))

    assert len(table.queries) == 1
    where_clause, _limit_value = table.queries[0]
    assert where_clause == "document_id IN (2, 5)", f"unexpected where: {where_clause}"
    result_ids = {r["document_id"] for r in result["results"]}
    assert result_ids == {2, 5}, f"expected docs 2,5 got {result_ids}"
    assert 1 not in result_ids
    assert 3 not in result_ids


def test_single_document_generates_equals_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single document_id=2 must generate document_id = 2."""
    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    service.search_passage_vectors("test", limit=10, document_ids=(2,))

    where_clause, _limit = table.queries[0]
    assert where_clause == "document_id = 2"


# ===================================================================
# Test E: Input boundary tests via _normalize_document_ids
# ===================================================================

def test_normalize_document_ids_none_and_empty() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    assert _normalize_document_ids(None) is None
    assert _normalize_document_ids([]) is None


def test_normalize_document_ids_single_and_multi() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    assert _normalize_document_ids([2]) == (2,)
    assert _normalize_document_ids([5, 2]) == (2, 5)


def test_normalize_document_ids_deduplicates() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    assert _normalize_document_ids([2, 2]) == (2,)
    assert _normalize_document_ids([5, 2, 2, 5]) == (2, 5)


def test_normalize_document_ids_rejects_bool() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    with pytest.raises(ValueError, match="positive integers"):
        _normalize_document_ids([True])


def test_normalize_document_ids_rejects_zero_and_negative() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    with pytest.raises(ValueError, match="positive"):
        _normalize_document_ids([0])
    with pytest.raises(ValueError, match="positive"):
        _normalize_document_ids([-1])


def test_normalize_document_ids_rejects_string() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    with pytest.raises(ValueError):
        _normalize_document_ids(["2"])  # type: ignore[arg-type]


def test_normalize_document_ids_deterministic_order() -> None:
    from app.domains.retrieval.notebook_search_service import _normalize_document_ids

    result = _normalize_document_ids([9, 3, 7, 1])
    assert result == (1, 3, 7, 9)


# ===================================================================
# Test F: Old schema — missing document_id column
# ===================================================================

def test_old_schema_missing_document_id_falls_back_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When passage table lacks document_id column, no exception and prefilter not applied."""
    rows = [
        _make_row(1, 101),
        _make_row(2, 201),
    ]
    # Schema without document_id field
    table = _Table(
        rows,
        fields=("chunk_id", "passage_text", "vector"),
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = service.search_passage_vectors("test", limit=10, document_ids=(2,))

    # Must not throw
    assert result["status"] == "ok"
    # Prefilter not applied but reported
    assert result.get("document_prefilter", {}).get("applied") is False
    assert result["document_prefilter"]["available"] is False
    # No where clause in query
    where_clause, _limit = table.queries[0]
    assert where_clause is None, "no where expected for old schema"
    # Results still include all docs (post-filter must handle later)
    result_ids = {r["document_id"] for r in result["results"]}
    assert 1 in result_ids, "document 1 results present (post-filter will remove)"


# ===================================================================
# Test G: Backend ignores filter — defense-in-depth
# ===================================================================

def test_backend_ignore_filter_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake backend that ignores the where clause returns documents from all docs."""
    rows = [
        _make_row(1, 101),
        _make_row(2, 201),
        _make_row(3, 301),
    ]
    table = _Table(
        rows,
        fields=("document_id", "chunk_id", "passage_text", "vector"),
        ignore_filter=True,
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = service.search_passage_vectors("test", limit=10, document_ids=(2,))

    # Should not crash even though backend returned all docs
    assert result["status"] == "ok"
    # Prefilter was "applied" from our perspective (we sent the clause)
    assert result.get("document_prefilter", {}).get("applied") is True
    # But backend returned docs from all 3 documents
    result_ids = {r["document_id"] for r in result["results"]}
    assert result_ids == {1, 2, 3}, (
        "backend ignored filter; defense-in-depth post-filter will handle"
    )


def test_filter_failure_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real filter-execution failure must surface as document_prefilter_failed.

    The schema confirms document_id support, the WHERE query fails, and the
    embedding sidecar falls back to in-memory retrieval while preserving the
    failed status and the requested document_ids.
    """
    from app.services import local_embedding_service as embedding
    from app.services.local_embedding_service import EmbeddingCandidate

    rows = [
        _make_row(1, 101, "doc 1 passage"),
        _make_row(2, 201, "doc 2 passage"),
    ]
    table = _Table(
        rows,
        fields=("document_id", "chunk_id", "passage_text", "vector"),
        fail_filter=True,
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    monkeypatch.setattr(
        embedding,
        "_load_candidates",
        lambda: [
            EmbeddingCandidate(
                chunk_id=101,
                document_id=1,
                title="Doc 1",
                heading_path="",
                passage_text="doc 1 passage",
            ),
            EmbeddingCandidate(
                chunk_id=201,
                document_id=2,
                title="Doc 2",
                heading_path="",
                passage_text="doc 2 passage",
            ),
        ],
    )

    # Must NOT raise: the failure falls back to in-memory retrieval.
    result = embedding.search_embedding_sidecar("test", limit=10, document_ids=(2,))

    assert result["fallback_reason"] == "document_prefilter_failed"
    assert result["retrieval_backend"] == "fallback_in_memory"
    assert result["document_prefilter"] == {
        "applied": False,
        "available": True,
        "document_ids": [2],
    }
    # The in-memory fallback enforced document_ids: only document 2 remains.
    result_ids = {item["document_id"] for item in result["results"]}
    assert result_ids == {2}, f"expected only document 2, got {result_ids}"
    assert 1 not in result_ids


def test_vector_prefilter_execution_failure_returns_failed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WHERE-query execution failure on a document_id-capable schema must be
    returned as document_prefilter_failed, never re-raised or masked."""
    rows = [_make_row(1, 101)]
    table = _Table(
        rows,
        fields=("document_id", "chunk_id", "passage_text", "vector"),
        fail_filter=True,
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = service.search_passage_vectors("test", limit=10, document_ids=(2,))

    assert result["status"] == "document_prefilter_failed"
    assert result["results"] == []
    assert result["document_prefilter"] == {
        "applied": False,
        "available": True,
        "document_ids": [2],
    }


# ===================================================================
# Test H: Regression — empty document_ids preserves full-corpus search
# ===================================================================

def test_none_document_ids_returns_all_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When document_ids is None, all documents are returned (no filter)."""
    rows = [
        _make_row(1, 101, score=0.9),
        _make_row(2, 201, score=0.8),
        _make_row(3, 301, score=0.7),
    ]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "_distance", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = service.search_passage_vectors("test", limit=10)

    result_ids = {r["document_id"] for r in result["results"]}
    assert result_ids == {1, 2, 3}
    assert "document_prefilter" not in result


# ===================================================================
# Test: _build_document_id_where helper
# ===================================================================

def test_build_document_id_where_single() -> None:
    assert service._build_document_id_where((2,)) == "document_id = 2"  # noqa: SLF001


def test_build_document_id_where_multi() -> None:
    clause = service._build_document_id_where((2, 5))  # noqa: SLF001
    assert clause == "document_id IN (2, 5)"


def test_build_document_id_where_rejects_empty() -> None:
    with pytest.raises(ValueError):
        service._build_document_id_where(())  # noqa: SLF001


# ===================================================================
# Test: _build_document_id_where defensive validation
# ===================================================================

def test_build_document_id_where_rejects_bool() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where((True,))  # noqa: SLF001


def test_build_document_id_where_rejects_non_int() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where(("2",))  # type: ignore[arg-type]  # noqa: SLF001
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where((2.0,))  # type: ignore[arg-type]  # noqa: SLF001
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where((2, object()))  # type: ignore[arg-type]  # noqa: SLF001


def test_build_document_id_where_rejects_zero_and_negative() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where((0,))  # noqa: SLF001
    with pytest.raises(ValueError, match="positive integers"):
        service._build_document_id_where((-2,))  # noqa: SLF001


def test_build_document_id_where_deduplicates_and_sorts() -> None:
    clause = service._build_document_id_where((5, 2, 2, 5))  # noqa: SLF001
    assert clause == "document_id IN (2, 5)"


# ===================================================================
# Test: document_ids strict schema validation (before Pydantic coercion)
# ===================================================================

def test_schema_rejects_bool_string_float_zero_negative_document_ids() -> None:
    from pydantic import ValidationError

    from app.schemas.notebook_search import NotebookSearchRequest

    for bad in (
        [True],
        [1, True],
        ["1"],
        [2, "3"],
        [1.0],
        [1.5],
        [0],
        [-1],
        [0, 2],
    ):
        with pytest.raises(ValidationError):
            NotebookSearchRequest.model_validate(
                {"query": "q", "source_types": ["pdf_chunk"], "document_ids": bad}
            )


def test_schema_accepts_positive_ints_deduplicates_and_allows_empty() -> None:
    from app.schemas.notebook_search import NotebookSearchRequest

    request = NotebookSearchRequest.model_validate(
        {"query": "q", "source_types": ["pdf_chunk"], "document_ids": [5, 2, 2, 5]}
    )
    assert request.document_ids == [5, 2]

    empty = NotebookSearchRequest.model_validate(
        {"query": "q", "source_types": ["pdf_chunk"], "document_ids": []}
    )
    assert empty.document_ids == []

    omitted = NotebookSearchRequest.model_validate(
        {"query": "q", "source_types": ["pdf_chunk"]}
    )
    assert omitted.document_ids == []


# ===================================================================
# Test: document_prefilter propagation through the sidecar chain
# ===================================================================

def test_embedding_sidecar_propagates_applied_document_prefilter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import local_embedding_service as embedding

    monkeypatch.setattr(
        service,
        "load_chunk_page_metadata",
        lambda _chunk_ids: {},
    )

    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("document_id", "chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    result = embedding.search_embedding_sidecar("q", limit=10, document_ids=(2,))

    assert result["retrieval_backend"] == "lancedb"
    assert result["document_prefilter"] == {
        "applied": True,
        "available": True,
        "document_ids": [2],
    }


def test_reranker_and_high_quality_propagate_document_prefilter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import high_quality_search_service, local_reranker_service

    _patch_high_quality_document_metadata(monkeypatch)

    # Old schema (no document_id column): prefilter unavailable record must
    # flow embedding → reranker → high_quality.
    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)
    monkeypatch.setattr(
        high_quality_search_service,
        "require_runtime_machine_config",
        lambda: _ready_machine_config(),
    )
    monkeypatch.setattr(local_reranker_service, "_load_reranker", lambda _timings: object())
    monkeypatch.setattr(
        local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.9] * len(pairs),
    )

    expected = {"applied": False, "available": False, "document_ids": [2]}

    reranker_payload = local_reranker_service.search_reranker_sidecar(
        "q", recall_limit=20, limit=5, document_ids=(2,)
    )
    assert reranker_payload["document_prefilter"] == expected

    high_quality_payload = high_quality_search_service.search_high_quality(
        "q", include_objects=False, document_ids=(2,)
    )
    assert high_quality_payload["document_prefilter"] == expected


# ===================================================================
# Test: multi-variant conservative prefilter aggregation
# ===================================================================

def test_aggregate_document_prefilter_rules() -> None:
    from app.services.local_reranker_service import _aggregate_document_prefilter

    applied = {"applied": True, "available": True, "document_ids": [2]}
    failed = {"applied": False, "available": True, "document_ids": [2]}
    unavailable = {"applied": False, "available": False, "document_ids": [2]}

    # 1. No document_ids → no record.
    assert _aggregate_document_prefilter([("a", {})], None) is None
    assert _aggregate_document_prefilter([("a", {})], ()) is None

    # 4. All variants applied → applied.
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": applied}), ("b", {"document_prefilter": applied})],
        (2,),
    ) == applied

    # 3. Any variant missing the record → unavailable.
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": applied}), ("b", {})],
        (2,),
    ) == unavailable

    # 3. Any variant available=False → unavailable.
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": applied}), ("b", {"document_prefilter": unavailable})],
        (2,),
    ) == unavailable

    # 2 + 5. failed beats unavailable regardless of order.
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": unavailable}), ("b", {"document_prefilter": failed})],
        (2,),
    ) == failed
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": failed}), ("b", {"document_prefilter": unavailable})],
        (2,),
    ) == failed

    # 2 + 5. failed beats success regardless of order.
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": applied}), ("b", {"document_prefilter": failed})],
        (2,),
    ) == failed
    assert _aggregate_document_prefilter(
        [("a", {"document_prefilter": failed}), ("b", {"document_prefilter": applied})],
        (2,),
    ) == failed


def test_reranker_aggregates_document_prefilter_across_all_query_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure occurs in the SECOND variant; the aggregated result must be
    failed even though the first variant succeeded."""
    from app.services import local_reranker_service as reranker

    def fake_variants(query: str) -> list[str]:
        return ["variant-success", "variant-failed"]

    def fake_embedding(
        variant: str,
        *,
        limit: int,
        document_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        if variant == "variant-success":
            return {
                "results": [_make_row(2, 101, "doc2 vector hit")],
                "retrieval_backend": "lancedb",
                "document_prefilter": {
                    "applied": True,
                    "available": True,
                    "document_ids": [2],
                },
            }
        return {
            "results": [_make_row(2, 201, "doc2 fallback hit")],
            "retrieval_backend": "fallback_in_memory",
            "fallback_reason": "document_prefilter_failed",
            "document_prefilter": {
                "applied": False,
                "available": True,
                "document_ids": [2],
            },
        }

    monkeypatch.setattr(reranker, "_query_recall_variants", fake_variants)
    monkeypatch.setattr(
        reranker.local_embedding_service,
        "search_embedding_sidecar",
        fake_embedding,
    )
    monkeypatch.setattr(reranker, "_load_reranker", lambda _timings: object())
    monkeypatch.setattr(
        reranker,
        "_predict_scores",
        lambda _model, pairs: [0.9] * len(pairs),
    )

    result = reranker.search_reranker_sidecar(
        "test query", recall_limit=20, limit=5, document_ids=(2,)
    )

    assert result["document_prefilter"] == {
        "applied": False,
        "available": True,
        "document_ids": [2],
    }


def _ready_machine_config() -> Any:
    from pathlib import Path

    from app.runtime.machine_config import MachineConfig, MachineModelConfig

    return MachineConfig(
        path=Path("/fixture"),
        status="model_ready",
        embedding=MachineModelConfig(Path("/fixture/embedding"), "Qwen3-Embedding-0.6B"),
        reranker=MachineModelConfig(Path("/fixture/reranker"), "Qwen3-Reranker-0.6B"),
    )


def _notebook_fragment(
    fragment_id: str,
    *,
    document_id: int,
    chunk_id: int | None = None,
    text: str | None = None,
) -> Any:
    from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget

    return NotebookFragment(
        fragment_id=fragment_id,
        source_type="pdf_chunk",
        document_id=document_id,
        document_title=f"Document {document_id}",
        document_type="paper",
        chunk_id=chunk_id,
        text=text,
        content_hash="a" * 64,
        provenance=[{"store": "fixture"}],
        open_target=OpenTarget(zotero_disabled_reason="fixture"),
    )


# ===================================================================
# Test: notebook end-to-end — backend ignores the filter
# ===================================================================

def test_notebook_search_excludes_other_documents_when_backend_ignores_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: even when LanceDB ignores the where clause, the final
    notebook response must never contain documents outside the requested set."""
    from app.domains.retrieval import notebook_search_service as ns
    from app.services import local_reranker_service
    from app.services.retrieval.fragment_id import (
        canonical_source_locator,
        fragment_uuid,
    )

    _patch_high_quality_document_metadata(monkeypatch)

    rows = [_make_row(1, 101), _make_row(2, 201), _make_row(3, 301)]
    table = _Table(
        rows,
        fields=("document_id", "chunk_id", "passage_text", "vector"),
        ignore_filter=True,
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    fragment_id_by_doc = {
        row["document_id"]: fragment_uuid(
            canonical_source_locator(
                "pdf_chunk",
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
            )
        )
        for row in rows
    }

    def fake_details(
        ids: Any,
        *,
        document_ids: Any = None,
        registry: Any = None,
    ) -> list[Any]:
        assert registry is None
        assert set(document_ids or []) == {2}
        result = []
        for value in ids:
            document_id = next(
                doc for doc, fragment_id in fragment_id_by_doc.items() if fragment_id == value
            )
            chunk_id = next(
                row["chunk_id"] for row in rows if row["document_id"] == document_id
            )
            result.append(
                _notebook_fragment(
                    value,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=f"full source chunk {document_id}:{chunk_id}",
                )
            )
        return result

    monkeypatch.setattr(ns, "_requested_document_warnings", lambda _ids: [])
    monkeypatch.setattr(ns, "get_notebook_fragments", fake_details)
    monkeypatch.setattr(
        ns.high_quality_search_service,
        "require_runtime_machine_config",
        lambda: _ready_machine_config(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_load_reranker",
        lambda _timings: object(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.9] * len(pairs),
    )

    response = ns.search_notebook(
        {
            "query": "test query",
            "limit": 10,
            "source_types": ["pdf_chunk"],
            "document_ids": [2],
        }
    )

    # The where clause WAS sent to the (ignoring) backend.
    where_clause, _limit = table.queries[0]
    assert where_clause == "document_id = 2"
    # Prefilter applied from our perspective → no prefilter warning.
    assert response["warnings"] == []
    # Defense in depth: the final response contains only document 2.
    result_document_ids = {item["document_id"] for item in response["results"]}
    assert result_document_ids == {2}, (
        f"other documents leaked into the response: {result_document_ids}"
    )


# ===================================================================
# Test: notebook end-to-end — old schema, prefilter unavailable
# ===================================================================

def test_notebook_search_returns_stable_warning_when_prefilter_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old schema (no document_id column) must yield a stable warning and the
    final response must still exclude other documents."""
    from app.domains.retrieval import notebook_search_service as ns
    from app.services import local_reranker_service
    from app.services.retrieval.fragment_id import (
        canonical_source_locator,
        fragment_uuid,
    )

    _patch_high_quality_document_metadata(monkeypatch)

    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(rows, fields=("chunk_id", "passage_text", "vector"))
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    fragment_id_by_doc = {
        row["document_id"]: fragment_uuid(
            canonical_source_locator(
                "pdf_chunk",
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
            )
        )
        for row in rows
    }

    def fake_details(
        ids: Any,
        *,
        document_ids: Any = None,
        registry: Any = None,
    ) -> list[Any]:
        assert registry is None
        result = []
        for value in ids:
            document_id = next(
                doc for doc, fragment_id in fragment_id_by_doc.items() if fragment_id == value
            )
            chunk_id = next(
                row["chunk_id"] for row in rows if row["document_id"] == document_id
            )
            result.append(
                _notebook_fragment(
                    value,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=f"full source chunk {document_id}:{chunk_id}",
                )
            )
        return result

    monkeypatch.setattr(ns, "_requested_document_warnings", lambda _ids: [])
    monkeypatch.setattr(ns, "get_notebook_fragments", fake_details)
    monkeypatch.setattr(
        ns.high_quality_search_service,
        "require_runtime_machine_config",
        lambda: _ready_machine_config(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_load_reranker",
        lambda _timings: object(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.9] * len(pairs),
    )

    response = ns.search_notebook(
        {
            "query": "test query",
            "limit": 10,
            "source_types": ["pdf_chunk"],
            "document_ids": [2],
        }
    )

    assert {"code": "document_prefilter_unavailable"} in response["warnings"]
    result_document_ids = {item["document_id"] for item in response["results"]}
    assert result_document_ids == {2}, (
        f"other documents leaked into the response: {result_document_ids}"
    )


# ===================================================================
# Test: notebook end-to-end — vector prefilter execution failure
# ===================================================================

def test_notebook_search_returns_failed_warning_when_vector_prefilter_execution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real chain: LanceDB filter query fails → in-memory fallback → reranker →
    high_quality → notebook search. The warning must be the stable failed code,
    never unavailable, and the final response must exclude other documents."""
    from app.domains.retrieval import notebook_search_service as ns
    from app.services import local_embedding_service as embedding
    from app.services import local_reranker_service
    from app.services.local_embedding_service import EmbeddingCandidate
    from app.services.retrieval.fragment_id import (
        canonical_source_locator,
        fragment_uuid,
    )

    _patch_high_quality_document_metadata(monkeypatch)

    rows = [_make_row(1, 101), _make_row(2, 201)]
    table = _Table(
        rows,
        fields=("document_id", "chunk_id", "passage_text", "vector"),
        fail_filter=True,
    )
    db = _Db({service.PASSAGE_TABLE: table})
    _patch_vector_store(monkeypatch, db)

    # In-memory fallback candidates include BOTH documents; only the requested
    # one may survive.
    monkeypatch.setattr(
        embedding,
        "_load_candidates",
        lambda: [
            EmbeddingCandidate(
                chunk_id=101,
                document_id=1,
                title="Doc 1",
                heading_path="",
                passage_text="doc 1 passage",
            ),
            EmbeddingCandidate(
                chunk_id=201,
                document_id=2,
                title="Doc 2",
                heading_path="",
                passage_text="doc 2 passage",
            ),
        ],
    )

    fragment_id_by_doc = {
        row["document_id"]: fragment_uuid(
            canonical_source_locator(
                "pdf_chunk",
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
            )
        )
        for row in rows
    }

    def fake_details(
        ids: Any,
        *,
        document_ids: Any = None,
        registry: Any = None,
    ) -> list[Any]:
        assert registry is None
        assert set(document_ids or []) == {2}
        result = []
        for value in ids:
            document_id = next(
                doc for doc, fragment_id in fragment_id_by_doc.items() if fragment_id == value
            )
            chunk_id = next(
                row["chunk_id"] for row in rows if row["document_id"] == document_id
            )
            result.append(
                _notebook_fragment(
                    value,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=f"full source chunk {document_id}:{chunk_id}",
                )
            )
        return result

    monkeypatch.setattr(ns, "_requested_document_warnings", lambda _ids: [])
    monkeypatch.setattr(ns, "get_notebook_fragments", fake_details)
    monkeypatch.setattr(
        ns.high_quality_search_service,
        "require_runtime_machine_config",
        lambda: _ready_machine_config(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_load_reranker",
        lambda _timings: object(),
    )
    monkeypatch.setattr(
        local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.9] * len(pairs),
    )

    response = ns.search_notebook(
        {
            "query": "test query",
            "limit": 10,
            "source_types": ["pdf_chunk"],
            "document_ids": [2],
        }
    )

    assert {"code": "document_prefilter_failed"} in response["warnings"]
    assert {"code": "document_prefilter_unavailable"} not in response["warnings"]
    result_document_ids = {item["document_id"] for item in response["results"]}
    assert result_document_ids == {2}, (
        f"other documents leaked into the response: {result_document_ids}"
    )
