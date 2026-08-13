from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR
from app.domains.retrieval import fragment_repository, note_vector_index
from app.domains.retrieval.result_contracts import NOTE_SOURCE_TYPES
from app.services import (
    chat_local_note_import_service,
    commit_book_service,
    commit_paper_service,
    local_pdf_source_binding_service,
    retrieval_generation_mutation_service,
    retrieval_generation_service,
    vector_store_service,
)
from app.services.library import document_deletion_service
from app.services.retrieval import fts_index_service, fts_status_service
from app.services.retrieval.source_registry import RetrievalSourceRegistry
from app.services.vector_store_service import MANIFEST_PATH


@dataclass(frozen=True)
class ChatPdfImportRuntime:
    db_path: Path
    data_dir: Path
    fts_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path
    deletion_runtime: document_deletion_service.DeletionRuntime
    body_commit: Callable[
        [
            str,
            str,
            local_pdf_source_binding_service.LocalPdfSourceBinding,
        ],
        dict[str, Any],
    ]
    persistence_scope: str = "temp"

    @classmethod
    def production(cls) -> "ChatPdfImportRuntime":
        def body(
            job_id: str,
            document_type: str,
            source_binding: (
                local_pdf_source_binding_service.LocalPdfSourceBinding
            ),
        ) -> dict[str, Any]:
            if document_type in {"book", "thesis", "report"}:
                return commit_book_service.commit_book_from_staging(
                    job_id,
                    db_path=DEFAULT_DB_PATH,
                    backup=False,
                    local_pdf_source_binding=source_binding,
                )
            return commit_paper_service.commit_paper_from_staging(
                job_id,
                db_path=DEFAULT_DB_PATH,
                rebuild_legacy_vector_index=False,
                local_pdf_source_binding=source_binding,
            )
        return cls(DEFAULT_DB_PATH, DATA_DIR, FTS_DB_PATH, FTS_MANIFEST_PATH, LANCEDB_DIR, MANIFEST_PATH,
                   document_deletion_service.DeletionRuntime(db_path=DEFAULT_DB_PATH, data_dir=DATA_DIR,
                       fts_path=FTS_DB_PATH, fts_manifest_path=FTS_MANIFEST_PATH,
                       vector_store_path=LANCEDB_DIR, vector_manifest_path=MANIFEST_PATH), body,
                   "production")


def _is_production_runtime(runtime: ChatPdfImportRuntime) -> bool:
    if runtime.persistence_scope == "production":
        return True
    if runtime.persistence_scope != "temp":
        raise RuntimeError("chat_import_persistence_scope_invalid")
    pairs = ((runtime.db_path, DEFAULT_DB_PATH), (runtime.data_dir, DATA_DIR), (runtime.fts_path, FTS_DB_PATH),
             (runtime.fts_manifest_path, FTS_MANIFEST_PATH), (runtime.vector_store_path, LANCEDB_DIR),
             (runtime.vector_manifest_path, MANIFEST_PATH))
    return all(Path(a).resolve(strict=False) == Path(b).resolve(strict=False) for a, b in pairs)


def _document_ids(db_path: Path) -> set[int]:
    with sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True) as c:
        return {int(row[0]) for row in c.execute("SELECT id FROM documents")}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fts_status(runtime: ChatPdfImportRuntime) -> dict[str, Any]:
    if _is_production_runtime(runtime):
        return fts_status_service.get_index_status(index_path=runtime.fts_path, manifest_path=runtime.fts_manifest_path, production_db_path=runtime.db_path)
    missing_zotero = runtime.db_path.with_name(".b5b1-zotero-snapshot-absent.sqlite")
    missing_notes = runtime.db_path.with_name(".b5b1-notes-absent")
    return fts_status_service.get_index_status(index_path=runtime.fts_path, manifest_path=runtime.fts_manifest_path, production_db_path=runtime.db_path, zotero_snapshot_path=missing_zotero, notes_root=missing_notes)


def _rollback_document(document_id: int, runtime: ChatPdfImportRuntime) -> dict[str, Any]:
    preview = document_deletion_service.create_deletion_preview(document_id, runtime=runtime.deletion_runtime)
    result = document_deletion_service.delete_document(document_id=document_id, preview_token=str(preview["preview_token"]), expected_document_revision=str(preview["document_revision"]), confirmation_text="删除", runtime=runtime.deletion_runtime)
    if result.get("status") != "completed":
        raise RuntimeError("chat_import_rollback_failed")
    return result


def _verified_post_write_snapshot(db_path: Path) -> Path:
    target = Path(db_path).resolve(strict=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.local-pdf-post-write-",
        suffix=".sqlite",
        dir=str(target.parent),
    )
    os.close(descriptor)
    snapshot = Path(raw_path)
    expected_sha = _sha(target)
    expected_size = target.stat().st_size
    try:
        shutil.copy2(target, snapshot)
        if snapshot.stat().st_size != expected_size or _sha(snapshot) != expected_sha:
            raise RuntimeError("chat_import_post_write_snapshot_invalid")
    except BaseException:
        snapshot.unlink(missing_ok=True)
        raise
    return snapshot


def _native_note_fragments_for_document(
    *,
    source_db_path: Path,
    data_root: Path,
    document_id: int,
) -> list[Any]:
    registry = RetrievalSourceRegistry(
        research_db_path=source_db_path,
        zotero_snapshot_path=(
            data_root / "zotero" / "snapshot" / "zotero.sqlite"
        ),
        notes_root=data_root / "notes",
        project_root=data_root.parent,
    )
    return fragment_repository.list_notebook_fragments(
        source_types=NOTE_SOURCE_TYPES,
        document_ids=(document_id,),
        registry=registry,
    )


def _generation_fts_status(
    *,
    index_path: Path,
    manifest_path: Path,
    db_path: Path,
    data_dir: Path,
) -> dict[str, Any]:
    return fts_status_service.get_index_status(
        index_path=index_path,
        manifest_path=manifest_path,
        production_db_path=db_path,
        zotero_snapshot_path=(
            data_dir / "zotero" / "snapshot" / "zotero.sqlite"
        ),
        notes_root=data_dir / "notes",
    )


def _require_strict_vector_state(
    state: dict[str, Any],
    *,
    code: str,
) -> None:
    if (
        state.get("status") != "ok"
        or int(state.get("missing_count") or 0) != 0
        or int(state.get("orphan_count") or 0) != 0
        or int(state.get("duplicate_count") or 0) != 0
        or int(state.get("stale_count") or 0) != 0
    ):
        raise RuntimeError(code)


def _validate_generation_artifacts(
    *,
    document_id: int,
    db_path: Path,
    data_dir: Path,
    expected_db_sha256: str,
    fts_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    expected_passage_sources: list[dict[str, Any]],
    expected_note_sources: list[dict[str, Any]],
    native_note_vector_path: Path,
    expected_native_fragment_ids: set[str],
) -> None:
    if _sha(db_path).lower() != expected_db_sha256.lower():
        raise RuntimeError("chat_import_generation_database_revision_invalid")
    fts_status = _generation_fts_status(
        index_path=fts_path,
        manifest_path=fts_manifest_path,
        db_path=db_path,
        data_dir=data_dir,
    )
    if fts_status.get("status") != "ready" or fts_status.get("ready") is not True:
        raise RuntimeError("chat_import_generation_fts_invalid")

    passage_state = vector_store_service.inspect_document_passage_vector_state(
        document_id=document_id,
        expected_sources=expected_passage_sources,
        store_path=vector_store_path,
    )
    _require_strict_vector_state(
        passage_state,
        code="chat_import_generation_passage_vectors_invalid",
    )
    note_state = vector_store_service.inspect_document_note_vector_state(
        document_id=document_id,
        expected_sources=expected_note_sources,
        store_path=vector_store_path,
    )
    _require_strict_vector_state(
        note_state,
        code="chat_import_generation_note_vectors_invalid",
    )

    native_state = note_vector_index.inspect_zotero_note_vector_document_impact(
        document_id,
        index_dir=native_note_vector_path,
    )
    actual_native_ids = {
        str(value)
        for value in native_state.get("fragment_ids") or []
        if str(value)
    }
    if (
        native_state.get("status") != "ready"
        or int(native_state.get("document_entry_count") or 0)
        != len(expected_native_fragment_ids)
        or actual_native_ids != expected_native_fragment_ids
    ):
        raise RuntimeError("chat_import_generation_native_notes_invalid")


def _import_document_with_generation(
    *,
    actual: ChatPdfImportRuntime,
    import_job_id: str,
    document_type: str,
    source_binding: (
        local_pdf_source_binding_service.LocalPdfSourceBinding | None
    ),
    note_files: list[Path],
    inbox_root: Path,
    expected_before_db_sha256: str | None,
    stage_callback: Callable[[str, dict[str, Any]], None] | None,
) -> dict[str, Any]:
    post_write_snapshot: Path | None = None
    document_id: int | None = None
    result: dict[str, Any] = {}
    notes: dict[str, Any] = {}
    passage_vectors: dict[str, Any] = {}
    note_vectors: dict[str, Any] = {}
    native_vectors: dict[str, Any] = {}
    expected_passage_sources: list[dict[str, Any]] = []
    expected_note_sources: list[dict[str, Any]] = []
    expected_native_fragment_ids: set[str] = set()
    verified_source: dict[str, Any] | None = None

    def emit(stage: str, **metadata: Any) -> None:
        if stage_callback is not None:
            stage_callback(stage, metadata)

    try:
        with retrieval_generation_mutation_service.ProductionGenerationMutationSession(
            data_dir=actual.data_dir,
            db_path=actual.db_path,
        ) as mutation:
            before_ids = _document_ids(actual.db_path)
            locked_before_sha = _sha(actual.db_path)
            if (
                expected_before_db_sha256
                and locked_before_sha.lower()
                != expected_before_db_sha256.lower()
            ):
                raise RuntimeError("chat_import_production_revision_changed")
            result = actual.body_commit(
                import_job_id,
                document_type,
                source_binding,
            )
            created = _document_ids(actual.db_path) - before_ids
            if (
                len(created) != 1
                or int(result.get("document_id") or 0) not in created
            ):
                raise RuntimeError("chat_import_document_delta_invalid")
            document_id = next(iter(created))
            mutation.mark_body_db_mutated()
            emit(
                "body_import_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )

            notes = chat_local_note_import_service.import_local_notes(
                db_path=actual.db_path,
                document_id=document_id,
                note_files=note_files,
                inbox_root=inbox_root,
            )
            after_db_sha256 = mutation.capture_post_write_database()
            post_write_snapshot = _verified_post_write_snapshot(actual.db_path)
            if _sha(post_write_snapshot).lower() != after_db_sha256:
                raise RuntimeError("chat_import_post_write_snapshot_invalid")
            emit(
                "staging_snapshot_created",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
                source_count=int(notes.get("note_count") or 0),
            )

            candidate = mutation.candidate
            if candidate is None:
                raise RuntimeError("chat_import_generation_candidate_missing")
            emit(
                "staging_fts_started",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            fts_sync = fts_index_service.upsert_document_retrieval_fts(
                document_id=document_id,
                index_path=candidate.fts_index_path,
                manifest_path=candidate.fts_manifest_path,
                research_db_path=post_write_snapshot,
            )
            if (
                fts_sync.get("full_rebuild_performed") is not False
                or fts_sync.get("production_db_write_performed") is not False
            ):
                raise RuntimeError("chat_import_generation_fts_scope_invalid")
            emit(
                "staging_fts_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )

            expected_passage_sources = vector_store_service.collect_passage_sources(
                document_id=document_id,
                source_db_path=post_write_snapshot,
            )
            passage_source_ids = [
                str(source["source_id"])
                for source in expected_passage_sources
            ]
            emit(
                "staging_vector_started",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
                source_count=len(passage_source_ids),
            )
            passage_vectors = (
                vector_store_service.sync_affected_passage_embeddings(
                    passage_source_ids,
                    dry_run=False,
                    apply=True,
                    source_db_path=post_write_snapshot,
                    store_path=candidate.vector_store_path,
                    manifest_path=candidate.vector_manifest_path,
                )
            )
            if (
                passage_vectors.get("scope") != "affected_source_ids_only"
                or passage_vectors.get("full_rebuild_allowed") is not False
                or passage_vectors.get("delete_orphans_allowed") is not False
            ):
                raise RuntimeError("chat_import_generation_passage_scope_invalid")

            expected_note_sources = vector_store_service.collect_personal_note_sources(
                document_id=document_id,
                source_db_path=post_write_snapshot,
            )
            note_vectors = vector_store_service.sync_document_note_embeddings(
                document_id,
                dry_run=False,
                apply=True,
                source_db_path=post_write_snapshot,
                store_path=candidate.vector_store_path,
                manifest_path=candidate.vector_manifest_path,
            )
            if (
                note_vectors.get("scope") != "document_only"
                or note_vectors.get("full_rebuild_performed") is not False
                or note_vectors.get("orphan_delete_performed") is not False
            ):
                raise RuntimeError("chat_import_generation_note_scope_invalid")

            native_fragments = _native_note_fragments_for_document(
                source_db_path=post_write_snapshot,
                data_root=actual.data_dir,
                document_id=document_id,
            )
            expected_native_fragment_ids = {
                str(fragment.fragment_id) for fragment in native_fragments
            }
            if native_fragments:
                native_vectors = (
                    note_vector_index.attach_zotero_note_vector_document_scope(
                        document_id,
                        fragments=native_fragments,
                        index_dir=candidate.native_note_vector_path,
                    )
                )
                if (
                    native_vectors.get("scope") != "affected_fragment_ids_only"
                    or native_vectors.get("full_rebuild_performed") is not False
                    or native_vectors.get("orphan_delete_performed") is not False
                ):
                    raise RuntimeError("chat_import_generation_native_scope_invalid")
            else:
                native_vectors = {
                    "status": "skipped",
                    "scope": "affected_fragment_ids_only",
                    "scoped_entry_count_after": 0,
                    "full_rebuild_performed": False,
                    "orphan_delete_performed": False,
                }
            emit(
                "staging_vector_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
                source_count=(
                    len(expected_passage_sources)
                    + len(expected_note_sources)
                    + len(expected_native_fragment_ids)
                ),
            )

            mutation.mark_candidate_synced()

            def validate_candidate(candidate_value, expected_sha: str) -> None:
                _validate_generation_artifacts(
                    document_id=document_id,
                    db_path=post_write_snapshot,
                    data_dir=actual.data_dir,
                    expected_db_sha256=expected_sha,
                    fts_path=candidate_value.fts_index_path,
                    fts_manifest_path=candidate_value.fts_manifest_path,
                    vector_store_path=candidate_value.vector_store_path,
                    expected_passage_sources=expected_passage_sources,
                    expected_note_sources=expected_note_sources,
                    native_note_vector_path=candidate_value.native_note_vector_path,
                    expected_native_fragment_ids=expected_native_fragment_ids,
                )
                if source_binding is not None:
                    local_pdf_source_binding_service.verify_document_source(
                        db_path=post_write_snapshot,
                        data_dir=actual.data_dir,
                        document_id=document_id,
                        binding=source_binding,
                    )

            mutation.validate_candidate(validate_candidate)
            emit(
                "derived_backup_started",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            finalized = mutation.finalize_candidate(
                profile_versions={
                    "fts_schema": fts_status_service.INDEX_SCHEMA_VERSION,
                    "source_registry": fts_status_service.SOURCE_REGISTRY_VERSION,
                }
            )
            emit(
                "derived_backup_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            emit(
                "publish_started",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            mutation.begin_activation()
            mutation.publish_active()
            emit(
                "publish_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            emit(
                "final_verification_started",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )

            def validate_active(active_value) -> None:
                nonlocal verified_source
                if active_value.generation_id != finalized.generation_id:
                    raise RuntimeError("chat_import_active_generation_mismatch")
                _validate_generation_artifacts(
                    document_id=document_id,
                    db_path=actual.db_path,
                    data_dir=actual.data_dir,
                    expected_db_sha256=after_db_sha256,
                    fts_path=active_value.fts_index_path,
                    fts_manifest_path=active_value.fts_manifest_path,
                    vector_store_path=active_value.vector_store_path,
                    expected_passage_sources=expected_passage_sources,
                    expected_note_sources=expected_note_sources,
                    native_note_vector_path=active_value.native_note_vector_path,
                    expected_native_fragment_ids=expected_native_fragment_ids,
                )
                if source_binding is not None:
                    verified_source = (
                        local_pdf_source_binding_service
                        .verify_document_source(
                            db_path=actual.db_path,
                            data_dir=actual.data_dir,
                            document_id=document_id,
                            binding=source_binding,
                        )
                    )

            mutation.verify_active(validate_active)
            emit(
                "final_verification_completed",
                document_id=document_id,
                chunk_count=int(result.get("chunk_count") or 0),
                writes_performed=True,
            )
            # The source snapshot is transaction-local evidence.  Removing it
            # is part of activation completion: a Windows handle leak must
            # roll back while the fail-closed marker is still durable rather
            # than surface an error after the commit point.
            post_write_snapshot.unlink()
            post_write_snapshot = None
            mutation.clear_activation()

        if verified_source is None and source_binding is not None:
            raise RuntimeError("chat_import_source_verification_missing")
        payload = {
            "status": "completed",
            "document_id": document_id,
            "title": result.get("title", ""),
            "document_type": document_type,
            "chunk_count": result.get("chunk_count", 0),
            "note_count": notes.get("note_count", 0),
            "evidence_link_count": notes.get("evidence_link_count", 0),
            "fts_status": "ready",
            "passage_vectors_upserted": passage_vectors.get("upserted_count", 0),
            "note_vectors_upserted": (
                int(note_vectors.get("inserted_count") or 0)
                + int(note_vectors.get("updated_count") or 0)
            ),
            "native_note_vector_count": native_vectors.get(
                "scoped_entry_count_after", 0
            ),
            "full_rebuild_performed": False,
            "derived_index_publish_performed": True,
            "generation_id": finalized.generation_id,
        }
        if source_binding is not None:
            payload["source_binding_count"] = verified_source[
                "source_binding_count"
            ]
            payload["source_type"] = verified_source["source_type"]
        return payload
    finally:
        if post_write_snapshot is not None:
            try:
                post_write_snapshot.unlink(missing_ok=True)
            except OSError:
                # A failed transaction is already rolled back/fail-closed by
                # the shared session.  Cleanup must not obscure that outcome.
                pass


def import_staging_document_to_production(
    *,
    import_job_id: str,
    document_type: str,
    allow_production: bool = False,
    runtime: ChatPdfImportRuntime | None = None,
) -> dict[str, Any]:
    """Commit a legacy staging document through the immutable-generation transaction.

    The legacy ``/imports/{job}/commit-paper|commit-book`` entry points create
    production documents.  Under versioned production those writes must travel
    through the same generation transaction as the chat import pipeline:
    production DB body write, candidate FTS/vector/note sync, strict
    validation, activation marker, pointer switch, post-switch verification.
    The fixed legacy artifacts are never modified.
    """
    actual = runtime or ChatPdfImportRuntime.production()
    production = _is_production_runtime(actual)
    if production and not allow_production:
        raise RuntimeError("chat_import_production_opt_in_required")
    if not production:
        raise RuntimeError(
            "legacy staging commits are only supported for production runtime"
        )
    active_before = (
        retrieval_generation_service.resolve_active_retrieval_generation(
            data_dir=actual.data_dir,
            db_path=actual.db_path,
            verify_fingerprints=True,
        )
    )
    retrieval_generation_service.verify_generation_database_revision(
        active_before,
        actual.db_path,
    )
    status = _generation_fts_status(
        index_path=active_before.fts_index_path,
        manifest_path=active_before.fts_manifest_path,
        db_path=actual.db_path,
        data_dir=actual.data_dir,
    )
    if status.get("status") != "ready":
        raise RuntimeError("chat_import_fts_not_ready")
    return _import_document_with_generation(
        actual=actual,
        import_job_id=import_job_id,
        document_type=document_type,
        source_binding=None,
        note_files=[],
        inbox_root=Path("."),
        expected_before_db_sha256=None,
        stage_callback=None,
    )


def import_document_to_production(
    *,
    import_job_id: str,
    document_type: str,
    source_binding: (
        local_pdf_source_binding_service.LocalPdfSourceBinding | None
    ) = None,
    note_files: list[Path] | None = None,
    inbox_root: Path | None = None,
    expected_before_db_sha256: str | None = None,
    allow_production: bool = False,
    runtime: ChatPdfImportRuntime | None = None,
    stage_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    actual = runtime or ChatPdfImportRuntime.production()
    production = _is_production_runtime(actual)
    if production and not allow_production:
        raise RuntimeError("chat_import_production_opt_in_required")
    if not production and allow_production:
        raise RuntimeError("chat_import_temp_runtime_rejects_production_opt_in")
    if source_binding is None:
        raise RuntimeError("chat_import_local_source_binding_required")
    local_pdf_source_binding_service.validate_binding(source_binding)
    local_pdf_source_binding_service.verify_managed_pdf(
        data_dir=actual.data_dir,
        binding=source_binding,
    )
    if production:
        active_before = (
            retrieval_generation_service.resolve_active_retrieval_generation(
                data_dir=actual.data_dir,
                db_path=actual.db_path,
                verify_fingerprints=True,
            )
        )
        retrieval_generation_service.verify_generation_database_revision(
            active_before,
            actual.db_path,
        )
        status = _generation_fts_status(
            index_path=active_before.fts_index_path,
            manifest_path=active_before.fts_manifest_path,
            db_path=actual.db_path,
            data_dir=actual.data_dir,
        )
    else:
        status = _fts_status(actual)
    if status.get("status") != "ready":
        raise RuntimeError("chat_import_fts_not_ready")
    before_ids = _document_ids(actual.db_path)
    before_sha = _sha(actual.db_path)
    if expected_before_db_sha256 and before_sha.lower() != expected_before_db_sha256.lower():
        raise RuntimeError("chat_import_production_revision_changed")
    if production:
        return _import_document_with_generation(
            actual=actual,
            import_job_id=import_job_id,
            document_type=document_type,
            source_binding=source_binding,
            note_files=note_files or [],
            inbox_root=inbox_root or Path("."),
            expected_before_db_sha256=expected_before_db_sha256,
            stage_callback=stage_callback,
        )
    document_id: int | None = None
    try:
        result = actual.body_commit(
            import_job_id,
            document_type,
            source_binding,
        )
        created = _document_ids(actual.db_path) - before_ids
        if len(created) != 1 or int(result.get("document_id") or 0) not in created:
            raise RuntimeError("chat_import_document_delta_invalid")
        document_id = next(iter(created))
        if stage_callback is not None:
            stage_callback(
                "body_import_completed",
                {
                    "document_id": document_id,
                    "chunk_count": int(result.get("chunk_count") or 0),
                    "writes_performed": True,
                },
            )
    except BaseException as exc:
        created = _document_ids(actual.db_path) - before_ids
        if len(created) == 1:
            try:
                _rollback_document(next(iter(created)), actual)
            except Exception as rollback_exc:
                raise RuntimeError("chat_import_rollback_failed") from rollback_exc
        elif len(created) > 1:
            raise RuntimeError("chat_import_rollback_ambiguous")
        raise exc
    try:
        notes = chat_local_note_import_service.import_local_notes(db_path=actual.db_path, document_id=document_id, note_files=note_files or [], inbox_root=inbox_root or Path("."))
        after_sha = _sha(actual.db_path)
        fts = fts_index_service.upsert_document_retrieval_fts(document_id=document_id, index_path=actual.fts_path, manifest_path=actual.fts_manifest_path, research_db_path=actual.db_path, allow_production=production, expected_before_db_sha256=before_sha if production else None, expected_after_db_sha256=after_sha if production else None)
        with sqlite3.connect(f"file:{actual.db_path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            ids = [f"chunk:{document_id}:{int(row[0])}" for row in connection.execute("SELECT id FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index,id", (document_id,))]
        vectors = vector_store_service.sync_affected_passage_embeddings(ids, dry_run=False, apply=True, source_db_path=None if production else actual.db_path, store_path=actual.vector_store_path, manifest_path=actual.vector_manifest_path)
        if stage_callback is not None:
            stage_callback(
                "final_verification_started",
                {
                    "document_id": document_id,
                    "chunk_count": int(result.get("chunk_count") or 0),
                    "writes_performed": True,
                },
            )
        final_status = _fts_status(actual)
        with sqlite3.connect(f"file:{actual.db_path.resolve().as_posix()}?mode=ro", uri=True) as verify_connection:
            document_count = int(verify_connection.execute("SELECT COUNT(*) FROM documents WHERE id=?", (document_id,)).fetchone()[0])
            chunk_count = int(verify_connection.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (document_id,)).fetchone()[0])
        if (final_status.get("status") != "ready" or document_count != 1 or chunk_count <= 0
                or vectors.get("scope") != "affected_source_ids_only"
                or vectors.get("full_rebuild_allowed") is not False
                or vectors.get("delete_orphans_allowed") is not False):
            raise RuntimeError("chat_import_final_verify_failed")
        source = local_pdf_source_binding_service.verify_document_source(
            db_path=actual.db_path,
            data_dir=actual.data_dir,
            document_id=document_id,
            binding=source_binding,
        )
        return {"status": "completed", "document_id": document_id, "title": result.get("title", ""), "document_type": document_type, "chunk_count": result.get("chunk_count", 0), "note_count": notes["note_count"], "evidence_link_count": notes["evidence_link_count"], "fts_status": final_status.get("status"), "passage_vectors_upserted": vectors.get("upserted_count", 0), "source_binding_count": source["source_binding_count"], "source_type": source["source_type"], "full_rebuild_performed": False}
    except BaseException:
        try:
            _rollback_document(document_id, actual)
        except Exception as rollback_exc:
            raise RuntimeError("chat_import_rollback_failed") from rollback_exc
        raise
