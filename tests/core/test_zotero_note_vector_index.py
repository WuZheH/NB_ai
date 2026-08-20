from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.domains.retrieval.note_vector_index import (
    get_zotero_note_vector_status,
    plan_zotero_note_vector_sync,
    search_zotero_note_vectors,
    sync_zotero_note_vectors,
    validate_zotero_note_vector_projection,
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


def test_plan_matches_incremental_add_change_remove_and_reuse(tmp_path: Path) -> None:
    retained = _note("11111111-1111-5111-8111-111111111111", note_text="retained")
    changed_before = _note(
        "22222222-2222-5222-8222-222222222222",
        note_text="before",
    )
    removed = _note("33333333-3333-5333-8333-333333333333", note_text="removed")
    sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[retained, changed_before, removed],
        encode_text=lambda _value: [1.0, 0.0, 0.0],
    )

    changed_after = changed_before.model_copy(update={"note_text": "after"})
    added = _note("44444444-4444-5444-8444-444444444444", note_text="added")
    current = [retained, changed_after, added]
    plan = plan_zotero_note_vector_sync(index_dir=tmp_path, fragments=current)

    assert plan["expected_total"] == 3
    assert plan["previous_total"] == 3
    assert plan["reused_count"] == 1
    assert plan["added_count"] == 1
    assert plan["removed_count"] == 1
    assert plan["changed_count"] == 1
    assert plan["expected_inference_count"] == 2

    calls: list[str] = []
    synced = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=current,
        encode_text=_encoder(calls),
    )
    assert synced["recomputed_count"] == 2
    assert len(calls) == 2
    validation = validate_zotero_note_vector_projection(
        index_dir=tmp_path,
        fragments=current,
    )
    assert validation["ready"] is True
    assert validation["missing_count"] == 0
    assert validation["orphan_count"] == 0
    assert validation["duplicate_count"] == 0


def test_invalid_existing_embedding_is_reembedded_not_reused(tmp_path: Path) -> None:
    note = _note("55555555-5555-5555-8555-555555555555", note_text="stable")
    sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[note],
        encode_text=lambda _value: [1.0, 0.0, 0.0],
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    index_path = tmp_path / manifest["index_file"]
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["entries"][0]["embedding"][0] = float("nan")
    encoded = json.dumps(payload).encode("utf-8")
    index_path.write_bytes(encoded)
    manifest["index_sha256"] = hashlib.sha256(encoded).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    plan = plan_zotero_note_vector_sync(index_dir=tmp_path, fragments=[note])
    assert plan["reused_count"] == 0
    assert plan["changed_count"] == 1
    assert plan["expected_inference_count"] == 1
    assert get_zotero_note_vector_status(index_dir=tmp_path)["status"] == "not_ready"

    calls: list[str] = []
    synced = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[note],
        encode_text=_encoder(calls),
    )
    assert synced["recomputed_count"] == 1
    assert len(calls) == 1
    assert get_zotero_note_vector_status(index_dir=tmp_path)["status"] == "ready"


def test_model_incompatibility_reembeds_only_authoritative_existing_notes(
    tmp_path: Path,
) -> None:
    first = _note("66666666-6666-5666-8666-666666666666", note_text="first")
    second = _note("77777777-7777-5777-8777-777777777777", note_text="second")
    sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[first, second],
        encode_text=lambda _value: [1.0, 0.0, 0.0],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    index_path = tmp_path / manifest["index_file"]
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    manifest["model"] = "incompatible-model"
    payload["model"] = "incompatible-model"
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    index_path.write_bytes(encoded)
    manifest["index_sha256"] = hashlib.sha256(encoded).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    plan = plan_zotero_note_vector_sync(
        index_dir=tmp_path,
        fragments=[first, second],
    )
    assert plan["template_compatible"] is False
    assert plan["added_count"] == 0
    assert plan["removed_count"] == 0
    assert plan["changed_count"] == 2
    assert plan["expected_inference_count"] == 2

    calls: list[str] = []
    synced = sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=[first, second],
        encode_text=_encoder(calls),
    )
    assert synced["recomputed_count"] == 2
    assert len(calls) == 2
    assert get_zotero_note_vector_status(index_dir=tmp_path)["status"] == "ready"


def test_r22_sized_authoritative_projection_plans_only_required_embeddings(
    tmp_path: Path,
) -> None:
    def fixture_note(index: int, *, text: str | None = None) -> NotebookFragment:
        fragment_id = str(uuid5(NAMESPACE_URL, f"read-r22-note-{index}"))
        return _note(fragment_id, note_text=text or f"note {index}")

    previous = [fixture_note(index) for index in range(161)]
    sync_zotero_note_vectors(
        index_dir=tmp_path,
        fragments=previous,
        encode_text=lambda _value: [1.0, 0.0, 0.0],
    )
    retained = previous[2:]
    retained[0] = retained[0].model_copy(update={"selected_text": "changed"})
    added = [fixture_note(index) for index in range(161, 635)]
    current = retained + added

    plan = plan_zotero_note_vector_sync(index_dir=tmp_path, fragments=current)

    assert len(current) == 633
    assert plan["expected_total"] == 633
    assert plan["previous_total"] == 161
    assert plan["added_count"] == 474
    assert plan["removed_count"] == 2
    assert plan["changed_count"] == 1
    assert plan["reused_count"] == 158
    assert plan["expected_inference_count"] == 475
