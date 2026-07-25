from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.core.paths import DEFAULT_DB_PATH
from app.services import (
    book_import_service,
    commit_book_service,
    zotero_direction_b_commit_service,
    zotero_selected_book_preview_service,
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


def commit_selected_book_import_to_temp_db(
    *,
    preview_token: str,
    db_path: str | Path,
    body_importer: Callable[..., dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    path = Path(
        db_path
    ).resolve(strict=False)

    if path == Path(
        DEFAULT_DB_PATH
    ).resolve(strict=False):
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

    rollback_path = (
        _create_rollback_copy(
            path
        )
    )

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
            "body_importer": (
                "core_book_import"
                if body_importer is None
                else "runtime_override"
            ),
            "persistence_scope": "tempdb",
            "production_data_modified": False,
            "production_schema_migrated": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": False,
            "fts_write_performed": False,
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
                "Direction-B selected-book "
                "import failed."
            ),
            status_code=500,
        ) from exc

    finally:
        rollback_path.unlink(
            missing_ok=True
        )


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
