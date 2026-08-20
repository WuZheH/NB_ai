from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, NOTES_DIR
from app.domains.retrieval.fragment_repository import list_notebook_fragments
from app.domains.retrieval.note_vector_index import (
    PASSAGE_TEMPLATE_VERSION,
    get_zotero_note_vector_status,
    plan_zotero_note_vector_sync,
    sync_zotero_note_vectors,
    validate_zotero_note_vector_projection,
)
from app.domains.retrieval.result_contracts import NOTE_SOURCE_TYPES, NotebookFragment
from app.schemas.retrieval_fragment import RetrievalFragment
from app.services import retrieval_generation_service as generations
from app.services import retrieval_generation_mutation_service as mutations
from app.services import zotero_live_capture_service
from app.services.retrieval.fts_index_service import build_retrieval_fts
from app.services.retrieval.fts_schema import (
    ORDINARY_TABLE,
    TRIGRAM_FTS_TABLE,
    UNICODE_FTS_TABLE,
)
from app.services.retrieval.fts_status_service import (
    DEFAULT_QUERY_ALIASES_PATH,
    aggregate_markdown_hash,
    get_index_status,
)
from app.services.retrieval.source_registry import RetrievalSourceRegistry


SYNC_WORKFLOW_VERSION = "zotero_retrieval_generation_sync.v1"
SUPPORTED_ZOTERO_DRIFT_REASONS = frozenset(
    {"zotero_snapshot_sha256_changed"}
)


class ZoteroRetrievalSyncError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def sync_zotero_retrieval_generation(
    *,
    data_dir: str | Path = DATA_DIR,
    db_path: str | Path = DEFAULT_DB_PATH,
    notes_root: str | Path = NOTES_DIR,
    project_root: str | Path | None = None,
    capture_dir: str | Path | None = None,
    source_db_path: str | Path | None = None,
    capture: zotero_live_capture_service.ZoteroReadCapture | None = None,
    encode_text: Callable[[str], list[float]] | None = None,
    generation_id: str | None = None,
    required_pdf_documents: Mapping[int, int] | None = None,
    forbidden_pdf_pages: Iterable[tuple[int, int]] = (),
) -> dict[str, Any]:
    """Synchronize Zotero-derived FTS and note vectors as one generation.

    The production database, PDF passage vectors, and the live Zotero database
    are immutable inputs.  A single SQLite-backup capture is pinned into the
    candidate and used by every derived artifact before one atomic pointer
    publication.
    """

    data_root = Path(data_dir).resolve(strict=False)
    database = Path(db_path).resolve(strict=False)
    notes = Path(notes_root).resolve(strict=False)
    registry_project_root = Path(project_root or data_root.parent).resolve(
        strict=False
    )
    captures = Path(
        capture_dir or data_root / "runtime" / "zotero_read_snapshots"
    ).resolve(strict=False)
    required_documents = {
        int(document_id): int(count)
        for document_id, count in dict(required_pdf_documents or {}).items()
    }
    forbidden_pages = {
        (int(document_id), int(page_number))
        for document_id, page_number in forbidden_pdf_pages
    }
    if not database.is_file() or database.is_symlink():
        raise ZoteroRetrievalSyncError(
            "zotero_sync_production_db_invalid",
            "The canonical production database is unavailable.",
        )

    previous = generations.resolve_active_retrieval_generation(
        data_dir=data_root,
        db_path=database,
        verify_fingerprints=True,
    )
    generations.verify_generation_database_revision(previous, database)
    previous_snapshot = previous.zotero_snapshot_path
    if previous_snapshot is None:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_active_snapshot_missing",
            "The active retrieval generation has no resolvable Zotero source.",
        )

    production_db_sha256_before = generations.sha256_file(database).lower()
    local_markdown_hash_before = aggregate_markdown_hash(notes)
    passage_vector_tree_before = generations.tree_fingerprint(
        previous.vector_store_path
    )
    vector_manifest_sha256_before = generations.sha256_file(
        previous.vector_manifest_path
    )
    active_status = get_index_status(
        index_path=previous.fts_index_path,
        manifest_path=previous.fts_manifest_path,
        production_db_path=database,
        zotero_snapshot_path=previous_snapshot,
        notes_root=notes,
        query_aliases_path=DEFAULT_QUERY_ALIASES_PATH,
    )
    _assert_sync_eligible(active_status)

    pinned = capture or _capture_snapshot(
        source_db_path=source_db_path,
        capture_dir=captures,
    )
    zotero_live_capture_service.verify_zotero_capture_file(
        pinned.snapshot_path,
        pinned.revision,
    )
    pinned_registry = RetrievalSourceRegistry(
        research_db_path=database,
        zotero_snapshot_path=pinned.snapshot_path,
        notes_root=notes,
        project_root=registry_project_root,
    )
    pinned_catalog = pinned_registry.read()
    pinned_fragments = list(pinned_catalog.fragments)
    _assert_unique_registry_fragments(pinned_fragments)
    note_fragments = list_notebook_fragments(
        source_types=NOTE_SOURCE_TYPES,
        registry=pinned_registry,
    )
    note_plan = plan_zotero_note_vector_sync(
        index_dir=previous.native_note_vector_path,
        fragments=note_fragments,
    )
    active_manifest = _read_json(previous.fts_manifest_path)
    snapshot_changed = (
        str(active_manifest.get("zotero_snapshot_sha256") or "").lower()
        != pinned.revision.lower()
    )
    note_projection_changed = any(
        int(note_plan[key]) > 0
        for key in (
            "added_count",
            "removed_count",
            "changed_count",
            "metadata_only_count",
        )
    )
    if (
        active_status.get("ready") is True
        and not snapshot_changed
        and not note_projection_changed
    ):
        return {
            "status": "unchanged",
            "workflow": SYNC_WORKFLOW_VERSION,
            "sync_performed": False,
            "pinned_snapshot_path": str(pinned.snapshot_path),
            "pinned_snapshot_sha256": pinned.revision,
            "old_active_generation": previous.generation_id,
            "new_active_generation": previous.generation_id,
            "fts_fragment_count_before": int(
                active_manifest.get("fragment_count") or 0
            ),
            "fts_fragment_count_after": int(
                active_manifest.get("fragment_count") or 0
            ),
            "note_vector_count_before": int(note_plan["previous_total"]),
            "note_vector_count_after": int(note_plan["expected_total"]),
            "note_vector_reused": int(note_plan["reused_count"]),
            "note_vector_added": 0,
            "note_vector_removed": 0,
            "note_vector_changed": 0,
            "new_note_embedding_inference_count": 0,
            "pdf_passage_vector_rebuild": False,
            "pdf_passage_embedding_inference_count": 0,
            "passage_vector_tree_sha256_before": passage_vector_tree_before,
            "passage_vector_tree_sha256_after": passage_vector_tree_before,
            "source_diff_accounted": True,
            "production_db_sha256_before": production_db_sha256_before,
            "production_db_sha256_after": production_db_sha256_before,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_write_performed": False,
            "note_vector_write_performed": False,
            "activation_performed": False,
            "capture_created": pinned.created,
        }

    old_rows = _read_fts_projection(previous.fts_index_path)
    validation_result: dict[str, Any] = {}
    note_sync_result: dict[str, Any] = {}
    fts_build_result: dict[str, Any] = {}
    candidate_snapshot_sha256: str | None = None

    try:
        with mutations.ProductionGenerationMutationSession(
            data_dir=data_root,
            db_path=database,
            generation_id=generation_id,
            database_mode="unchanged",
        ) as session:
            session.pin_unchanged_database_revision()
            if session.candidate is None:
                raise mutations.ProductionGenerationProtocolError(
                    "candidate generation is missing"
                )
            candidate_snapshot_sha256 = _stage_pinned_snapshot(
                source=pinned.snapshot_path,
                expected_sha256=pinned.revision,
                target=session.candidate.zotero_snapshot_path,
            )
            fts_build_result = build_retrieval_fts(
                index_path=session.candidate.fts_index_path,
                manifest_path=session.candidate.fts_manifest_path,
                registry=pinned_registry,
                query_aliases_path=DEFAULT_QUERY_ALIASES_PATH,
                target_root=data_root,
            )
            note_sync_result = sync_zotero_note_vectors(
                index_dir=session.candidate.native_note_vector_path,
                fragments=note_fragments,
                encode_text=encode_text,
            )
            if int(note_sync_result.get("recomputed_count") or 0) != int(
                note_plan["expected_inference_count"]
            ):
                raise ZoteroRetrievalSyncError(
                    "zotero_note_embedding_accounting_mismatch",
                    "The note-vector inference count did not match the preflight plan.",
                )
            session.mark_candidate_synced()

            def validate_candidate(
                candidate: generations.CandidateGeneration,
                database_sha256: str,
            ) -> None:
                nonlocal validation_result
                validation_result = _validate_combined_candidate(
                    fts_index_path=candidate.fts_index_path,
                    fts_manifest_path=candidate.fts_manifest_path,
                    native_note_vector_path=candidate.native_note_vector_path,
                    vector_store_path=candidate.vector_store_path,
                    vector_manifest_path=candidate.vector_manifest_path,
                    zotero_snapshot_path=candidate.zotero_snapshot_path,
                    expected_snapshot_sha256=pinned.revision,
                    expected_database_sha256=database_sha256,
                    database=database,
                    notes=notes,
                    pinned_fragments=pinned_fragments,
                    note_fragments=note_fragments,
                    old_rows=old_rows,
                    passage_vector_tree_before=passage_vector_tree_before,
                    vector_manifest_sha256_before=vector_manifest_sha256_before,
                    required_pdf_documents=required_documents,
                    forbidden_pdf_pages=forbidden_pages,
                )

            session.validate_candidate(validate_candidate)
            finalized = session.finalize_candidate(
                profile_versions={
                    "zotero_retrieval_sync": SYNC_WORKFLOW_VERSION,
                    "zotero_snapshot_sha256": pinned.revision,
                    "note_passage_template": PASSAGE_TEMPLATE_VERSION,
                }
            )
            session.begin_activation()
            session.publish_active()

            def validate_active(
                snapshot: generations.RetrievalGenerationSnapshot,
            ) -> None:
                if snapshot.zotero_snapshot_path is None:
                    raise ZoteroRetrievalSyncError(
                        "zotero_sync_active_snapshot_missing",
                        "The published generation lost its pinned Zotero source.",
                    )
                _validate_combined_candidate(
                    fts_index_path=snapshot.fts_index_path,
                    fts_manifest_path=snapshot.fts_manifest_path,
                    native_note_vector_path=snapshot.native_note_vector_path,
                    vector_store_path=snapshot.vector_store_path,
                    vector_manifest_path=snapshot.vector_manifest_path,
                    zotero_snapshot_path=snapshot.zotero_snapshot_path,
                    expected_snapshot_sha256=pinned.revision,
                    expected_database_sha256=production_db_sha256_before,
                    database=database,
                    notes=notes,
                    pinned_fragments=pinned_fragments,
                    note_fragments=note_fragments,
                    old_rows=old_rows,
                    passage_vector_tree_before=passage_vector_tree_before,
                    vector_manifest_sha256_before=vector_manifest_sha256_before,
                    required_pdf_documents=required_documents,
                    forbidden_pdf_pages=forbidden_pages,
                )

            session.verify_active(validate_active)
            session.clear_activation()
    except ZoteroRetrievalSyncError:
        raise
    except Exception as exc:
        raise ZoteroRetrievalSyncError(
            "zotero_retrieval_sync_failed",
            "The Zotero retrieval generation sync failed safely.",
            details={"cause_type": type(exc).__name__},
        ) from exc

    active = generations.resolve_active_retrieval_generation(
        data_dir=data_root,
        db_path=database,
        verify_fingerprints=True,
    )
    post_status = get_index_status(
        index_path=active.fts_index_path,
        manifest_path=active.fts_manifest_path,
        production_db_path=database,
        zotero_snapshot_path=active.zotero_snapshot_path,
        notes_root=notes,
        query_aliases_path=DEFAULT_QUERY_ALIASES_PATH,
    )
    if post_status.get("ready") is not True or post_status.get("reasons") != []:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_post_activation_not_ready",
            "The activated retrieval generation did not remain ready.",
        )
    post_note_status = get_zotero_note_vector_status(
        index_dir=active.native_note_vector_path
    )
    if post_note_status.get("status") != "ready":
        raise ZoteroRetrievalSyncError(
            "zotero_sync_post_activation_note_vectors_not_ready",
            "The activated Zotero note-vector index is not ready.",
        )
    production_db_sha256_after = generations.sha256_file(database).lower()
    local_markdown_hash_after = aggregate_markdown_hash(notes)
    passage_vector_tree_after = generations.tree_fingerprint(
        active.vector_store_path
    )
    vector_manifest_sha256_after = generations.sha256_file(
        active.vector_manifest_path
    )
    if production_db_sha256_after != production_db_sha256_before:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_production_db_changed",
            "The production database changed during Zotero retrieval sync.",
        )
    if local_markdown_hash_after != local_markdown_hash_before:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_local_markdown_changed",
            "Local Markdown changed during Zotero retrieval sync.",
        )
    if (
        passage_vector_tree_after != passage_vector_tree_before
        or vector_manifest_sha256_after != vector_manifest_sha256_before
    ):
        raise ZoteroRetrievalSyncError(
            "zotero_sync_passage_vectors_changed",
            "PDF passage vector content changed during Zotero retrieval sync.",
        )

    diff = validation_result["fts_diff"]
    return {
        "status": "ready",
        "workflow": SYNC_WORKFLOW_VERSION,
        "sync_performed": True,
        "pinned_snapshot_path": str(active.zotero_snapshot_path),
        "pinned_snapshot_sha256": candidate_snapshot_sha256,
        "old_active_generation": previous.generation_id,
        "new_active_generation": active.generation_id,
        "fts_fragment_count_before": len(old_rows),
        "fts_fragment_count_after": int(validation_result["fts_actual"]),
        "fts_expected": int(validation_result["fts_expected"]),
        "fts_actual": int(validation_result["fts_actual"]),
        "fts_missing": int(validation_result["fts_missing"]),
        "fts_orphan": int(validation_result["fts_orphan"]),
        "fts_duplicate": int(validation_result["fts_duplicate"]),
        "note_vector_count_before": int(note_plan["previous_total"]),
        "note_vector_count_after": int(note_plan["expected_total"]),
        "note_vector_reused": int(note_sync_result.get("reused_count") or 0),
        "note_vector_added": int(note_plan["added_count"]),
        "note_vector_removed": int(note_plan["removed_count"]),
        "note_vector_changed": int(note_plan["changed_count"]),
        "note_vector_metadata_only": int(note_plan["metadata_only_count"]),
        "new_note_embedding_inference_count": int(
            note_sync_result.get("recomputed_count") or 0
        ),
        "note_added_by_source_type": note_plan["added_by_source_type"],
        "note_removed_by_source_type": note_plan["removed_by_source_type"],
        "note_changed_by_source_type": note_plan["changed_by_source_type"],
        "note_reused_by_source_type": note_plan["reused_by_source_type"],
        "pdf_passage_vector_rebuild": False,
        "pdf_passage_embedding_inference_count": 0,
        "passage_vector_tree_sha256_before": passage_vector_tree_before,
        "passage_vector_tree_sha256_after": passage_vector_tree_after,
        "added_by_source_type": diff["added_by_source_type"],
        "removed_by_source_type": diff["removed_by_source_type"],
        "changed_by_source_type": diff["changed_by_source_type"],
        "source_diff_accounted": bool(diff["accounted"]),
        "pdf_corpus_invariant_ok": bool(validation_result["pdf_corpus_invariant_ok"]),
        "required_pdf_documents": validation_result["required_pdf_documents"],
        "forbidden_pdf_pages": validation_result["forbidden_pdf_pages"],
        "post_activation_fts_ready": True,
        "post_activation_note_vector_ready": True,
        "post_activation_drift_reasons": [],
        "production_db_sha256_before": production_db_sha256_before,
        "production_db_sha256_after": production_db_sha256_after,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": bool(
            note_sync_result.get("vector_write_performed")
        ),
        "note_vector_write_performed": bool(
            note_sync_result.get("vector_write_performed")
        ),
        "passage_vector_write_performed": False,
        "fts_write_performed": bool(
            fts_build_result.get("derived_index_write_performed")
        ),
        "activation_performed": True,
        "capture_created": pinned.created,
    }


def _capture_snapshot(
    *,
    source_db_path: str | Path | None,
    capture_dir: Path,
) -> zotero_live_capture_service.ZoteroReadCapture:
    if source_db_path is not None:
        return zotero_live_capture_service.capture_live_zotero_database(
            source_db_path=source_db_path,
            capture_dir=capture_dir,
        )
    return zotero_live_capture_service.capture_configured_live_zotero(
        capture_dir=capture_dir
    )


def _assert_sync_eligible(status: Mapping[str, Any]) -> None:
    state = str(status.get("status") or "")
    reasons = {str(value) for value in status.get("reasons") or []}
    if state == "ready" and not reasons:
        return
    if state == "source_drift" and reasons == SUPPORTED_ZOTERO_DRIFT_REASONS:
        return
    raise ZoteroRetrievalSyncError(
        "zotero_sync_unsupported_drift",
        "Retrieval state contains drift or corruption outside the supported Zotero source update.",
        details={"status": state, "reasons": sorted(reasons)},
    )


def _assert_unique_registry_fragments(
    fragments: Iterable[RetrievalFragment],
) -> None:
    counts = Counter(item.fragment_id for item in fragments)
    duplicates = sorted(
        fragment_id for fragment_id, count in counts.items() if count > 1
    )
    if duplicates:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_source_identity_collision",
            "The pinned retrieval source contains duplicate fragment identities.",
            details={"duplicate_fragment_ids": duplicates[:20]},
        )


def _stage_pinned_snapshot(
    *,
    source: Path,
    expected_sha256: str,
    target: Path,
) -> str:
    zotero_live_capture_service.verify_zotero_capture_file(
        source,
        expected_sha256,
    )
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        actual = generations.sha256_file(temporary).lower()
        if actual != expected_sha256.lower():
            raise ZoteroRetrievalSyncError(
                "zotero_sync_snapshot_stage_mismatch",
                "The staged Zotero snapshot failed fingerprint validation.",
            )
        os.replace(temporary, target)
        if generations.sha256_file(target).lower() != actual:
            raise ZoteroRetrievalSyncError(
                "zotero_sync_snapshot_publish_mismatch",
                "The candidate Zotero snapshot failed publish validation.",
            )
        return actual
    finally:
        temporary.unlink(missing_ok=True)


def _validate_combined_candidate(
    *,
    fts_index_path: Path,
    fts_manifest_path: Path,
    native_note_vector_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
    zotero_snapshot_path: Path,
    expected_snapshot_sha256: str,
    expected_database_sha256: str,
    database: Path,
    notes: Path,
    pinned_fragments: list[RetrievalFragment],
    note_fragments: list[NotebookFragment],
    old_rows: dict[str, dict[str, Any]],
    passage_vector_tree_before: str,
    vector_manifest_sha256_before: str,
    required_pdf_documents: Mapping[int, int],
    forbidden_pdf_pages: set[tuple[int, int]],
) -> dict[str, Any]:
    if generations.sha256_file(database).lower() != expected_database_sha256:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_production_db_changed",
            "The production database changed while validating the candidate.",
        )
    if generations.sha256_file(zotero_snapshot_path).lower() != expected_snapshot_sha256:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_candidate_snapshot_mismatch",
            "The candidate Zotero snapshot revision is not the pinned revision.",
        )
    if generations.tree_fingerprint(vector_store_path) != passage_vector_tree_before:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_passage_vectors_changed",
            "The candidate changed the frozen PDF passage vector tree.",
        )
    if generations.sha256_file(vector_manifest_path) != vector_manifest_sha256_before:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_vector_manifest_changed",
            "The candidate changed the frozen passage vector manifest.",
        )

    fts_status = get_index_status(
        index_path=fts_index_path,
        manifest_path=fts_manifest_path,
        production_db_path=database,
        zotero_snapshot_path=zotero_snapshot_path,
        notes_root=notes,
        query_aliases_path=DEFAULT_QUERY_ALIASES_PATH,
    )
    if fts_status.get("ready") is not True or fts_status.get("reasons") != []:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_candidate_fts_not_ready",
            "The candidate FTS failed strict readiness validation.",
            details={
                "status": fts_status.get("status"),
                "reasons": list(fts_status.get("reasons") or []),
            },
        )
    new_rows = _read_fts_projection(fts_index_path)
    expected_ids = {item.fragment_id for item in pinned_fragments}
    actual_ids = set(new_rows)
    missing = sorted(expected_ids - actual_ids)
    orphan = sorted(actual_ids - expected_ids)
    duplicates = _duplicate_fts_fragment_ids(fts_index_path)
    if missing or orphan or duplicates:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_candidate_fts_identity_mismatch",
            "The candidate FTS contains missing, orphan, or duplicate fragment identities.",
            details={
                "missing": missing[:20],
                "orphan": orphan[:20],
                "duplicates": duplicates[:20],
            },
        )
    table_counts = _fts_table_counts(fts_index_path)
    if len(set(table_counts.values())) != 1 or next(iter(table_counts.values())) != len(
        expected_ids
    ):
        raise ZoteroRetrievalSyncError(
            "zotero_sync_candidate_fts_count_mismatch",
            "The candidate FTS table counts are inconsistent.",
            details=table_counts,
        )

    note_validation = validate_zotero_note_vector_projection(
        index_dir=native_note_vector_path,
        fragments=note_fragments,
    )
    if note_validation.get("ready") is not True:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_candidate_note_vectors_invalid",
            "The candidate note-vector projection failed validation.",
            details={
                key: note_validation[key]
                for key in (
                    "missing_count",
                    "orphan_count",
                    "duplicate_count",
                    "mismatched_count",
                )
            },
        )

    pdf_before = {
        fragment_id: row
        for fragment_id, row in old_rows.items()
        if row.get("source_type") == "pdf_chunk"
    }
    pdf_after = {
        fragment_id: row
        for fragment_id, row in new_rows.items()
        if row.get("source_type") == "pdf_chunk"
    }
    pdf_diff = _projection_diff(pdf_before, pdf_after)
    pdf_invariant_ok = not (
        pdf_diff["added_count"]
        or pdf_diff["removed_count"]
        or pdf_diff["changed_count"]
    )
    if not pdf_invariant_ok:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_pdf_corpus_regression",
            "The candidate changed the frozen production PDF corpus.",
            details=pdf_diff,
        )

    required_results: dict[str, Any] = {}
    for document_id, expected_count in sorted(required_pdf_documents.items()):
        actual_count = sum(
            1
            for row in pdf_after.values()
            if _int_or_none(row.get("document_id")) == document_id
        )
        required_results[str(document_id)] = {
            "expected": expected_count,
            "actual": actual_count,
        }
        if actual_count != expected_count:
            raise ZoteroRetrievalSyncError(
                "zotero_sync_required_pdf_count_mismatch",
                "A required production PDF document lost retrieval chunks.",
                details={
                    "document_id": document_id,
                    "expected": expected_count,
                    "actual": actual_count,
                },
            )
    forbidden_results: list[dict[str, int]] = []
    for document_id, page_number in sorted(forbidden_pdf_pages):
        count = sum(
            1
            for row in pdf_after.values()
            if _int_or_none(row.get("document_id")) == document_id
            and _int_or_none(row.get("page_number")) == page_number
        )
        forbidden_results.append(
            {
                "document_id": document_id,
                "page_number": page_number,
                "normal_pdf_chunk_count": count,
            }
        )
        if count:
            raise ZoteroRetrievalSyncError(
                "zotero_sync_forbidden_pdf_page_present",
                "A blocked PDF page appeared as a normal retrieval chunk.",
                details={
                    "document_id": document_id,
                    "page_number": page_number,
                    "count": count,
                },
            )

    return {
        "fts_expected": len(expected_ids),
        "fts_actual": len(actual_ids),
        "fts_missing": len(missing),
        "fts_orphan": len(orphan),
        "fts_duplicate": len(duplicates),
        "fts_table_counts": table_counts,
        "fts_diff": _projection_diff(old_rows, new_rows),
        "note_validation": note_validation,
        "pdf_corpus_invariant_ok": pdf_invariant_ok,
        "required_pdf_documents": required_results,
        "forbidden_pdf_pages": forbidden_results,
    }


def _read_fts_projection(index_path: Path) -> dict[str, dict[str, Any]]:
    with closing(
        sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            f"SELECT * FROM {ORDINARY_TABLE} ORDER BY fragment_id"
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        payload.pop("row_id", None)
        fragment_id = str(payload.get("fragment_id") or "")
        if not fragment_id or fragment_id in result:
            continue
        result[fragment_id] = payload
    return result


def _duplicate_fts_fragment_ids(index_path: Path) -> list[str]:
    with closing(
        sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            f"SELECT fragment_id FROM {ORDINARY_TABLE} "
            "GROUP BY fragment_id HAVING COUNT(*) > 1 ORDER BY fragment_id"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _fts_table_counts(index_path: Path) -> dict[str, int]:
    with closing(
        sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        connection.execute("PRAGMA query_only = ON")
        return {
            "ordinary": int(
                connection.execute(f"SELECT COUNT(*) FROM {ORDINARY_TABLE}").fetchone()[0]
            ),
            "unicode": int(
                connection.execute(f"SELECT COUNT(*) FROM {UNICODE_FTS_TABLE}").fetchone()[0]
            ),
            "trigram": int(
                connection.execute(f"SELECT COUNT(*) FROM {TRIGRAM_FTS_TABLE}").fetchone()[0]
            ),
        }


def _projection_diff(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    before_ids = set(before)
    after_ids = set(after)
    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    changed = sorted(
        fragment_id
        for fragment_id in before_ids.intersection(after_ids)
        if dict(before[fragment_id]) != dict(after[fragment_id])
    )
    added_by_type = Counter(
        str(after[fragment_id].get("source_type") or "unknown")
        for fragment_id in added
    )
    removed_by_type = Counter(
        str(before[fragment_id].get("source_type") or "unknown")
        for fragment_id in removed
    )
    changed_by_type = Counter(
        str(after[fragment_id].get("source_type") or "unknown")
        for fragment_id in changed
    )
    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "retained_count": len(before_ids.intersection(after_ids)) - len(changed),
        "added_fragment_ids": added,
        "removed_fragment_ids": removed,
        "changed_fragment_ids": changed,
        "added_by_source_type": dict(sorted(added_by_type.items())),
        "removed_by_source_type": dict(sorted(removed_by_type.items())),
        "changed_by_source_type": dict(sorted(changed_by_type.items())),
        "accounted": (
            len(before_ids) + len(added) - len(removed) == len(after_ids)
        ),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ZoteroRetrievalSyncError(
            "zotero_sync_manifest_unreadable",
            "Retrieval manifest metadata is unreadable.",
        ) from exc
    if not isinstance(payload, dict):
        raise ZoteroRetrievalSyncError(
            "zotero_sync_manifest_invalid",
            "Retrieval manifest metadata is invalid.",
        )
    return payload


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "SUPPORTED_ZOTERO_DRIFT_REASONS",
    "SYNC_WORKFLOW_VERSION",
    "ZoteroRetrievalSyncError",
    "sync_zotero_retrieval_generation",
]
