from __future__ import annotations

import os
import json
import hashlib
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
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
from app.services.retrieval import fts_index_service, fts_status_service
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



@dataclass(frozen=True)
class DatabaseRollbackSnapshot:
    path: Path
    sha256: str
    size: int


_DIRECTION_B_IMPORT_LOCK = threading.RLock()


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
    with _DIRECTION_B_IMPORT_LOCK:
        return _commit_selected_book_import_locked(
            preview_token=preview_token,
            runtime=runtime,
            body_importer=body_importer,
            now_ts=now_ts,
        )


def _commit_selected_book_import_locked(
    *,
    preview_token: str,
    runtime: SelectedBookImportRuntime,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    path = Path(runtime.db_path).resolve(strict=False)
    data_root = Path(runtime.data_dir).resolve(strict=False)
    fts_index_path = Path(runtime.fts_index_path).resolve(strict=False)
    fts_manifest_path = Path(runtime.fts_manifest_path).resolve(strict=False)
    vector_store_path = Path(runtime.vector_store_path).resolve(strict=False)
    vector_manifest_path = Path(runtime.vector_manifest_path).resolve(strict=False)

    if runtime.persistence_scope not in {"tempdb", "production"}:
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_runtime_scope_invalid",
            message="Direction-B runtime scope is invalid.",
            status_code=500,
        )

    production = runtime.persistence_scope == "production"

    canonical_pairs = (
        (path, Path(DEFAULT_DB_PATH).resolve(strict=False)),
        (data_root, Path(DATA_DIR).resolve(strict=False)),
        (fts_index_path, Path(DEFAULT_INDEX_PATH).resolve(strict=False)),
        (fts_manifest_path, Path(DEFAULT_MANIFEST_PATH).resolve(strict=False)),
        (vector_store_path, Path(LANCEDB_DIR).resolve(strict=False)),
        (
            vector_manifest_path,
            Path(vector_store_service.MANIFEST_PATH).resolve(strict=False),
        ),
    )

    if production:
        if not all(left == right for left, right in canonical_pairs):
            raise DirectionBSelectedBookImportError(
                code="zotero_direction_b_production_runtime_invalid",
                message="Direction-B production runtime is not canonical.",
                status_code=503,
            )
    elif any(left == right for left, right in canonical_pairs):
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_production_not_enabled",
            message=(
                "Direction-B temp import cannot use production targets."
            ),
            status_code=503,
        )

    if not data_root.is_dir():
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_production_data_root_missing"
                if production
                else "zotero_direction_b_temp_data_root_missing"
            ),
            message="Direction-B data root does not exist.",
            status_code=503,
        )

    if not path.is_file():
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_target_db_missing",
            message="Direction-B target database does not exist.",
            status_code=503,
        )

    if not fts_index_path.is_file() or not fts_manifest_path.is_file():
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_production_fts_not_ready"
                if production
                else "zotero_direction_b_temp_fts_not_ready"
            ),
            message="Direction-B retrieval FTS is not ready.",
            status_code=503,
        )

    _assert_no_live_sidecars(path)

    try:
        preview, pdf_path = (
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
            code="zotero_direction_b_preview_not_ready",
            message="Direction-B selected-book preview is not ready.",
            status_code=409,
        )

    item_type = str(
        (
            preview.get("zotero_item")
            or {}
        ).get("item_type")
        or ""
    ).strip()

    if item_type != "book":
        raise DirectionBSelectedBookImportError(
            code="zotero_item_type_unsupported",
            message="Only Zotero book items can use selected-book import.",
            status_code=422,
            details={"item_type": item_type or "unknown"},
        )

    duplicate = preview.get("duplicate_check") or {}
    if bool(duplicate.get("duplicate_found")):
        raise DirectionBSelectedBookImportError(
            code="zotero_import_duplicate_requires_review",
            message=(
                "The selected Zotero book matches existing library data."
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

    staging_fts_index = (
        staging_root
        / "search_index"
        / fts_index_path.name
    )
    staging_fts_manifest = (
        staging_root
        / "search_index"
        / fts_manifest_path.name
    )
    staging_vector_store = (
        staging_root
        / "vector_store"
        / "lancedb"
    )
    staging_vector_manifest = (
        staging_root
        / "vector_store"
        / vector_manifest_path.name
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
        rollback_snapshot = _create_rollback_copy(path)
    except Exception:
        _remove_generated_tree(staging_root)
        raise

    derived_state: dict[str, Any] | None = None
    publish_attempted = False
    retain_db_rollback = False
    retain_derived_rollback = False

    try:
        try:
            if body_importer is None:
                body_result = _default_selected_book_body_importer(
                    preview=preview,
                    db_path=path,
                    pdf_path=pdf_path,
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
                code="zotero_direction_b_body_import_failed",
                message=(
                    "The selected book body could not be imported."
                ),
                status_code=500,
            ) from exc

        document_id = _required_document_id(body_result)

        try:
            if production:
                note_result = (
                    zotero_direction_b_commit_service
                    .commit_selected_book_preview_to_production(
                        preview_token=preview_token,
                        document_id=document_id,
                        now_ts=now_ts,
                    )
                )
            else:
                note_result = (
                    zotero_direction_b_commit_service
                    .commit_selected_book_preview_to_temp_db(
                        preview_token=preview_token,
                        document_id=document_id,
                        db_path=path,
                        now_ts=now_ts,
                    )
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

        post_write_snapshot = path
        after_db_sha256 = _sha256_file(path)

        if production:
            post_write_snapshot = (
                staging_root
                / "source_snapshot"
                / path.name
            )
            post_write_snapshot.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            after_db_size = path.stat().st_size
            shutil.copy2(
                path,
                post_write_snapshot,
            )

            if (
                _sha256_file(post_write_snapshot)
                != after_db_sha256
                or post_write_snapshot.stat().st_size
                != after_db_size
            ):
                raise DirectionBSelectedBookImportError(
                    code=(
                        "zotero_direction_b_"
                        "post_write_snapshot_invalid"
                    ),
                    message=(
                        "Post-write database snapshot "
                        "verification failed."
                    ),
                    status_code=500,
                )

        try:
            fts_sync = (
                fts_index_service
                .upsert_document_retrieval_fts(
                    document_id=document_id,
                    index_path=staging_fts_index,
                    manifest_path=staging_fts_manifest,
                    research_db_path=post_write_snapshot,
                )
            )

            if (
                fts_sync.get("full_rebuild_performed")
                is not False
                or fts_sync.get(
                    "production_db_write_performed"
                )
                is not False
            ):
                raise RuntimeError(
                    "unsafe FTS sync result"
                )

            passage_source_ids = (
                _passage_source_ids_for_document(
                    post_write_snapshot,
                    document_id,
                )
            )

            passage_vector_sync: dict[str, Any] = {
                "status": "skipped",
                "scope": "affected_source_ids_only",
                "reason": "no_passage_sources",
            }

            if passage_source_ids:
                passage_vector_sync = (
                    vector_store_service
                    .sync_affected_passage_embeddings(
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
                    or passage_vector_sync.get(
                        "full_rebuild_allowed"
                    )
                    is not False
                    or passage_vector_sync.get(
                        "delete_orphans_allowed"
                    )
                    is not False
                ):
                    raise RuntimeError(
                        "unsafe passage vector sync result"
                    )

            note_vector_sync: dict[str, Any] = {
                "status": "skipped",
                "scope": "document_only",
                "reason": "no_personal_notes",
            }

            if int(note_result.get("source_count") or 0) > 0:
                note_vector_sync = (
                    vector_store_service
                    .sync_document_note_embeddings(
                        document_id,
                        dry_run=False,
                        apply=True,
                        source_db_path=post_write_snapshot,
                        store_path=staging_vector_store,
                        manifest_path=staging_vector_manifest,
                    )
                )

                if (
                    note_vector_sync.get("scope")
                    != "document_only"
                    or note_vector_sync.get(
                        "full_rebuild_performed"
                    )
                    is not False
                    or note_vector_sync.get(
                        "orphan_delete_performed"
                    )
                    is not False
                ):
                    raise RuntimeError(
                        "unsafe note vector sync result"
                    )

        except DirectionBSelectedBookImportError:
            raise
        except Exception as exc:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    + (
                        "production_index_sync_failed"
                        if production
                        else "temp_index_sync_failed"
                    )
                ),
                message=(
                    "Direction-B derived index sync failed."
                ),
                status_code=500,
            ) from exc

        derived_state = _backup_derived_artifacts(
            rollback_root=derived_rollback_root,
            fts_index_path=fts_index_path,
            fts_manifest_path=fts_manifest_path,
            vector_store_path=vector_store_path,
            vector_manifest_path=vector_manifest_path,
        )

        publish_attempted = True
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
        except Exception as exc:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    + (
                        "production_index_publish_failed"
                        if production
                        else "temp_index_publish_failed"
                    )
                ),
                message=(
                    "Direction-B derived index publish failed."
                ),
                status_code=500,
            ) from exc

        if production:
            _verify_production_final_state(
                runtime=runtime,
                document_id=document_id,
                expected_db_sha256=after_db_sha256,
            )

        return {
            "status": "committed",
            "document_id": document_id,
            "title": str(
                body_result.get("title")
                or (
                    preview.get("zotero_item")
                    or {}
                ).get("title")
                or ""
            ),
            "document_type": str(
                body_result.get("document_type")
                or "book"
            ),
            "chunk_count": int(
                body_result.get("chunk_count")
                or body_result.get("inserted_chunks")
                or 0
            ),
            "body_import": dict(body_result),
            "note_import": dict(note_result),
            "fts_sync": dict(fts_sync),
            "passage_vector_sync": dict(
                passage_vector_sync
            ),
            "note_vector_sync": dict(
                note_vector_sync
            ),
            "body_importer": (
                "core_book_import"
                if body_importer is None
                else "runtime_override"
            ),
            "persistence_scope": (
                runtime.persistence_scope
            ),
            "production_data_modified": production,
            "production_schema_migrated": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": bool(
                passage_vector_sync.get(
                    "lancedb_writes_performed"
                )
                or note_vector_sync.get(
                    "lancedb_writes_performed"
                )
            ),
            "fts_write_performed": True,
            "derived_index_scope": (
                runtime.persistence_scope
            ),
            "derived_index_publish_performed": True,
            "full_rebuild_performed": False,
            "orphan_delete_performed": False,
        }

    except Exception as exc:
        derived_rollback_exc: Exception | None = None
        db_rollback_exc: Exception | None = None

        if publish_attempted and derived_state is not None:
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
                derived_rollback_exc = rollback_exc
                retain_derived_rollback = True

        try:
            _restore_rollback_copy(
                path,
                rollback_snapshot,
            )
        except Exception as rollback_exc:
            db_rollback_exc = rollback_exc
            retain_db_rollback = True

        if db_rollback_exc is not None:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    + (
                        "production_db_rollback_failed"
                        if production
                        else "temp_rollback_failed"
                    )
                ),
                message=(
                    "Direction-B database rollback failed."
                ),
                status_code=500,
                details=(
                    {
                        "derived_rollback_failed": (
                            derived_rollback_exc
                            is not None
                        )
                    }
                    if production
                    else {}
                ),
            ) from db_rollback_exc

        if derived_rollback_exc is not None:
            raise DirectionBSelectedBookImportError(
                code=(
                    "zotero_direction_b_"
                    + (
                        "production_derived_rollback_failed"
                        if production
                        else "temp_index_rollback_failed"
                    )
                ),
                message=(
                    "Direction-B derived index rollback failed."
                ),
                status_code=500,
            ) from derived_rollback_exc

        if isinstance(
            exc,
            DirectionBSelectedBookImportError,
        ):
            raise

        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                + (
                    "production_import_failed"
                    if production
                    else "import_failed"
                )
            ),
            message=(
                "Direction-B selected-book import failed."
            ),
            status_code=500,
        ) from exc

    finally:
        if not retain_db_rollback:
            rollback_snapshot.path.unlink(
                missing_ok=True
            )

        _remove_generated_tree(staging_root)

        if not retain_derived_rollback:
            _remove_generated_tree(
                derived_rollback_root
            )


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
    with closing(connect_readonly_sqlite(
        db_path,
        resolve_strict=True,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    )) as connection:
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
) -> dict[str, Any]:
    rollback_root.mkdir(
        parents=True,
        exist_ok=False,
    )

    state: dict[str, Any] = {
        "fts_index": fts_index_path.is_file(),
        "fts_manifest": fts_manifest_path.is_file(),
        "vector_store": vector_store_path.is_dir(),
        "vector_manifest": vector_manifest_path.is_file(),
        "fts_index_sha256": (
            _sha256_file(fts_index_path)
            if fts_index_path.is_file()
            else None
        ),
        "fts_manifest_sha256": (
            _sha256_file(fts_manifest_path)
            if fts_manifest_path.is_file()
            else None
        ),
        "vector_store_fingerprint": (
            _tree_fingerprint(vector_store_path)
            if vector_store_path.is_dir()
            else None
        ),
        "vector_manifest_sha256": (
            _sha256_file(vector_manifest_path)
            if vector_manifest_path.is_file()
            else None
        ),
    }

    if state["fts_index"]:
        target = rollback_root / "retrieval_fts_v1.db"
        shutil.copy2(
            fts_index_path,
            target,
        )
        if (
            _sha256_file(target)
            != state["fts_index_sha256"]
        ):
            raise RuntimeError(
                "FTS rollback backup verification failed"
            )

    if state["fts_manifest"]:
        target = (
            rollback_root
            / "retrieval_fts_v1_manifest.json"
        )
        shutil.copy2(
            fts_manifest_path,
            target,
        )
        if (
            _sha256_file(target)
            != state["fts_manifest_sha256"]
        ):
            raise RuntimeError(
                "FTS manifest rollback backup "
                "verification failed"
            )

    if state["vector_store"]:
        target = rollback_root / "lancedb"
        shutil.copytree(
            vector_store_path,
            target,
        )
        if (
            _tree_fingerprint(target)
            != state["vector_store_fingerprint"]
        ):
            raise RuntimeError(
                "Vector rollback backup "
                "verification failed"
            )

    if state["vector_manifest"]:
        target = (
            rollback_root
            / "vector_manifest.json"
        )
        shutil.copy2(
            vector_manifest_path,
            target,
        )
        if (
            _sha256_file(target)
            != state["vector_manifest_sha256"]
        ):
            raise RuntimeError(
                "Vector manifest rollback backup "
                "verification failed"
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
    state: dict[str, Any],
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
) -> None:
    _restore_file(
        fts_index_path,
        rollback_root / "retrieval_fts_v1.db",
        existed=bool(state["fts_index"]),
    )
    _restore_file(
        fts_manifest_path,
        rollback_root / "retrieval_fts_v1_manifest.json",
        existed=bool(state["fts_manifest"]),
    )

    if vector_store_path.exists():
        _remove_generated_tree(
            vector_store_path
        )

    if state["vector_store"]:
        shutil.copytree(
            rollback_root / "lancedb",
            vector_store_path,
        )

    _restore_file(
        vector_manifest_path,
        rollback_root / "vector_manifest.json",
        existed=bool(state["vector_manifest"]),
    )

    if (
        state["fts_index"]
        and _sha256_file(fts_index_path)
        != state["fts_index_sha256"]
    ):
        raise RuntimeError(
            "FTS rollback verification failed"
        )

    if (
        state["fts_manifest"]
        and _sha256_file(fts_manifest_path)
        != state["fts_manifest_sha256"]
    ):
        raise RuntimeError(
            "FTS manifest rollback verification failed"
        )

    if (
        state["vector_store"]
        and _tree_fingerprint(vector_store_path)
        != state["vector_store_fingerprint"]
    ):
        raise RuntimeError(
            "Vector rollback verification failed"
        )

    if (
        state["vector_manifest"]
        and _sha256_file(vector_manifest_path)
        != state["vector_manifest_sha256"]
    ):
        raise RuntimeError(
            "Vector manifest rollback verification failed"
        )

    if (
        not state["vector_store"]
        and vector_store_path.exists()
    ):
        raise RuntimeError(
            "Vector rollback left an unexpected store"
        )

    if (
        not state["vector_manifest"]
        and vector_manifest_path.exists()
    ):
        raise RuntimeError(
            "Vector rollback left an unexpected manifest"
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
) -> DatabaseRollbackSnapshot:
    before_sha256 = _sha256_file(path)
    before_size = path.stat().st_size

    descriptor, raw_path = tempfile.mkstemp(
        prefix=(
            f".{path.name}."
            "direction-b-rollback-"
        ),
        suffix=".sqlite",
        dir=str(path.parent),
    )
    os.close(descriptor)

    rollback_path = Path(raw_path)

    try:
        shutil.copy2(
            path,
            rollback_path,
        )

        if (
            rollback_path.stat().st_size
            != before_size
            or _sha256_file(rollback_path)
            != before_sha256
        ):
            raise RuntimeError(
                "database rollback backup "
                "verification failed"
            )

    except Exception:
        rollback_path.unlink(
            missing_ok=True
        )
        raise

    return DatabaseRollbackSnapshot(
        path=rollback_path,
        sha256=before_sha256,
        size=before_size,
    )


def _restore_rollback_copy(
    path: Path,
    rollback: DatabaseRollbackSnapshot,
) -> None:
    for sidecar in _sqlite_sidecars(path):
        sidecar.unlink(missing_ok=True)

    candidate = path.with_name(
        f".{path.name}.{uuid4().hex}.restore"
    )

    try:
        shutil.copy2(
            rollback.path,
            candidate,
        )

        if (
            candidate.stat().st_size
            != rollback.size
            or _sha256_file(candidate)
            != rollback.sha256
        ):
            raise RuntimeError(
                "database rollback restore "
                "candidate verification failed"
            )

        try:
            # Preferred path: atomic replacement.
            os.replace(
                candidate,
                path,
            )

        except PermissionError:
            # Windows can reject os.replace while a SQLite
            # connection from a failing traceback still owns
            # a share handle. The candidate has already been
            # byte-verified, so perform a verified emergency
            # overwrite instead of silently losing rollback.
            with candidate.open("rb") as source:
                with path.open("wb") as target:
                    shutil.copyfileobj(
                        source,
                        target,
                        length=1024 * 1024,
                    )
                    target.flush()
                    os.fsync(
                        target.fileno()
                    )

        if (
            path.stat().st_size
            != rollback.size
            or _sha256_file(path)
            != rollback.sha256
        ):
            raise RuntimeError(
                "database rollback target "
                "verification failed"
            )

    finally:
        candidate.unlink(
            missing_ok=True
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




def _tree_fingerprint(
    path: Path,
) -> str:
    root = Path(path)
    digest = hashlib.sha256()

    for item in sorted(
        (
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file()
        ),
        key=lambda candidate: (
            candidate.relative_to(root).as_posix()
        ),
    ):
        relative = item.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(
            _sha256_file(item).encode("ascii")
        )
        digest.update(b"\n")

    return digest.hexdigest()


def _verify_production_final_state(
    *,
    runtime: SelectedBookImportRuntime,
    document_id: int,
    expected_db_sha256: str,
) -> None:
    if (
        _sha256_file(runtime.db_path)
        != expected_db_sha256
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production database revision changed "
                "during final verification."
            ),
            status_code=500,
        )

    with closing(connect_readonly_sqlite(
        runtime.db_path,
        resolve_strict=True,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    )) as connection:
        document_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()[0]
        )
        chunk_count = int(
            connection.execute(
                (
                    "SELECT COUNT(*) "
                    "FROM knowledge_chunks "
                    "WHERE document_id = ?"
                ),
                (document_id,),
            ).fetchone()[0]
        )

    if document_count != 1 or chunk_count <= 0:
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production document verification failed."
            ),
            status_code=500,
        )

    try:
        manifest = json.loads(
            Path(runtime.fts_manifest_path)
            .read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production FTS manifest could not "
                "be verified."
            ),
            status_code=500,
        ) from exc

    if (
        str(
            manifest.get(
                "production_db_sha256"
            )
            or ""
        ).lower()
        != expected_db_sha256.lower()
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production FTS manifest is not bound "
                "to the committed database revision."
            ),
            status_code=500,
        )

    status = fts_status_service.get_index_status(
        index_path=runtime.fts_index_path,
        manifest_path=runtime.fts_manifest_path,
        production_db_path=runtime.db_path,
    )

    if (
        status.get("status") != "ready"
        or status.get("ready") is not True
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production retrieval FTS is not ready "
                "after selected-book import."
            ),
            status_code=500,
        )


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
