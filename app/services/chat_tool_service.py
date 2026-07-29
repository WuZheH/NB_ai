from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.database import connect_readonly_sqlite
from app.core.paths import DATA_DIR, DATA_PROJECT_ROOT, DEFAULT_DB_PATH
from app.schemas.library_deletion import DeletionOptions
from app.services import (
    chat_import_catalog_service,
    document_integrity_report_service,
    zotero_library_service,
    chat_pdf_production_import_service,
    commit_book_service,
    commit_paper_service,
    import_preview_service,
    pdf_import_classifier_service,
    zotero_direction_b_import_service,
    zotero_selected_book_preview_service,
)
from app.services.library import document_deletion_service


DELETE_CONFIRMATION_TTL_SECONDS = document_deletion_service.PREVIEW_TTL_SECONDS
IMPORT_CONFIRMATION_TTL_SECONDS = 10 * 60
IMPORT_COMPLETION_REPLAY_TTL_SECONDS = 60 * 60
MAX_IMPORT_BYTES = 200 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,255}$")
_TOKEN_LOCK = threading.RLock()
_DELETE_CONFIRMATIONS: dict[str, "DeleteConfirmation"] = {}
_IMPORT_CONFIRMATIONS: dict[
    str,
    "ImportConfirmation | ZoteroImportConfirmation",
] = {}
_IMPORT_IN_PROGRESS: set[str] = set()
_IMPORT_COMPLETIONS: dict[str, "ImportCompletion"] = {}


class ChatToolError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class DeleteConfirmation:
    document_id: int
    title: str
    preview_token: str
    document_revision: str
    expires_at: float


@dataclass(frozen=True)
class ImportConfirmation:
    source_path: Path
    source_sha256: str
    source_size: int
    source_mtime_ns: int
    title: str
    document_type: str
    object_import_mode: str
    page_count: int
    expires_at: float
    note_sources: tuple[dict[str, Any], ...] = ()
    inbox_root: Path | None = None


@dataclass(frozen=True)
class ZoteroImportConfirmation:
    preview_token: str
    target_db_path: Path
    target_data_dir: Path
    zotero_item_key: str
    zotero_attachment_key: str
    item_type: str
    source_revision_fingerprint: str
    title: str
    document_type: str
    page_count: int
    annotation_count: int
    child_note_count: int
    duplicate_status: str
    expires_at: float


@dataclass(frozen=True)
class ImportCompletion:
    response: dict[str, Any]
    expires_at: float


@dataclass(frozen=True)
class ChatToolRuntime:
    db_path: Path = DEFAULT_DB_PATH
    data_dir: Path = DATA_DIR
    inbox_root: Path | None = None
    deletion_runtime: document_deletion_service.DeletionRuntime | None = None
    classify_pdf: Callable[..., dict[str, Any]] | None = None
    commit_import: Callable[..., dict[str, Any]] | None = None
    zotero_body_importer: Callable[..., dict[str, Any]] | None = None
    commit_zotero_import: Callable[..., dict[str, Any]] | None = None
    integrity_runtime: document_integrity_report_service.IntegrityReportRuntime | None = None

    def resolved_inbox_root(self) -> Path:
        if self.inbox_root is not None:
            return Path(self.inbox_root).resolve(strict=False)
        configured = os.environ.get("SEARCH_IMPORT_INBOX", "").strip()
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                raise ChatToolError(
                    "import_inbox_path_invalid",
                    "SEARCH_IMPORT_INBOX must be an absolute path.",
                    status_code=503,
                )
            return path.resolve(strict=False)
        return DATA_PROJECT_ROOT.with_name("search-import-inbox").resolve(strict=False)

    def resolved_deletion_runtime(self) -> document_deletion_service.DeletionRuntime:
        if self.deletion_runtime is not None:
            return self.deletion_runtime
        return document_deletion_service.DeletionRuntime(
            db_path=self.db_path,
            data_dir=self.data_dir,
        )


def list_library(
    *,
    query: str | None = None,
    document_type: str | None = None,
    status: str = "active",
    limit: int = 20,
    scope: str = "imported",
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or ChatToolRuntime()
    if scope == "catalog":
        return chat_import_catalog_service.list_catalog(inbox_root=actual_runtime.resolved_inbox_root(), query=query, limit=limit)
    if scope == "zotero":
        return zotero_library_service.list_parent_items(
            query=query,
            document_type=document_type,
            limit=limit,
        )
    if scope != "imported":
        raise ChatToolError("library_scope_invalid", "Library scope is invalid.", status_code=422)
    normalized_status = str(status or "active").strip().lower()
    if normalized_status not in {"active", "archived", "all"}:
        raise ChatToolError("library_status_invalid", "Library status filter is invalid.", status_code=422)
    bounded_limit = max(1, min(int(limit), 50))
    clauses: list[str] = []
    parameters: list[Any] = []
    if query and query.strip():
        clauses.append("LOWER(d.title) LIKE ?")
        parameters.append(f"%{query.strip().lower()}%")
    if document_type and document_type.strip():
        clauses.append("d.document_type = ?")
        parameters.append(document_type.strip())
    if normalized_status == "active":
        clauses.append("COALESCE(d.read_status, '') <> 'archived'")
    elif normalized_status == "archived":
        clauses.append("COALESCE(d.read_status, '') = 'archived'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT
            d.id AS document_id,
            d.title,
            d.document_type,
            d.created_at AS imported_at,
            d.pdf_path,
            d.read_status,
            COUNT(c.id) AS chunk_count
        FROM documents AS d
        LEFT JOIN knowledge_chunks AS c ON c.document_id = d.id
        {where}
        GROUP BY d.id
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
    """
    parameters.append(bounded_limit)
    try:
        with connect_readonly_sqlite(
            actual_runtime.db_path,
            resolve_strict=True,
            row_factory=sqlite3.Row,
            query_only=True,
            temp_store="MEMORY",
        ) as connection:
            rows = connection.execute(sql, parameters).fetchall()
    except sqlite3.Error as exc:
        raise ChatToolError(
            "library_query_failed",
            "Search library could not be read.",
            status_code=503,
        ) from exc
    items = [
        {
            "document_id": int(row["document_id"]),
            "title": str(row["title"] or ""),
            "type": str(row["document_type"] or "other"),
            "imported_at": str(row["imported_at"] or ""),
            "chunk_count": int(row["chunk_count"] or 0),
            "has_pdf": _pdf_exists(row["pdf_path"], data_dir=actual_runtime.data_dir),
            "duplicate_status": "not_evaluated",
            "status": "archived" if str(row["read_status"] or "") == "archived" else "active",
            "source": "search_library",
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "scope": "imported",
        "count": len(items),
        "items": items,
        "truncated": len(items) >= bounded_limit,
    }


def integrity_report(
    document_id: int,
    *,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or ChatToolRuntime()
    report_runtime = actual_runtime.integrity_runtime
    if report_runtime is None:
        report_runtime = document_integrity_report_service.IntegrityReportRuntime.production()
    try:
        return document_integrity_report_service.build_integrity_report(
            document_id=document_id,
            runtime=report_runtime,
        )
    except document_integrity_report_service.IntegrityReportError as exc:
        raise ChatToolError(
            exc.error_code,
            str(exc),
            status_code=exc.status_code,
            details=exc.details,
        ) from exc


def delete_preview(
    document_id: int,
    *,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or ChatToolRuntime()
    try:
        preview = document_deletion_service.create_deletion_preview(
            int(document_id),
            deletion_options=DeletionOptions(),
            runtime=actual_runtime.resolved_deletion_runtime(),
        )
    except document_deletion_service.DeletionError as exc:
        raise ChatToolError(exc.error_code, str(exc), status_code=exc.status_code) from exc
    token = secrets.token_urlsafe(32)
    record = DeleteConfirmation(
        document_id=int(preview["document_id"]),
        title=str(preview["title"]),
        preview_token=str(preview["preview_token"]),
        document_revision=str(preview["document_revision"]),
        expires_at=time.monotonic() + DELETE_CONFIRMATION_TTL_SECONDS,
    )
    with _TOKEN_LOCK:
        _purge_expired_tokens()
        _DELETE_CONFIRMATIONS[_token_digest(token)] = record
    blockers = [
        str(item.get("code") or "deletion_blocked")
        for item in (preview.get("deletion_blockers") or [])
        if isinstance(item, dict)
    ]
    return {
        "status": "ok",
        "document_id": record.document_id,
        "title": record.title,
        "safe_to_delete": bool(preview.get("whether_safe_to_delete")),
        "pdf_preserved": True,
        "notes_preserved": True,
        "search_review_artifact_count": int(
            preview.get("search_review_artifact_count") or 0
        ),
        "warnings": [
            str(value)
            for value in preview.get("warnings") or []
        ][:8],
        "blockers": blockers,
        "confirmation_token": token,
        "confirmation_expires_in_seconds": DELETE_CONFIRMATION_TTL_SECONDS,
    }


def delete_document(
    *,
    confirmation_token: str,
    confirmed: bool,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    if confirmed is not True:
        raise ChatToolError(
            "chat_delete_confirmation_required",
            "Explicit user confirmation is required before deleting a document.",
            status_code=422,
        )
    actual_runtime = runtime or ChatToolRuntime()
    record = _consume_delete_confirmation(confirmation_token)
    try:
        result = document_deletion_service.delete_document(
            document_id=record.document_id,
            preview_token=record.preview_token,
            expected_document_revision=record.document_revision,
            confirmation_text="删除",
            deletion_options=DeletionOptions(),
            runtime=actual_runtime.resolved_deletion_runtime(),
        )
    except document_deletion_service.DeletionError as exc:
        raise ChatToolError(exc.error_code, str(exc), status_code=exc.status_code) from exc
    return {
        "status": str(result.get("status") or "unknown"),
        "document_id": record.document_id,
        "title": record.title,
        "recovery_created": bool(result.get("recovery_package")),
        "cleanup_complete": result.get("status") == "completed",
        "error_code": result.get("error_code"),
    }


def import_preview(
    inbox_filename: str | None = None,
    *,
    source_type: str = "local_pdf",
    zotero_item_key: str | None = None,
    zotero_attachment_key: str | None = None,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or ChatToolRuntime()
    if source_type == "zotero_selected_book":
        return _import_zotero_selected_book_preview(
            zotero_item_key=zotero_item_key,
            zotero_attachment_key=zotero_attachment_key,
            runtime=actual_runtime,
        )
    if source_type != "local_pdf":
        raise ChatToolError(
            "import_source_type_invalid",
            "Import preview source type is invalid.",
            status_code=422,
        )
    inbox_root = actual_runtime.resolved_inbox_root()
    source = _resolve_inbox_pdf(inbox_root, inbox_filename)
    source_size = source.stat().st_size
    if source_size > MAX_IMPORT_BYTES:
        raise ChatToolError("import_pdf_too_large", "PDF exceeds the 200 MB import limit.", status_code=413)
    if not _looks_like_pdf(source):
        raise ChatToolError("import_pdf_invalid", "Inbox file is not a valid PDF.", status_code=422)
    digest = _sha256_file(source)
    classifier = actual_runtime.classify_pdf or pdf_import_classifier_service.classify_pdf_import
    try:
        classification = classifier(
            source,
            allowed_root=actual_runtime.resolved_inbox_root(),
        )
    except FileNotFoundError as exc:
        raise ChatToolError("import_pdf_not_found", "Inbox PDF was not found.", status_code=404) from exc
    except Exception as exc:
        raise ChatToolError(
            "import_preview_failed",
            "Search could not inspect the PDF.",
            status_code=503,
        ) from exc
    signals = classification.get("signals") if isinstance(classification.get("signals"), dict) else {}
    page_count = int(signals.get("page_count") or classification.get("page_count") or 0)
    duplicate = bool(classification.get("duplicate"))
    token: str | None = None
    if not duplicate:
        token = secrets.token_urlsafe(32)
        stat = source.stat()
        record = ImportConfirmation(
            source_path=source,
            source_sha256=digest,
            source_size=source_size,
            source_mtime_ns=stat.st_mtime_ns,
            title=str(classification.get("title") or source.stem),
            document_type=str(classification.get("document_type") or "paper"),
            object_import_mode=str(classification.get("object_import_mode") or "full_document"),
            page_count=page_count,
            note_sources=tuple(chat_import_catalog_service.note_sources(pdf=source, inbox_root=inbox_root)),
            inbox_root=inbox_root,
            expires_at=time.monotonic() + IMPORT_CONFIRMATION_TTL_SECONDS,
        )
        with _TOKEN_LOCK:
            _purge_expired_tokens()
            _IMPORT_CONFIRMATIONS[_token_digest(token)] = record
    return {
        "status": "ok",
        "source_type": "local_pdf",
        "filename": source.name,
        "title": str(classification.get("title") or source.stem),
        "pdf_sha256": digest,
        "duplicate_status": "duplicate" if duplicate else "not_detected",
        "existing_document_id": classification.get("existing_document_id") if duplicate else None,
        "estimated_pages": page_count,
        "estimated_chunks": None,
        "extractor_strategy": None,
        "text_quality_score": None,
        "quality_reasons": [],
        "converted_markdown_status": None,
        "converted_markdown_path": None,
        "extraction_ready": None,
        "document_type": str(classification.get("document_type") or "paper"),
        "warnings": (
            [str(value) for value in classification.get("reasons") or []][:7]
            + ["chunk_count_not_precomputed_by_preview"]
        ),
        "confirmation_token": token,
        "confirmation_expires_in_seconds": IMPORT_CONFIRMATION_TTL_SECONDS if token else None,
        "attachment_choices": [],
        "annotation_count": None,
        "child_note_count": None,
        "note_count": len(record.note_sources) if token else len(chat_import_catalog_service.note_sources(pdf=source, inbox_root=inbox_root)),
        "note_files": [item["relative_path"] for item in (record.note_sources if token else chat_import_catalog_service.note_sources(pdf=source, inbox_root=inbox_root))],
    }


def _import_zotero_selected_book_preview(
    *,
    zotero_item_key: str | None,
    zotero_attachment_key: str | None,
    runtime: ChatToolRuntime,
) -> dict[str, Any]:
    item_key = str(zotero_item_key or "").strip()
    attachment_key = str(zotero_attachment_key or "").strip() or None
    if not item_key:
        raise ChatToolError(
            "zotero_item_key_required",
            "A Zotero parent item key is required.",
            status_code=422,
        )

    _zotero_runtime_is_production(runtime)

    try:
        preview = zotero_selected_book_preview_service.build_selected_book_preview(
            zotero_item_key=item_key,
            zotero_attachment_key=attachment_key,
            db_path=runtime.db_path,
            issue_token=True,
        )
    except zotero_selected_book_preview_service.ZoteroSelectedBookPreviewError as exc:
        raise ChatToolError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details=_safe_zotero_error_details(exc.details),
        ) from exc

    item = (
        preview.get("zotero_item")
        if isinstance(preview.get("zotero_item"), dict)
        else {}
    )
    item_type = str(item.get("item_type") or "").strip()
    if item_type != "book":
        raise ChatToolError(
            "zotero_import_preview_contract_invalid",
            "Selected-book preview did not return a validated Zotero book.",
            status_code=500,
        )

    choices = (
        preview.get("attachment_choices")
        if isinstance(preview.get("attachment_choices"), list)
        else []
    )
    safe_choices = [
        {
            key: choice.get(key)
            for key in (
                "zotero_attachment_key",
                "file_name",
                "path_exists",
                "path_status",
                "content_type",
                "date_modified",
                "version",
            )
        }
        for choice in choices
        if isinstance(choice, dict)
    ]

    base = {
        "status": "ok",
        "source_type": "zotero_selected_book",
        "filename": None,
        "title": str(item.get("title") or ""),
        "item_type": item_type,
        "pdf_sha256": None,
        "duplicate_status": "not_evaluated",
        "existing_document_id": None,
        "estimated_pages": None,
        "estimated_chunks": None,
        "document_type": "book",
        "warnings": (
            [
                str(value)
                for value in preview.get("warnings") or []
            ][:7]
            + ["chunk_count_not_precomputed_by_preview"]
        ),
        "confirmation_token": None,
        "confirmation_expires_in_seconds": None,
        "attachment_choices": safe_choices,
        "annotation_count": preview.get("annotation_count"),
        "child_note_count": preview.get("child_note_count"),
    }

    if preview.get("status") == "attachment_choice_required":
        return base

    if preview.get("status") != "ready":
        raise ChatToolError(
            "zotero_import_preview_not_ready",
            "The Zotero selected-book preview is not ready.",
            status_code=409,
        )

    selected = (
        preview.get("selected_attachment")
        if isinstance(preview.get("selected_attachment"), dict)
        else {}
    )
    duplicate = (
        preview.get("duplicate_check")
        if isinstance(preview.get("duplicate_check"), dict)
        else {}
    )
    existing = duplicate.get("existing_documents")
    unique_document_id = None
    if (
        isinstance(existing, list)
        and len(existing) == 1
        and isinstance(existing[0], dict)
    ):
        candidate = existing[0].get(
            "document_id",
            existing[0].get("id"),
        )
        if isinstance(candidate, int):
            unique_document_id = candidate

    base.update(
        {
            "filename": str(selected.get("file_name") or "") or None,
            "pdf_sha256": str(selected.get("pdf_sha256") or "") or None,
            "estimated_pages": selected.get("page_count"),
            "estimated_chunks": preview.get("estimated_chunks"),
            "extractor_strategy": preview.get("extractor_strategy"),
            "text_quality_score": preview.get("text_quality_score"),
            "quality_reasons": list(preview.get("quality_reasons") or []),
            "converted_markdown_status": preview.get(
                "converted_markdown_status"
            ),
            "converted_markdown_path": _safe_project_relative_path(
                preview.get("converted_markdown_path")
            ),
            "extraction_ready": bool(
                preview.get("extraction_ready", True)
            ),
        }
    )

    if bool(duplicate.get("duplicate_found")):
        base["duplicate_status"] = "duplicate"
        base["existing_document_id"] = unique_document_id
        return base

    if not bool(preview.get("extraction_ready", True)):
        return base

    preview_token = str(preview.get("preview_token") or "")
    if not preview_token:
        raise ChatToolError(
            "zotero_import_preview_token_missing",
            "The Zotero selected-book preview did not issue a confirmation token.",
            status_code=503,
        )

    confirmation = register_zotero_selected_book_import_preview(
        preview_token=preview_token,
        runtime=runtime,
    )
    base["duplicate_status"] = str(
        confirmation.get("duplicate_status") or "not_detected"
    )
    base["confirmation_token"] = confirmation.get("confirmation_token")
    base["confirmation_expires_in_seconds"] = confirmation.get(
        "confirmation_expires_in_seconds"
    )
    return base


def _safe_zotero_error_details(value: Any) -> Any:
    unsafe = {
        "resolved_pdf_path",
        "snapshot_path",
        "db_path",
        "zotero_data_dir",
        "zotero_storage_root",
    }
    if isinstance(value, dict):
        return {
            str(key): _safe_zotero_error_details(item)
            for key, item in value.items()
            if str(key) not in unsafe
        }
    if isinstance(value, list):
        return [_safe_zotero_error_details(item) for item in value]
    return value


def _safe_project_relative_path(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        path = Path(cleaned).resolve(strict=False)
        return path.relative_to(Path(DATA_PROJECT_ROOT).resolve()).as_posix()
    except (OSError, ValueError):
        return None



def _zotero_runtime_is_production(
    runtime: ChatToolRuntime,
) -> bool:
    runtime_db = Path(runtime.db_path).resolve(strict=False)
    runtime_data = Path(runtime.data_dir).resolve(strict=False)
    canonical_db = Path(DEFAULT_DB_PATH).resolve(strict=False)
    canonical_data = Path(DATA_DIR).resolve(strict=False)

    db_is_production = runtime_db == canonical_db
    data_is_production = runtime_data == canonical_data

    if db_is_production != data_is_production:
        raise ChatToolError(
            "zotero_import_runtime_target_mismatch",
            "The Zotero import runtime mixes production and non-production targets.",
            status_code=409,
        )

    return db_is_production


def register_zotero_selected_book_import_preview(
    *,
    preview_token: str,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or ChatToolRuntime()

    target_db_path = Path(
        actual_runtime.db_path
    ).resolve(strict=False)
    target_data_dir = Path(
        actual_runtime.data_dir
    ).resolve(strict=False)

    _zotero_runtime_is_production(actual_runtime)

    if not target_db_path.is_file():
        raise ChatToolError(
            "zotero_direction_b_target_db_missing",
            "Direction-B target database does not exist.",
            status_code=503,
        )

    try:
        preview = (
            zotero_selected_book_preview_service
            .resolve_selected_book_preview_token(
                preview_token,
                expected_db_path=target_db_path,
            )
        )
    except (
        zotero_selected_book_preview_service
        .ZoteroSelectedBookPreviewError
    ) as exc:
        raise ChatToolError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details=_safe_zotero_error_details(exc.details),
        ) from exc

    if preview.get("status") != "ready":
        raise ChatToolError(
            "zotero_import_preview_not_ready",
            "The Zotero selected-book preview is not ready.",
            status_code=409,
        )

    item = (
        preview.get("zotero_item")
        if isinstance(preview.get("zotero_item"), dict)
        else {}
    )
    selected = (
        preview.get("selected_attachment")
        if isinstance(preview.get("selected_attachment"), dict)
        else {}
    )
    source_revision = (
        preview.get("source_revision")
        if isinstance(preview.get("source_revision"), dict)
        else {}
    )

    item_type = str(item.get("item_type") or "").strip()
    item_key = str(item.get("zotero_item_key") or "").strip()
    attachment_key = str(
        selected.get("zotero_attachment_key") or ""
    ).strip()
    source_revision_fingerprint = str(
        source_revision.get("fingerprint") or ""
    ).strip()

    if (
        item_type != "book"
        or not item_key
        or not attachment_key
        or not source_revision_fingerprint
    ):
        raise ChatToolError(
            "zotero_import_preview_contract_invalid",
            "Selected-book preview metadata is incomplete.",
            status_code=500,
        )

    duplicate = (
        preview.get("duplicate_check")
        if isinstance(preview.get("duplicate_check"), dict)
        else {}
    )
    if bool(duplicate.get("duplicate_found")):
        raise ChatToolError(
            "zotero_import_duplicate_requires_review",
            (
                "The selected Zotero book already matches existing "
                "library data. Search will not create another book body."
            ),
            status_code=409,
        )

    token = secrets.token_urlsafe(32)

    record = ZoteroImportConfirmation(
        preview_token=str(preview_token),
        target_db_path=target_db_path,
        target_data_dir=target_data_dir,
        zotero_item_key=item_key,
        zotero_attachment_key=attachment_key,
        item_type=item_type,
        source_revision_fingerprint=source_revision_fingerprint,
        title=str(item.get("title") or ""),
        document_type="book",
        page_count=int(selected.get("page_count") or 0),
        annotation_count=int(preview.get("annotation_count") or 0),
        child_note_count=int(preview.get("child_note_count") or 0),
        duplicate_status="not_detected",
        expires_at=(
            time.monotonic()
            + IMPORT_CONFIRMATION_TTL_SECONDS
        ),
    )

    with _TOKEN_LOCK:
        _purge_expired_tokens()
        _IMPORT_CONFIRMATIONS[
            _token_digest(token)
        ] = record

    return {
        "status": "ok",
        "source_type": "zotero_selected_book",
        "title": record.title,
        "item_type": record.item_type,
        "document_type": record.document_type,
        "estimated_pages": record.page_count,
        "annotation_count": record.annotation_count,
        "child_note_count": record.child_note_count,
        "duplicate_status": record.duplicate_status,
        "confirmation_token": token,
        "confirmation_expires_in_seconds": (
            IMPORT_CONFIRMATION_TTL_SECONDS
        ),
    }


def import_document(
    *,
    confirmation_token: str,
    confirmed: bool,
    runtime: ChatToolRuntime | None = None,
) -> dict[str, Any]:
    if confirmed is not True:
        raise ChatToolError(
            "chat_import_confirmation_required",
            (
                "Explicit user confirmation "
                "is required before importing "
                "a document."
            ),
            status_code=422,
        )

    actual_runtime = runtime or ChatToolRuntime()

    token_digest, record, replay = _begin_import_confirmation(
        confirmation_token
    )

    if replay is not None:
        return replay

    assert record is not None

    try:
        if isinstance(record, ZoteroImportConfirmation):
            runtime_db = Path(
                actual_runtime.db_path
            ).resolve(strict=False)
            runtime_data = Path(
                actual_runtime.data_dir
            ).resolve(strict=False)

            if (
                runtime_db != record.target_db_path
                or runtime_data != record.target_data_dir
            ):
                raise ChatToolError(
                    "zotero_import_target_changed",
                    (
                        "The Zotero import target "
                        "changed after preview."
                    ),
                    status_code=409,
                )

            importer = (
                actual_runtime.commit_zotero_import
                or _commit_confirmed_zotero_import
            )
        else:
            _validate_import_source_unchanged(record)

            importer = (
                actual_runtime.commit_import
                or _commit_confirmed_import
            )

        result = importer(
            record=record,
            runtime=actual_runtime,
        )

        duplicate_status = (
            record.duplicate_status
            if isinstance(record, ZoteroImportConfirmation)
            else "not_detected"
        )

        response = {
            "status": str(
                result.get("status")
                or "unknown"
            ),
            "document_id": result.get(
                "document_id"
            ),
            "title": str(
                result.get("title")
                or record.title
            ),
            "document_type": (
                record.document_type
            ),
            "chunk_count": int(
                result.get("chunk_count")
                or result.get("inserted_chunks")
                or 0
            ),
            "duplicate_status": (
                duplicate_status
            ),
            "error_code": result.get(
                "error_code"
            ),
            "already_completed": False,
            "replayed_receipt": False,
        }

    except ChatToolError:
        _fail_import_confirmation(
            token_digest
        )
        raise

    except Exception as exc:
        _fail_import_confirmation(
            token_digest
        )
        raise ChatToolError(
            "import_document_failed",
            (
                "Search could not import "
                "the confirmed document."
            ),
            status_code=500,
        ) from exc

    _complete_import_confirmation(
        token_digest,
        response,
    )

    return response

def _commit_confirmed_zotero_import(
    *,
    record: ZoteroImportConfirmation,
    runtime: ChatToolRuntime,
) -> dict[str, Any]:
    try:
        production = _zotero_runtime_is_production(runtime)

        if production:
            return (
                zotero_direction_b_import_service
                .commit_selected_book_import_to_production(
                    preview_token=record.preview_token,
                    body_importer=runtime.zotero_body_importer,
                )
            )

        return (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_temp_db(
                preview_token=record.preview_token,
                db_path=runtime.db_path,
                data_dir=runtime.data_dir,
                body_importer=runtime.zotero_body_importer,
            )
        )
    except (
        zotero_direction_b_import_service
        .DirectionBSelectedBookImportError
    ) as exc:
        raise ChatToolError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details=exc.details,
        ) from exc


def _commit_confirmed_import(
    *,
    record: ImportConfirmation,
    runtime: ChatToolRuntime,
) -> dict[str, Any]:
    if runtime.commit_import is not None:
        return runtime.commit_import(record=record, runtime=runtime)
    production_runtime = _resolve_chat_pdf_import_runtime(runtime)
    destination_dir = Path(runtime.data_dir) / "pdfs" / "chat_imports"
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _managed_pdf_name(record)
    destination = destination_dir / safe_name
    created_copy = not destination.exists()
    if destination.exists() and _sha256_file(destination) != record.source_sha256:
        raise ChatToolError("import_managed_pdf_collision", "Managed PDF name collision.", status_code=409)
    try:
        if created_copy:
            temporary = destination.with_suffix(".pdf.tmp")
            shutil.copyfile(record.source_path, temporary)
            if _sha256_file(temporary) != record.source_sha256:
                raise ChatToolError("import_pdf_copy_hash_mismatch", "PDF copy verification failed.")
            os.replace(temporary, destination)
        preview = import_preview_service.create_import_preview(
            {
                "source_type": "local_pdf",
                "pdf_path": str(destination),
                "title_hint": record.title,
            }
        )
        import_job_id = str(preview["import_job_id"])
        return chat_pdf_production_import_service.import_document_to_production(
            import_job_id=import_job_id,
            document_type=record.document_type,
            note_files=[record.inbox_root / Path(item["relative_path"]) for item in record.note_sources] if record.inbox_root else [],
            inbox_root=record.inbox_root,
            allow_production=True,
            runtime=production_runtime,
        )
    except Exception:
        if created_copy and destination.is_file():
            destination.unlink()
        raise


def _resolve_chat_pdf_import_runtime(
    runtime: ChatToolRuntime,
) -> chat_pdf_production_import_service.ChatPdfImportRuntime:
    if (Path(runtime.db_path).resolve(strict=False) != Path(DEFAULT_DB_PATH).resolve(strict=False)
            or Path(runtime.data_dir).resolve(strict=False) != Path(DATA_DIR).resolve(strict=False)):
        raise ChatToolError("chat_import_runtime_not_configured", "Production import runtime is not configured.", status_code=503)
    resolved = chat_pdf_production_import_service.ChatPdfImportRuntime.production()
    if not chat_pdf_production_import_service._is_production_runtime(resolved):
        raise ChatToolError("chat_import_runtime_not_configured", "Production import runtime is not configured.", status_code=503)
    return resolved


def _resolve_inbox_pdf(inbox_root: Path, filename: str | None) -> Path:
    if not inbox_root.is_dir():
        raise ChatToolError(
            "import_inbox_unavailable",
            "Search Import Inbox is unavailable.",
            status_code=503,
        )
    if filename is not None:
        cleaned = filename.strip()
        path_value = Path(cleaned)
        if path_value.is_absolute() or ".." in path_value.parts or not cleaned or any(part in {"", "."} for part in path_value.parts):
            raise ChatToolError("import_inbox_filename_invalid", "Inbox filename is invalid.", status_code=422)
        candidate = (inbox_root / cleaned).resolve(strict=False)
        try:
            candidate.relative_to(inbox_root)
        except ValueError:
            raise ChatToolError("import_inbox_filename_invalid", "Inbox filename is invalid.", status_code=422)
        if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
            raise ChatToolError("import_inbox_pdf_not_found", "Inbox PDF was not found.", status_code=404)
        return candidate
    candidates = sorted(
        (
            path.resolve(strict=False)
            for path in inbox_root.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name.casefold()),
        reverse=True,
    )
    if not candidates:
        raise ChatToolError("import_inbox_empty", "Search Import Inbox contains no PDF.", status_code=404)
    if len(candidates) > 1 and candidates[0].stat().st_mtime_ns == candidates[1].stat().st_mtime_ns:
        raise ChatToolError(
            "import_inbox_selection_required",
            "Multiple equally recent PDFs require an explicit filename.",
            status_code=409,
            details={"candidates": [path.name for path in candidates[:10]]},
        )
    return candidates[0]


def _consume_delete_confirmation(token: str) -> DeleteConfirmation:
    digest = _token_digest(token)
    with _TOKEN_LOCK:
        _purge_expired_tokens()
        record = _DELETE_CONFIRMATIONS.pop(digest, None)
    if record is None:
        raise ChatToolError(
            "chat_delete_confirmation_invalid_or_expired",
            "Delete confirmation is invalid or expired.",
            status_code=409,
        )
    return record


def _begin_import_confirmation(
    token: str,
) -> tuple[
    str,
    ImportConfirmation | ZoteroImportConfirmation | None,
    dict[str, Any] | None,
]:
    digest = _token_digest(token)

    with _TOKEN_LOCK:
        _purge_expired_tokens()

        completion = _IMPORT_COMPLETIONS.get(
            digest
        )

        if completion is not None:
            replay = dict(
                completion.response
            )
            replay["already_completed"] = True
            replay["replayed_receipt"] = True

            return digest, None, replay

        if digest in _IMPORT_IN_PROGRESS:
            raise ChatToolError(
                "chat_import_operation_in_progress",
                (
                    "The confirmed import "
                    "is already in progress."
                ),
                status_code=409,
            )

        record = _IMPORT_CONFIRMATIONS.get(
            digest
        )

        if record is None:
            raise ChatToolError(
                (
                    "chat_import_confirmation_"
                    "invalid_or_expired"
                ),
                (
                    "Import confirmation "
                    "is invalid or expired."
                ),
                status_code=409,
            )

        _IMPORT_IN_PROGRESS.add(
            digest
        )

        return digest, record, None


def _complete_import_confirmation(
    digest: str,
    response: dict[str, Any],
) -> None:
    with _TOKEN_LOCK:
        _IMPORT_CONFIRMATIONS.pop(
            digest,
            None,
        )
        _IMPORT_IN_PROGRESS.discard(
            digest
        )
        _IMPORT_COMPLETIONS[digest] = (
            ImportCompletion(
                response=dict(response),
                expires_at=(
                    time.monotonic()
                    + IMPORT_COMPLETION_REPLAY_TTL_SECONDS
                ),
            )
        )


def _fail_import_confirmation(
    digest: str,
) -> None:
    with _TOKEN_LOCK:
        _IMPORT_CONFIRMATIONS.pop(
            digest,
            None,
        )
        _IMPORT_IN_PROGRESS.discard(
            digest
        )

def _purge_expired_tokens() -> None:
    now = time.monotonic()

    for store in (
        _DELETE_CONFIRMATIONS,
        _IMPORT_CONFIRMATIONS,
        _IMPORT_COMPLETIONS,
    ):
        expired = [
            key
            for key, record in store.items()
            if record.expires_at <= now
        ]

        for key in expired:
            store.pop(
                key,
                None,
            )

def _validate_import_source_unchanged(record: ImportConfirmation) -> None:
    try:
        stat = record.source_path.stat()
    except FileNotFoundError as exc:
        raise ChatToolError("import_source_changed", "Inbox PDF changed after preview.", status_code=409) from exc
    if (
        stat.st_size != record.source_size
        or stat.st_mtime_ns != record.source_mtime_ns
        or _sha256_file(record.source_path) != record.source_sha256
    ):
        raise ChatToolError("import_source_changed", "Inbox PDF changed after preview.", status_code=409)
    root = record.inbox_root or record.source_path.parent
    current_sources = tuple(chat_import_catalog_service.note_sources(pdf=record.source_path, inbox_root=root))
    if current_sources != record.note_sources:
        raise ChatToolError("chat_import_bundle_changed", "Import note bundle changed after preview.", status_code=409)
    if not current_sources:
        return
    for source in record.note_sources:
        path = (root / Path(str(source["relative_path"]))).resolve(strict=False)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise ChatToolError("chat_import_bundle_changed", "Import notes changed after preview.", status_code=409) from exc
        if stat.st_size != source["size_bytes"] or stat.st_mtime_ns != source["mtime_ns"] or _sha256_file(path) != source["sha256"]:
            raise ChatToolError("chat_import_bundle_changed", "Import notes changed after preview.", status_code=409)


def _managed_pdf_name(record: ImportConfirmation) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", record.source_path.stem).strip(".-") or "document"
    return f"{record.source_sha256[:16]}-{stem[:80]}.pdf"


def _looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False


def _pdf_exists(value: Any, *, data_dir: Path) -> bool:
    if not value:
        return False
    try:
        path = Path(str(value))
        if path.is_absolute():
            return path.is_file()
        return any(
            candidate.is_file()
            for candidate in (Path(data_dir).parent / path, Path(data_dir) / path)
        )
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def reset_chat_tool_state_for_tests() -> None:
    with _TOKEN_LOCK:
        _DELETE_CONFIRMATIONS.clear()
        _IMPORT_CONFIRMATIONS.clear()
        _IMPORT_IN_PROGRESS.clear()
        _IMPORT_COMPLETIONS.clear()
