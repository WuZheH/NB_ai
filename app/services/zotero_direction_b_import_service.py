from __future__ import annotations

import os
import json
import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.core.database import connect_readonly_sqlite
from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, LANCEDB_DIR
from app.services import (
    book_import_service,
    commit_book_service,
    vector_store_service,
    zotero_direction_b_commit_service,
    zotero_selected_book_preview_service,
)
from app.services.retrieval import fts_index_service
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
)
from app.services.pdf_parser_backends import (
    PYMUPDF_BACKEND,
)


class DirectionBSelectedBookImportError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = int(status_code)
        self.details = details or {}


@dataclass(frozen=True)
class SelectedBookImportRuntime:
    db_path: Path
    data_dir: Path
    fts_index_path: Path
    fts_manifest_path: Path
    vector_store_path: Path
    vector_manifest_path: Path
    persistence_scope: str


def _make_temp_runtime(db_path: str | Path, data_dir: str | Path) -> SelectedBookImportRuntime:
    root = Path(data_dir).resolve(strict=False)
    return SelectedBookImportRuntime(
        db_path=Path(db_path).resolve(strict=False),
        data_dir=root,
        fts_index_path=root / "search_index" / "retrieval_fts_v1.db",
        fts_manifest_path=root / "search_index" / "retrieval_fts_v1_manifest.json",
        vector_store_path=root / "vector_store" / "lancedb",
        vector_manifest_path=root / "vector_store" / "vector_manifest.json",
        persistence_scope="tempdb",
    )


def _production_runtime() -> SelectedBookImportRuntime:
    return SelectedBookImportRuntime(
        db_path=Path(DEFAULT_DB_PATH).resolve(strict=False),
        data_dir=Path(DATA_DIR).resolve(strict=False),
        fts_index_path=Path(DEFAULT_INDEX_PATH).resolve(strict=False),
        fts_manifest_path=Path(DEFAULT_MANIFEST_PATH).resolve(strict=False),
        vector_store_path=Path(LANCEDB_DIR).resolve(strict=False),
        vector_manifest_path=Path(vector_store_service.MANIFEST_PATH).resolve(strict=False),
        persistence_scope="production",
    )


def _commit_selected_book_import(
    *,
    preview_token: str,
    runtime: SelectedBookImportRuntime,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    path = runtime.db_path
    data_root = runtime.data_dir
    fts_index_path = runtime.fts_index_path
    fts_manifest_path = runtime.fts_manifest_path
    vector_store_path = runtime.vector_store_path
    vector_manifest_path = runtime.vector_manifest_path

    if runtime.persistence_scope != "production" and (
        path == Path(DEFAULT_DB_PATH).resolve(strict=False)
        or data_root == Path(DATA_DIR).resolve(strict=False)
        or fts_index_path.resolve(strict=False)
        == Path(DEFAULT_INDEX_PATH).resolve(strict=False)
        or fts_manifest_path.resolve(strict=False)
        == Path(DEFAULT_MANIFEST_PATH).resolve(strict=False)
        or vector_store_path.resolve(strict=False)
        == Path(LANCEDB_DIR).resolve(strict=False)
        or vector_manifest_path.resolve(strict=False)
        == Path(vector_store_service.MANIFEST_PATH).resolve(strict=False)
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_not_enabled"
            ),
            message=(
                "Direction-B selected-book "
                "import is not enabled for "
                "production."
            ),
            status_code=503,
        )

    if not data_root.is_dir():
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_temp_data_root_missing",
            message="Direction-B temp data root does not exist.",
            status_code=503,
        )

    if not path.is_file():
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "target_db_missing"
            ),
            message=(
                "Direction-B target database "
                "does not exist."
            ),
            status_code=503,
        )

    if not fts_index_path.is_file() or not fts_manifest_path.is_file():
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_temp_fts_not_ready",
            message="Direction-B temp retrieval FTS is not ready.",
            status_code=503,
        )

    _assert_no_live_sidecars(
        path
    )

    try:
        (
            preview,
            pdf_path,
        ) = (
            zotero_selected_book_preview_service
            .resolve_selected_book_preview_source(
                preview_token,
                now_ts=now_ts,
                expected_db_path=path,
            )
        )
    except (
        zotero_selected_book_preview_service
        .ZoteroSelectedBookPreviewError
    ) as exc:
        raise DirectionBSelectedBookImportError(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details,
        ) from exc

    if preview.get("status") != "ready":
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "preview_not_ready"
            ),
            message=(
                "Direction-B selected-book "
                "preview is not ready."
            ),
            status_code=409,
        )

    duplicate = (
        preview.get(
            "duplicate_check"
        )
        or {}
    )

    if bool(
        duplicate.get(
            "duplicate_found"
        )
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_import_duplicate_"
                "requires_review"
            ),
            message=(
                "The selected Zotero book "
                "matches existing library data. "
                "B4 refuses to create a "
                "duplicate body."
            ),
            status_code=409,
        )

    staging_root = (
        data_root
        / ".direction_b_index_staging"
        / uuid4().hex[:8]
    )
    derived_rollback_root = (
        data_root
        / ".direction_b_index_rollback"
        / uuid4().hex[:8]
    )
    staging_fts_index = staging_root / "search_index" / fts_index_path.name
    staging_fts_manifest = (
        staging_root / "search_index" / fts_manifest_path.name
    )
    staging_vector_store = staging_root / "vector_store" / "lancedb"
    staging_vector_manifest = (
        staging_root / "vector_store" / vector_manifest_path.name
    )
    try:
        _prepare_derived_staging(
            fts_index_path=fts_index_path,
            fts_manifest_path=fts_manifest_path,
            vector_store_path=vector_store_path,
            vector_manifest_path=vector_manifest_path,
            staging_fts_index=staging_fts_index,
            staging_fts_manifest=staging_fts_manifest,
            staging_vector_store=staging_vector_store,
            staging_vector_manifest=staging_vector_manifest,
        )
        rollback_path = _create_rollback_copy(path)
    except Exception:
        _remove_generated_tree(staging_root)
        raise

    try:
        try:
            if body_importer is None:
                body_result = (
                    _default_selected_book_body_importer(
                        preview=preview,
                        db_path=path,
                        pdf_path=pdf_path,
                    )
                )
            else:
                body_result = body_importer(
                    preview=preview,
                    db_path=path,
                )

        except DirectionBSelectedBookImportError:
            raise

        except Exception as exc:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    "body_import_failed"
                ),
                message=(
                    "The selected book body "
                    "could not be imported."
                ),
                status_code=500,
            ) from exc

        document_id = (
            _required_document_id(
                body_result
            )
        )

        try:
            note_commit = (
                zotero_direction_b_commit_service
                .commit_selected_book_preview_to_production
                if runtime.persistence_scope == "production"
                else zotero_direction_b_commit_service
                .commit_selected_book_preview_to_temp_db
            )
            note_result = note_commit(
                    preview_token=preview_token,
                    document_id=document_id,
                    **({} if runtime.persistence_scope == "production" else {"db_path": path}),
                    now_ts=now_ts,
            )
        except (
            zotero_direction_b_commit_service
            .DirectionBCommitError
        ) as exc:
            raise DirectionBSelectedBookImportError(
                code=exc.code,
                message=exc.message,
                status_code=409,
                details=exc.details,
            ) from exc

        except (
            zotero_selected_book_preview_service
            .ZoteroSelectedBookPreviewError
        ) as exc:
            raise DirectionBSelectedBookImportError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                details=exc.details,
            ) from exc

        post_write_snapshot = path
        if runtime.persistence_scope == "production":
            post_write_snapshot = staging_root / "source_snapshot" / path.name
            post_write_snapshot.parent.mkdir(parents=True, exist_ok=True)
            after_db_sha256 = _sha256_file(path)
            after_db_size = path.stat().st_size
            shutil.copy2(path, post_write_snapshot)
            if _sha256_file(post_write_snapshot) != after_db_sha256 or post_write_snapshot.stat().st_size != after_db_size:
                raise DirectionBSelectedBookImportError(
                    code="zotero_direction_b_post_write_snapshot_invalid",
                    message="Post-write database snapshot verification failed.",
                    status_code=500,
                )

        try:
            fts_sync = fts_index_service.upsert_document_retrieval_fts(
                document_id=document_id,
                index_path=staging_fts_index,
                manifest_path=staging_fts_manifest,
                research_db_path=post_write_snapshot,
            )
            if (
                fts_sync.get("full_rebuild_performed") is not False
                or fts_sync.get("production_db_write_performed") is not False
            ):
                raise RuntimeError("unsafe FTS sync result")

            passage_source_ids = _passage_source_ids_for_document(
                path,
                document_id,
            )
            passage_vector_sync: dict[str, Any] = {
                "status": "skipped",
                "scope": "affected_source_ids_only",
                "reason": "no_passage_sources",
            }
            if passage_source_ids:
                passage_vector_sync = (
                    vector_store_service.sync_affected_passage_embeddings(
                        passage_source_ids,
                        dry_run=False,
                        apply=True,
                        source_db_path=post_write_snapshot,
                        store_path=staging_vector_store,
                        manifest_path=staging_vector_manifest,
                    )
                )
                if (
                    passage_vector_sync.get("scope")
                    != "affected_source_ids_only"
                    or passage_vector_sync.get("full_rebuild_allowed") is not False
                    or passage_vector_sync.get("delete_orphans_allowed") is not False
                ):
                    raise RuntimeError("unsafe passage vector sync result")

            note_vector_sync: dict[str, Any] = {
                "status": "skipped",
                "scope": "document_only",
                "reason": "no_personal_notes",
            }
            if int(note_result.get("source_count") or 0) > 0:
                note_vector_sync = (
                    vector_store_service.sync_document_note_embeddings(
                        document_id,
                        dry_run=False,
                        apply=True,
                        source_db_path=post_write_snapshot,
                        store_path=staging_vector_store,
                        manifest_path=staging_vector_manifest,
                    )
                )
                if (
                    note_vector_sync.get("scope") != "document_only"
                    or note_vector_sync.get("full_rebuild_performed") is not False
                    or note_vector_sync.get("orphan_delete_performed") is not False
                ):
                    raise RuntimeError("unsafe note vector sync result")
        except DirectionBSelectedBookImportError:
            raise
        except Exception as exc:
            raise DirectionBSelectedBookImportError(
                code="zotero_direction_b_temp_index_sync_failed",
                message="Direction-B temp derived index sync failed.",
                status_code=500,
            ) from exc

        derived_state = _backup_derived_artifacts(
            rollback_root=derived_rollback_root,
            fts_index_path=fts_index_path,
            fts_manifest_path=fts_manifest_path,
            vector_store_path=vector_store_path,
            vector_manifest_path=vector_manifest_path,
        )
        try:
            _publish_staged_derived_indexes(
                staging_fts_index=staging_fts_index,
                staging_fts_manifest=staging_fts_manifest,
                staging_vector_store=staging_vector_store,
                staging_vector_manifest=staging_vector_manifest,
                fts_index_path=fts_index_path,
                fts_manifest_path=fts_manifest_path,
                vector_store_path=vector_store_path,
                vector_manifest_path=vector_manifest_path,
            )
        except Exception as publish_exc:
            try:
                _restore_derived_artifacts(
                    rollback_root=derived_rollback_root,
                    state=derived_state,
                    fts_index_path=fts_index_path,
                    fts_manifest_path=fts_manifest_path,
                    vector_store_path=vector_store_path,
                    vector_manifest_path=vector_manifest_path,
                )
            except Exception as rollback_exc:
                raise DirectionBSelectedBookImportError(
                    code="zotero_direction_b_temp_index_rollback_failed",
                    message="Direction-B temp derived index rollback failed.",
                    status_code=500,
                ) from rollback_exc
            raise DirectionBSelectedBookImportError(
                code="zotero_direction_b_temp_index_publish_failed",
                message="Direction-B temp derived index publish failed.",
                status_code=500,
            ) from publish_exc

        return {
            "status": "committed",
            "document_id": document_id,
            "title": str(
                body_result.get(
                    "title"
                )
                or (
                    preview.get(
                        "zotero_item"
                    )
                    or {}
                ).get(
                    "title"
                )
                or ""
            ),
            "document_type": str(
                body_result.get(
                    "document_type"
                )
                or "book"
            ),
            "chunk_count": int(
                body_result.get(
                    "chunk_count"
                )
                or body_result.get(
                    "inserted_chunks"
                )
                or 0
            ),
            "body_import": dict(
                body_result
            ),
            "note_import": dict(
                note_result
            ),
            "fts_sync": dict(fts_sync),
            "passage_vector_sync": dict(passage_vector_sync),
            "note_vector_sync": dict(note_vector_sync),
            "body_importer": (
                "core_book_import"
                if body_importer is None
                else "runtime_override"
            ),
        "persistence_scope": runtime.persistence_scope,
            "production_data_modified": runtime.persistence_scope == "production",
            "production_schema_migrated": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": bool(
                passage_vector_sync.get("lancedb_writes_performed")
                or note_vector_sync.get("lancedb_writes_performed")
            ),
            "fts_write_performed": True,
            "derived_index_scope": "tempdb",
            "derived_index_publish_performed": True,
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
        }

    except Exception as exc:
        try:
            _restore_rollback_copy(
                path,
                rollback_path,
            )

        except Exception as rollback_exc:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    "temp_rollback_failed"
                ),
                message=(
                    "Direction-B temp database "
                    "rollback failed."
                ),
                status_code=500,
            ) from rollback_exc

        if isinstance(
            exc,
            DirectionBSelectedBookImportError,
        ):
            raise

        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "import_failed"
            ),
            message=(
                    "Direction-B selected-book import failed."
            ),
            status_code=500,
        ) from exc

    finally:
        rollback_path.unlink(
            missing_ok=True
        )
        _remove_generated_tree(staging_root)
        _remove_generated_tree(derived_rollback_root)


def commit_selected_book_import_to_temp_db(
    *,
    preview_token: str,
    db_path: str | Path,
    data_dir: str | Path,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    runtime = _make_temp_runtime(db_path, data_dir)
    if runtime.db_path == Path(DEFAULT_DB_PATH).resolve(strict=False):
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_production_not_enabled",
            message="Direction-B selected-book import is not enabled for production.",
            status_code=503,
        )
    return _commit_selected_book_import(
        preview_token=preview_token,
        runtime=runtime,
        body_importer=body_importer,
        now_ts=now_ts,
    )


def commit_selected_book_import_to_production(
    *,
    preview_token: str,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    return _commit_selected_book_import(
        preview_token=preview_token,
        runtime=_production_runtime(),
        body_importer=body_importer,
        now_ts=now_ts,
    )


def _prepare_derived_staging(
    *,
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
    staging_fts_index: Path,
    staging_fts_manifest: Path,
    staging_vector_store: Path,
    staging_vector_manifest: Path,
) -> None:
    staging_fts_index.parent.mkdir(parents=True, exist_ok=False)
    staging_vector_store.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(fts_index_path, staging_fts_index)
    shutil.copy2(fts_manifest_path, staging_fts_manifest)
    if vector_store_path.is_dir():
        shutil.copytree(vector_store_path, staging_vector_store)
    if vector_manifest_path.is_file():
        shutil.copy2(vector_manifest_path, staging_vector_manifest)


def _passage_source_ids_for_document(
    db_path: Path,
    document_id: int,
) -> list[str]:
    with connect_readonly_sqlite(
        db_path,
        resolve_strict=True,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    ) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM knowledge_chunks
            WHERE document_id = ?
            ORDER BY chunk_index, id
            """,
            (document_id,),
        ).fetchall()
    return [
        vector_store_service.make_passage_source_id(
            document_id,
            int(row["id"]),
        )
        for row in rows
    ]


def _backup_derived_artifacts(
    *,
    rollback_root: Path,
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
) -> dict[str, bool]:
    rollback_root.mkdir(parents=True, exist_ok=False)
    state = {
        "fts_index": fts_index_path.is_file(),
        "fts_manifest": fts_manifest_path.is_file(),
        "vector_store": vector_store_path.is_dir(),
        "vector_manifest": vector_manifest_path.is_file(),
    }
    if state["fts_index"]:
        shutil.copy2(fts_index_path, rollback_root / "retrieval_fts_v1.db")
    if state["fts_manifest"]:
        shutil.copy2(
            fts_manifest_path,
            rollback_root / "retrieval_fts_v1_manifest.json",
        )
    if state["vector_store"]:
        shutil.copytree(vector_store_path, rollback_root / "lancedb")
    if state["vector_manifest"]:
        shutil.copy2(
            vector_manifest_path,
            rollback_root / "vector_manifest.json",
        )
    return state


def _publish_staged_derived_indexes(
    *,
    staging_fts_index: Path,
    staging_fts_manifest: Path,
    staging_vector_store: Path,
    staging_vector_manifest: Path,
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
) -> None:
    os.replace(staging_fts_index, fts_index_path)
    os.replace(staging_fts_manifest, fts_manifest_path)
    if staging_vector_store.is_dir():
        retired_store = staging_vector_store.parent / ".retired-lancedb"
        if vector_store_path.exists():
            os.replace(vector_store_path, retired_store)
        try:
            os.replace(staging_vector_store, vector_store_path)
        except Exception:
            if retired_store.exists() and not vector_store_path.exists():
                os.replace(retired_store, vector_store_path)
            raise
        _remove_generated_tree(retired_store)
    if staging_vector_manifest.is_file():
        vector_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_vector_manifest, vector_manifest_path)


def _restore_derived_artifacts(
    *,
    rollback_root: Path,
    state: dict[str, bool],
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
) -> None:
    _restore_file(
        fts_index_path,
        rollback_root / "retrieval_fts_v1.db",
        existed=state["fts_index"],
    )
    _restore_file(
        fts_manifest_path,
        rollback_root / "retrieval_fts_v1_manifest.json",
        existed=state["fts_manifest"],
    )
    if vector_store_path.exists():
        _remove_generated_tree(vector_store_path)
    if state["vector_store"]:
        shutil.copytree(rollback_root / "lancedb", vector_store_path)
    _restore_file(
        vector_manifest_path,
        rollback_root / "vector_manifest.json",
        existed=state["vector_manifest"],
    )


def _restore_file(path: Path, backup: Path, *, existed: bool) -> None:
    if existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, path)
    else:
        path.unlink(missing_ok=True)


def _remove_generated_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _default_selected_book_body_importer(
    *,
    preview: dict[str, Any],
    db_path: Path,
    pdf_path: Path,
) -> dict[str, Any]:
    parent = (
        preview.get(
            "zotero_item"
        )
        or {}
    )

    attachment = (
        preview.get(
            "selected_attachment"
        )
        or {}
    )

    title = str(
        parent.get(
            "title"
        )
        or pdf_path.stem
    )

    # Explicitly use the existing lightweight
    # PyMuPDF backend in B4. No Marker/Surya
    # model download or LLM call is introduced.
    prepared = (
        book_import_service
        .prepare_book_import(
            pdf_path,
            title=title,
            backend=PYMUPDF_BACKEND,
        )
    )

    apply_result = (
        book_import_service
        .apply_prepared_book_import(
            prepared,
            db_path=db_path,
            backup=False,
        )
    )

    document_id = (
        _required_document_id(
            apply_result
        )
    )

    source_trace = {
        "source_type": "zotero_pdf",
        "zotero_library_id": int(
            parent.get(
                "library_id"
            )
            or 0
        ),
        "zotero_item_key": str(
            parent.get(
                "zotero_item_key"
            )
            or ""
        ),
        "zotero_attachment_key": str(
            attachment.get(
                "zotero_attachment_key"
            )
            or ""
        ),
        "zotero_source_id": str(
            attachment.get(
                "zotero_attachment_key"
            )
            or ""
        ),
        "zotero_select_uri": (
            parent.get(
                "zotero_select_uri"
            )
        ),
        "zotero_open_pdf_uri": (
            attachment.get(
                "zotero_open_pdf_uri"
            )
        ),
        "source_pdf_sha256": (
            attachment.get(
                "pdf_sha256"
            )
        ),
        "source_revision_fingerprint": (
            (
                preview.get(
                    "source_revision"
                )
                or {}
            ).get(
                "fingerprint"
            )
        ),
    }

    document_source_written = (
        commit_book_service
        ._record_document_source(
            db_path,
            document_id,
            source_trace,
            pdf_path,
        )
    )

    (
        commit_book_service
        ._record_document_zotero_key(
            db_path,
            document_id,
            source_trace,
        )
    )

    return {
        "status": "committed",
        "document_id": document_id,
        "title": title,
        "document_type": "book",
        "chunk_count": int(
            apply_result.get(
                "inserted_chunks"
            )
            or 0
        ),
        "inserted_chunks": int(
            apply_result.get(
                "inserted_chunks"
            )
            or 0
        ),
        "inserted_chapters": int(
            apply_result.get(
                "inserted_chapters"
            )
            or 0
        ),
        "parser_backend": (
            PYMUPDF_BACKEND
        ),
        "document_source_written": bool(
            document_source_written
        ),
        "book_safety_decision": (
            apply_result.get(
                "book_safety_decision"
            )
        ),
    }


def _required_document_id(
    result: dict[str, Any],
) -> int:
    try:
        value = int(
            result.get("document_id")
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "body_document_id_missing"
            ),
            message=(
                "Body importer did not return "
                "a valid document ID."
            ),
            status_code=500,
        ) from exc

    if value <= 0:
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "body_document_id_missing"
            ),
            message=(
                "Body importer did not return "
                "a valid document ID."
            ),
            status_code=500,
        )

    return value


def _create_rollback_copy(
    path: Path,
) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=(
            f".{path.name}."
            "direction-b-rollback-"
        ),
        suffix=".sqlite",
        dir=str(path.parent),
    )
    os.close(descriptor)

    rollback_path = Path(
        raw_path
    )

    try:
        shutil.copy2(
            path,
            rollback_path,
        )
    except Exception:
        rollback_path.unlink(
            missing_ok=True
        )
        raise

    return rollback_path


def _restore_rollback_copy(
    path: Path,
    rollback_path: Path,
) -> None:
    # This service can only reach a temp DB.
    # Remove transaction sidecars before restoring
    # the reviewed snapshot.
    for sidecar in _sqlite_sidecars(
        path
    ):
        sidecar.unlink(
            missing_ok=True
        )

    shutil.copy2(
        rollback_path,
        path,
    )


def _assert_no_live_sidecars(
    path: Path,
) -> None:
    live = [
        item.name
        for item in _sqlite_sidecars(path)
        if item.exists()
    ]

    if live:
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "temp_db_busy"
            ),
            message=(
                "Direction-B temp database "
                "has active SQLite sidecars."
            ),
            status_code=409,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_sidecars(
    path: Path,
) -> tuple[Path, ...]:
    return tuple(
        Path(
            str(path) + suffix
        )
        for suffix in (
            "-wal",
            "-shm",
            "-journal",
        )
    )
