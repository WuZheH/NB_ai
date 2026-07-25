from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.domains.retrieval import fragment_repository
from app.domains.retrieval import notebook_search_service
from app.domains.retrieval import note_vector_index
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget
from app.domains.retrieval.evidence_export_adapter import render_notebook_evidence
from app.services import high_quality_search_service
from app.services import local_reranker_service
from app.services import vector_store_service


def _fragment(fragment_id: str, source_type: str = "zotero_inspiration_note"):
    return NotebookFragment(
        fragment_id=fragment_id,
        source_type=source_type,
        document_id=5,
        document_title="fixture",
        note_text="fixture note" if source_type != "pdf_chunk" else None,
        text="fixture pdf text" if source_type == "pdf_chunk" else None,
        content_hash=f"hash-{fragment_id}",
        provenance=[{"source": "fixture"}],
        open_target=OpenTarget(),
    )


def test_fragment_cache_serves_fetch_and_export_without_registry_rebuild(monkeypatch):
    monkeypatch.setattr(
        fragment_repository,
        "_notebook_corpus_revision",
        lambda: ((1, 1), (2, 2)),
    )
    with fragment_repository._FRAGMENT_CACHE_LOCK:
        fragment_repository._FRAGMENT_CACHE.clear()
        fragment_repository._FRAGMENT_CACHE_REVISION = None

    fragment = _fragment("cached-fragment")
    fragment_repository.cache_notebook_fragments([fragment])

    def fail_read(*args, **kwargs):
        raise AssertionError("full RetrievalSourceRegistry.read must not run")

    monkeypatch.setattr(
        fragment_repository.RetrievalSourceRegistry,
        "read",
        fail_read,
    )

    fetched = fragment_repository.get_notebook_fragment(fragment.fragment_id)
    assert fetched.fragment_id == fragment.fragment_id

    rendered = render_notebook_evidence(
        [fragment.fragment_id],
        format="markdown",
        query="motion diffusion",
    )
    assert rendered["evidence_count"] == 1
    assert fragment.fragment_id in rendered["content"]


def test_note_vector_generation_is_not_reparsed_when_files_are_unchanged(
    tmp_path,
    monkeypatch,
):
    index_name = "notes-fixture.json"
    payload = {
        "schema_version": note_vector_index.INDEX_SCHEMA_VERSION,
        "content_hash": "fixture-content",
        "entries": [
            {
                "fragment_id": "fixture",
                "embedding": [1.0, 0.0],
            }
        ],
    }
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    index_path = tmp_path / index_name
    index_path.write_bytes(payload_bytes)

    manifest = {
        "status": "ready",
        "schema_version": note_vector_index.INDEX_SCHEMA_VERSION,
        "model": note_vector_index.local_embedding_service.MODEL_NAME,
        "dimension": 2,
        "normalization": note_vector_index.NORMALIZE_EMBEDDINGS,
        "count": 1,
        "content_hash": "fixture-content",
        "index_file": index_name,
        "index_sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    (tmp_path / note_vector_index.MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with note_vector_index._INDEX_CACHE_LOCK:
        note_vector_index._INDEX_CACHE.clear()

    original_read_bytes = Path.read_bytes
    reads = {"index": 0}

    def counted_read_bytes(self):
        if self == index_path:
            reads["index"] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    note_vector_index._load_existing(tmp_path, required=True)
    note_vector_index._load_existing(tmp_path, required=True)

    assert reads["index"] == 1


def test_reranker_uses_real_batching():
    observed = {}

    class FakeReranker:
        def predict(self, pairs, *, batch_size, show_progress_bar):
            observed["batch_size"] = batch_size
            observed["show_progress_bar"] = show_progress_bar
            return [float(index) for index in range(len(pairs))]

    pairs = [("query", f"candidate {index}") for index in range(30)]
    scores = local_reranker_service._raw_predict_scores(FakeReranker(), pairs)

    assert len(scores) == 30
    assert observed["batch_size"] == local_reranker_service.RERANKER_BATCH_SIZE
    assert observed["batch_size"] > 1
    assert observed["show_progress_bar"] is False


def test_pdf_candidate_hydration_is_scoped_to_hit_documents(monkeypatch):
    observed = {}

    def fake_get_notebook_fragments(fragment_ids, *, document_ids=None, registry=None):
        ids = list(fragment_ids)
        observed["document_ids"] = set(document_ids or [])
        return [_fragment(fragment_id, source_type="pdf_chunk") for fragment_id in ids]

    monkeypatch.setattr(
        notebook_search_service,
        "get_notebook_fragments",
        fake_get_notebook_fragments,
    )

    payload = {
        "papers": [
            {
                "document_id": 5,
                "title": "MLD",
                "top_passages": [
                    {
                        "chunk_id": 11,
                        "passage_text": "latent motion diffusion",
                    }
                ],
            },
            {
                "document_id": 7,
                "title": "Other",
                "top_passages": [
                    {
                        "chunk_id": 22,
                        "passage_text": "motion diffusion",
                    }
                ],
            },
        ]
    }

    candidates = notebook_search_service._pdf_candidates(
        payload,
        document_ids=set(),
    )

    assert len(candidates) == 2
    assert observed["document_ids"] == {5, 7}
def test_passage_only_high_quality_search_skips_object_retrieval(monkeypatch):
    monkeypatch.setattr(
        high_quality_search_service,
        "require_runtime_machine_config",
        lambda: None,
    )

    def fail_object_search(*args, **kwargs):
        raise AssertionError(
            "passage-only notebook search must not run object retrieval"
        )

    monkeypatch.setattr(
        high_quality_search_service.object_semantic_search_service,
        "search_semantic_objects",
        fail_object_search,
    )
    monkeypatch.setattr(
        high_quality_search_service.local_reranker_service,
        "search_reranker_sidecar",
        lambda *args, **kwargs: {
            "results": [],
            "retrieval_backend": "lancedb",
            "fallback_reason": None,
            "vector_store_status": {"available": True},
            "degraded_reason": None,
            "timing": {"total_ms": 1.0},
        },
    )

    result = high_quality_search_service.search_high_quality(
        "motion diffusion",
        include_objects=False,
    )

    assert result["status"] == "ok"
    assert result["objects"] == []
    assert result["papers"] == []
    assert result["debug"]["object_count"] == 0
    assert result["debug"]["object_total_candidates"] == 0
    assert result["debug"]["object_timing"]["total_ms"] == 0.0
def test_vector_record_freshness_uses_runtime_model_path(monkeypatch):
    runtime_path = r"D:\LEARNING\Tools\model_cache\Qwen3-Embedding-0.6B"
    monkeypatch.setattr(
        vector_store_service,
        "_active_embedding_model_path",
        lambda: runtime_path,
    )

    source = {
        "source_hash": "same-hash",
        "profile_version": vector_store_service.PASSAGE_PROFILE_VERSION,
        "embedding_model": vector_store_service.EMBEDDING_MODEL,
    }
    record = {
        **source,
        "embedding_model_path": runtime_path,
    }

    assert vector_store_service._record_stale(record, source) is False

    moved_record = {
        **record,
        "embedding_model_path": (
            r"D:\LEARNING\Tools\search\data\models\Qwen3-Embedding-0.6B"
        ),
    }
    assert vector_store_service._record_stale(moved_record, source) is True


def test_manifest_absolute_model_path_does_not_define_vector_semantics():
    manifest = {
        "backend": vector_store_service.BACKEND,
        "embedding_model": vector_store_service.EMBEDDING_MODEL,
        "embedding_model_path": r"D:\obsolete\model\location",
        "embedding_dim": 1024,
        "passage_profile_version": (
            vector_store_service.PASSAGE_PROFILE_VERSION
        ),
        "object_profile_version": (
            vector_store_service.OBJECT_PROFILE_VERSION
        ),
    }

    assert vector_store_service._stale_reason(manifest) is None


def test_passage_table_remains_usable_when_only_object_table_drifts():
    status = {
        "available": True,
        "stale": True,
        "reason": "vector_store_source_drift",
        "manifest": {
            "backend": vector_store_service.BACKEND,
            "embedding_model": vector_store_service.EMBEDDING_MODEL,
            "embedding_dim": 1024,
            "passage_profile_version": (
                vector_store_service.PASSAGE_PROFILE_VERSION
            ),
            "object_profile_version": (
                vector_store_service.OBJECT_PROFILE_VERSION
            ),
        },
        "tables": {
            vector_store_service.PASSAGE_TABLE: {
                "exists": True,
                "count": 11133,
            },
            vector_store_service.OBJECT_TABLE: {
                "exists": True,
                "count": 35,
            },
        },
        "freshness": {
            "tables": {
                "passages": {
                    "source_count": 11133,
                    "indexed_count": 11133,
                    "missing_count": 0,
                    "stale_count": 0,
                    "orphan_count": 0,
                },
                "objects": {
                    "source_count": 34,
                    "indexed_count": 35,
                    "missing_count": 0,
                    "stale_count": 0,
                    "orphan_count": 1,
                },
            },
        },
    }

    assert (
        vector_store_service.vector_table_fallback_reason(
            status,
            vector_store_service.PASSAGE_TABLE,
        )
        is None
    )
    assert (
        vector_store_service.vector_table_fallback_reason(
            status,
            vector_store_service.OBJECT_TABLE,
        )
        == "vector_store_source_drift"
    )
def test_active_embedding_model_path_uses_default_config_without_runtime_env(
    monkeypatch,
    tmp_path,
):
    configured_path = tmp_path / "Qwen3-Embedding-0.6B"
    configured_path.mkdir()

    config = SimpleNamespace(
        ready=True,
        embedding=SimpleNamespace(path=configured_path),
    )

    def runtime_config_missing():
        raise vector_store_service.MachineConfigUnavailable("config_missing")

    monkeypatch.setattr(
        vector_store_service,
        "require_runtime_machine_config",
        runtime_config_missing,
    )
    monkeypatch.setattr(
        vector_store_service,
        "default_machine_config_path",
        lambda: tmp_path / "machine-config.json",
    )
    monkeypatch.setattr(
        vector_store_service,
        "load_machine_config",
        lambda _path: config,
    )

    vector_store_service._active_embedding_model_path.cache_clear()
    try:
        assert (
            vector_store_service._active_embedding_model_path()
            == str(configured_path)
        )
    finally:
        vector_store_service._active_embedding_model_path.cache_clear()
