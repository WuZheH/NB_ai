from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

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
from app.services.import_operation_journal import (
    SCHEMA_VERSION as IMPORT_JOURNAL_SCHEMA_VERSION,
    ImportOperationJournal,
    ImportOperationJournalStore,
    JournalConflictError,
)


DELETE_CONFIRMATION_TTL_SECONDS = document_deletion_service.PREVIEW_TTL_SECONDS
IMPORT_CONFIRMATION_TTL_SECONDS = 10 * 60
IMPORT_COMPLETION_REPLAY_TTL_SECONDS = 60 * 60
IMPORT_CONCURRENT_WAIT_SECONDS = 5.0
MAX_IMPORT_BYTES = 200 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,255}$")
_TOKEN_LOCK = threading.RLock()
_IMPORT_CONDITION = threading.Condition(_TOKEN_LOCK)
_DELETE_CONFIRMATIONS: dict[str, "DeleteConfirmation"] = {}
_IMPORT_CONFIRMATIONS: dict[
    str,
    "ImportConfirmation | ZoteroImportConfirmation",
] = {}
_IMPORT_IN_PROGRESS: set[str] = set()
_IMPORT_COMPLETIONS: dict[str, "ImportCompletion"] = {}
_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


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
    source_pdf_sha256: str
    confirmation_token_fingerprint: str
    previewed_at: str
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
    import_journal_dir: Path | None = None
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

    def resolved_import_journal_dir(self) -> Path:
        if self.import_journal_dir is not None:
            return Path(self.import_journal_dir).resolve(strict=False)
        return (
            Path(self.data_dir).resolve(strict=False)
            / "import_operation_journal"
        )


def _import_journal_store(
    runtime: ChatToolRuntime,
) -> ImportOperationJournalStore:
    return ImportOperationJournalStore(
        runtime.resolved_import_journal_dir()
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
        try:
            return zotero_library_service.list_parent_items(
                query=query,
                document_type=document_type,
                status=status,
                limit=limit,
                db_path=actual_runtime.db_path,
            )
        except ValueError as exc:
            if str(exc) == "zotero_status_invalid":
                raise ChatToolError(
                    "library_status_invalid",
                    "The Zotero library status filter is invalid.",
                    status_code=422,
                ) from exc
            raise
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
        "chapter_count": None,
        "page_marker_count": None,
        "detection_method": None,
        "binding_rate": None,
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
    try:
        document_type = (
            zotero_selected_book_preview_service
            .document_type_for_item_type(item_type)
        )
    except ValueError as exc:
        raise ChatToolError(
            "zotero_import_preview_contract_invalid",
            "The Zotero preview did not return a supported bibliographic item.",
            status_code=500,
        ) from exc

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
        "parent_key": str(item.get("zotero_item_key") or "") or None,
        "zotero_item_key": str(item.get("zotero_item_key") or "") or None,
        "zotero_attachment_key": None,
        "pdf_sha256": None,
        "duplicate_status": "not_evaluated",
        "existing_document_id": None,
        "estimated_pages": None,
        "estimated_chunks": None,
        "chapter_count": None,
        "page_marker_count": None,
        "detection_method": None,
        "binding_rate": None,
        "document_type": document_type,
        "warnings": [
            str(value)
            for value in preview.get("warnings") or []
        ][:7],
        "blockers": list(preview.get("blockers") or []),
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
            "zotero_attachment_key": (
                str(selected.get("zotero_attachment_key") or "") or None
            ),
            "pdf_sha256": str(selected.get("pdf_sha256") or "") or None,
            "estimated_pages": preview.get(
                "estimated_pages",
                selected.get("page_count"),
            ),
            "estimated_chunks": preview.get("estimated_chunks"),
            "chapter_count": preview.get("chapter_count"),
            "page_marker_count": preview.get(
                "page_marker_count",
                preview.get("converted_markdown_page_markers"),
            ),
            "detection_method": preview.get("detection_method"),
            "binding_rate": preview.get("binding_rate"),
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
                preview.get("extraction_ready", False)
            ),
            "blockers": list(preview.get("blockers") or []),
        }
    )

    if bool(duplicate.get("duplicate_found")):
        base["duplicate_status"] = "duplicate"
        base["existing_document_id"] = unique_document_id
        return base

    if (
        not bool(preview.get("extraction_ready"))
        or int(preview.get("estimated_chunks") or 0) <= 0
        or bool(preview.get("blockers"))
    ):
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
    source_pdf_sha256 = str(
        selected.get("pdf_sha256") or ""
    ).strip()

    try:
        document_type = (
            zotero_selected_book_preview_service
            .document_type_for_item_type(item_type)
        )
    except ValueError as exc:
        raise ChatToolError(
            "zotero_import_preview_contract_invalid",
            "Selected-book preview metadata has an unsupported item type.",
            status_code=500,
        ) from exc

    if (
        not item_key
        or not attachment_key
        or not source_revision_fingerprint
        or _SHA256_HEX_RE.fullmatch(source_pdf_sha256) is None
        or not bool(preview.get("extraction_ready"))
        or int(preview.get("estimated_chunks") or 0) <= 0
        or bool(preview.get("blockers"))
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
    token_fingerprint = _token_digest(token)
    preview_audit = (
        preview.get("_preview_audit")
        if isinstance(preview.get("_preview_audit"), dict)
        else {}
    )

    record = ZoteroImportConfirmation(
        preview_token=str(preview_token),
        target_db_path=target_db_path,
        target_data_dir=target_data_dir,
        zotero_item_key=item_key,
        zotero_attachment_key=attachment_key,
        item_type=item_type,
        source_revision_fingerprint=source_revision_fingerprint,
        source_pdf_sha256=source_pdf_sha256,
        confirmation_token_fingerprint=token_fingerprint,
        previewed_at=str(preview_audit.get("previewed_at") or ""),
        title=str(item.get("title") or ""),
        document_type=document_type,
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
            token_fingerprint
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


class _ImportReceiptPersistError(RuntimeError):
    def __init__(self, *, writes_performed: bool | None) -> None:
        super().__init__("import receipt persistence failed")
        self.writes_performed = writes_performed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_source_revision_fingerprint(
    record: ImportConfirmation,
) -> str:
    return _stable_sha256(
        {
            "source_mtime_ns": int(record.source_mtime_ns),
            "source_sha256": record.source_sha256,
            "source_size": int(record.source_size),
        }
    )


def _local_transaction_fingerprint(
    record: ImportConfirmation,
    *,
    token_digest: str,
    source_revision_fingerprint: str,
) -> str:
    return _stable_sha256(
        {
            "confirmation_token_digest": token_digest,
            "document_type": record.document_type,
            "operation_type": "import_document",
            "source_pdf_sha256": record.source_sha256,
            "source_revision_fingerprint": source_revision_fingerprint,
            "title": record.title,
        }
    )


def _new_import_journal(
    *,
    record: ImportConfirmation | ZoteroImportConfirmation,
    token_digest: str,
) -> tuple[ImportOperationJournal, dict[str, Any] | None]:
    now = _utc_now()
    import_audit: dict[str, Any] | None = None
    if isinstance(record, ZoteroImportConfirmation):
        source_revision_fingerprint = record.source_revision_fingerprint
        source_pdf_sha256 = record.source_pdf_sha256
        import_audit = _zotero_import_audit(record)
        transaction_fingerprint = str(
            import_audit["transaction_fingerprint"]
        )
        zotero_item_key = record.zotero_item_key
        zotero_attachment_key = record.zotero_attachment_key
    else:
        source_revision_fingerprint = (
            _local_source_revision_fingerprint(record)
        )
        source_pdf_sha256 = record.source_sha256
        transaction_fingerprint = _local_transaction_fingerprint(
            record,
            token_digest=token_digest,
            source_revision_fingerprint=source_revision_fingerprint,
        )
        zotero_item_key = ""
        zotero_attachment_key = ""

    return (
        ImportOperationJournal(
            schema_version=IMPORT_JOURNAL_SCHEMA_VERSION,
            operation_id=uuid4().hex,
            operation_type="import_document",
            confirmation_token_digest=token_digest,
            transaction_fingerprint=transaction_fingerprint,
            source_revision_fingerprint=source_revision_fingerprint,
            title=record.title,
            zotero_item_key=zotero_item_key,
            zotero_attachment_key=zotero_attachment_key,
            source_pdf_sha256=source_pdf_sha256,
            owner_process_id=os.getpid(),
            owner_process_started_at=_PROCESS_STARTED_AT,
            owner_thread_id=threading.get_ident(),
            started_at=now,
            updated_at=now,
            heartbeat_at=now,
            revision=0,
            status="accepted",
            stage="confirmation_accepted",
            writes_performed=None,
            document_id=None,
            chunk_count=0,
            error=None,
            rollback=None,
            warnings=[],
            completion_receipt=None,
        ),
        import_audit,
    )


def _resolve_import_journal(
    store: ImportOperationJournalStore,
    token_digest: str,
) -> ImportOperationJournal | None:
    try:
        return store.resolve_by_token_digest(token_digest)
    except JournalConflictError as exc:
        raise ChatToolError(
            "chat_import_journal_conflict",
            "Multiple durable records exist for this confirmed import.",
            status_code=409,
            details={"safe_to_retry": False},
        ) from exc


def _resolve_import_journal_outcome(
    journal: ImportOperationJournal,
) -> dict[str, Any]:
    if journal.status == "committed":
        receipt = dict(journal.completion_receipt or {})
        response = receipt.get("response")
        if receipt.get("kind") != "success" or not isinstance(
            response, Mapping
        ):
            raise ChatToolError(
                "chat_import_journal_receipt_invalid",
                "The durable import receipt is invalid.",
                status_code=500,
                details={"safe_to_retry": False},
            )
        replay = dict(response)
        replay.update(
            {
                "already_completed": True,
                "replayed_receipt": True,
                "operation_in_progress": False,
            }
        )
        return replay

    if journal.status == "failed":
        receipt = dict(journal.completion_receipt or {})
        if receipt.get("kind") != "failure":
            raise ChatToolError(
                "chat_import_journal_receipt_invalid",
                "The durable import failure receipt is invalid.",
                status_code=500,
                details={"safe_to_retry": False},
            )
        details = dict(receipt.get("details") or {})
        details.update(
            {
                "already_completed": True,
                "replayed_receipt": True,
                "operation_in_progress": False,
                "safe_to_retry": False,
            }
        )
        raise ChatToolError(
            str(receipt.get("error_code") or "import_document_failed"),
            str(receipt.get("message") or "The confirmed import failed."),
            status_code=int(receipt.get("status_code") or 500),
            details=details,
        )

    if journal.status in {"accepted", "running"}:
        return _import_in_progress_response(journal)

    if journal.status == "orphaned":
        raise ChatToolError(
            "chat_import_operation_orphaned",
            "The confirmed import owner ended without a terminal receipt.",
            status_code=409,
            details={
                "safe_to_retry": False,
                "writes_performed": journal.writes_performed,
            },
        )

    raise ChatToolError(
        "chat_import_journal_status_invalid",
        "The durable import status is invalid.",
        status_code=500,
        details={"safe_to_retry": False},
    )


class _JournalStageRecorder:
    def __init__(
        self,
        store: ImportOperationJournalStore,
        journal: ImportOperationJournal,
    ) -> None:
        self.store = store
        self.current_journal = journal

    def __call__(
        self,
        stage: str,
        metadata: dict[str, Any],
    ) -> None:
        current = self.current_journal
        changes: dict[str, Any] = {
            "status": "running",
            "stage": str(stage),
            "heartbeat_at": _utc_now(),
        }
        document_id = metadata.get("document_id")
        if (
            isinstance(document_id, int)
            and not isinstance(document_id, bool)
            and document_id > 0
        ):
            changes["document_id"] = document_id
        chunk_count = metadata.get("chunk_count")
        if (
            isinstance(chunk_count, int)
            and not isinstance(chunk_count, bool)
            and chunk_count >= 0
        ):
            changes["chunk_count"] = chunk_count
        if isinstance(metadata.get("writes_performed"), bool):
            changes["writes_performed"] = metadata["writes_performed"]
        if stage == "rollback_started":
            changes["rollback"] = {"attempted": True}
        elif stage == "rollback_completed":
            changes["rollback"] = {
                "attempted": True,
                "completed": metadata.get("rollback_completed") is True,
            }
        warning_codes = metadata.get("warning_codes")
        if isinstance(warning_codes, list):
            changes["warnings"] = [
                {"code": str(code)[:128]}
                for code in warning_codes
                if str(code).strip()
            ]
        self.current_journal = self.store.update(
            current.operation_id,
            expected_revision=current.revision,
            expected_status=current.status,
            **changes,
        )


def _journal_stage_callback(
    store: ImportOperationJournalStore,
    journal: ImportOperationJournal,
) -> _JournalStageRecorder:
    return _JournalStageRecorder(store, journal)


def _normalize_committed_import_result(
    result: dict[str, Any],
    *,
    record: ImportConfirmation | ZoteroImportConfirmation,
) -> dict[str, Any]:
    invalid = not isinstance(result, dict) or result.get("status") != "committed"
    if not isinstance(result, dict):
        result = {}
    document_id = result.get("document_id")
    chunk_count = result.get("chunk_count", result.get("inserted_chunks"))
    title = result.get("title")
    document_type = result.get("document_type")
    invalid = invalid or not (
        isinstance(document_id, int)
        and not isinstance(document_id, bool)
        and document_id > 0
        and isinstance(chunk_count, int)
        and not isinstance(chunk_count, bool)
        and chunk_count > 0
        and isinstance(title, str)
        and bool(title.strip())
        and isinstance(document_type, str)
        and bool(document_type.strip())
    )
    writes_performed = result.get("writes_performed")
    if not isinstance(writes_performed, bool):
        writes_performed = any(
            result.get(field) is True
            for field in (
                "production_data_modified",
                "fts_write_performed",
                "vector_store_write_performed",
                "derived_index_publish_performed",
            )
        )
    if (
        writes_performed is False
        and isinstance(record, ImportConfirmation)
        and not invalid
    ):
        writes_performed = True
    invalid = invalid or writes_performed is not True
    if invalid:
        raise ChatToolError(
            "import_result_contract_invalid",
            "The importer returned an invalid committed result.",
            status_code=500,
            details={
                "writes_performed": (
                    writes_performed
                    if isinstance(writes_performed, bool)
                    else None
                ),
                "safe_to_retry": False,
            },
        )
    return {
        "status": "committed",
        "document_id": document_id,
        "title": title.strip(),
        "document_type": document_type.strip(),
        "chunk_count": chunk_count,
        "error_code": result.get("error_code"),
        "writes_performed": True,
    }


def _success_import_response(
    normalized: dict[str, Any],
    *,
    record: ImportConfirmation | ZoteroImportConfirmation,
) -> dict[str, Any]:
    return {
        "status": "committed",
        "document_id": normalized["document_id"],
        "title": normalized["title"],
        "document_type": normalized["document_type"],
        "chunk_count": normalized["chunk_count"],
        "duplicate_status": (
            record.duplicate_status
            if isinstance(record, ZoteroImportConfirmation)
            else "not_detected"
        ),
        "error_code": normalized.get("error_code"),
        "already_completed": False,
        "replayed_receipt": False,
        "operation_in_progress": False,
        "token_consumed": True,
        "writes_performed": True,
        "safe_to_retry": False,
    }


_SAFE_FAILURE_DETAIL_FIELDS = frozenset(
    {
        "writes_performed",
        "rollback",
        "rollback_attempted",
        "rollback_completed",
        "error_stage",
        "document_id",
        "chunk_count",
        "production_data_modified",
        "derived_index_publish_performed",
        "safe_to_retry",
        "warnings",
    }
)


def _safe_failure_details(details: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in _SAFE_FAILURE_DETAIL_FIELDS:
        value = details.get(key)
        if key == "rollback" and isinstance(value, dict):
            safe[key] = {
                nested: bool(value[nested])
                for nested in ("attempted", "completed")
                if isinstance(value.get(nested), bool)
            }
        elif key == "warnings" and isinstance(value, list):
            safe[key] = [
                text
                for item in value[:50]
                for text in [str(item).strip()]
                if re.fullmatch(r"[A-Za-z0-9_.:]{1,128}", text)
            ]
        elif key in {
            "writes_performed",
            "rollback_attempted",
            "rollback_completed",
            "production_data_modified",
            "derived_index_publish_performed",
            "safe_to_retry",
        } and isinstance(value, bool):
            safe[key] = value
        elif key == "document_id" and (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        ):
            safe[key] = value
        elif key == "chunk_count" and (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            safe[key] = value
        elif key == "error_stage" and isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"[A-Za-z0-9_.:]{1,128}", text):
                safe[key] = text
    return safe


def _safe_failure_message(error: ChatToolError) -> str:
    message = " ".join(str(error).split()).strip()
    if (
        not message
        or re.search(r"[A-Za-z]:[\\/]", message)
        or re.search(r"(?:^|\s)/(?:[^\s/]+/)+", message)
        or len(message) > 300
    ):
        return "The confirmed import failed."
    return message


def _persist_failed_import_receipt(
    *,
    store: ImportOperationJournalStore,
    journal: ImportOperationJournal,
    record: ImportConfirmation | ZoteroImportConfirmation,
    error: ChatToolError,
    exception_type: str | None = None,
) -> ImportOperationJournal:
    details = _safe_failure_details(dict(error.details))
    original_error_stage = details.get("error_stage")
    if not (
        isinstance(original_error_stage, str)
        and re.fullmatch(
            r"[A-Za-z0-9_.:]{1,128}",
            original_error_stage,
        )
    ):
        original_error_stage = journal.stage
    details["error_stage"] = original_error_stage
    details["safe_to_retry"] = False
    writes_performed = details.get("writes_performed")
    if not isinstance(writes_performed, bool):
        writes_performed = journal.writes_performed
    if writes_performed is not True and any(
        details.get(key) is True
        for key in (
            "production_data_modified",
            "derived_index_publish_performed",
        )
    ):
        writes_performed = True
    document_id = details.get("document_id", journal.document_id)
    if not (
        isinstance(document_id, int)
        and not isinstance(document_id, bool)
        and document_id > 0
    ):
        document_id = journal.document_id
    chunk_count = details.get("chunk_count", journal.chunk_count)
    if not (
        isinstance(chunk_count, int)
        and not isinstance(chunk_count, bool)
        and chunk_count >= 0
    ):
        chunk_count = journal.chunk_count
    rollback = details.get("rollback")
    if not isinstance(rollback, dict):
        attempted = details.get("rollback_attempted")
        completed = details.get("rollback_completed")
        rollback = (
            {
                "attempted": bool(attempted),
                "completed": bool(completed),
            }
            if isinstance(attempted, bool) or isinstance(completed, bool)
            else None
        )
    message = _safe_failure_message(error)
    public_response = {
        "status": "failed",
        "document_id": document_id,
        "title": record.title,
        "document_type": record.document_type,
        "chunk_count": chunk_count,
        "duplicate_status": (
            record.duplicate_status
            if isinstance(record, ZoteroImportConfirmation)
            else "not_detected"
        ),
        "error_code": error.error_code,
        "already_completed": False,
        "replayed_receipt": False,
        "operation_in_progress": False,
        "token_consumed": True,
        "writes_performed": writes_performed,
        "safe_to_retry": False,
    }
    receipt = {
        "kind": "failure",
        "error_code": error.error_code,
        "message": message,
        "status_code": int(error.status_code),
        "details": details,
        "public_response": public_response,
    }
    warnings = details.get("warnings")
    journal_warnings = (
        [{"code": str(item)[:128]} for item in warnings]
        if isinstance(warnings, list)
        else list(journal.warnings)
    )
    return store.update(
        journal.operation_id,
        expected_revision=journal.revision,
        expected_status=journal.status,
        status="failed",
        stage="receipt_persisted",
        heartbeat_at=_utc_now(),
        writes_performed=writes_performed,
        document_id=document_id,
        chunk_count=chunk_count,
        error={
            "error_code": error.error_code,
            "message": message,
            "status_code": int(error.status_code),
            "error_stage": original_error_stage,
            "exception_type": exception_type or type(error).__name__,
        },
        rollback=rollback,
        warnings=journal_warnings,
        completion_receipt=receipt,
    )


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
    token_digest = _token_digest(confirmation_token)
    store = _import_journal_store(actual_runtime)

    existing = _resolve_import_journal(store, token_digest)
    if existing is not None:
        return _resolve_import_journal_outcome(existing)

    record, concurrent_owner = _claim_import_owner(token_digest)
    if concurrent_owner:
        return _wait_for_import_resolution(
            token_digest,
            store=store,
            record=record,
        )

    assert record is not None
    journal: ImportOperationJournal | None = None
    cached_response: dict[str, Any] | None = None
    import_audit: dict[str, Any] | None = None

    try:
        journal, import_audit = _new_import_journal(
            record=record,
            token_digest=token_digest,
        )
        try:
            journal = store.create(journal)
        except JournalConflictError:
            _release_import_owner(token_digest)
            existing = _resolve_import_journal(store, token_digest)
            if existing is None:
                raise ChatToolError(
                    "chat_import_journal_conflict",
                    "The confirmed import journal is conflicted.",
                    status_code=409,
                    details={"safe_to_retry": False},
                )
            return _resolve_import_journal_outcome(existing)
        except Exception as exc:
            existing = _resolve_import_journal(store, token_digest)
            _release_import_owner(token_digest)
            if existing is not None:
                return _resolve_import_journal_outcome(existing)
            raise ChatToolError(
                "chat_import_journal_create_failed",
                "The confirmed import could not be durably accepted.",
                status_code=500,
                details={
                    "safe_to_retry": False,
                    "writes_performed": False,
                },
            ) from exc

        stage_callback = _journal_stage_callback(store, journal)

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
                        "The Zotero import target changed after preview.",
                        status_code=409,
                    )

                if actual_runtime.commit_zotero_import is None:
                    result = _commit_confirmed_zotero_import(
                        record=record,
                        runtime=actual_runtime,
                        stage_callback=stage_callback,
                        import_audit=import_audit,
                    )
                else:
                    stage_callback("body_import_started", {})
                    result = actual_runtime.commit_zotero_import(
                        record=record,
                        runtime=actual_runtime,
                    )
            else:
                _validate_import_source_unchanged(record)
                stage_callback("body_import_started", {})
                importer = (
                    actual_runtime.commit_import
                    or _commit_confirmed_import
                )
                result = importer(
                    record=record,
                    runtime=actual_runtime,
                )

            normalized = _normalize_committed_import_result(
                result,
                record=record,
            )
            journal = stage_callback.current_journal
            if journal.stage != "final_verification_completed":
                stage_callback(
                    "final_verification_completed",
                    {
                        "document_id": normalized["document_id"],
                        "chunk_count": normalized["chunk_count"],
                        "writes_performed": True,
                    },
                )
            journal = stage_callback.current_journal
            response = _success_import_response(
                normalized,
                record=record,
            )
            receipt = {
                "kind": "success",
                "response": dict(response),
            }
            try:
                journal = store.update(
                    journal.operation_id,
                    expected_revision=journal.revision,
                    expected_status=journal.status,
                    status="committed",
                    stage="receipt_persisted",
                    heartbeat_at=_utc_now(),
                    writes_performed=True,
                    document_id=normalized["document_id"],
                    chunk_count=normalized["chunk_count"],
                    completion_receipt=receipt,
                )
            except Exception as exc:
                raise _ImportReceiptPersistError(
                    writes_performed=True,
                ) from exc
            cached_response = dict(response)
            return response

        except _ImportReceiptPersistError as exc:
            raise ChatToolError(
                "chat_import_receipt_persist_failed",
                "The import completed but its durable receipt could not be saved.",
                status_code=500,
                details={
                    "writes_performed": exc.writes_performed,
                    "safe_to_retry": False,
                },
            ) from exc
        except ChatToolError as exc:
            journal = stage_callback.current_journal
            try:
                journal = _persist_failed_import_receipt(
                    store=store,
                    journal=journal,
                    record=record,
                    error=exc,
                )
            except Exception as persist_exc:
                raise ChatToolError(
                    "chat_import_receipt_persist_failed",
                    "The import failure receipt could not be saved.",
                    status_code=500,
                    details={
                        "writes_performed": journal.writes_performed,
                        "safe_to_retry": False,
                    },
                ) from persist_exc
            raise
        except Exception as exc:
            journal = stage_callback.current_journal
            wrapped = ChatToolError(
                "import_document_failed",
                "Search could not import the confirmed document.",
                status_code=500,
                details={
                    "writes_performed": journal.writes_performed,
                    "safe_to_retry": False,
                },
            )
            try:
                journal = _persist_failed_import_receipt(
                    store=store,
                    journal=journal,
                    record=record,
                    error=wrapped,
                    exception_type=type(exc).__name__,
                )
            except Exception as persist_exc:
                raise ChatToolError(
                    "chat_import_receipt_persist_failed",
                    "The import failure receipt could not be saved.",
                    status_code=500,
                    details={
                        "writes_performed": journal.writes_performed,
                        "safe_to_retry": False,
                    },
                ) from persist_exc
            raise wrapped from exc
        except BaseException as exc:
            journal = stage_callback.current_journal
            try:
                store.update(
                    journal.operation_id,
                    expected_revision=journal.revision,
                    expected_status=journal.status,
                    status="orphaned",
                    heartbeat_at=_utc_now(),
                    error={
                        "error_code": "import_owner_aborted",
                        "exception_type": type(exc).__name__,
                        "error_stage": journal.stage,
                    },
                    completion_receipt=None,
                )
            except Exception:
                pass
            raise
    finally:
        _release_import_owner(
            token_digest,
            response=cached_response,
        )

def _commit_confirmed_zotero_import(
    *,
    record: ZoteroImportConfirmation,
    runtime: ChatToolRuntime,
    stage_callback: (
        zotero_direction_b_import_service.DirectionBStageCallback
        | None
    ) = None,
    import_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        production = _zotero_runtime_is_production(runtime)

        if production:
            return (
                zotero_direction_b_import_service
                .commit_selected_book_import_to_production(
                    preview_token=record.preview_token,
                    body_importer=runtime.zotero_body_importer,
                    import_audit=(
                        import_audit or _zotero_import_audit(record)
                    ),
                    stage_callback=stage_callback,
                )
            )

        return (
            zotero_direction_b_import_service
            .commit_selected_book_import_to_temp_db(
                preview_token=record.preview_token,
                db_path=runtime.db_path,
                data_dir=runtime.data_dir,
                body_importer=runtime.zotero_body_importer,
                import_audit=(
                    import_audit or _zotero_import_audit(record)
                ),
                stage_callback=stage_callback,
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


def _zotero_import_audit(
    record: ZoteroImportConfirmation,
) -> dict[str, Any]:
    confirmed_at = datetime.now(timezone.utc).isoformat()
    transaction_fingerprint = hashlib.sha256(
        "|".join(
            (
                record.confirmation_token_fingerprint,
                record.source_revision_fingerprint,
                record.zotero_item_key,
                record.zotero_attachment_key,
                confirmed_at,
            )
        ).encode("utf-8")
    ).hexdigest()
    return {
        "confirmation_token_fingerprint": (
            record.confirmation_token_fingerprint
        ),
        "previewed_at": record.previewed_at or "not_recorded",
        "confirmed_at": confirmed_at,
        "transaction_fingerprint": transaction_fingerprint,
        "source_revision_fingerprint": (
            record.source_revision_fingerprint
        ),
        "lifecycle_events": [
            "previewed",
            "confirmed",
            "transaction_started",
        ],
    }


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
        result = chat_pdf_production_import_service.import_document_to_production(
            import_job_id=import_job_id,
            document_type=record.document_type,
            note_files=[record.inbox_root / Path(item["relative_path"]) for item in record.note_sources] if record.inbox_root else [],
            inbox_root=record.inbox_root,
            allow_production=True,
            runtime=production_runtime,
        )
        if isinstance(result, dict) and result.get("status") == "completed":
            result = dict(result)
            result["status"] = "committed"
            result.setdefault("title", record.title)
            result.setdefault("document_type", record.document_type)
            result["writes_performed"] = True
        return result
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


def _claim_import_owner(
    digest: str,
) -> tuple[
    ImportConfirmation | ZoteroImportConfirmation | None,
    bool,
]:
    with _TOKEN_LOCK:
        _purge_expired_tokens()
        if digest in _IMPORT_IN_PROGRESS:
            return (
                _IMPORT_CONFIRMATIONS.get(digest),
                True,
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
        return record, False


def _wait_for_import_resolution(
    digest: str,
    *,
    store: ImportOperationJournalStore,
    record: ImportConfirmation | ZoteroImportConfirmation | None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    timeout = (
        IMPORT_CONCURRENT_WAIT_SECONDS
        if timeout_seconds is None
        else max(0.0, float(timeout_seconds))
    )
    deadline = time.monotonic() + timeout

    with _IMPORT_CONDITION:
        while digest in _IMPORT_IN_PROGRESS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _IMPORT_CONDITION.wait(remaining)
        owner_still_running = digest in _IMPORT_IN_PROGRESS

    journal = _resolve_import_journal(store, digest)
    if journal is not None:
        return _resolve_import_journal_outcome(journal)
    if owner_still_running:
        return _import_in_progress_response(record)
    raise ChatToolError(
        "chat_import_owner_failed",
        "The original confirmed import ended before a durable record was created.",
        status_code=409,
        details={
            "safe_to_retry": False,
            "token_consumed": True,
            "writes_performed": None,
        },
    )


def _import_in_progress_response(
    record: (
        ImportConfirmation
        | ZoteroImportConfirmation
        | ImportOperationJournal
        | None
    ),
) -> dict[str, Any]:
    persisted_journal = (
        record
        if isinstance(record, ImportOperationJournal)
        else None
    )
    duplicate_status = (
        record.duplicate_status
        if isinstance(record, ZoteroImportConfirmation)
        else "not_detected"
    )
    return {
        "status": "in_progress",
        "document_id": (
            persisted_journal.document_id
            if persisted_journal is not None
            else None
        ),
        "title": str(record.title if record is not None else ""),
        "document_type": str(
            getattr(record, "document_type", "")
            if record is not None
            else ""
        ),
        "chunk_count": (
            persisted_journal.chunk_count
            if persisted_journal is not None
            else 0
        ),
        "duplicate_status": duplicate_status,
        "error_code": None,
        "already_completed": False,
        "replayed_receipt": False,
        "operation_in_progress": True,
        "token_consumed": True,
        "writes_performed": (
            persisted_journal.writes_performed
            if persisted_journal is not None
            else None
        ),
        "safe_to_retry": False,
    }


def _release_import_owner(
    digest: str,
    *,
    response: dict[str, Any] | None = None,
) -> None:
    with _IMPORT_CONDITION:
        _IMPORT_CONFIRMATIONS.pop(
            digest,
            None,
        )
        _IMPORT_IN_PROGRESS.discard(
            digest
        )
        if response is not None:
            _IMPORT_COMPLETIONS[digest] = (
                ImportCompletion(
                    response=dict(response),
                    expires_at=(
                        time.monotonic()
                        + IMPORT_COMPLETION_REPLAY_TTL_SECONDS
                    ),
                )
            )
        _IMPORT_CONDITION.notify_all()


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
