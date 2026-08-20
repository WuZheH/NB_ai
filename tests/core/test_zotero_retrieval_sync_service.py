from __future__ import annotations

from collections import Counter
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

from app.domains.retrieval.note_vector_index import sync_zotero_note_vectors
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget
from app.schemas.retrieval_fragment import (
    RETRIEVAL_FRAGMENT_NAMESPACE,
    RetrievalFragment,
)
from app.services import retrieval_generation_service as generations
from app.services import zotero_retrieval_sync_service as service
from app.services.retrieval.fts_index_service import build_retrieval_fts
from app.services.retrieval.source_registry import (
    ALL_SOURCE_TYPES,
    RetrievalRegistryResult,
)
from app.services.zotero_live_capture_service import ZoteroReadCapture


@pytest.fixture(autouse=True)
def isolate_generation_coordinator(monkeypatch: pytest.MonkeyPatch):
    coordinator = generations.ProductionGenerationCoordinator()
    monkeypatch.setattr(
        generations,
        "PRODUCTION_GENERATION_COORDINATOR",
        coordinator,
    )
    token = generations._PINNED_GENERATION.set(None)
    try:
        yield coordinator
    finally:
        generations._PINNED_GENERATION.reset(token)


class StaticRegistry:
    def __init__(
        self,
        *,
        research_db_path: Path,
        zotero_snapshot_path: Path,
        notes_root: Path,
        project_root: Path,
        fragments: list[RetrievalFragment],
    ) -> None:
        self.research_db_path = Path(research_db_path)
        self.zotero_snapshot_path = Path(zotero_snapshot_path)
        self.notes_root = Path(notes_root)
        self.project_root = Path(project_root)
        self._fragments = tuple(fragments)

    def read(self, **_kwargs: object) -> RetrievalRegistryResult:
        source_counts = Counter(item.source_type for item in self._fragments)
        return RetrievalRegistryResult(
            fragments=self._fragments,
            source_counts={
                source_type: int(source_counts.get(source_type, 0))
                for source_type in ALL_SOURCE_TYPES
            },
            origin_counts=dict(
                Counter(item.origin_kind for item in self._fragments)
            ),
            source_record_counts={},
            warnings=(),
        )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _retrieval_fragment(
    key: str,
    *,
    source_type: str,
    text: str,
    page_number: int | None = None,
) -> RetrievalFragment:
    locator = f"test://r22/{source_type}/{key}"
    return RetrievalFragment(
        fragment_id=str(uuid5(RETRIEVAL_FRAGMENT_NAMESPACE, locator)),
        display_id=key,
        source_type=source_type,
        origin_kind="native" if source_type != "pdf_chunk" else "manual_import",
        source_record_id=key,
        canonical_source_locator=locator,
        document_id=11,
        zotero_item_key="PARENT001",
        zotero_attachment_key="ATTACH01",
        zotero_annotation_key=key if source_type == "zotero_annotation_comment" else None,
        title="Synthetic Discrete Mathematics",
        page_number=page_number,
        page_label=str(page_number) if page_number is not None else None,
        text=text,
        note_comment=text if source_type == "zotero_annotation_comment" else None,
        context_status="not_requested",
        index_text=text,
        content_hash=_sha(text),
        adapter_version="r22.fixture.v1",
    )


def _notebook_fragment(fragment: RetrievalFragment) -> NotebookFragment:
    return NotebookFragment(
        fragment_id=fragment.fragment_id,
        source_type="zotero_annotation_comment",
        zotero_item_key=fragment.zotero_item_key,
        zotero_attachment_key=fragment.zotero_attachment_key,
        zotero_annotation_key=fragment.zotero_annotation_key,
        document_id=fragment.document_id,
        document_title=fragment.title,
        note_text=fragment.note_comment or fragment.text,
        selected_text=f"selected {fragment.display_id}",
        content_hash=fragment.content_hash,
        provenance=[],
        open_target=OpenTarget(),
    )


def _seed_runtime(tmp_path: Path) -> dict[str, object]:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "db" / "research_memory.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES('immutable')")
        connection.commit()
    notes = data / "notes"
    notes.mkdir()
    old_snapshot = tmp_path / "old-zotero.sqlite"
    old_snapshot.write_bytes(b"old pinned snapshot")

    pdf = _retrieval_fragment(
        "pdf-11-1",
        source_type="pdf_chunk",
        text="stable PDF passage",
        page_number=1,
    )
    retained = _retrieval_fragment(
        "note-retained",
        source_type="zotero_annotation_comment",
        text="retained comment",
    )
    changed_before = _retrieval_fragment(
        "note-changed",
        source_type="zotero_annotation_comment",
        text="old comment",
    )
    removed = _retrieval_fragment(
        "note-removed",
        source_type="zotero_annotation_comment",
        text="removed comment",
    )
    old_fragments = [pdf, retained, changed_before, removed]
    old_notes = [_notebook_fragment(item) for item in old_fragments[1:]]

    seed = data / "seed"
    fts_index = seed / generations.FTS_INDEX_NAME
    fts_manifest = seed / generations.FTS_MANIFEST_NAME
    registry = StaticRegistry(
        research_db_path=database,
        zotero_snapshot_path=old_snapshot,
        notes_root=notes,
        project_root=tmp_path,
        fragments=old_fragments,
    )
    build_retrieval_fts(
        index_path=fts_index,
        manifest_path=fts_manifest,
        registry=registry,
        target_root=data,
    )
    vector_store = seed / generations.VECTOR_STORE_NAME
    (vector_store / "passage-table").mkdir(parents=True)
    (vector_store / "passage-table" / "data.bin").write_bytes(b"frozen passage vectors")
    vector_manifest = seed / generations.VECTOR_MANIFEST_NAME
    vector_manifest.write_text('{"passage_count": 1}\n', encoding="utf-8")
    native_notes = seed / generations.NATIVE_NOTE_VECTOR_NAME
    sync_zotero_note_vectors(
        index_dir=native_notes,
        fragments=old_notes,
        encode_text=lambda _value: [1.0, 0.0, 0.0],
    )
    source = generations.RetrievalGenerationSnapshot(
        mode="legacy",
        generation_id=None,
        production_db_sha256=generations.sha256_file(database),
        fts_index_path=fts_index,
        fts_manifest_path=fts_manifest,
        vector_store_path=vector_store,
        vector_manifest_path=vector_manifest,
        native_note_vector_path=native_notes,
        zotero_snapshot_path=old_snapshot,
    )
    candidate = generations.prepare_candidate_generation(
        source,
        data_dir=data,
        generation_id="g-old",
    )
    active = generations.finalize_candidate_generation(
        candidate,
        production_db_sha256=generations.sha256_file(database),
    )
    generations.publish_active_generation(active, data_dir=data)

    changed_after = _retrieval_fragment(
        "note-changed",
        source_type="zotero_annotation_comment",
        text="new comment",
    )
    added = _retrieval_fragment(
        "note-added",
        source_type="zotero_annotation_comment",
        text="added comment",
    )
    new_fragments = [pdf, retained, changed_after, added]
    new_notes = [_notebook_fragment(item) for item in new_fragments[1:]]
    new_snapshot = tmp_path / "new-zotero.sqlite"
    new_snapshot.write_bytes(b"new pinned snapshot")
    metadata = tmp_path / "new-zotero.json"
    metadata.write_text("{}\n", encoding="utf-8")
    revision = generations.sha256_file(new_snapshot)
    capture = ZoteroReadCapture(
        revision=revision,
        snapshot_path=new_snapshot,
        metadata_path=metadata,
        captured_at="2026-08-20T00:00:00+00:00",
        source_db_mtime_ns=1,
        source_db_size=new_snapshot.stat().st_size,
        created=True,
    )
    return {
        "data": data,
        "database": database,
        "database_bytes": database.read_bytes(),
        "notes": notes,
        "active": active,
        "new_fragments": new_fragments,
        "new_notes": new_notes,
        "new_snapshot": new_snapshot,
        "capture": capture,
    }


def _install_pinned_registry(
    monkeypatch: pytest.MonkeyPatch,
    fixture: dict[str, object],
) -> None:
    def registry_factory(**kwargs: object) -> StaticRegistry:
        return StaticRegistry(
            research_db_path=Path(kwargs["research_db_path"]),
            zotero_snapshot_path=Path(kwargs["zotero_snapshot_path"]),
            notes_root=Path(kwargs["notes_root"]),
            project_root=Path(kwargs["project_root"]),
            fragments=list(fixture["new_fragments"]),
        )

    monkeypatch.setattr(service, "RetrievalSourceRegistry", registry_factory)
    monkeypatch.setattr(
        service,
        "list_notebook_fragments",
        lambda **_kwargs: list(fixture["new_notes"]),
    )


def test_combined_sync_reuses_passage_vectors_and_atomically_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _seed_runtime(tmp_path)
    _install_pinned_registry(monkeypatch, fixture)
    old_active = fixture["active"]
    old_generation_tree = generations.tree_fingerprint(old_active.generation_dir)
    encode_calls: list[str] = []

    result = service.sync_zotero_retrieval_generation(
        data_dir=fixture["data"],
        db_path=fixture["database"],
        notes_root=fixture["notes"],
        project_root=tmp_path,
        capture=fixture["capture"],
        encode_text=lambda value: encode_calls.append(value) or [0.0, 1.0, 0.0],
        generation_id="g-new",
        required_pdf_documents={11: 1},
        forbidden_pdf_pages={(11, 45)},
    )

    assert result["status"] == "ready"
    assert result["new_active_generation"] == "g-new"
    assert result["note_vector_reused"] == 1
    assert result["note_vector_added"] == 1
    assert result["note_vector_removed"] == 1
    assert result["note_vector_changed"] == 1
    assert result["new_note_embedding_inference_count"] == 2
    assert len(encode_calls) == 2
    assert result["pdf_passage_embedding_inference_count"] == 0
    assert result["pdf_passage_vector_rebuild"] is False
    assert (
        result["passage_vector_tree_sha256_before"]
        == result["passage_vector_tree_sha256_after"]
    )
    assert result["fts_missing"] == 0
    assert result["fts_orphan"] == 0
    assert result["fts_duplicate"] == 0
    assert result["source_diff_accounted"] is True
    assert result["required_pdf_documents"] == {
        "11": {"expected": 1, "actual": 1}
    }
    assert result["forbidden_pdf_pages"] == [
        {"document_id": 11, "page_number": 45, "normal_pdf_chunk_count": 0}
    ]
    assert fixture["database"].read_bytes() == fixture["database_bytes"]
    assert generations.tree_fingerprint(old_active.generation_dir) == old_generation_tree
    active = generations.resolve_active_retrieval_generation(
        data_dir=fixture["data"],
        db_path=fixture["database"],
        verify_fingerprints=True,
    )
    assert active.generation_id == "g-new"
    assert active.zotero_snapshot_path is not None
    assert generations.sha256_file(active.zotero_snapshot_path) == fixture["capture"].revision
    assert not generations.activation_state_path(fixture["data"]).exists()


def test_note_embedding_failure_keeps_old_pointer_and_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _seed_runtime(tmp_path)
    _install_pinned_registry(monkeypatch, fixture)
    pointer_before = generations.read_active_pointer_bytes(data_dir=fixture["data"])
    old_generation_tree = generations.tree_fingerprint(
        fixture["active"].generation_dir
    )

    def fail(_value: str) -> list[float]:
        raise RuntimeError("simulated note embedding failure")

    with pytest.raises(service.ZoteroRetrievalSyncError) as caught:
        service.sync_zotero_retrieval_generation(
            data_dir=fixture["data"],
            db_path=fixture["database"],
            notes_root=fixture["notes"],
            project_root=tmp_path,
            capture=fixture["capture"],
            encode_text=fail,
            generation_id="g-failed",
        )

    assert caught.value.code == "zotero_retrieval_sync_failed"
    assert generations.read_active_pointer_bytes(
        data_dir=fixture["data"]
    ) == pointer_before
    assert generations.tree_fingerprint(
        fixture["active"].generation_dir
    ) == old_generation_tree
    assert fixture["database"].read_bytes() == fixture["database_bytes"]
    assert not (
        Path(fixture["data"]) / generations.GENERATION_ROOT_NAME / "g-failed"
    ).exists()
    assert not generations.activation_state_path(fixture["data"]).exists()


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        ({"status": "ready", "reasons": []}, True),
        (
            {
                "status": "source_drift",
                "reasons": ["zotero_snapshot_sha256_changed"],
            },
            True,
        ),
        (
            {
                "status": "source_drift",
                "reasons": ["production_db_sha256_changed"],
            },
            False,
        ),
        (
            {
                "status": "source_drift",
                "reasons": ["local_markdown_aggregate_hash_changed"],
            },
            False,
        ),
        ({"status": "corrupt", "reasons": ["sqlite_integrity_failed"]}, False),
    ],
)
def test_only_normal_zotero_source_update_is_sync_eligible(
    status: dict[str, object],
    allowed: bool,
) -> None:
    if allowed:
        service._assert_sync_eligible(status)
        return
    with pytest.raises(service.ZoteroRetrievalSyncError) as caught:
        service._assert_sync_eligible(status)
    assert caught.value.code == "zotero_sync_unsupported_drift"
