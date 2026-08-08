from __future__ import annotations

import os
import json
import hashlib
import re
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
from app.domains.retrieval import fragment_repository, note_vector_index
from app.domains.retrieval.result_contracts import NOTE_SOURCE_TYPES
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
from app.services.retrieval.source_registry import (
    RetrievalSourceRegistry,
)
from app.services.pdf_parser_backends import (
    MARKER_SURYA_PAGE_BLOCKS_BACKEND,
    PYMUPDF_BACKEND,
    parse_pdf_to_markdown,
)
from app.services import pdf_extraction_strategy_service


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


class DirectionBDerivedPublishError(RuntimeError):
    """Internal exception that records the exact os.replace substage.

    This is NEVER exposed as a public error_code.  The public contract
    continues to use the existing ``*_index_publish_failed`` codes.
    """

    def __init__(
        self,
        *,
        publish_substage: str,
        original_exception: BaseException,
    ) -> None:
        super().__init__(str(original_exception))
        self.publish_substage = publish_substage
        self.original_exception = original_exception


def _extract_cause_metadata(exc: BaseException) -> dict[str, Any]:
    """Safely extract os-level cause fields from an exception.

    Returns a dict with keys that are always present (null when absent).
    Never includes traceback strings, token values, or env vars.
    """
    cause: dict[str, Any] = {
        "cause_type": type(exc).__name__,
        "cause_message": _safe_exception_message(exc),
        "cause_errno": None,
        "cause_winerror": None,
        "cause_filename": None,
        "cause_filename2": None,
    }
    if isinstance(exc, OSError):
        cause["cause_errno"] = getattr(exc, "errno", None)
        cause["cause_winerror"] = getattr(exc, "winerror", None)
        cause["cause_filename"] = _safe_path_str(getattr(exc, "filename", None))
        cause["cause_filename2"] = _safe_path_str(getattr(exc, "filename2", None))
    return cause


_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Each tuple is (case-insensitive regex, replacement template).
    # Order matters: longer / more-specific patterns first to avoid
    # partial matches (e.g. access_token matched before token alone).
    (
        (
            r"confirmation[_\-\s]?token"
            r"(?![ _\-]?digest\b)[=:\s]+[^\s,;)\]}]+"
        ),
        "confirmation_token=[REDACTED]",
    ),
    (
        r"authorization:\s*bearer\s+[A-Za-z0-9._~+/=\-]+",
        "authorization: Bearer [REDACTED]",
    ),
    (
        r"bearer\s+[A-Za-z0-9._~+/=\-]{20,}",
        "bearer [REDACTED]",
    ),
    (
        r"api[_\-\s]?key[=:\s]+[^\s,;)\]}]+",
        "api_key=[REDACTED]",
    ),
    (
        r"access[_\-\s]?token[=:\s]+[^\s,;)\]}]+",
        "access_token=[REDACTED]",
    ),
    (
        r"refresh[_\-\s]?token[=:\s]+[^\s,;)\]}]+",
        "refresh_token=[REDACTED]",
    ),
    (
        r"secret[=:\s]+[^\s,;)\]}]+",
        "secret=[REDACTED]",
    ),
    (
        r"password[=:\s]+[^\s,;)\]}]+",
        "password=[REDACTED]",
    ),
]


def _redact_secrets(text: str) -> str:
    """Deterministic, minimal secret redaction.

    Matches confirmation tokens, bearer tokens, API keys,
    access/refresh tokens, secrets, and passwords — case-insensitive.
    Does NOT redact hex strings that don't match known secret key
    prefixes.  File paths and normal error messages are preserved.
    """
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def _safe_exception_message(exc: BaseException) -> str | None:
    raw = str(exc) if exc is not None else None
    if raw is None:
        return None
    cleaned = " ".join(str(raw).split()).strip()
    if not cleaned:
        return None
    # Redact secrets BEFORE length truncation so that the truncation
    # cannot accidentally expose a partial secret.
    cleaned = _redact_secrets(cleaned)
    if len(cleaned) > 512:
        cleaned = cleaned[:509] + "..."
    return cleaned


def _safe_path_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes)):
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


DirectionBStageCallback = Callable[
    [str, dict[str, Any]],
    None,
]


def _emit_stage(
    callback: DirectionBStageCallback | None,
    stage: str,
    **metadata: Any,
) -> None:
    allowed_metadata = {
        "document_id",
        "chunk_count",
        "writes_performed",
        "source_count",
        "rollback_attempted",
        "rollback_completed",
        "warning_codes",
    }
    unexpected = set(metadata) - allowed_metadata
    if unexpected:
        raise ValueError(
            "unsafe Direction-B stage metadata fields: "
            + ", ".join(sorted(unexpected))
        )
    if callback is not None:
        callback(stage, dict(metadata))


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
    import_audit: dict[str, Any] | None = None,
    now_ts: float | None = None,
    stage_callback: DirectionBStageCallback | None = None,
) -> dict[str, Any]:
    with _DIRECTION_B_IMPORT_LOCK:
        try:
            return _commit_selected_book_import_locked(
                preview_token=preview_token,
                runtime=runtime,
                body_importer=body_importer,
                import_audit=import_audit,
                now_ts=now_ts,
                stage_callback=stage_callback,
            )
        except DirectionBSelectedBookImportError as exc:
            details = dict(exc.details)
            details.setdefault("error_stage", "preflight")
            details.setdefault("writes_performed", False)
            details.setdefault("rollback_attempted", False)
            details.setdefault("rollback_completed", False)
            raise DirectionBSelectedBookImportError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                details=details,
            ) from exc
        except Exception as exc:
            raise DirectionBSelectedBookImportError(
                code="zotero_direction_b_import_failed",
                message="Direction-B selected-book import failed.",
                status_code=500,
                details={
                    "error_stage": "preflight",
                    "writes_performed": False,
                    "rollback_attempted": False,
                    "rollback_completed": False,
                },
            ) from exc


def _commit_selected_book_import_locked(
    *,
    preview_token: str,
    runtime: SelectedBookImportRuntime,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    import_audit: dict[str, Any] | None = None,
    now_ts: float | None = None,
    stage_callback: DirectionBStageCallback | None = None,
) -> dict[str, Any]:
    path = Path(runtime.db_path).resolve(strict=False)
    data_root = Path(runtime.data_dir).resolve(strict=False)
    fts_index_path = Path(runtime.fts_index_path).resolve(strict=False)
    fts_manifest_path = Path(runtime.fts_manifest_path).resolve(strict=False)
    vector_store_path = Path(runtime.vector_store_path).resolve(strict=False)
    vector_manifest_path = Path(runtime.vector_manifest_path).resolve(strict=False)
    zotero_note_vector_path = (
        data_root
        / "vector_store"
        / "zotero_user_notes_v1"
    )

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

    if (
        production
        and not (
            zotero_note_vector_path
            / note_vector_index.MANIFEST_NAME
        ).is_file()
    ):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_production_"
                "note_vectors_not_ready"
            ),
            message=(
                "Direction-B Zotero note vectors are not ready."
            ),
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

    try:
        document_type = (
            zotero_selected_book_preview_service
            .document_type_for_item_type(item_type)
        )
    except ValueError as exc:
        raise DirectionBSelectedBookImportError(
            code="zotero_item_type_unsupported",
            message=(
                "The selected Zotero item type is not supported for "
                "bibliographic PDF import."
            ),
            status_code=422,
            details={"item_type": item_type or "unknown"},
        ) from exc

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
    staging_zotero_note_vector_path = (
        staging_root
        / "n"
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
            zotero_note_vector_path=(
                zotero_note_vector_path
            ),
            staging_zotero_note_vector_path=(
                staging_zotero_note_vector_path
                ),
            )
            if not production:
                _initialize_empty_temp_staging_fts_manifest(
                    research_db_path=path,
                    data_root=data_root,
                    staging_fts_index=staging_fts_index,
                    staging_fts_manifest=staging_fts_manifest,
                )
            rollback_snapshot = _create_rollback_copy(path)
    except Exception:
        _remove_generated_tree(staging_root)
        raise

    derived_state: dict[str, Any] | None = None
    publish_attempted = False
    retain_db_rollback = False
    retain_derived_rollback = False
    writes_performed = False
    production_data_modified = False
    derived_index_publish_performed = False
    rollback_attempted = False
    rollback_completed = False
    current_stage = "confirmation_accepted"
    document_id: int | None = None
    chunk_count = 0
    callback_warning_codes: list[str] = []

    def forward_stage(
        stage: str,
        **metadata: Any,
    ) -> None:
        nonlocal current_stage
        current_stage = stage
        _emit_stage(
            stage_callback,
            stage,
            **metadata,
        )

    try:
        try:
            forward_stage(
                "body_import_started",
                writes_performed=False,
            )
            # Once control enters the body importer, a failed call may
            # have performed partial writes before raising. Track that
            # conservatively so failure receipts never claim a clean
            # no-write outcome without rollback evidence.
            writes_performed = True
            production_data_modified = production
            if body_importer is None:
                body_result = _default_selected_book_body_importer(
                    preview=preview,
                    db_path=path,
                    pdf_path=pdf_path,
                    import_audit=import_audit,
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
        chunk_count = int(
            body_result.get("chunk_count")
            or body_result.get("inserted_chunks")
            or 0
        )
        forward_stage(
            "body_import_completed",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
        )

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

        forward_stage(
            "staging_snapshot_created",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
            source_count=int(note_result.get("source_count") or 0),
        )

        try:
            forward_stage(
                "staging_fts_started",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
            )
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

            forward_stage(
                "staging_fts_completed",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
            )

            passage_source_ids = (
                _passage_source_ids_for_document(
                    post_write_snapshot,
                    document_id,
                )
            )

            forward_stage(
                "staging_vector_started",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
                source_count=len(passage_source_ids),
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

            native_note_vector_sync: dict[str, Any] = {
                "status": "skipped",
                "scope": "affected_fragment_ids_only",
                "reason": "note_vector_index_not_present",
                "scoped_entry_count_after": 0,
                "full_rebuild_performed": False,
                "orphan_delete_performed": False,
            }

            if (
                staging_zotero_note_vector_path
                / note_vector_index.MANIFEST_NAME
            ).is_file():
                native_note_fragments = (
                    _native_note_fragments_for_document(
                        source_db_path=post_write_snapshot,
                        data_root=data_root,
                        document_id=document_id,
                    )
                )
                if native_note_fragments:
                    native_note_vector_sync = (
                        note_vector_index
                        .attach_zotero_note_vector_document_scope(
                            document_id,
                            fragments=native_note_fragments,
                            index_dir=(
                                staging_zotero_note_vector_path
                            ),
                        )
                    )
                else:
                    native_note_vector_sync = {
                        "status": "skipped",
                        "scope": "affected_fragment_ids_only",
                        "reason": "no_native_note_fragments",
                        "scoped_entry_count_after": 0,
                        "full_rebuild_performed": False,
                        "orphan_delete_performed": False,
                    }

                if (
                    native_note_vector_sync.get("scope")
                    != "affected_fragment_ids_only"
                    or native_note_vector_sync.get(
                        "full_rebuild_performed"
                    )
                    is not False
                    or native_note_vector_sync.get(
                        "orphan_delete_performed"
                    )
                    is not False
                ):
                    raise RuntimeError(
                        "unsafe native note vector sync result"
                    )

            forward_stage(
                "staging_vector_completed",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
                source_count=(
                    len(passage_source_ids)
                    + int(note_result.get("source_count") or 0)
                ),
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

        _verify_staging_final_state(
            runtime=runtime,
            post_write_snapshot=post_write_snapshot,
            expected_db_sha256=after_db_sha256,
            document_id=document_id,
            passage_source_ids=passage_source_ids,
            expected_native_note_vector_count=int(
                native_note_vector_sync.get(
                    "scoped_entry_count_after"
                )
                or 0
            ),
            staging_fts_index=staging_fts_index,
            staging_fts_manifest=staging_fts_manifest,
            staging_vector_store=staging_vector_store,
            staging_vector_manifest=staging_vector_manifest,
            staging_zotero_note_vector_path=(
                staging_zotero_note_vector_path
            ),
        )

        forward_stage(
            "derived_backup_started",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
        )
        derived_state = _backup_derived_artifacts(
            rollback_root=derived_rollback_root,
            fts_index_path=fts_index_path,
            fts_manifest_path=fts_manifest_path,
            vector_store_path=vector_store_path,
            vector_manifest_path=vector_manifest_path,
            zotero_note_vector_path=(
                zotero_note_vector_path
            ),
        )
        forward_stage(
            "derived_backup_completed",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
        )

        try:
            forward_stage(
                "publish_started",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
            )
            publish_attempted = True
            _publish_staged_derived_indexes(
                staging_fts_index=staging_fts_index,
                staging_fts_manifest=staging_fts_manifest,
                staging_vector_store=staging_vector_store,
                staging_vector_manifest=staging_vector_manifest,
                fts_index_path=fts_index_path,
                fts_manifest_path=fts_manifest_path,
                vector_store_path=vector_store_path,
                vector_manifest_path=vector_manifest_path,
                staging_zotero_note_vector_path=(
                    staging_zotero_note_vector_path
                ),
                zotero_note_vector_path=(
                    zotero_note_vector_path
                ),
            )
            derived_index_publish_performed = True
            forward_stage(
                "publish_completed",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=True,
            )
        except Exception as exc:
            publish_substage: str | None = None
            cause_meta: dict[str, Any] = {}
            if isinstance(exc, DirectionBDerivedPublishError):
                publish_substage = exc.publish_substage
                cause_meta = _extract_cause_metadata(exc.original_exception)
            else:
                cause_meta = _extract_cause_metadata(exc)
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
                details={
                    "publish_substage": publish_substage,
                    **cause_meta,
                },
            ) from exc

        forward_stage(
            "final_verification_started",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
        )
        if production:
            _verify_production_final_state(
                runtime=runtime,
                document_id=document_id,
                expected_db_sha256=after_db_sha256,
                expected_native_note_vector_count=int(
                    native_note_vector_sync.get(
                        "scoped_entry_count_after"
                    )
                    or 0
                ),
            )
        forward_stage(
            "final_verification_completed",
            document_id=document_id,
            chunk_count=chunk_count,
            writes_performed=True,
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
                or document_type
            ),
            "chunk_count": chunk_count,
            "body_import": dict(body_result),
            "note_import": dict(note_result),
            "fts_sync": dict(fts_sync),
            "passage_vector_sync": dict(
                passage_vector_sync
            ),
            "note_vector_sync": dict(
                note_vector_sync
            ),
            "native_note_vector_sync": dict(
                native_note_vector_sync
            ),
            "body_importer": (
                "core_book_import"
                if body_importer is None
                else "runtime_override"
            ),
            "persistence_scope": (
                runtime.persistence_scope
            ),
            "writes_performed": True,
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
                or native_note_vector_sync.get(
                    "vector_write_performed"
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
        failure_stage = current_stage
        rollback_attempted = True
        current_stage = "rollback_started"
        try:
            _emit_stage(
                stage_callback,
                "rollback_started",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=writes_performed,
                rollback_attempted=True,
                rollback_completed=False,
            )
        except Exception as callback_exc:
            callback_warning_codes.append(
                "rollback_started_callback_failed:"
                + type(callback_exc).__name__
            )

        if publish_attempted and derived_state is not None:
            try:
                _restore_derived_artifacts(
                    rollback_root=derived_rollback_root,
                    state=derived_state,
                    fts_index_path=fts_index_path,
                    fts_manifest_path=fts_manifest_path,
                    vector_store_path=vector_store_path,
                    vector_manifest_path=vector_manifest_path,
                    zotero_note_vector_path=(
                        zotero_note_vector_path
                    ),
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

        rollback_completed = (
            db_rollback_exc is None
            and derived_rollback_exc is None
        )
        current_stage = "rollback_completed"
        try:
            _emit_stage(
                stage_callback,
                "rollback_completed",
                document_id=document_id,
                chunk_count=chunk_count,
                writes_performed=writes_performed,
                rollback_attempted=True,
                rollback_completed=rollback_completed,
                warning_codes=callback_warning_codes,
            )
        except Exception as callback_exc:
            callback_warning_codes.append(
                "rollback_completed_callback_failed:"
                + type(callback_exc).__name__
            )

        original_details = (
            dict(exc.details)
            if isinstance(exc, DirectionBSelectedBookImportError)
            else {}
        )
        stable_details = dict(original_details)
        stable_details.setdefault("error_stage", failure_stage)
        stable_details.setdefault("writes_performed", writes_performed)
        stable_details.setdefault(
            "production_data_modified",
            production_data_modified,
        )
        stable_details.setdefault("publish_attempted", publish_attempted)
        stable_details.setdefault(
            "derived_index_publish_performed",
            derived_index_publish_performed,
        )
        stable_details["rollback_attempted"] = rollback_attempted
        stable_details["rollback_completed"] = rollback_completed
        if document_id is not None:
            stable_details.setdefault("document_id", document_id)
        stable_details.setdefault("chunk_count", chunk_count)
        stable_details.setdefault("publish_substage", None)
        for cause_key in (
            "cause_type",
            "cause_message",
            "cause_errno",
            "cause_winerror",
            "cause_filename",
            "cause_filename2",
        ):
            stable_details.setdefault(cause_key, None)
        if callback_warning_codes:
            stable_details.setdefault(
                "warnings",
                list(callback_warning_codes),
            )

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
                details={
                    **stable_details,
                    "derived_rollback_failed": (
                        derived_rollback_exc is not None
                    ),
                    "rollback_completed": False,
                },
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
                details={
                    **stable_details,
                    "rollback_completed": False,
                },
            ) from derived_rollback_exc

        if isinstance(
            exc,
            DirectionBSelectedBookImportError,
        ):
            raise DirectionBSelectedBookImportError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                details=stable_details,
            ) from exc

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
            details=stable_details,
        ) from exc

    finally:
        if not retain_db_rollback:
            try:
                rollback_snapshot.path.unlink(
                    missing_ok=True
                )
            except Exception:
                # Cleanup failure must never change the
                # committed/rollback result. Leaving a
                # verified recovery artifact is fail-safe.
                pass

        _best_effort_remove_generated_tree(
            staging_root
        )

        if not retain_derived_rollback:
            _best_effort_remove_generated_tree(
                derived_rollback_root
            )


def commit_selected_book_import_to_temp_db(
    *,
    preview_token: str,
    db_path: str | Path,
    data_dir: str | Path,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    import_audit: dict[str, Any] | None = None,
    now_ts: float | None = None,
    stage_callback: DirectionBStageCallback | None = None,
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
        import_audit=import_audit,
        now_ts=now_ts,
        stage_callback=stage_callback,
    )


def commit_selected_book_import_to_production(
    *,
    preview_token: str,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    import_audit: dict[str, Any] | None = None,
    now_ts: float | None = None,
    stage_callback: DirectionBStageCallback | None = None,
) -> dict[str, Any]:
    return _commit_selected_book_import(
        preview_token=preview_token,
        runtime=_production_runtime(),
        body_importer=body_importer,
        import_audit=import_audit,
        now_ts=now_ts,
        stage_callback=stage_callback,
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
    zotero_note_vector_path: Path,
    staging_zotero_note_vector_path: Path,
) -> None:
    staging_fts_index.parent.mkdir(parents=True, exist_ok=False)
    staging_vector_store.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(fts_index_path, staging_fts_index)
    shutil.copy2(fts_manifest_path, staging_fts_manifest)
    if vector_store_path.is_dir():
        shutil.copytree(vector_store_path, staging_vector_store)
    if vector_manifest_path.is_file():
        shutil.copy2(vector_manifest_path, staging_vector_manifest)
    if zotero_note_vector_path.is_dir():
        shutil.copytree(
            zotero_note_vector_path,
            staging_zotero_note_vector_path,
        )


def _initialize_empty_temp_staging_fts_manifest(
    *,
    research_db_path: Path,
    data_root: Path,
    staging_fts_index: Path,
    staging_fts_manifest: Path,
) -> None:
    try:
        manifest = json.loads(
            staging_fts_manifest.read_text(encoding="utf-8")
        )
    except Exception:
        return
    if manifest != {}:
        return

    with closing(
        connect_readonly_sqlite(
            research_db_path,
            resolve_strict=True,
            row_factory=sqlite3.Row,
            query_only=True,
            temp_store="MEMORY",
        )
    ) as connection:
        document_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
        )
    if document_count != 0:
        return

    with closing(
        fts_status_service.connect_readonly_index(staging_fts_index)
    ) as connection:
        validation = fts_status_service.validate_index_database(
            connection,
            expected_fragment_count=0,
        )
    if validation.get("valid") is not True:
        return

    source_fingerprints = fts_status_service.source_fingerprints(
        production_db_path=research_db_path,
        zotero_snapshot_path=(
            data_root / "zotero" / "snapshot" / "zotero.sqlite"
        ),
        notes_root=data_root / "notes",
    )
    query_aliases_path = fts_status_service.DEFAULT_QUERY_ALIASES_PATH
    manifest.update(
        {
            "index_schema_version": (
                fts_status_service.INDEX_SCHEMA_VERSION
            ),
            "source_registry_version": (
                fts_status_service.SOURCE_REGISTRY_VERSION
            ),
            "adapter_versions": (
                fts_status_service.EXPECTED_ADAPTER_VERSIONS
            ),
            "production_db_sha256": source_fingerprints[
                "production_db_sha256"
            ],
            "zotero_snapshot_sha256": source_fingerprints[
                "zotero_snapshot_sha256"
            ],
            "local_markdown_aggregate_hash": source_fingerprints[
                "local_markdown_aggregate_hash"
            ],
            "query_aliases_sha256": (
                fts_status_service.sha256_file(query_aliases_path)
                if query_aliases_path.is_file()
                else None
            ),
            "fragment_count": 0,
            "tokenizers": fts_status_service.TOKENIZER_CONFIG,
            "index_content_hash": (
                fts_status_service.sha256_file(staging_fts_index)
            ),
            "index_file_bytes": staging_fts_index.stat().st_size,
        }
    )
    temporary_manifest = staging_fts_manifest.with_name(
        f".{staging_fts_manifest.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary_manifest.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, staging_fts_manifest)
    finally:
        temporary_manifest.unlink(missing_ok=True)


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


def _native_note_fragments_for_document(
    *,
    source_db_path: Path,
    data_root: Path,
    document_id: int,
) -> list[Any]:
    registry = RetrievalSourceRegistry(
        research_db_path=source_db_path,
        zotero_snapshot_path=(
            data_root
            / "zotero"
            / "snapshot"
            / "zotero.sqlite"
        ),
        notes_root=data_root / "notes",
        project_root=data_root.parent,
    )
    return fragment_repository.list_notebook_fragments(
        source_types=NOTE_SOURCE_TYPES,
        document_ids=(document_id,),
        registry=registry,
    )


def _verify_staging_final_state(
    *,
    runtime: SelectedBookImportRuntime,
    post_write_snapshot: Path,
    expected_db_sha256: str,
    document_id: int,
    passage_source_ids: list[str],
    expected_native_note_vector_count: int,
    staging_fts_index: Path,
    staging_fts_manifest: Path,
    staging_vector_store: Path,
    staging_vector_manifest: Path,
    staging_zotero_note_vector_path: Path,
) -> None:
    missing_components: list[str] = []
    staging_targets = (
        Path(staging_fts_index).resolve(strict=False),
        Path(staging_fts_manifest).resolve(strict=False),
        Path(staging_vector_store).resolve(strict=False),
        Path(staging_vector_manifest).resolve(strict=False),
        Path(staging_zotero_note_vector_path).resolve(strict=False),
    )
    production_targets = {
        Path(runtime.fts_index_path).resolve(strict=False),
        Path(runtime.fts_manifest_path).resolve(strict=False),
        Path(runtime.vector_store_path).resolve(strict=False),
        Path(runtime.vector_manifest_path).resolve(strict=False),
        (
            Path(runtime.data_dir).resolve(strict=False)
            / "vector_store"
            / "zotero_user_notes_v1"
        ),
    }
    if any(target in production_targets for target in staging_targets):
        missing_components.append("staging_target_is_production")

    if not staging_fts_index.is_file():
        missing_components.append("staging_fts_index")
    if not staging_fts_manifest.is_file():
        missing_components.append("staging_fts_manifest")
    fts_manifest: dict[str, Any] | None = None
    if staging_fts_manifest.is_file():
        try:
            parsed = json.loads(
                staging_fts_manifest.read_text(encoding="utf-8")
            )
            if not isinstance(parsed, dict):
                raise TypeError("manifest is not an object")
            fts_manifest = parsed
        except Exception:
            missing_components.append("staging_fts_manifest_invalid")
    if (
        fts_manifest is not None
        and str(fts_manifest.get("production_db_sha256") or "").lower()
        != expected_db_sha256.lower()
    ):
        missing_components.append("staging_fts_db_revision")
    if staging_fts_index.is_file() and fts_manifest is not None:
        try:
            status = fts_status_service.get_index_status(
                index_path=staging_fts_index,
                manifest_path=staging_fts_manifest,
                production_db_path=post_write_snapshot,
                zotero_snapshot_path=(
                    Path(runtime.data_dir)
                    / "zotero"
                    / "snapshot"
                    / "zotero.sqlite"
                ),
                notes_root=Path(runtime.data_dir) / "notes",
            )
            if (
                status.get("status") != "ready"
                or status.get("ready") is not True
            ):
                missing_components.append("staging_fts_not_ready")
        except Exception:
            missing_components.append("staging_fts_unreadable")

    if _sha256_file(post_write_snapshot) != expected_db_sha256:
        missing_components.append("staging_database_revision")

    if not staging_vector_store.is_dir():
        missing_components.append("staging_vector_store")
    if not staging_vector_manifest.is_file():
        missing_components.append("staging_vector_manifest")
    if staging_vector_manifest.is_file():
        try:
            vector_manifest = json.loads(
                staging_vector_manifest.read_text(encoding="utf-8")
            )
            if not isinstance(vector_manifest, dict):
                raise TypeError("manifest is not an object")
        except Exception:
            missing_components.append("staging_vector_manifest_invalid")
    if staging_vector_store.is_dir():
        try:
            impact = vector_store_service.inspect_document_vector_impact(
                passage_source_ids=passage_source_ids,
                object_keys=[],
                store_path=staging_vector_store,
            )
            if int(impact.get("passage_vector_count") or 0) != len(
                passage_source_ids
            ):
                missing_components.append("staging_passage_vectors")
        except Exception:
            missing_components.append("staging_vector_store_unreadable")

    note_vectors_required = (
        runtime.persistence_scope == "production"
        or expected_native_note_vector_count > 0
    )
    if note_vectors_required and not staging_zotero_note_vector_path.is_dir():
        missing_components.append("staging_zotero_note_vectors")
    elif staging_zotero_note_vector_path.is_dir():
        note_manifest = (
            staging_zotero_note_vector_path
            / note_vector_index.MANIFEST_NAME
        )
        if not note_manifest.is_file():
            missing_components.append("staging_zotero_note_manifest")
        else:
            try:
                impact = (
                    note_vector_index
                    .inspect_zotero_note_vector_document_impact(
                        document_id,
                        index_dir=staging_zotero_note_vector_path,
                    )
                )
                if int(
                    impact.get("document_entry_count") or 0
                ) != int(expected_native_note_vector_count):
                    missing_components.append(
                        "staging_zotero_note_vectors"
                    )
            except Exception:
                missing_components.append(
                    "staging_zotero_note_vectors_unreadable"
                )
    elif staging_zotero_note_vector_path.exists():
        missing_components.append("staging_zotero_note_vectors")

    if missing_components:
        raise DirectionBSelectedBookImportError(
            code="zotero_direction_b_staging_validation_failed",
            message="Direction-B staging validation failed.",
            status_code=500,
            details={
                "error_stage": "staging_validation",
                "writes_performed": True,
                "rollback_attempted": False,
                "rollback_completed": False,
                "missing_components": sorted(set(missing_components)),
            },
        )


def _backup_derived_artifacts(
    *,
    rollback_root: Path,
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
    zotero_note_vector_path: Path,
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
        "zotero_note_vectors":
            zotero_note_vector_path.is_dir(),
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
        "zotero_note_vector_fingerprint": (
            _tree_fingerprint(
                zotero_note_vector_path
            )
            if zotero_note_vector_path.is_dir()
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

    if state["zotero_note_vectors"]:
        target = (
            rollback_root
            / "zotero_user_notes_v1"
        )
        shutil.copytree(
            zotero_note_vector_path,
            target,
        )
        if (
            _tree_fingerprint(target)
            != state[
                "zotero_note_vector_fingerprint"
            ]
        ):
            raise RuntimeError(
                "Zotero note-vector rollback backup "
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
    staging_zotero_note_vector_path: Path,
    zotero_note_vector_path: Path,
) -> None:
    # Each os.replace / shutil operation is wrapped in a try/except that
    # raises DirectionBDerivedPublishError so the caller can record the
    # exact substage without exposing a new public error_code.

    # -- FTS index -----------------------------------------------------------
    try:
        os.replace(staging_fts_index, fts_index_path)
    except Exception as exc:
        raise DirectionBDerivedPublishError(
            publish_substage="fts_index_replace",
            original_exception=exc,
        ) from exc

    # -- FTS manifest --------------------------------------------------------
    try:
        os.replace(staging_fts_manifest, fts_manifest_path)
    except Exception as exc:
        raise DirectionBDerivedPublishError(
            publish_substage="fts_manifest_replace",
            original_exception=exc,
        ) from exc

    # -- vector store (retire + publish) -------------------------------------
    if staging_vector_store.is_dir():
        vector_store_path.parent.mkdir(parents=True, exist_ok=True)
        retired_store = staging_vector_store.parent / ".retired-lancedb"
        if vector_store_path.exists():
            try:
                os.replace(vector_store_path, retired_store)
            except Exception as exc:
                raise DirectionBDerivedPublishError(
                    publish_substage="vector_store_retire",
                    original_exception=exc,
                ) from exc
        try:
            os.replace(staging_vector_store, vector_store_path)
        except Exception as exc:
            if retired_store.exists() and not vector_store_path.exists():
                try:
                    os.replace(retired_store, vector_store_path)
                except Exception:
                    pass
            raise DirectionBDerivedPublishError(
                publish_substage="vector_store_publish",
                original_exception=exc,
            ) from exc
        _best_effort_remove_generated_tree(retired_store)

    # -- vector manifest -----------------------------------------------------
    if staging_vector_manifest.is_file():
        vector_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staging_vector_manifest, vector_manifest_path)
        except Exception as exc:
            raise DirectionBDerivedPublishError(
                publish_substage="vector_manifest_replace",
                original_exception=exc,
            ) from exc

    # -- native note-vector (retire + publish + cache invalidate) ------------
    if staging_zotero_note_vector_path.is_dir():
        retired_note_vectors = (
            staging_zotero_note_vector_path.parent
            / ".retired-zotero-user-notes"
        )
        if zotero_note_vector_path.exists():
            try:
                os.replace(
                    zotero_note_vector_path,
                    retired_note_vectors,
                )
            except Exception as exc:
                raise DirectionBDerivedPublishError(
                    publish_substage="native_note_vector_retire",
                    original_exception=exc,
                ) from exc
        try:
            os.replace(
                staging_zotero_note_vector_path,
                zotero_note_vector_path,
            )
        except Exception as exc:
            if (
                retired_note_vectors.exists()
                and not zotero_note_vector_path.exists()
            ):
                try:
                    os.replace(
                        retired_note_vectors,
                        zotero_note_vector_path,
                    )
                except Exception:
                    pass
            raise DirectionBDerivedPublishError(
                publish_substage="native_note_vector_publish",
                original_exception=exc,
            ) from exc
        _best_effort_remove_generated_tree(
            retired_note_vectors
        )
        try:
            with note_vector_index._INDEX_CACHE_LOCK:
                note_vector_index._INDEX_CACHE.pop(
                    str(
                        zotero_note_vector_path.resolve()
                    ),
                    None,
                )
        except Exception as exc:
            raise DirectionBDerivedPublishError(
                publish_substage="native_note_vector_cache_invalidate",
                original_exception=exc,
            ) from exc


def _restore_derived_artifacts(
    *,
    rollback_root: Path,
    state: dict[str, Any],
    fts_index_path: Path,
    fts_manifest_path: Path,
    vector_store_path: Path,
    vector_manifest_path: Path,
    zotero_note_vector_path: Path,
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

    if zotero_note_vector_path.exists():
        _remove_generated_tree(
            zotero_note_vector_path
        )
    if state["zotero_note_vectors"]:
        shutil.copytree(
            rollback_root / "zotero_user_notes_v1",
            zotero_note_vector_path,
        )
    with note_vector_index._INDEX_CACHE_LOCK:
        note_vector_index._INDEX_CACHE.pop(
            str(zotero_note_vector_path.resolve()),
            None,
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
        state["zotero_note_vectors"]
        and _tree_fingerprint(
            zotero_note_vector_path
        )
        != state["zotero_note_vector_fingerprint"]
    ):
        raise RuntimeError(
            "Zotero note-vector rollback verification failed"
        )

    if (
        not state["vector_store"]
        and vector_store_path.exists()
    ):
        raise RuntimeError(
            "Vector rollback left an unexpected store"
        )
    if (
        not state["zotero_note_vectors"]
        and zotero_note_vector_path.exists()
    ):
        raise RuntimeError(
            "Zotero note-vector rollback left an unexpected store"
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

def _best_effort_remove_generated_tree(
    path: Path,
) -> bool:
    try:
        _remove_generated_tree(path)
    except Exception:
        # Staging/rollback cleanup is secondary to
        # the already-decided import/rollback result.
        # Leave the artifact for later inspection.
        return False

    return True


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
    import_audit: dict[str, Any] | None = None,
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
    document_type = (
        zotero_selected_book_preview_service
        .document_type_for_item_type(
            str(parent.get("item_type") or "")
        )
    )

    strategy = str(
        preview.get("extractor_strategy")
        or pdf_extraction_strategy_service.NATIVE_TEXT
    )
    if not bool(preview.get("extraction_ready", True)):
        raise pdf_extraction_strategy_service.PdfExtractionStrategyError(
            "pdf_extraction_strategy_unavailable"
        )
    if strategy == pdf_extraction_strategy_service.NATIVE_TEXT:
        # The source-revision fingerprint binds this native strategy to the
        # confirmation token. Low-quality PDFs cannot silently fall back here.
        prepared = (
            book_import_service
            .prepare_book_import(
                pdf_path,
                title=title,
                backend=PYMUPDF_BACKEND,
            )
        )
    elif strategy == pdf_extraction_strategy_service.HIGH_QUALITY_MARKDOWN:
        markdown_path_text = str(
            preview.get("converted_markdown_path") or ""
        ).strip()
        if markdown_path_text:
            markdown_path = Path(
                markdown_path_text
            ).resolve(strict=False)
            if not markdown_path.is_file():
                raise pdf_extraction_strategy_service.PdfExtractionStrategyError(
                    "verified_converted_markdown_missing"
                )
            markdown_text = markdown_path.read_text(encoding="utf-8")
        elif (
            preview.get("converted_markdown_status")
            == "conversion_required"
        ):
            converted = parse_pdf_to_markdown(
                pdf_path,
                backend=MARKER_SURYA_PAGE_BLOCKS_BACKEND,
            )
            markdown_text = (
                "<!-- SOURCE_PDF_SHA256: "
                f"{attachment.get('pdf_sha256') or ''} -->\n\n"
                + converted.markdown_text
            )
        else:
            raise pdf_extraction_strategy_service.PdfExtractionStrategyError(
                "verified_converted_markdown_missing"
            )
        pdf_extraction_strategy_service.validate_markdown_for_import(
            markdown_text,
            expected_pdf_sha256=str(
                attachment.get("pdf_sha256") or ""
            ),
        )
        prepared = (
            book_import_service
            .prepare_book_import_from_markdown(
                pdf_path,
                markdown_text,
                title=title,
            )
        )
    else:
        raise pdf_extraction_strategy_service.PdfExtractionStrategyError(
            "unsupported_pdf_extraction_strategy"
        )

    apply_result = (
        book_import_service
        .apply_prepared_book_import(
            prepared,
            db_path=db_path,
            backup=False,
            document_type=document_type,
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
        "import_history": _safe_import_history(import_audit),
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
        "document_type": str(
            apply_result.get("document_type")
            or document_type
        ),
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
            if strategy == pdf_extraction_strategy_service.NATIVE_TEXT
            else MARKER_SURYA_PAGE_BLOCKS_BACKEND
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


def _safe_import_history(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: str(value.get(key) or "")
        for key in (
            "confirmation_token_fingerprint",
            "previewed_at",
            "confirmed_at",
            "transaction_fingerprint",
            "source_revision_fingerprint",
        )
        if value.get(key)
    }
    events = [
        str(item)
        for item in value.get("lifecycle_events") or []
        if str(item)
        in {
            "previewed",
            "confirmed",
            "transaction_started",
        }
    ]
    if events:
        result["lifecycle_events"] = events
    return result or None


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
    expected_native_note_vector_count: int,
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

    note_vector_impact = (
        note_vector_index
        .inspect_zotero_note_vector_document_impact(
            document_id,
            index_dir=(
                Path(runtime.data_dir)
                / "vector_store"
                / "zotero_user_notes_v1"
            ),
        )
    )
    if int(
        note_vector_impact.get(
            "document_entry_count"
        )
        or 0
    ) != int(expected_native_note_vector_count):
        raise DirectionBSelectedBookImportError(
            code=(
                "zotero_direction_b_"
                "production_final_verify_failed"
            ),
            message=(
                "Production Zotero note vectors could not "
                "be verified."
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
