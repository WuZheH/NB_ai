from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json

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
        zotero_item_key="ITEM0003",
        zotero_attachment_key="ATTCH002",
        zotero_annotation_key="ANNOT002",
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


def test_scoped_note_vector_refresh_ignores_unrelated_live_drift(
    tmp_path,
) -> None:
    index_dir = tmp_path / "note-index"

    target_before = _note(10)

    unrelated_before = _note(20).model_copy(
        update={
            "fragment_id": "unrelated-fragment",
            "zotero_annotation_key": "UNRELATED1",
            "note_text": "old unrelated note text",
            "content_hash": "unrelated-old-hash",
        }
    )

    note_vector_index.build_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[
            target_before,
            unrelated_before,
        ],
        encode_text=lambda _text: [1.0, 0.0],
    )

    _manifest, old_entries = (
        note_vector_index._load_existing(
            index_dir,
            required=True,
        )
    )

    old_unrelated = next(
        entry
        for entry in old_entries
        if entry["fragment_id"]
        == "unrelated-fragment"
    )

    unrelated_live_drift = unrelated_before.model_copy(
        update={
            "note_text":
                "new unrelated text that would require re-embedding",
            "content_hash":
                "unrelated-new-hash",
        }
    )

    def must_not_encode(_text: str) -> list[float]:
        raise AssertionError(
            "unrelated drift must not be re-embedded"
        )

    result = (
        note_vector_index
        .refresh_zotero_note_vector_document_scope(
            10,
            index_dir=index_dir,
            fragments=[
                _note(None),
                unrelated_live_drift,
            ],
            encode_text=must_not_encode,
        )
    )

    assert result["scoped_entry_count_before"] == 1
    assert result["scoped_entry_count_after"] == 0
    assert result["recomputed_count"] == 0
    assert result["unrelated_preserved_count"] == 1

    _manifest, new_entries = (
        note_vector_index._load_existing(
            index_dir,
            required=True,
        )
    )

    new_unrelated = next(
        entry
        for entry in new_entries
        if entry["fragment_id"]
        == "unrelated-fragment"
    )

    assert new_unrelated == old_unrelated

    scoped = note_vector_index.search_zotero_note_vectors(
        "对数",
        source_types=("zotero_annotation_comment",),
        document_ids=(10,),
        index_dir=index_dir,
        encode_query=lambda _query: [1.0, 0.0],
    )
    assert scoped["results"] == []


def test_scoped_note_vector_attach_maps_imported_document_without_reembedding(
    tmp_path,
) -> None:
    index_dir = tmp_path / "note-index"
    target_detached = _note(None)
    unrelated = _note(20).model_copy(
        update={
            "fragment_id": "unrelated-fragment",
            "zotero_annotation_key": "UNRELATED1",
            "note_text": "unrelated note text",
            "content_hash": "unrelated-content-hash",
        }
    )
    note_vector_index.build_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[target_detached, unrelated],
        encode_text=lambda _text: [1.0, 0.0],
    )
    _manifest, before_entries = note_vector_index._load_existing(
        index_dir,
        required=True,
    )
    unrelated_before = next(
        entry
        for entry in before_entries
        if entry["fragment_id"] == "unrelated-fragment"
    )

    result = (
        note_vector_index
        .attach_zotero_note_vector_document_scope(
            8,
            index_dir=index_dir,
            fragments=[_note(8)],
            encode_text=lambda _text: (
                (_ for _ in ()).throw(
                    AssertionError(
                        "metadata-only attach must reuse embedding"
                    )
                )
            ),
        )
    )

    assert result["scope"] == "affected_fragment_ids_only"
    assert result["scoped_entry_count_after"] == 1
    assert result["recomputed_count"] == 0
    assert result["full_rebuild_performed"] is False
    assert result["orphan_delete_performed"] is False

    _manifest, after_entries = note_vector_index._load_existing(
        index_dir,
        required=True,
    )
    unrelated_after = next(
        entry
        for entry in after_entries
        if entry["fragment_id"] == "unrelated-fragment"
    )
    assert unrelated_after == unrelated_before

    scoped = note_vector_index.search_zotero_note_vectors(
        "对数",
        source_types=("zotero_annotation_comment",),
        document_ids=(8,),
        index_dir=index_dir,
        encode_query=lambda _query: [1.0, 0.0],
    )
    assert len(scoped["results"]) == 1
    assert scoped["results"][0]["fragment"]["document_id"] == 8


def test_scoped_note_vector_attach_refuses_another_document_mapping(
    tmp_path,
) -> None:
    index_dir = tmp_path / "note-index"
    note_vector_index.build_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[_note(20)],
        encode_text=lambda _text: [1.0, 0.0],
    )

    with pytest.raises(
        ValueError,
        match="would steal another document mapping",
    ):
        note_vector_index.attach_zotero_note_vector_document_scope(
            8,
            index_dir=index_dir,
            fragments=[_note(8)],
            encode_text=lambda _text: [1.0, 0.0],
        )



def test_scoped_refresh_preserves_legacy_unrelated_entry_exactly(
    tmp_path,
) -> None:
    index_dir = tmp_path / "legacy-note-index"

    target = _note(10)
    unrelated = _note(20).model_copy(
        update={
            "fragment_id": "legacy-unrelated",
            "zotero_annotation_key": "LEGACY001",
            "note_text": "unrelated historical note",
            "content_hash": "legacy-unrelated-hash",
        }
    )

    note_vector_index.build_zotero_note_vectors(
        index_dir=index_dir,
        fragments=[target, unrelated],
        encode_text=lambda _text: [1.0, 0.0],
    )

    manifest_path = (
        index_dir / note_vector_index.MANIFEST_NAME
    )
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    index_path = index_dir / manifest["index_file"]
    payload = json.loads(
        index_path.read_text(encoding="utf-8")
    )

    # Convert the fixture to the real production legacy shape:
    # no fragment_snapshot_version and no per-entry snapshot hash.
    manifest.pop("fragment_snapshot_version", None)
    payload.pop("fragment_snapshot_version", None)

    for entry in payload["entries"]:
        entry.pop("fragment_snapshot_hash", None)

    payload_bytes = note_vector_index._json_bytes(payload)
    index_path.write_bytes(payload_bytes)

    manifest["index_sha256"] = hashlib.sha256(
        payload_bytes
    ).hexdigest()

    manifest_path.write_bytes(
        note_vector_index._json_bytes(manifest)
    )

    with note_vector_index._INDEX_CACHE_LOCK:
        note_vector_index._INDEX_CACHE.clear()

    old_manifest, old_entries = (
        note_vector_index._load_existing(
            index_dir,
            required=True,
        )
    )

    assert (
        old_manifest.get("fragment_snapshot_version")
        is None
    )

    old_unrelated = next(
        entry
        for entry in old_entries
        if entry["fragment_id"] == "legacy-unrelated"
    )

    assert "fragment_snapshot_hash" not in old_unrelated

    result = (
        note_vector_index
        .refresh_zotero_note_vector_document_scope(
            10,
            index_dir=index_dir,
            fragments=[_note(None)],
            encode_text=lambda _text: (
                (_ for _ in ()).throw(
                    AssertionError(
                        "metadata-only detach must reuse embedding"
                    )
                )
            ),
        )
    )

    assert result["scoped_entry_count_before"] == 1
    assert result["scoped_entry_count_after"] == 0
    assert result["recomputed_count"] == 0

    new_manifest, new_entries = (
        note_vector_index._load_existing(
            index_dir,
            required=True,
        )
    )

    assert (
        new_manifest.get("fragment_snapshot_version")
        is None
    )

    new_unrelated = next(
        entry
        for entry in new_entries
        if entry["fragment_id"] == "legacy-unrelated"
    )

    assert new_unrelated == old_unrelated
    assert "fragment_snapshot_hash" not in new_unrelated

    impact = (
        note_vector_index
        .inspect_zotero_note_vector_document_impact(
            10,
            index_dir=index_dir,
        )
    )
    assert impact["document_entry_count"] == 0


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
            "fragment_ids": ["fragment-stable-1"],
        },
    )

    monkeypatch.setattr(
        fragment_repository,
        "list_notebook_fragments",
        lambda **_kwargs: [_note(None)],
    )

    captured: dict[str, object] = {}

    def fake_refresh(
        document_id,
        *,
        index_dir,
        fragments,
    ):
        captured["document_id"] = document_id
        captured["index_dir"] = index_dir
        captured["fragments"] = list(fragments)
        return {
            "status": "ready",
            "vector_write_performed": True,
            "recomputed_count": 0,
            "metadata_updated_count": 1,
        }

    monkeypatch.setattr(
        note_vector_index,
        "refresh_zotero_note_vector_document_scope",
        fake_refresh,
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

    assert captured["document_id"] == 10
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
