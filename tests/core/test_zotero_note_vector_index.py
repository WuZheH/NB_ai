from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domains.retrieval.note_vector_index import (
    get_zotero_note_vector_status,
    search_zotero_note_vectors,
    sync_zotero_note_vectors,
)
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget


def _note(
    value: str,
    *,
    note_text: str,
    selected_text: str = "selected",
    content_hash: str = "a" * 64,
) -> NotebookFragment:
    return NotebookFragment(
        fragment_id=value,
        source_type="zotero_inspiration_note",
        server_note_id=value,
        document_id=1,
        document_title="Paper",
        note_text=note_text,
        selected_text=selected_text,
        context_before="before",
        context_after="after",
        tags=[],
        content_hash=content_hash,
        provenance=[],
        open_target=OpenTarget(zotero_disabled_reason="fixture"),
    )


def _encoder(calls: list[str]):
    def encode(text: str) -> list[float]:
        calls.append(text)
        checksum = float(sum(text.encode("utf-8")) % 17 + 1)
        return [checksum, 1.0, 0.5]

    return encode


def test_incremental_add_reuse_update_delete_and_search(tmp_path: Path) -> None:
    first = _note("11111111-1111-5111-8111-111111111111", note_text="first")
    second = _note("22222222-2222-5222-8222-222222222222", note_text="second")
    calls: list[str] = []
    initial = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[first, second],
        encode_text=_encoder(calls),
        built_at="2026-01-01T00:00:00+00:00",
    )
    assert initial["added_count"] == 2
    assert initial["recomputed_count"] == 2
    assert len(calls) == 2

    calls.clear()
    unchanged = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[first, second],
        encode_text=_encoder(calls),
    )
    assert unchanged["reused_count"] == 2
    assert unchanged["recomputed_count"] == 0
    assert unchanged["vector_write_performed"] is False
    assert calls == []

    # The primary source content_hash intentionally stays constant; selected text
    # still changes the derived vector hash and must trigger a recomputation.
    changed = second.model_copy(update={"selected_text": "changed selection"})
    calls.clear()
    updated = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[first, changed],
        encode_text=_encoder(calls),
    )
    assert updated["reused_count"] == 1
    assert updated["updated_count"] == 1
    assert len(calls) == 1

    calls.clear()
    deleted = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[changed],
        encode_text=_encoder(calls),
    )
    assert deleted["removed_count"] == 1
    assert deleted["recomputed_count"] == 0
    assert get_zotero_note_vector_status(index_dir=tmp_path)["validated_count"] == 1

    result = search_zotero_note_vectors(
        "query",
        index_dir=tmp_path,
        source_types=["zotero_inspiration_note"],
        document_ids=[1],
        encode_query=lambda _value: [1.0, 1.0, 0.5],
    )
    assert [item["fragment"]["fragment_id"] for item in result["results"]] == [
        changed.fragment_id
    ]
    assert result["fts_fallback_used"] is False


def test_failed_rebuild_keeps_previous_valid_manifest(tmp_path: Path) -> None:
    note = _note("33333333-3333-5333-8333-333333333333", note_text="stable")
    sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[note],
        encode_text=lambda _value: [1.0, 0.0, 0.0],
        built_at="2026-01-01T00:00:00+00:00",
    )
    manifest_path = tmp_path / "manifest.json"
    before = manifest_path.read_bytes()

    def fail(_value: str) -> list[float]:
        raise RuntimeError("simulated encoder failure")

    with pytest.raises(RuntimeError, match="simulated encoder failure"):
        sync_zotero_note_vectors(
            index_dir=tmp_path,
            fragments=[note.model_copy(update={"note_text": "changed"})],
            encode_text=fail,
            force=True,
        )

    assert manifest_path.read_bytes() == before
    assert get_zotero_note_vector_status(index_dir=tmp_path)["status"] == "ready"
    assert not list(tmp_path.glob("*.tmp"))
    manifest = json.loads(before)
    assert (tmp_path / manifest["index_file"]).is_file()
