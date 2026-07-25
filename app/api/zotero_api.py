from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.paths import DEFAULT_DB_PATH
from app.schemas.mechanism_draft import (
    MechanismDraftPromptDryRunRequest,
    MechanismDraftPromptDryRunResponse,
    MechanismDraftValidateDryRunRequest,
    MechanismDraftValidationReport,
)
from app.schemas.mechanism_draft_candidate import (
    MechanismDraftCandidateDetailResponse,
    MechanismDraftCandidatePersistRequest,
    MechanismDraftCandidatePersistResponse,
    MechanismDraftCandidateQueueResponse,
    MechanismDraftCandidateReviewHandoffResponse,
    MechanismDraftCandidateReviewRequest,
    MechanismDraftCandidateReviewResponse,
)
from app.schemas.mechanism_prompt_export import (
    MechanismPromptBatchExportRequest,
    MechanismPromptBatchExportResponse,
    MechanismPromptExportRequest,
    MechanismPromptExportResponse,
    MechanismPromptValidatePastedRequest,
    MechanismPromptValidatePastedResponse,
)
from app.schemas.zotero_inspiration import (
    ZoteroInspirationBatchMatchDryRunRequest,
    ZoteroInspirationBatchMatchDryRunResponse,
    ZoteroInspirationMatchDryRunRequest,
    ZoteroInspirationMatchDryRunResponse,
    ZoteroInspirationNoteBatchUpsertRequest,
    ZoteroInspirationNoteBatchUpsertResponse,
    ZoteroInspirationNoteUpsertRequest,
    ZoteroInspirationNoteUpsertResponse,
    ZoteroMechanismReadinessBatchDryRunRequest,
    ZoteroMechanismReadinessBatchDryRunResponse,
    ZoteroMechanismReadinessDryRunRequest,
    ZoteroMechanismReadinessDryRunResponse,
)
from app.schemas.zotero_markdown_export import ZoteroMarkdownExportRequest
from app.schemas.zotero_selected_book_import import ZoteroSelectedBookImportPreviewRequest
from app.services import (
    inspiration_note_matching_service,
    mechanism_draft_candidate_handoff_service,
    mechanism_draft_candidate_service,
    mechanism_draft_prompt_service,
    mechanism_prompt_export_service,
    mechanism_readiness_service,
    zotero_inspiration_note_service,
    zotero_markdown_export_service,
    zotero_selected_book_preview_service,
    zotero_source_cache_service,
)


router = APIRouter(prefix="/api/v1/zotero")
INSPIRATION_NOTE_CONNECTION_FACTORY: Callable[[], sqlite3.Connection] | None = None
INSPIRATION_MATCH_CONNECTION_FACTORY: Callable[[], sqlite3.Connection] | None = None
MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY: Callable[[], sqlite3.Connection] | None = None
PRODUCTION_CONNECTION_FACTORIES_ENABLED = False
MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED = False
PRODUCTION_DB_PATH = DEFAULT_DB_PATH


def configure_production_connection_factories(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    enable_mechanism_draft_candidate_writes: bool = False,
) -> None:
    """Register production DB factories without opening SQLite at import/startup time."""
    global INSPIRATION_NOTE_CONNECTION_FACTORY
    global INSPIRATION_MATCH_CONNECTION_FACTORY
    global MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY
    global PRODUCTION_CONNECTION_FACTORIES_ENABLED
    global MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED
    global PRODUCTION_DB_PATH

    PRODUCTION_DB_PATH = Path(db_path)
    INSPIRATION_NOTE_CONNECTION_FACTORY = lambda: _open_sqlite(PRODUCTION_DB_PATH, mode="rw")
    INSPIRATION_MATCH_CONNECTION_FACTORY = lambda: _open_sqlite_readonly(PRODUCTION_DB_PATH)
    MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY = (
        (lambda: _open_sqlite(PRODUCTION_DB_PATH, mode="rw"))
        if enable_mechanism_draft_candidate_writes
        else (lambda: _open_sqlite_readonly(PRODUCTION_DB_PATH))
    )
    PRODUCTION_CONNECTION_FACTORIES_ENABLED = True
    MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED = enable_mechanism_draft_candidate_writes


def disable_production_connection_factories() -> None:
    global INSPIRATION_NOTE_CONNECTION_FACTORY
    global INSPIRATION_MATCH_CONNECTION_FACTORY
    global MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY
    global PRODUCTION_CONNECTION_FACTORIES_ENABLED
    global MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED

    INSPIRATION_NOTE_CONNECTION_FACTORY = None
    INSPIRATION_MATCH_CONNECTION_FACTORY = None
    MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY = None
    PRODUCTION_CONNECTION_FACTORIES_ENABLED = False
    MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED = False


def _mechanism_draft_candidate_failure_detail(message: str) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "error": message,
        "db_write_performed": False,
        "persistence_scope": "disabled",
        "connection_is_production": False,
        "mechanism_card_created": False,
        "llm_called": False,
        "external_model_called": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
        "production_persistence_enabled": False,
    }


def _open_sqlite(db_path: Path, *, mode: str) -> sqlite3.Connection:
    path = db_path.resolve()
    return sqlite3.connect(f"file:{path.as_posix()}?mode={mode}", uri=True)


def _open_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    connection = _open_sqlite(db_path, mode="ro")
    connection.execute("PRAGMA query_only = ON")
    return connection


def _integrity_check_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _connection_database_path(connection: sqlite3.Connection) -> Path | None:
    for _, name, path in connection.execute("PRAGMA database_list").fetchall():
        if name == "main" and path:
            return Path(path).resolve()
    return None


def _candidate_connection_is_production(connection: sqlite3.Connection) -> bool:
    database_path = _connection_database_path(connection)
    return database_path is not None and database_path == PRODUCTION_DB_PATH.resolve()


def _candidate_persistence_scope(connection: sqlite3.Connection) -> str:
    return "production" if _candidate_connection_is_production(connection) else "tempdb"


def get_inspiration_note_connection() -> Iterator[sqlite3.Connection]:
    if INSPIRATION_NOTE_CONNECTION_FACTORY is None:
        raise HTTPException(
            status_code=503,
            detail="Inspiration-note persistence is disabled until an explicit K-C-Apply database configuration.",
        )
    try:
        connection = INSPIRATION_NOTE_CONNECTION_FACTORY()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Inspiration-note persistence database is unavailable: {exc}",
        ) from exc
    try:
        integrity_ok = _integrity_check_ok(connection)
        status = zotero_inspiration_note_service.get_sync_status(
            connection,
            production_persistence_enabled=PRODUCTION_CONNECTION_FACTORIES_ENABLED,
            integrity_check_ok=integrity_ok,
        )
        if not integrity_ok:
            raise HTTPException(
                status_code=503,
                detail="Production SQLite integrity_check failed; inspiration-note persistence is unavailable.",
            )
        if not status["schema_ready"]:
            raise HTTPException(
                status_code=503,
                detail="Inspiration-note schema is unavailable; K-C does not apply it to production automatically.",
            )
        yield connection
    finally:
        connection.close()


def get_inspiration_match_connection() -> Iterator[sqlite3.Connection]:
    if INSPIRATION_MATCH_CONNECTION_FACTORY is None:
        raise HTTPException(
            status_code=503,
            detail="Inspiration-note dry-run matching requires an explicitly configured read-only connection.",
        )
    try:
        connection = INSPIRATION_MATCH_CONNECTION_FACTORY()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Inspiration-note matching database is unavailable: {exc}",
        ) from exc
    try:
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA query_only = ON")
        if not _integrity_check_ok(connection):
            raise HTTPException(
                status_code=503,
                detail="Production SQLite integrity_check failed; inspiration-note matching is unavailable.",
            )
        yield connection
    finally:
        connection.close()


def _get_mechanism_draft_candidate_connection(
    *,
    require_tempdb: bool,
) -> Iterator[sqlite3.Connection]:
    if MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY is None:
        raise HTTPException(
            status_code=503,
            detail=_mechanism_draft_candidate_failure_detail(
                "Mechanism-draft candidate persistence is disabled; "
                "K-H requires an explicit tempdb/test connection."
            ),
        )
    try:
        connection = MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY()
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=_mechanism_draft_candidate_failure_detail(
                f"Mechanism-draft candidate database is unavailable: {exc}"
            ),
        ) from exc
    try:
        if require_tempdb and _candidate_connection_is_production(connection):
            raise HTTPException(
                status_code=503,
                detail=_mechanism_draft_candidate_failure_detail(
                    "Tempdb-only mechanism-draft candidate route rejected the production SQLite database."
                ),
            )
        integrity_ok = _integrity_check_ok(connection)
        status = mechanism_draft_candidate_service.get_schema_status(
            connection,
            production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
            integrity_check_ok=integrity_ok,
            persistence_scope=_candidate_persistence_scope(connection),
        )
        if not integrity_ok:
            raise HTTPException(
                status_code=503,
                detail=_mechanism_draft_candidate_failure_detail(
                    "Production SQLite integrity_check failed; mechanism-draft candidate persistence is unavailable."
                ),
            )
        if not status["schema_ready"]:
            raise HTTPException(
                status_code=503,
                detail=_mechanism_draft_candidate_failure_detail(
                    "Mechanism-draft candidate schema is unavailable; "
                    "K-H does not apply it to production automatically."
                ),
            )
        yield connection
    finally:
        connection.close()


def get_mechanism_draft_candidate_connection() -> Iterator[sqlite3.Connection]:
    yield from _get_mechanism_draft_candidate_connection(require_tempdb=False)


def get_mechanism_draft_candidate_tempdb_connection() -> Iterator[sqlite3.Connection]:
    yield from _get_mechanism_draft_candidate_connection(require_tempdb=True)


@router.post("/refresh-snapshot")
def refresh_snapshot() -> dict[str, Any]:
    try:
        return zotero_source_cache_service.refresh_snapshot()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sync-pdf-sources")
def sync_pdf_sources() -> dict[str, Any]:
    try:
        return zotero_source_cache_service.sync_pdf_sources()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pdf-sources")
def pdf_sources(
    q: str = Query(default=""),
    status: str | None = Query(default="available"),
) -> dict[str, Any]:
    try:
        return zotero_source_cache_service.list_pdf_sources(q=q, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import-preview")
def selected_book_import_preview(
    request: ZoteroSelectedBookImportPreviewRequest,
) -> dict[str, Any]:
    try:
        return (
            zotero_selected_book_preview_service
            .build_selected_book_preview(
                zotero_item_key=request.zotero_item_key,
                zotero_attachment_key=(
                    request.zotero_attachment_key
                ),
            )
        )
    except (
        zotero_selected_book_preview_service
        .ZoteroSelectedBookPreviewError
    ) as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail(),
        ) from exc


@router.post("/export-markdown")
def export_markdown(
    request: ZoteroMarkdownExportRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return zotero_markdown_export_service.build_zotero_markdown_export(
            connection,
            zotero_attachment_key=request.zotero_attachment_key,
            zotero_item_key=request.zotero_item_key,
            save_to_file=request.save_to_file,
        )
    except zotero_markdown_export_service.ZoteroMarkdownExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


@router.get("/export-markdown/by-attachment/{attachment_key}")
def export_markdown_by_attachment(
    attachment_key: str,
    save_to_file: bool = Query(default=False),
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return zotero_markdown_export_service.build_zotero_markdown_export(
            connection,
            zotero_attachment_key=attachment_key,
            save_to_file=save_to_file,
        )
    except zotero_markdown_export_service.ZoteroMarkdownExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


@router.post(
    "/inspiration-notes/upsert",
    response_model=ZoteroInspirationNoteUpsertResponse,
)
def upsert_inspiration_note(
    request: ZoteroInspirationNoteUpsertRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_note_connection),
) -> dict[str, Any]:
    try:
        return zotero_inspiration_note_service.upsert_inspiration_note(
            connection,
            _request_dict(request),
        )
    except zotero_inspiration_note_service.InspirationPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/inspiration-notes/batch-upsert",
    response_model=ZoteroInspirationNoteBatchUpsertResponse,
)
def batch_upsert_inspiration_notes(
    request: ZoteroInspirationNoteBatchUpsertRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_note_connection),
) -> dict[str, Any]:
    try:
        return zotero_inspiration_note_service.batch_upsert_inspiration_notes(
            connection,
            [_request_dict(note) for note in request.notes],
        )
    except zotero_inspiration_note_service.InspirationPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/inspiration-notes/by-attachment/{attachment_key}")
def inspiration_notes_by_attachment(
    attachment_key: str,
    connection: sqlite3.Connection = Depends(get_inspiration_note_connection),
) -> dict[str, Any]:
    return zotero_inspiration_note_service.list_inspiration_notes_by_attachment(
        connection,
        attachment_key,
    )


@router.get("/inspiration-notes/by-document/{document_id}")
def inspiration_notes_by_document(
    document_id: int,
    tag: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    connection: sqlite3.Connection = Depends(get_inspiration_note_connection),
) -> dict[str, Any]:
    return zotero_inspiration_note_service.list_inspiration_notes_by_document(
        connection,
        document_id,
        tag=tag,
        limit=limit,
    )


@router.get("/inspiration-notes/sync-status")
def inspiration_note_sync_status() -> dict[str, Any]:
    if INSPIRATION_NOTE_CONNECTION_FACTORY is None:
        return {
            **zotero_inspiration_note_service.get_sync_status(None),
            "mechanism_draft_candidate_storage": mechanism_draft_candidate_service.get_schema_status(None),
            "match_read_available": INSPIRATION_MATCH_CONNECTION_FACTORY is not None,
        }
    try:
        connection = INSPIRATION_NOTE_CONNECTION_FACTORY()
    except sqlite3.Error as exc:
        return {
            **zotero_inspiration_note_service.get_sync_status(
                None,
                production_persistence_enabled=PRODUCTION_CONNECTION_FACTORIES_ENABLED,
                integrity_check_ok=False,
            ),
            "available": False,
            "write_available": False,
            "error": str(exc),
            "mode": "production_factory_unavailable",
            "match_read_available": False,
            "mechanism_draft_candidate_storage": mechanism_draft_candidate_service.get_schema_status(None),
        }
    try:
        integrity_ok = _integrity_check_ok(connection)
        status = zotero_inspiration_note_service.get_sync_status(
            connection,
            production_persistence_enabled=PRODUCTION_CONNECTION_FACTORIES_ENABLED,
            integrity_check_ok=integrity_ok,
        )
        status["match_read_available"] = (
            INSPIRATION_MATCH_CONNECTION_FACTORY is not None and integrity_ok
        )
        status["mechanism_draft_candidate_storage"] = _mechanism_candidate_status()
        return status
    except sqlite3.Error as exc:
        return {
            **zotero_inspiration_note_service.get_sync_status(
                None,
                production_persistence_enabled=PRODUCTION_CONNECTION_FACTORIES_ENABLED,
                integrity_check_ok=False,
            ),
            "available": False,
            "write_available": False,
            "error": str(exc),
            "mode": "production_factory_unavailable",
            "match_read_available": False,
            "mechanism_draft_candidate_storage": mechanism_draft_candidate_service.get_schema_status(None),
        }
    finally:
        connection.close()


@router.post(
    "/inspiration-notes/match-dry-run",
    response_model=ZoteroInspirationMatchDryRunResponse,
)
def inspiration_note_match_dry_run(
    request: ZoteroInspirationMatchDryRunRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return inspiration_note_matching_service.build_inspiration_match_report(
            connection,
            _model_dict(request.note),
            document_id=request.document_id,
            max_candidates=request.max_candidates,
        )
    except inspiration_note_matching_service.InspirationMatchingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/inspiration-notes/batch-match-dry-run",
    response_model=ZoteroInspirationBatchMatchDryRunResponse,
)
def inspiration_notes_batch_match_dry_run(
    request: ZoteroInspirationBatchMatchDryRunRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return inspiration_note_matching_service.match_inspiration_notes_batch(
            connection,
            [_model_dict(note) for note in request.notes],
            document_id=request.document_id,
            max_candidates=request.max_candidates,
        )
    except inspiration_note_matching_service.InspirationMatchingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/inspiration-notes/mechanism-readiness-dry-run",
    response_model=ZoteroMechanismReadinessDryRunResponse,
)
def inspiration_note_mechanism_readiness_dry_run(
    request: ZoteroMechanismReadinessDryRunRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return mechanism_readiness_service.build_mechanism_readiness_report(
            connection,
            _model_dict(request.note),
            _model_dict(request.match_report) if request.match_report is not None else None,
            max_neighbor_chunks=request.max_neighbor_chunks,
        )
    except inspiration_note_matching_service.InspirationMatchingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/inspiration-notes/batch-mechanism-readiness-dry-run",
    response_model=ZoteroMechanismReadinessBatchDryRunResponse,
)
def inspiration_notes_batch_mechanism_readiness_dry_run(
    request: ZoteroMechanismReadinessBatchDryRunRequest,
    connection: sqlite3.Connection = Depends(get_inspiration_match_connection),
) -> dict[str, Any]:
    try:
        return mechanism_readiness_service.build_mechanism_readiness_batch(
            connection,
            [_model_dict(note) for note in request.notes],
            match_reports=[_model_dict(report) for report in request.match_reports],
            max_neighbor_chunks=request.max_neighbor_chunks,
        )
    except inspiration_note_matching_service.InspirationMatchingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/inspiration-notes/mechanism-draft-prompt-dry-run",
    response_model=MechanismDraftPromptDryRunResponse,
)
def inspiration_note_mechanism_draft_prompt_dry_run(
    request: MechanismDraftPromptDryRunRequest,
) -> dict[str, Any]:
    return mechanism_draft_prompt_service.build_mechanism_draft_prompt(
        _model_dict(request.readiness_report),
        include_prompt_text=request.include_prompt_text,
        include_expected_schema=request.include_expected_schema,
    )


@router.post(
    "/inspiration-notes/mechanism-draft-validate-response-dry-run",
    response_model=MechanismDraftValidationReport,
)
def inspiration_note_mechanism_draft_validate_response_dry_run(
    request: MechanismDraftValidateDryRunRequest,
) -> dict[str, Any]:
    return mechanism_draft_prompt_service.build_mechanism_draft_validation_report(
        request.candidate_response_json,
        _model_dict(request.readiness_report),
    )


@router.post(
    "/inspiration-notes/mechanism-drafts/persist-candidate-tempdb",
    response_model=MechanismDraftCandidatePersistResponse,
)
def persist_mechanism_draft_candidate_tempdb(
    request: MechanismDraftCandidatePersistRequest,
    connection: sqlite3.Connection = Depends(get_mechanism_draft_candidate_tempdb_connection),
) -> dict[str, Any]:
    try:
        return mechanism_draft_candidate_service.persist_validated_pending_draft_candidate(
            connection,
            request.pending_draft_preview,
            request.validation_report,
            request.source_context,
            persistence_scope="tempdb",
        )
    except mechanism_draft_candidate_service.MechanismDraftCandidateError as exc:
        raise HTTPException(
            status_code=422,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc


@router.get(
    "/inspiration-notes/mechanism-drafts/review-queue",
    response_model=MechanismDraftCandidateQueueResponse,
)
def mechanism_draft_review_queue(
    status: str = Query(default="pending"),
    document_id: int | None = Query(default=None),
    mechanism_type: str | None = Query(default=None),
    connection: sqlite3.Connection = Depends(get_mechanism_draft_candidate_connection),
) -> dict[str, Any]:
    try:
        return mechanism_draft_candidate_service.list_mechanism_draft_candidates(
            connection,
            status=status,
            document_id=document_id,
            mechanism_type=mechanism_type,
            persistence_scope=_candidate_persistence_scope(connection),
            production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
        )
    except mechanism_draft_candidate_service.MechanismDraftCandidateError as exc:
        raise HTTPException(
            status_code=422,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc


@router.get(
    "/inspiration-notes/mechanism-drafts/{draft_id}",
    response_model=MechanismDraftCandidateDetailResponse,
)
def mechanism_draft_candidate_detail(
    draft_id: str,
    connection: sqlite3.Connection = Depends(get_mechanism_draft_candidate_connection),
) -> dict[str, Any]:
    try:
        return mechanism_draft_candidate_service.get_mechanism_draft_candidate(
            connection,
            draft_id,
            persistence_scope=_candidate_persistence_scope(connection),
            production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
        )
    except mechanism_draft_candidate_service.MechanismDraftCandidateNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc


@router.get(
    "/inspiration-notes/mechanism-drafts/{draft_id}/review-packet-preview",
    response_model=MechanismDraftCandidateReviewHandoffResponse,
)
def mechanism_draft_candidate_review_packet_preview(
    draft_id: str,
    connection: sqlite3.Connection = Depends(get_mechanism_draft_candidate_connection),
) -> dict[str, Any]:
    try:
        return mechanism_draft_candidate_handoff_service.build_candidate_review_handoff(
            connection,
            draft_id,
            persistence_scope=_candidate_persistence_scope(connection),
            production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
        )
    except mechanism_draft_candidate_service.MechanismDraftCandidateNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc


@router.post(
    "/inspiration-notes/mechanism-drafts/{draft_id}/review",
    response_model=MechanismDraftCandidateReviewResponse,
)
def review_mechanism_draft_candidate(
    draft_id: str,
    request: MechanismDraftCandidateReviewRequest,
    connection: sqlite3.Connection = Depends(get_mechanism_draft_candidate_tempdb_connection),
) -> dict[str, Any]:
    try:
        return mechanism_draft_candidate_service.update_mechanism_draft_candidate_review_status(
            connection,
            draft_id,
            request.action,
            review_notes=request.review_notes,
            merge_target_draft_id=request.merge_target_draft_id,
            persistence_scope="tempdb",
        )
    except mechanism_draft_candidate_service.MechanismDraftCandidateNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc
    except mechanism_draft_candidate_service.MechanismDraftCandidateError as exc:
        raise HTTPException(
            status_code=422,
            detail=_mechanism_draft_candidate_failure_detail(str(exc)),
        ) from exc


@router.post(
    "/inspiration-notes/mechanism-prompt/export",
    response_model=MechanismPromptExportResponse,
)
def inspiration_note_mechanism_prompt_export(
    request: MechanismPromptExportRequest,
) -> dict[str, Any]:
    return mechanism_prompt_export_service.build_chatgpt_prompt_export_package(
        _model_dict(request.readiness_report),
        include_expected_schema=request.include_expected_schema,
        include_prompt_payload=request.include_prompt_payload,
        chapter_id=request.chapter_id,
        import_batch_id=request.import_batch_id,
    )


@router.post(
    "/inspiration-notes/mechanism-prompt/batch-export",
    response_model=MechanismPromptBatchExportResponse,
)
def inspiration_notes_mechanism_prompt_batch_export(
    request: MechanismPromptBatchExportRequest,
) -> dict[str, Any]:
    return mechanism_prompt_export_service.build_chatgpt_prompt_batch_export(
        [_model_dict(report) for report in request.readiness_reports],
        chapter_id=request.chapter_id,
        import_batch_id=request.import_batch_id,
        merge_selected_by_user=request.merge_selected_by_user,
        include_expected_schema=request.include_expected_schema,
        include_prompt_payload=request.include_prompt_payload,
    )


@router.post(
    "/inspiration-notes/mechanism-prompt/validate-pasted-response",
    response_model=MechanismPromptValidatePastedResponse,
)
def inspiration_note_mechanism_prompt_validate_pasted_response(
    request: MechanismPromptValidatePastedRequest,
) -> dict[str, Any]:
    return mechanism_prompt_export_service.validate_pasted_chatgpt_response(
        _model_dict(request.readiness_report),
        request.pasted_chatgpt_response_json,
    )


def _request_dict(request: ZoteroInspirationNoteUpsertRequest) -> dict[str, Any]:
    return _model_dict(request)


def _model_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _mechanism_candidate_status() -> dict[str, Any]:
    if MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY is None:
        return mechanism_draft_candidate_service.get_schema_status(None)
    try:
        connection = MECHANISM_DRAFT_CANDIDATE_CONNECTION_FACTORY()
    except sqlite3.Error as exc:
        return {
            **mechanism_draft_candidate_service.get_schema_status(
                None,
                production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
                integrity_check_ok=False,
            ),
            "error": str(exc),
        }
    try:
        integrity_ok = _integrity_check_ok(connection)
        return mechanism_draft_candidate_service.get_schema_status(
            connection,
            production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
            integrity_check_ok=integrity_ok,
            persistence_scope=_candidate_persistence_scope(connection),
        )
    except sqlite3.Error as exc:
        return {
            **mechanism_draft_candidate_service.get_schema_status(
                None,
                production_persistence_enabled=MECHANISM_DRAFT_CANDIDATE_PRODUCTION_WRITES_ENABLED,
                integrity_check_ok=False,
            ),
            "error": str(exc),
        }
    finally:
        connection.close()
