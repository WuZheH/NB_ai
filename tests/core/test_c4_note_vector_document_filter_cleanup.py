from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domains.retrieval import (
    fragment_repository,
    note_vector_index,
)
from app.domains.retrieval.result_contracts import (
    NotebookFragment,
    OpenTarget,
)
from app.services.library import document_deletion_service


def _note(document_id: int | None) -> NotebookFragment:
    return NotebookFragment(
        fragment_id="fragment-stable-1",
        source_type="zotero_annotation_comment",
        zotero_item_key="DW8Q4DWN",
        zotero_attachment_key="EHB9L2P8",
        zotero_annotation_key="KZDUWAIU",
        document_id=document_id,
        document_title="Probabilistic machine learning: an introduction",
        note_text="这个可以作为对数推入",
        selected_text="First, consider a set of arbitrary distributions.",
        content_hash="same-content-hash",
        provenance=[
            {
                "store": "zotero_snapshot",
                "table": "itemAnnotations",
                "row_id": 286,
            }
        ],
        open_target=OpenTarget(),
    )


def test_note_vector_sync_publishes_metadata_only_document_detach_without_reembedding(
    tmp_path,
) -> None:
    index_dir = tmp_path / "note-index"
    encode_calls: list[str] = []

    def encode(value: str) -> list[float]:
        encode_calls.append(value)
        return [1.0, 0.0]

    first = note_vector_index.build_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[_note(10)],
        encode_text=encode,
    )
    assert first["count"] == 1
    assert len(encode_calls) == 1

    before = note_vector_index.inspect_zotero_note_vector_document_impact(
        10,
        index_dir=index_dir,
    )
    assert before["document_entry_count"] == 1

    def must_not_reencode(_value: str) -> list[float]:
        raise AssertionError("metadata-only refresh must reuse the embedding")

    refreshed = note_vector_index.sync_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[_note(None)],
        encode_text=must_not_reencode,
    )

    assert refreshed["vector_write_performed"] is True
    assert refreshed["recomputed_count"] == 0
    assert refreshed["metadata_updated_count"] == 1

    after = note_vector_index.inspect_zotero_note_vector_document_impact(
        10,
        index_dir=index_dir,
    )
    assert after["document_entry_count"] == 0

    scoped = note_vector_index.search_zotero_note_vectors(
        "对数",
        source_types=("zotero_annotation_comment",),
        document_ids=(10,),
        index_dir=index_dir,
        encode_query=lambda _query: [1.0, 0.0],
    )
    assert scoped["results"] == []

    global_result = note_vector_index.search_zotero_note_vectors(
        "对数",
        source_types=("zotero_annotation_comment",),
        index_dir=index_dir,
        encode_query=lambda _query: [1.0, 0.0],
    )
    assert len(global_result["results"]) == 1
    assert global_result["results"][0]["fragment"]["document_id"] is None


def test_deletion_note_vector_cleanup_uses_fresh_post_delete_fragments(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    index_dir = data_dir / "vector_store" / "zotero_user_notes_v1"
    index_dir.mkdir(parents=True)
    (index_dir / note_vector_index.MANIFEST_NAME).write_text(
        "{}",
        encoding="utf-8",
    )

    impact_counts = iter((1, 0))

    monkeypatch.setattr(
        note_vector_index,
        "inspect_zotero_note_vector_document_impact",
        lambda document_id, *, index_dir: {
            "status": "ready",
            "document_id": document_id,
            "document_entry_count": next(impact_counts),
        },
    )

    monkeypatch.setattr(
        fragment_repository,
        "list_notebook_fragments",
        lambda **_kwargs: [_note(None)],
    )

    captured: dict[str, object] = {}

    def fake_sync(*, index_dir, fragments):
        captured["index_dir"] = index_dir
        captured["fragments"] = list(fragments)
        return {
            "status": "ready",
            "vector_write_performed": True,
            "metadata_updated_count": 1,
        }

    monkeypatch.setattr(
        note_vector_index,
        "sync_zotero_note_vectors",
        fake_sync,
    )

    runtime = document_deletion_service.DeletionRuntime(
        db_path=data_dir / "db" / "research_memory.db",
        data_dir=data_dir,
    )

    result = (
        document_deletion_service._sync_note_vectors_after_document_delete(
            SimpleNamespace(document_id=10),
            runtime=runtime,
        )
    )

    assert captured["index_dir"] == index_dir
    fragments = captured["fragments"]
    assert len(fragments) == 1
    assert fragments[0].document_id is None
    assert result["removed_document_entries"] == 1
    assert result["stale_document_entries"] == 0


def test_scoped_fragment_fetch_does_not_bypass_document_filter_via_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment_repository._FRAGMENT_CACHE.clear()
    fragment_repository._FRAGMENT_CACHE_REVISION = None

    fragment_repository.cache_notebook_fragments([_note(10)])

    fake_registry = SimpleNamespace(
        read=lambda **_kwargs: SimpleNamespace(fragments=())
    )
    monkeypatch.setattr(
        fragment_repository,
        "RetrievalSourceRegistry",
        lambda: fake_registry,
    )

    try:
        with pytest.raises(fragment_repository.NotebookFragmentNotFound):
            fragment_repository.get_notebook_fragments(
                ["fragment-stable-1"],
                document_ids=(11,),
            )
    finally:
        fragment_repository._FRAGMENT_CACHE.clear()
        fragment_repository._FRAGMENT_CACHE_REVISION = None
