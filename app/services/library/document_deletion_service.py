from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.core.paths import (
    CONVERTED_MD_DIR,
    DATA_DIR,
    DEFAULT_DB_PATH,
    FTS_DB_PATH,
    FTS_MANIFEST_PATH,
    LANCEDB_DIR,
    PDFS_DIR,
)
from app.domains.retrieval import fragment_repository, note_vector_index
from app.domains.retrieval.result_contracts import NOTE_SOURCE_TYPES
from app.schemas.library_deletion import (
    DeletionOptions,
    ManualPreservationAcknowledgment,
)
from app.services import vector_store_service
from app.services.retrieval.fts_index_service import cleanup_document_retrieval_fts
from app.services.retrieval.source_registry import RetrievalSourceRegistry


PREVIEW_TTL_SECONDS = 5 * 60
MAX_BATCH_SIZE = 5
DELETION_SCHEMA_VERSION = "search_book_deletion.v1"
AUDIT_SCHEMA_VERSION = "search_book_deletion_audit.v1"
MANUAL_PRESERVATION_BLOCKER_TYPE = (
    "object_user_comment_requires_manual_preservation"
)
PRESERVATION_MANIFEST_FILENAME = "preservation_manifest.json"
PRESERVATION_MANIFEST_SCHEMA_VERSION = (
    "search_deletion_preservation_manifest.v1"
)
RELEVANT_REFERENCE_COLUMNS = {
    "document_id",
    "matched_document_id",
    "source_doc_id",
    "chunk_id",
    "matched_chunk_id",
    "source_chunk_id",
    "evidence_chunk_id",
}
KNOWN_REFERENCE_TABLES = {
    "book_chapters",
    "chunk_layout_line_links",
    "chunk_layout_links",
    "chunk_tags",
    "document_sources",
    "documents",
    "inspiration_card_sources",
    "knowledge_chunks",
    "knowledge_relations",
    "library_archive_states",
    "markdown_nodes",
    "mechanism_draft_candidates",
    "note_classification_review_items",
    "note_classification_reviews",
    "note_correction_review_items",
    "note_correction_reviews",
    "note_evidence_links",
    "object_candidate_draft_review_items",
    "object_candidate_draft_reviews",
    "object_candidate_human_review_items",
    "object_candidate_human_reviews",
    "object_candidates",
    "ocr_first_candidate_corrections",
    "ocr_first_chunk_candidates",
    "ocr_first_promote_snapshots",
    "pdf_page_layout_blocks",
    "pdf_page_layout_lines",
    "pdf_page_layout_spans",
    "pdf_page_text_layer_cache",
    "personal_notes",
    "zotero_inspiration_notes",
}
REVIEW_TABLES = (
    "note_correction_reviews",
    "note_classification_reviews",
    "object_candidate_draft_reviews",
    "object_candidate_human_reviews",
    "object_candidate_draft_review_items",
    "object_candidate_human_review_items",
)
NOTE_REVIEW_CHILD_TABLES = (
    ("note_correction_review_items", "note_correction_reviews"),
    ("note_classification_review_items", "note_classification_reviews"),
)
DERIVED_DOCUMENT_TABLES = (
    "ocr_first_candidate_corrections",
    "ocr_first_chunk_candidates",
    "ocr_first_promote_snapshots",
    "pdf_page_layout_blocks",
    "pdf_page_layout_lines",
    "pdf_page_layout_spans",
    "pdf_page_text_layer_cache",
    "chunk_layout_line_links",
    "chunk_layout_links",
)


class DeletionError(RuntimeError):
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
class DeletionRuntime:
    db_path: Path = DEFAULT_DB_PATH
    data_dir: Path = DATA_DIR
    fts_path: Path = FTS_DB_PATH
    fts_manifest_path: Path = FTS_MANIFEST_PATH
    vector_store_path: Path = LANCEDB_DIR
    vector_manifest_path: Path = vector_store_service.MANIFEST_PATH
    archive_root: Path | None = None
    cleanup_fts: Callable[..., dict[str, Any]] | None = None
    inspect_vectors: Callable[..., dict[str, Any]] | None = None
    cleanup_vectors: Callable[..., dict[str, Any]] | None = None

    def resolved_archive_root(self) -> Path:
        if self.archive_root is not None:
            return Path(self.archive_root)
        configured = os.environ.get("SEARCH_BOOK_DELETION_ARCHIVE_ROOT", "").strip()
        if configured:
            return Path(configured).resolve()
        project_root = Path(self.data_dir).resolve().parent
        learning_root = project_root.parents[1] if len(project_root.parents) > 1 else project_root.parent
        return learning_root / "Archives" / "SearchBookDeletion"


@dataclass(frozen=True)
class InternalDeletionPlan:
    document_id: int
    title: str
    document_revision: str
    impact_hash: str
    chunk_ids: tuple[int, ...]
    node_ids: tuple[int, ...]
    chapter_ids: tuple[int, ...]
    object_ids: tuple[int, ...]
    object_keys: tuple[str, ...]
    exclusive_object_keys: tuple[str, ...]
    shared_object_keys: tuple[str, ...]
    passage_source_ids: tuple[str, ...]
    derived_files: tuple[Path, ...]
    generated_cache_files: tuple[Path, ...]
    managed_pdf: Path | None
    deletion_options: dict[str, Any]
    row_counts: dict[str, int]
    orphan_baseline: dict[str, int]
    manual_preservation_acknowledgment: dict[str, Any] | None


@dataclass(frozen=True)
class PreparedPreview:
    public: dict[str, Any]
    plan: InternalDeletionPlan


@dataclass(frozen=True)
class PreviewRecord:
    token_digest: str
    document_id: int
    document_revision: str
    impact_hash: str
    options_hash: str
    acknowledgment_hash: str
    expires_at: float


@dataclass
class CleanupState:
    fts: dict[str, Any] = field(default_factory=dict)
    vectors: dict[str, Any] = field(default_factory=dict)
    note_vectors: dict[str, Any] = field(default_factory=dict)
    files: dict[str, Any] = field(default_factory=dict)
    orphan_scan: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)


_MUTATION_LOCK = threading.RLock()
_TOKEN_LOCK = threading.RLock()
_PREVIEW_TOKENS: dict[str, PreviewRecord] = {}


def create_deletion_preview(
    document_id: int,
    *,
    deletion_options: DeletionOptions | dict[str, Any] | None = None,
    manual_preservation_acknowledgment: (
        ManualPreservationAcknowledgment | dict[str, Any] | None
    ) = None,
    runtime: DeletionRuntime | None = None,
    issue_token: bool = True,
) -> dict[str, Any]:
    actual_runtime = runtime or DeletionRuntime()
    options = _normalize_options(deletion_options)
    acknowledgment = _normalize_acknowledgment(
        manual_preservation_acknowledgment
    )
    prepared = _prepare_preview(
        int(document_id),
        options=options,
        manual_preservation_acknowledgment=acknowledgment,
        runtime=actual_runtime,
    )
    payload = dict(prepared.public)
    if issue_token:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        record = PreviewRecord(
            token_digest=_sha256_text(token),
            document_id=prepared.plan.document_id,
            document_revision=prepared.plan.document_revision,
            impact_hash=prepared.plan.impact_hash,
            options_hash=_hash_json(options),
            acknowledgment_hash=_hash_acknowledgment(acknowledgment),
            expires_at=now + PREVIEW_TTL_SECONDS,
        )
        with _TOKEN_LOCK:
            _purge_preview_tokens(now)
            _PREVIEW_TOKENS[record.token_digest] = record
        payload.update(
            {
                "preview_token": token,
                "preview_expires_in_seconds": PREVIEW_TTL_SECONDS,
            }
        )
    return payload


def delete_document(
    *,
    document_id: int,
    preview_token: str,
    expected_document_revision: str,
    confirmation_text: str,
    deletion_options: DeletionOptions | dict[str, Any] | None = None,
    manual_preservation_acknowledgment: (
        ManualPreservationAcknowledgment | dict[str, Any] | None
    ) = None,
    runtime: DeletionRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or DeletionRuntime()
    options = _normalize_options(deletion_options)
    acknowledgment = _normalize_acknowledgment(
        manual_preservation_acknowledgment
    )
    record = _consume_preview_token(preview_token)
    if record.document_id != int(document_id):
        raise DeletionError("deletion_preview_document_mismatch", "删除预览不属于该文档。")
    if record.options_hash != _hash_json(options):
        raise DeletionError("deletion_preview_options_changed", "删除选项已变化，请重新预览。")
    if record.acknowledgment_hash != _hash_acknowledgment(acknowledgment):
        raise DeletionError(
            "deletion_preview_acknowledgment_changed",
            "人工保存确认内容已变化，请重新预览。",
        )

    with _MUTATION_LOCK:
        prepared = _prepare_preview(
            int(document_id),
            options=options,
            manual_preservation_acknowledgment=acknowledgment,
            runtime=actual_runtime,
        )
        plan = prepared.plan
        if expected_document_revision != plan.document_revision:
            raise DeletionError("deletion_document_revision_stale", "文档已变化，请重新预览。")
        if record.document_revision != plan.document_revision or record.impact_hash != plan.impact_hash:
            raise DeletionError("deletion_preview_stale", "删除影响已变化，请重新预览。")
        if confirmation_text not in {plan.title, "删除"}:
            raise DeletionError("deletion_confirmation_invalid", "确认文字不正确。", status_code=422)
        blockers = list(prepared.public.get("deletion_blockers") or [])
        if blockers:
            raise DeletionError(
                "deletion_blocked",
                "该文档存在无法安全处理的关联数据。",
                details={"blockers": blockers},
            )

        audit_id = _audit_id()
        archive_dir = _create_recovery_package(
            audit_id=audit_id,
            plan=plan,
            runtime=actual_runtime,
            preview=prepared.public,
        )
        try:
            db_result = _execute_database_transaction(plan, runtime=actual_runtime)
        except Exception as exc:
            _write_audit_result(
                archive_dir,
                _audit_payload(
                    audit_id=audit_id,
                    plan=plan,
                    result="rolled_back",
                    error_code=getattr(exc, "error_code", "deletion_transaction_rolled_back"),
                    db_result={},
                    cleanup=CleanupState(),
                ),
            )
            if isinstance(exc, DeletionError):
                raise
            raise DeletionError(
                "deletion_transaction_rolled_back",
                "数据库事务失败，已完整回滚，未处理向量或文件。",
                details={"exception_type": type(exc).__name__},
            ) from exc

        cleanup = _run_post_commit_cleanup(plan, runtime=actual_runtime)
        result = "cleanup_incomplete" if cleanup.errors else "completed"
        error_code = "deletion_cleanup_incomplete" if cleanup.errors else ""
        audit = _audit_payload(
            audit_id=audit_id,
            plan=plan,
            result=result,
            error_code=error_code,
            db_result=db_result,
            cleanup=cleanup,
        )
        _write_audit_result(archive_dir, audit)
        return {
            "status": result,
            "error_code": error_code or None,
            "audit_id": audit_id,
            "document_id": plan.document_id,
            "database": db_result,
            "vectors": cleanup.vectors,
            "note_vectors": cleanup.note_vectors,
            "files": cleanup.files,
            "fts": cleanup.fts,
            "orphan_scan": cleanup.orphan_scan,
            "remediation": _remediation(cleanup.errors, audit_id=audit_id),
            "recovery_package": _archive_summary(archive_dir),
        }


def preflight_delete_document(
    *,
    document_id: int,
    preview_token: str,
    expected_document_revision: str,
    confirmation_text: str,
    deletion_options: DeletionOptions | dict[str, Any] | None = None,
    manual_preservation_acknowledgment: (
        ManualPreservationAcknowledgment | dict[str, Any] | None
    ) = None,
    runtime: DeletionRuntime | None = None,
) -> PreparedPreview:
    actual_runtime = runtime or DeletionRuntime()
    options = _normalize_options(deletion_options)
    acknowledgment = _normalize_acknowledgment(
        manual_preservation_acknowledgment
    )
    record = _preview_record(preview_token, consume=False)
    prepared = _prepare_preview(
        int(document_id),
        options=options,
        manual_preservation_acknowledgment=acknowledgment,
        runtime=actual_runtime,
    )
    plan = prepared.plan
    if record.document_id != int(document_id):
        raise DeletionError("deletion_preview_document_mismatch", "删除预览不属于该文档。")
    if record.options_hash != _hash_json(options):
        raise DeletionError("deletion_preview_options_changed", "删除选项已变化，请重新预览。")
    if record.acknowledgment_hash != _hash_acknowledgment(acknowledgment):
        raise DeletionError(
            "deletion_preview_acknowledgment_changed",
            "人工保存确认内容已变化，请重新预览。",
        )
    if expected_document_revision != plan.document_revision:
        raise DeletionError("deletion_document_revision_stale", "文档已变化，请重新预览。")
    if record.document_revision != plan.document_revision or record.impact_hash != plan.impact_hash:
        raise DeletionError("deletion_preview_stale", "删除影响已变化，请重新预览。")
    if confirmation_text not in {plan.title, "删除"}:
        raise DeletionError("deletion_confirmation_invalid", "确认文字不正确。", status_code=422)
    blockers = list(prepared.public.get("deletion_blockers") or [])
    if blockers:
        raise DeletionError(
            "deletion_blocked",
            "该文档存在无法安全处理的关联数据。",
            details={"blockers": blockers},
        )
    return prepared


def delete_documents_batch(
    *,
    document_ids: list[int],
    requests: list[dict[str, Any]],
    confirmation_text: str,
    runtime: DeletionRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or DeletionRuntime()
    ids = [int(value) for value in document_ids]
    if not ids or len(ids) > MAX_BATCH_SIZE or len(set(ids)) != len(ids) or any(value < 1 for value in ids) or len(requests) != len(ids):
        raise DeletionError(
            "deletion_batch_document_ids_invalid",
            "批量删除必须明确提供 1 到 5 个不同 document ID。",
            status_code=422,
        )
    if confirmation_text != "删除":
        raise DeletionError("deletion_batch_confirmation_invalid", "批量删除必须输入“删除”。", status_code=422)
    request_by_id = {int(item.get("document_id") or 0): item for item in requests}
    if set(request_by_id) != set(ids):
        raise DeletionError(
            "deletion_batch_request_ids_mismatch",
            "确认的 document ID 列表与删除请求不一致。",
            status_code=422,
        )

    with _MUTATION_LOCK:
        prepared: list[PreparedPreview] = []
        for document_id in ids:
            item = request_by_id[document_id]
            prepared.append(
                preflight_delete_document(
                    document_id=document_id,
                    preview_token=str(item.get("preview_token") or ""),
                    expected_document_revision=str(item.get("expected_document_revision") or ""),
                    confirmation_text="删除",
                    deletion_options=item.get("deletion_options"),
                    manual_preservation_acknowledgment=item.get(
                        "manual_preservation_acknowledgment"
                    ),
                    runtime=actual_runtime,
                )
            )
        object_key_owners: dict[str, list[int]] = {}
        for preview in prepared:
            for key in preview.plan.object_keys:
                object_key_owners.setdefault(key, []).append(preview.plan.document_id)
        overlaps = {key: owners for key, owners in object_key_owners.items() if len(set(owners)) > 1}
        if overlaps:
            raise DeletionError(
                "deletion_batch_shared_object_overlap",
                "所选文档之间存在共享对象，请逐本删除。",
                details={"overlap_count": len(overlaps)},
            )

        results: list[dict[str, Any]] = []
        for document_id in ids:
            item = request_by_id[document_id]
            try:
                result = delete_document(
                    document_id=document_id,
                    preview_token=str(item.get("preview_token") or ""),
                    expected_document_revision=str(item.get("expected_document_revision") or ""),
                    confirmation_text="删除",
                    deletion_options=item.get("deletion_options"),
                    manual_preservation_acknowledgment=item.get(
                        "manual_preservation_acknowledgment"
                    ),
                    runtime=actual_runtime,
                )
            except DeletionError as exc:
                return {
                    "status": "partial_failure" if results else "failed",
                    "error_code": "deletion_batch_partial_failure" if results else exc.error_code,
                    "message": str(exc),
                    "requested_document_ids": ids,
                    "completed_document_ids": [result["document_id"] for result in results],
                    "results": results,
                }
            results.append(result)
            if result.get("status") != "completed":
                return {
                    "status": "cleanup_incomplete",
                    "error_code": "deletion_batch_cleanup_incomplete",
                    "requested_document_ids": ids,
                    "completed_document_ids": [value["document_id"] for value in results],
                    "results": results,
                }
        return {
            "status": "completed",
            "requested_document_ids": ids,
            "completed_document_ids": ids,
            "results": results,
        }


def reset_preview_tokens_for_tests() -> None:
    with _TOKEN_LOCK:
        _PREVIEW_TOKENS.clear()


def retry_incomplete_cleanup(
    audit_id: str,
    *,
    apply: bool = False,
    runtime: DeletionRuntime | None = None,
) -> dict[str, Any]:
    actual_runtime = runtime or DeletionRuntime()
    if not re.fullmatch(r"delete-\d{8}T\d{6}Z-[a-f0-9]{12}", str(audit_id or "")):
        raise DeletionError("deletion_audit_id_invalid", "删除审计 ID 格式无效。", status_code=422)
    root = actual_runtime.resolved_archive_root().resolve()
    archive_dir = (root / audit_id).resolve()
    if not archive_dir.is_relative_to(root) or not archive_dir.is_dir():
        raise DeletionError("deletion_recovery_package_not_found", "未找到删除恢复包。", status_code=404)
    manifest_path = archive_dir / "recovery_manifest.json"
    report_path = archive_dir / "deletion_report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise DeletionError("deletion_recovery_package_incomplete", "恢复包缺少必要文件。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous_report = json.loads(report_path.read_text(encoding="utf-8"))
    cleanup_plan = dict(manifest.get("cleanup_plan") or {})
    document_id = int(manifest.get("document_id") or 0)
    with _readonly_connection(actual_runtime.db_path) as connection:
        if _count(connection, "documents", "id = ?", (document_id,)):
            raise DeletionError(
                "deletion_retry_document_still_exists",
                "文档仍存在；不得用清理重试替代数据库删除。",
            )
        retry_orphan_baseline = {
            str(key): int(value)
            for key, value in (
                cleanup_plan.get("orphan_baseline")
                or _global_orphan_counts(connection)
            ).items()
        }
    preview = dict(manifest.get("preview") or {})
    plan = InternalDeletionPlan(
        document_id=document_id,
        title=str(preview.get("title") or ""),
        document_revision=str(manifest.get("document_revision") or ""),
        impact_hash=str(manifest.get("preview_hash") or ""),
        chunk_ids=tuple(int(value) for value in cleanup_plan.get("chunk_ids") or []),
        node_ids=(),
        chapter_ids=(),
        object_ids=(),
        object_keys=tuple(str(value) for value in cleanup_plan.get("object_keys") or []),
        exclusive_object_keys=tuple(str(value) for value in cleanup_plan.get("exclusive_object_keys") or []),
        shared_object_keys=tuple(str(value) for value in cleanup_plan.get("shared_object_keys") or []),
        passage_source_ids=tuple(str(value) for value in cleanup_plan.get("passage_source_ids") or []),
        derived_files=tuple(_validated_retry_file(value, actual_runtime.data_dir) for value in cleanup_plan.get("derived_files") or []),
        generated_cache_files=tuple(_validated_retry_file(value, actual_runtime.data_dir) for value in cleanup_plan.get("generated_cache_files") or []),
        managed_pdf=(
            _validated_retry_file(cleanup_plan["managed_pdf"], Path(actual_runtime.data_dir) / "pdfs")
            if cleanup_plan.get("managed_pdf")
            else None
        ),
        deletion_options=_normalize_options(manifest.get("deletion_options")),
        row_counts={},
        orphan_baseline=retry_orphan_baseline,
        manual_preservation_acknowledgment=(
            dict(manifest["manual_preservation_acknowledgment"])
            if manifest.get("manual_preservation_acknowledgment")
            else None
        ),
    )
    dry_run = {
        "status": "ready_to_retry" if previous_report.get("result") == "cleanup_incomplete" else "review_required",
        "apply": False,
        "audit_id": audit_id,
        "document_id": document_id,
        "previous_result": previous_report.get("result"),
        "planned_passage_vectors": len(plan.passage_source_ids),
        "planned_object_keys": len(plan.object_keys),
        "planned_files": len(plan.derived_files) + len(plan.generated_cache_files) + (1 if plan.managed_pdf else 0),
    }
    if not apply:
        return dry_run
    cleanup = _run_post_commit_cleanup(plan, runtime=actual_runtime)
    result = "cleanup_incomplete" if cleanup.errors else "completed"
    updated = {
        **previous_report,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "error_code": "deletion_cleanup_incomplete" if cleanup.errors else None,
        "cleanup_retry": {
            "fts": cleanup.fts,
            "vectors": cleanup.vectors,
            "files": cleanup.files,
            "orphan_scan": cleanup.orphan_scan,
            "errors": cleanup.errors,
        },
    }
    _write_audit_result(archive_dir, updated)
    return {
        **dry_run,
        "status": result,
        "apply": True,
        "cleanup": updated["cleanup_retry"],
    }


def _prepare_preview(
    document_id: int,
    *,
    options: dict[str, Any],
    manual_preservation_acknowledgment: dict[str, Any] | None,
    runtime: DeletionRuntime,
) -> PreparedPreview:
    db_path = Path(runtime.db_path)
    if not db_path.is_file():
        raise DeletionError("deletion_database_missing", "资料库数据库不存在。", status_code=503)
    with _readonly_connection(db_path) as connection:
        document = connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if document is None:
            raise DeletionError("deletion_document_not_found", "文档不存在。", status_code=404)
        document_row = dict(document)
        chunks = _rows(connection, "SELECT id, node_id, content_hash, embedding_id FROM knowledge_chunks WHERE document_id = ? ORDER BY id", (document_id,))
        nodes = _rows(connection, "SELECT id FROM markdown_nodes WHERE document_id = ? ORDER BY id", (document_id,))
        chapters = _rows(connection, "SELECT id FROM book_chapters WHERE document_id = ? ORDER BY id", (document_id,))
        objects = _rows(
            connection,
            """
            SELECT id, object_key, user_comment,
                   source_package_path, source_import_manifest_path
            FROM object_candidates
            WHERE document_id = ?
            ORDER BY id
            """,
            (document_id,),
        )
        chunk_ids = tuple(int(row["id"]) for row in chunks)
        node_ids = tuple(int(row["id"]) for row in nodes)
        chapter_ids = tuple(int(row["id"]) for row in chapters)
        object_ids = tuple(int(row["id"]) for row in objects)
        object_keys = tuple(sorted({str(row["object_key"]) for row in objects if row["object_key"]}))
        shared_keys = tuple(
            sorted(
                key
                for key in object_keys
                if _count(connection, "object_candidates", "object_key = ? AND (document_id IS NULL OR document_id <> ?)", (key, document_id))
            )
        )
        exclusive_keys = tuple(sorted(set(object_keys) - set(shared_keys)))
        row_counts = _impact_row_counts(
            connection,
            document_id=document_id,
            chunk_ids=chunk_ids,
            object_ids=object_ids,
        )
        personal_note_count = _personal_note_count(connection, document_id, chunk_ids)
        zotero_note_count = _zotero_note_count(
            connection,
            document_id,
            chunk_ids,
            object_ids,
        )
        review_count = _review_artifact_count(connection, document_id)
        review_delete_count = sum(
            len(rows)
            for rows in _review_artifact_rows(
                connection,
                document_id,
            ).values()
        )
        user_object_comment_count = sum(
            1 for row in objects if str(row["user_comment"] or "").strip()
        )
        user_object_comment_record_ids = tuple(
            int(row["id"])
            for row in objects
            if str(row["user_comment"] or "").strip()
        )
        note_evidence_link_detach_count = (
            _note_evidence_link_detach_count(
                connection,
                document_id=document_id,
                chunk_ids=chunk_ids,
            )
        )
        cross_document_references = _cross_document_reference_count(
            connection,
            document_id=document_id,
            chunk_ids=chunk_ids,
            shared_object_keys=shared_keys,
        )
        unknown_references = _unknown_schema_references(
            connection,
            document_id=document_id,
            chunk_ids=chunk_ids,
        )
        orphan_baseline = _global_orphan_counts(connection)

    pdf_path = _document_pdf_path(document_row)
    pdf_descriptor = _path_descriptor(pdf_path, data_dir=runtime.data_dir, hash_file=True)
    source_descriptor = _path_descriptor(
        _safe_path(document_row.get("source_path")),
        data_dir=runtime.data_dir,
        hash_file=False,
    )
    managed_pdf = pdf_path if pdf_path and _is_within(pdf_path, Path(runtime.data_dir) / "pdfs") else None
    managed_pdf_shared = False
    if managed_pdf is not None:
        with _readonly_connection(db_path) as connection:
            other_paths = [
                _document_pdf_path(dict(row))
                for row in connection.execute("SELECT * FROM documents WHERE id <> ?", (document_id,))
            ]
        managed_pdf_shared = any(path and _same_path(path, managed_pdf) for path in other_paths)

    derived_files, cache_files = _associated_files(
        document_row,
        objects,
        data_dir=Path(runtime.data_dir),
    )
    fts_count, fts_warning = _fts_impact(runtime.fts_path, document_id)
    passage_source_ids = tuple(
        vector_store_service.make_passage_source_id(document_id, chunk_id)
        for chunk_id in chunk_ids
    )
    vector_warning = ""
    vector_impact: dict[str, Any]
    try:
        inspector = runtime.inspect_vectors or vector_store_service.inspect_document_vector_impact
        vector_impact = inspector(
            passage_source_ids=list(passage_source_ids),
            object_keys=list(object_keys),
            store_path=runtime.vector_store_path,
        )
    except Exception as exc:
        vector_impact = {
            "status": "unavailable",
            "passage_vector_count": 0,
            "object_vector_count": 0,
        }
        vector_warning = f"vector_impact_unavailable:{type(exc).__name__}"

    preservation = _validate_manual_preservation_acknowledgment(
        manual_preservation_acknowledgment,
        document_id=document_id,
        blocker_record_ids=user_object_comment_record_ids,
    )
    blockers = _deletion_blockers(
        options=options,
        review_count=review_count,
        user_object_comment_count=user_object_comment_count,
        manual_preservation_acknowledged=preservation is not None,
        cross_document_references=cross_document_references,
        unknown_references=unknown_references,
        vector_warning=vector_warning,
        fts_warning=fts_warning,
        managed_pdf_shared=managed_pdf_shared,
    )
    warnings = [
        value
        for value in (
            "personal_notes_will_be_detached" if personal_note_count else "",
            "zotero_notes_will_be_detached" if zotero_note_count else "",
            (
                "note_evidence_links_will_be_detached"
                if note_evidence_link_detach_count
                else ""
            ),
            (
                "search_review_artifacts_will_be_deleted"
                if review_delete_count
                else ""
            ),
            "shared_objects_will_be_preserved" if shared_keys else "",
            "managed_pdf_preserved_by_default" if managed_pdf else "external_pdf_preserved",
            fts_warning,
            vector_warning,
        )
        if value
    ]
    revision_material = {
        "document": document_row,
        "chunks": [dict(row) for row in chunks],
        "nodes": list(node_ids),
        "chapters": list(chapter_ids),
        "objects": [dict(row) for row in objects],
        "row_counts": row_counts,
        "personal_note_count": personal_note_count,
        "zotero_note_count": zotero_note_count,
        "review_count": review_count,
        "review_delete_count": review_delete_count,
        "note_evidence_link_detach_count": (
            note_evidence_link_detach_count
        ),
        "orphan_baseline": orphan_baseline,
        "database_impact_fingerprint": _database_impact_fingerprint(
            connection_path=db_path,
            document_id=document_id,
            chunk_ids=chunk_ids,
            object_ids=object_ids,
        ),
        "fts_count": fts_count,
        "vector_impact": vector_impact,
        "files": [_file_fingerprint(path) for path in (*derived_files, *cache_files)],
        "pdf": _file_fingerprint(pdf_path) if pdf_path else None,
        "options": options,
    }
    document_revision = _hash_json(revision_material)
    estimated_deleted_rows = (
        sum(row_counts.values())
        + review_delete_count
        + 1
    )
    estimated_detached_rows = (
        personal_note_count
        + zotero_note_count
        + row_counts.get("knowledge_relations", 0)
        + note_evidence_link_detach_count
    )
    archive_root = runtime.resolved_archive_root()
    public = {
        "status": "ok",
        "schema_version": DELETION_SCHEMA_VERSION,
        "read_only": True,
        "document_id": document_id,
        "title": str(document_row.get("title") or ""),
        "document_type": str(document_row.get("document_type") or "unknown"),
        "source": _source_kind(document_row, pdf_descriptor),
        "source_descriptor": source_descriptor,
        "pdf": pdf_descriptor,
        "pdf_hash": pdf_descriptor.get("file_hash") if pdf_descriptor else None,
        "pdf_exists": bool(pdf_descriptor and pdf_descriptor.get("exists")),
        "pdf_is_search_managed": bool(pdf_descriptor and pdf_descriptor.get("scope") == "managed"),
        "chunk_count": len(chunk_ids),
        "chapter_count": len(chapter_ids),
        "node_count": len(node_ids),
        "object_link_count": len(object_ids),
        "shared_object_count": len(shared_keys),
        "exclusive_object_count": len(exclusive_keys),
        "evidence_link_count": row_counts.get("note_evidence_links", 0) + row_counts.get("knowledge_relations", 0) + row_counts.get("inspiration_card_sources", 0),
        "personal_note_count": personal_note_count,
        "zotero_note_count": zotero_note_count,
        "note_evidence_link_detach_count": (
            note_evidence_link_detach_count
        ),
        "search_review_artifact_count": review_delete_count,
        "fts_row_count": fts_count,
        "passage_vector_count": int(vector_impact.get("passage_vector_count") or 0),
        "object_vector_impact_count": int(vector_impact.get("object_vector_count") or 0),
        "manifest_index_impact": {
            "fts_rebuild_required": bool(Path(runtime.fts_path).is_file()),
            "vector_manifest_update_required": bool(Path(runtime.vector_manifest_path).is_file()),
        },
        "derived_markdown_files": [_path_descriptor(path, data_dir=runtime.data_dir, hash_file=True) for path in derived_files],
        "generated_cache": [_path_descriptor(path, data_dir=runtime.data_dir, hash_file=True) for path in cache_files],
        "deletion_blockers": blockers,
        "orphan_baseline": orphan_baseline,
        "manual_preservation_acknowledgment": preservation,
        "warnings": warnings,
        "estimated_deleted_rows": estimated_deleted_rows,
        "estimated_detached_rows": estimated_detached_rows,
        "estimated_deleted_files": len(derived_files) + len(cache_files) + (1 if managed_pdf and options["delete_managed_pdf"] else 0),
        "whether_safe_to_delete": not blockers,
        "document_revision": document_revision,
        "deletion_options": options,
        "retention": {
            "external_pdf": "always_preserved",
            "managed_pdf": "delete" if options["delete_managed_pdf"] else "preserve",
            "personal_notes": "preserve_and_detach",
            "zotero_data": "preserve_and_detach",
            "shared_objects": "preserve",
        },
        "recovery_package": {
            "created_before_delete": True,
            "location": _path_descriptor(archive_root, data_dir=runtime.data_dir, hash_file=False),
            "contains_external_pdf": False,
        },
    }
    impact_hash = _stable_impact_hash(public)
    public["preview_hash"] = impact_hash
    plan = InternalDeletionPlan(
        document_id=document_id,
        title=str(document_row.get("title") or ""),
        document_revision=document_revision,
        impact_hash=impact_hash,
        chunk_ids=chunk_ids,
        node_ids=node_ids,
        chapter_ids=chapter_ids,
        object_ids=object_ids,
        object_keys=object_keys,
        exclusive_object_keys=exclusive_keys,
        shared_object_keys=shared_keys,
        passage_source_ids=passage_source_ids,
        derived_files=tuple(derived_files),
        generated_cache_files=tuple(cache_files),
        managed_pdf=managed_pdf,
        deletion_options=options,
        row_counts=row_counts,
        orphan_baseline=orphan_baseline,
        manual_preservation_acknowledgment=manual_preservation_acknowledgment,
    )
    return PreparedPreview(public=public, plan=plan)


def _impact_row_counts(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
    object_ids: tuple[int, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("book_chapters", "document_sources", "knowledge_chunks", "markdown_nodes", "object_candidates", *DERIVED_DOCUMENT_TABLES):
        if not _table_exists(connection, table):
            continue
        column = "document_id"
        counts[table] = _count(connection, table, f"{column} = ?", (document_id,))
    if _table_exists(connection, "library_archive_states"):
        counts["library_archive_states"] = _count(connection, "library_archive_states", "document_id = ?", (document_id,))
    if chunk_ids:
        placeholders = _placeholders(chunk_ids)
        for table, column in (
            ("chunk_tags", "chunk_id"),
            ("knowledge_relations", "evidence_chunk_id"),
        ):
            if _table_exists(connection, table):
                counts[table] = _count(connection, table, f"{column} IN ({placeholders})", chunk_ids)
        if _table_exists(connection, "inspiration_card_sources"):
            counts["inspiration_card_sources"] = _count(
                connection,
                "inspiration_card_sources",
                f"source_doc_id = ? OR source_chunk_id IN ({placeholders})",
                (document_id, *chunk_ids),
            )
    else:
        counts["inspiration_card_sources"] = _count(connection, "inspiration_card_sources", "source_doc_id = ?", (document_id,)) if _table_exists(connection, "inspiration_card_sources") else 0
    if _table_exists(connection, "note_evidence_links"):
        condition, params = _note_evidence_scope(
            connection,
            document_id=document_id,
            chunk_ids=chunk_ids,
        )
        counts["note_evidence_links"] = _count(
            connection,
            "note_evidence_links",
            condition,
            params,
        )
    return counts


def _note_evidence_scope(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
) -> tuple[str, tuple[Any, ...]]:
    has_document_id = (
        "document_id" in _table_columns(connection, "note_evidence_links")
    )
    if chunk_ids:
        chunk_condition = (
            f"chunk_id IN ({_placeholders(chunk_ids)})"
        )
        if has_document_id:
            return (
                f"document_id = ? OR {chunk_condition}",
                (document_id, *chunk_ids),
            )
        return chunk_condition, tuple(chunk_ids)
    if has_document_id:
        return "document_id = ?", (document_id,)
    return "0 = 1", ()


def _note_evidence_link_detach_count(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
) -> int:
    if (
        not _table_exists(connection, "note_evidence_links")
        or "document_id"
        not in _table_columns(connection, "note_evidence_links")
    ):
        return 0
    if not chunk_ids:
        return _count(
            connection,
            "note_evidence_links",
            "document_id = ?",
            (document_id,),
        )
    return _count(
        connection,
        "note_evidence_links",
        "document_id = ? AND "
        f"(chunk_id IS NULL OR chunk_id NOT IN ({_placeholders(chunk_ids)}))",
        (document_id, *chunk_ids),
    )


def _execute_database_transaction(plan: InternalDeletionPlan, *, runtime: DeletionRuntime) -> dict[str, Any]:
    connection = sqlite3.connect(Path(runtime.db_path), timeout=20, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 20000")
    counts: dict[str, int] = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT title FROM documents WHERE id = ?", (plan.document_id,)).fetchone()
        if current is None:
            raise DeletionError("deletion_document_not_found", "文档不存在。", status_code=404)
        locked_preview = _prepare_preview(
            plan.document_id,
            options=plan.deletion_options,
            manual_preservation_acknowledgment=(
                plan.manual_preservation_acknowledgment
            ),
            runtime=runtime,
        )
        if (
            locked_preview.plan.document_revision != plan.document_revision
            or locked_preview.plan.impact_hash != plan.impact_hash
        ):
            raise DeletionError(
                "deletion_preview_stale_after_write_lock",
                "取得数据库写锁后发现删除影响已变化，事务已回滚。",
            )

        counts.update(
            _delete_review_artifacts(
                connection,
                plan.document_id,
            )
        )

        if plan.chunk_ids:
            chunk_placeholders = _placeholders(plan.chunk_ids)
            counts["personal_notes_detached"] = _execute_count(
                connection,
                "UPDATE personal_notes SET document_id = NULL WHERE document_id = ?",
                (plan.document_id,),
            ) if _table_exists(connection, "personal_notes") else 0
            counts["note_evidence_links_deleted"] = _execute_count(
                connection,
                f"DELETE FROM note_evidence_links WHERE chunk_id IN ({chunk_placeholders})",
                plan.chunk_ids,
            ) if _table_exists(connection, "note_evidence_links") else 0
            counts["knowledge_relations_detached"] = _execute_count(
                connection,
                f"UPDATE knowledge_relations SET evidence_chunk_id = NULL WHERE evidence_chunk_id IN ({chunk_placeholders})",
                plan.chunk_ids,
            ) if _table_exists(connection, "knowledge_relations") else 0
            counts["inspiration_card_sources_deleted"] = _execute_count(
                connection,
                f"DELETE FROM inspiration_card_sources WHERE source_doc_id = ? OR source_chunk_id IN ({chunk_placeholders})",
                (plan.document_id, *plan.chunk_ids),
            ) if _table_exists(connection, "inspiration_card_sources") else 0
            counts["chunk_tags_deleted"] = _execute_count(
                connection,
                f"DELETE FROM chunk_tags WHERE chunk_id IN ({chunk_placeholders})",
                plan.chunk_ids,
            ) if _table_exists(connection, "chunk_tags") else 0
        else:
            counts["personal_notes_detached"] = _execute_count(
                connection,
                "UPDATE personal_notes SET document_id = NULL WHERE document_id = ?",
                (plan.document_id,),
            ) if _table_exists(connection, "personal_notes") else 0
            counts["inspiration_card_sources_deleted"] = _execute_count(
                connection,
                "DELETE FROM inspiration_card_sources WHERE source_doc_id = ?",
                (plan.document_id,),
            ) if _table_exists(connection, "inspiration_card_sources") else 0

        counts["note_evidence_links_detached"] = (
            _execute_count(
                connection,
                "UPDATE note_evidence_links SET document_id = NULL "
                "WHERE document_id = ?",
                (plan.document_id,),
            )
            if (
                _table_exists(connection, "note_evidence_links")
                and "document_id"
                in _table_columns(connection, "note_evidence_links")
            )
            else 0
        )
        counts["zotero_notes_detached"] = _detach_zotero_notes(connection, plan)
        counts["mechanism_drafts_detached"] = _detach_mechanism_drafts(connection, plan)
        for table in DERIVED_DOCUMENT_TABLES:
            if _table_exists(connection, table):
                counts[f"{table}_deleted"] = _execute_count(
                    connection,
                    f"DELETE FROM {table} WHERE document_id = ?",
                    (plan.document_id,),
                )
        for table in ("document_sources", "object_candidates", "book_chapters"):
            if _table_exists(connection, table):
                counts[f"{table}_deleted"] = _execute_count(
                    connection,
                    f"DELETE FROM {table} WHERE document_id = ?",
                    (plan.document_id,),
                )
        if _table_exists(connection, "library_archive_states"):
            counts["library_archive_states_deleted"] = _execute_count(
                connection,
                "DELETE FROM library_archive_states WHERE document_id = ?",
                (plan.document_id,),
            )
        counts["knowledge_chunks_deleted"] = _execute_count(
            connection,
            "DELETE FROM knowledge_chunks WHERE document_id = ?",
            (plan.document_id,),
        )
        counts["markdown_nodes_deleted"] = _execute_count(
            connection,
            "DELETE FROM markdown_nodes WHERE document_id = ?",
            (plan.document_id,),
        )
        counts["documents_deleted"] = _execute_count(
            connection,
            "DELETE FROM documents WHERE id = ?",
            (plan.document_id,),
        )
        foreign_key_issues = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if foreign_key_issues:
            raise DeletionError(
                "deletion_foreign_key_check_failed",
                "删除事务产生外键不一致，已回滚。",
                details={"issue_count": len(foreign_key_issues)},
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "status": "committed",
        "deleted_rows": sum(value for key, value in counts.items() if key.endswith("_deleted")),
        "detached_rows": sum(value for key, value in counts.items() if key.endswith("_detached")),
        "counts": counts,
    }


def _run_post_commit_cleanup(plan: InternalDeletionPlan, *, runtime: DeletionRuntime) -> CleanupState:
    state = CleanupState()
    try:
        if Path(runtime.fts_path).is_file():
            cleaner = runtime.cleanup_fts or cleanup_document_retrieval_fts
            state.fts = cleaner(
                document_id=plan.document_id,
                index_path=runtime.fts_path,
                manifest_path=runtime.fts_manifest_path,
                production_db_path=runtime.db_path,
            )
        else:
            state.fts = {"status": "not_present", "fragment_count": 0}
    except Exception as exc:
        state.errors.append(_cleanup_error("fts_cleanup_failed", exc))
        state.fts = {"status": "failed", "error_code": "fts_cleanup_failed"}
    try:
        cleaner = runtime.cleanup_vectors or vector_store_service.cleanup_document_vectors
        state.vectors = cleaner(
            passage_source_ids=list(plan.passage_source_ids),
            affected_object_keys=list(plan.object_keys),
            store_path=runtime.vector_store_path,
            manifest_path=runtime.vector_manifest_path,
        )
    except Exception as exc:
        state.errors.append(_cleanup_error("vector_cleanup_failed", exc))
        state.vectors = {"status": "failed", "error_code": "vector_cleanup_failed"}
    try:
        state.note_vectors = _sync_note_vectors_after_document_delete(
            plan,
            runtime=runtime,
        )
    except Exception as exc:
        state.errors.append(_cleanup_error("note_vector_cleanup_failed", exc))
        state.note_vectors = {
            "status": "failed",
            "error_code": "note_vector_cleanup_failed",
        }
    try:
        state.files = _cleanup_files(plan)
    except Exception as exc:
        state.errors.append(_cleanup_error("file_cleanup_failed", exc))
        state.files = {"status": "failed", "error_code": "file_cleanup_failed"}
    try:
        preserved_object_source_ids = (
            state.vectors.pop("preserved_object_source_ids", None)
            if isinstance(state.vectors, dict)
            else None
        )
        state.orphan_scan = _orphan_scan(
            plan,
            runtime=runtime,
            preserved_object_source_ids=preserved_object_source_ids,
        )
        if not state.orphan_scan.get("ok"):
            state.errors.append(
                {
                    "error_code": "deletion_orphan_scan_failed",
                    "exception_type": "ConsistencyError",
                }
            )
    except Exception as exc:
        state.errors.append(_cleanup_error("deletion_orphan_scan_failed", exc))
        state.orphan_scan = {"ok": False, "error_code": "deletion_orphan_scan_failed"}
    return state


def _sync_note_vectors_after_document_delete(
    plan: InternalDeletionPlan,
    *,
    runtime: DeletionRuntime,
) -> dict[str, Any]:
    data_dir = Path(runtime.data_dir).resolve(strict=False)
    index_dir = data_dir / "vector_store" / "zotero_user_notes_v1"
    manifest_path = index_dir / note_vector_index.MANIFEST_NAME

    if not manifest_path.is_file():
        return {
            "status": "not_present",
            "document_id": plan.document_id,
            "removed_document_entries": 0,
            "stale_document_entries": 0,
            "vector_write_performed": False,
        }

    before = note_vector_index.inspect_zotero_note_vector_document_impact(
        plan.document_id,
        index_dir=index_dir,
    )

    registry = RetrievalSourceRegistry(
        research_db_path=runtime.db_path,
        zotero_snapshot_path=data_dir / "zotero" / "snapshot" / "zotero.sqlite",
        notes_root=data_dir / "notes",
        project_root=data_dir.parent,
    )
    affected_fragment_ids = set(
        before.get("fragment_ids") or []
    )

    fragments = [
        fragment
        for fragment in fragment_repository.list_notebook_fragments(
            source_types=NOTE_SOURCE_TYPES,
            registry=registry,
        )
        if fragment.fragment_id in affected_fragment_ids
    ]

    result = (
        note_vector_index
        .refresh_zotero_note_vector_document_scope(
            plan.document_id,
            index_dir=index_dir,
            fragments=fragments,
        )
    )

    after = note_vector_index.inspect_zotero_note_vector_document_impact(
        plan.document_id,
        index_dir=index_dir,
    )
    stale = int(after.get("document_entry_count") or 0)
    if stale:
        raise RuntimeError(
            f"note vector index still contains {stale} entries for deleted document"
        )

    return {
        **result,
        "document_id": plan.document_id,
        "removed_document_entries": max(
            0,
            int(before.get("document_entry_count") or 0) - stale,
        ),
        "stale_document_entries": stale,
    }


def _cleanup_files(plan: InternalDeletionPlan) -> dict[str, Any]:
    targets: list[Path] = []
    if plan.deletion_options["delete_generated_markdown"]:
        targets.extend(plan.derived_files)
    if plan.deletion_options["delete_generated_cache"]:
        targets.extend(plan.generated_cache_files)
    if plan.deletion_options["delete_managed_pdf"] and plan.managed_pdf is not None:
        targets.append(plan.managed_pdf)
    removed = 0
    missing = 0
    for path in sorted(set(targets), key=lambda item: str(item).lower()):
        if not path.exists():
            missing += 1
            continue
        if not path.is_file():
            raise DeletionError("deletion_file_target_invalid", "派生文件目标不是普通文件。")
        path.unlink()
        removed += 1
    return {"status": "ok", "removed_files": removed, "already_missing_files": missing}


def _orphan_scan(
    plan: InternalDeletionPlan,
    *,
    runtime: DeletionRuntime,
    preserved_object_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    with _readonly_connection(runtime.db_path) as connection:
        global_orphans = _global_orphan_counts(connection)
        checks = {
            "document_rows": _count(connection, "documents", "id = ?", (plan.document_id,)),
            "dangling_personal_notes": _count(connection, "personal_notes", "document_id = ?", (plan.document_id,)) if _table_exists(connection, "personal_notes") else 0,
            "dangling_zotero_notes": _dangling_zotero_note_count(connection, plan),
            "dangling_review_artifacts": sum(
                len(rows)
                for rows in _review_artifact_rows(
                    connection,
                    plan.document_id,
                ).values()
            ),
        }
    fts_remaining = 0
    if Path(runtime.fts_path).is_file():
        with _readonly_connection(runtime.fts_path) as fts:
            fts_remaining = _count(fts, "retrieval_fragments", "document_id = ?", (plan.document_id,))
    vector_orphans = 0
    preserved_object_ids = {
        str(value)
        for value in (preserved_object_source_ids or [])
        if str(value or "").strip()
    }
    orphan_check_object_keys = [
        key
        for key in plan.exclusive_object_keys
        if vector_store_service.make_object_source_id(key)
        not in preserved_object_ids
    ]
    try:
        inspector = runtime.inspect_vectors or vector_store_service.inspect_document_vector_impact
        impact = inspector(
            passage_source_ids=list(plan.passage_source_ids),
            object_keys=orphan_check_object_keys,
            store_path=runtime.vector_store_path,
        )
        vector_orphans = int(impact.get("passage_vector_count") or 0) + int(impact.get("object_vector_count") or 0)
    except Exception:
        vector_orphans = -1

    note_vector_orphans = 0
    try:
        note_vector_impact = (
            note_vector_index.inspect_zotero_note_vector_document_impact(
                plan.document_id,
                index_dir=(
                    Path(runtime.data_dir).resolve(strict=False)
                    / "vector_store"
                    / "zotero_user_notes_v1"
                ),
            )
        )
        note_vector_orphans = int(
            note_vector_impact.get("document_entry_count") or 0
        )
    except Exception:
        note_vector_orphans = -1

    checks.update(
        {
            **global_orphans,
            "fts_rows": fts_remaining,
            "orphan_vectors": vector_orphans,
            "orphan_note_vectors": note_vector_orphans,
            "preserved_object_vectors": len(preserved_object_ids),
        }
    )
    new_global_orphans = {
        f"new_{key}": max(
            0,
            int(value) - int(plan.orphan_baseline.get(key, 0)),
        )
        for key, value in global_orphans.items()
    }
    target_checks = {
        key: value
        for key, value in checks.items()
        if key not in global_orphans and key != "preserved_object_vectors"
    }
    return {
        "ok": (
            all(value == 0 for value in target_checks.values())
            and all(value == 0 for value in new_global_orphans.values())
        ),
        **checks,
        "orphan_baseline": dict(plan.orphan_baseline),
        **new_global_orphans,
    }


def _global_orphan_counts(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    return {
        "orphan_chunks": _scalar(
            connection,
            "SELECT COUNT(*) FROM knowledge_chunks AS child "
            "LEFT JOIN documents AS parent ON parent.id = child.document_id "
            "WHERE parent.id IS NULL",
        ),
        "orphan_nodes": _scalar(
            connection,
            "SELECT COUNT(*) FROM markdown_nodes AS child "
            "LEFT JOIN documents AS parent ON parent.id = child.document_id "
            "WHERE parent.id IS NULL",
        ),
        "orphan_evidence_links": (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM note_evidence_links AS child "
                "LEFT JOIN knowledge_chunks AS parent "
                "ON parent.id = child.chunk_id WHERE parent.id IS NULL",
            )
            if _table_exists(connection, "note_evidence_links")
            else 0
        ),
        "orphan_document_object_links": (
            _scalar(
                connection,
                "SELECT COUNT(*) FROM object_candidates AS child "
                "LEFT JOIN documents AS parent "
                "ON parent.id = child.document_id "
                "WHERE child.document_id IS NOT NULL AND parent.id IS NULL",
            )
            if _table_exists(connection, "object_candidates")
            else 0
        ),
    }


def _create_recovery_package(
    *,
    audit_id: str,
    plan: InternalDeletionPlan,
    runtime: DeletionRuntime,
    preview: dict[str, Any],
) -> Path:
    root = runtime.resolved_archive_root().resolve()
    data_dir = Path(runtime.data_dir).resolve()
    if root == data_dir or root.is_relative_to(data_dir):
        raise DeletionError("deletion_archive_inside_data_root", "恢复包必须位于 canonical 数据根之外。")
    archive_dir = root / audit_id
    if archive_dir.exists():
        raise DeletionError("deletion_archive_collision", "恢复包 ID 冲突，请重试。")
    try:
        archive_dir.mkdir(parents=True, exist_ok=False)
        with _readonly_connection(runtime.db_path) as connection:
            rows = _collect_recovery_rows(connection, plan)
        _write_json(
            archive_dir / "database_rows.json",
            {
                "schema_version": DELETION_SCHEMA_VERSION,
                "audit_id": audit_id,
                "document_id": plan.document_id,
                "tables": rows,
            },
        )
        _write_json(
            archive_dir / "recovery_manifest.json",
            {
                "schema_version": DELETION_SCHEMA_VERSION,
                "audit_id": audit_id,
                "document_id": plan.document_id,
                "document_revision": plan.document_revision,
                "preview_hash": plan.impact_hash,
                "deletion_options": plan.deletion_options,
                "manual_preservation_acknowledgment": (
                    plan.manual_preservation_acknowledgment
                ),
                "file_hashes": [
                    _file_fingerprint(path)
                    for path in (*plan.derived_files, *plan.generated_cache_files, plan.managed_pdf)
                    if path is not None
                ],
                "external_pdf_copied": False,
                "cleanup_plan": {
                    "chunk_ids": list(plan.chunk_ids),
                    "object_keys": list(plan.object_keys),
                    "exclusive_object_keys": list(plan.exclusive_object_keys),
                    "shared_object_keys": list(plan.shared_object_keys),
                    "passage_source_ids": list(plan.passage_source_ids),
                    "derived_files": [str(path) for path in plan.derived_files],
                    "generated_cache_files": [str(path) for path in plan.generated_cache_files],
                    "managed_pdf": str(plan.managed_pdf) if plan.managed_pdf is not None else None,
                    "orphan_baseline": plan.orphan_baseline,
                },
                "preview": {key: value for key, value in preview.items() if key != "preview_token"},
            },
        )
        for required in ("database_rows.json", "recovery_manifest.json"):
            with (archive_dir / required).open("r", encoding="utf-8") as handle:
                json.load(handle)
    except Exception as exc:
        raise DeletionError(
            "deletion_recovery_package_failed",
            "恢复包创建或校验失败，删除已停止。",
            details={"exception_type": type(exc).__name__},
        ) from exc
    return archive_dir


def _collect_recovery_rows(connection: sqlite3.Connection, plan: InternalDeletionPlan) -> dict[str, list[dict[str, Any]]]:
    collected: dict[str, list[dict[str, Any]]] = {}
    direct_tables = ("documents", "book_chapters", "document_sources", "knowledge_chunks", "markdown_nodes", "object_candidates", *DERIVED_DOCUMENT_TABLES)
    for table in direct_tables:
        if not _table_exists(connection, table):
            continue
        column = "id" if table == "documents" else "document_id"
        collected[table] = _rows(connection, f"SELECT * FROM {table} WHERE {column} = ?", (plan.document_id,))
    if _table_exists(connection, "library_archive_states"):
        collected["library_archive_states"] = _rows(connection, "SELECT * FROM library_archive_states WHERE document_id = ?", (plan.document_id,))
    if plan.chunk_ids:
        placeholders = _placeholders(plan.chunk_ids)
        for table, condition in (
            ("chunk_tags", f"chunk_id IN ({placeholders})"),
            ("knowledge_relations", f"evidence_chunk_id IN ({placeholders})"),
        ):
            if _table_exists(connection, table):
                collected[table] = _rows(connection, f"SELECT * FROM {table} WHERE {condition}", plan.chunk_ids)
        if _table_exists(connection, "inspiration_card_sources"):
            collected["inspiration_card_sources"] = _rows(
                connection,
                f"SELECT * FROM inspiration_card_sources WHERE source_doc_id = ? OR source_chunk_id IN ({placeholders})",
                (plan.document_id, *plan.chunk_ids),
            )
    if _table_exists(connection, "note_evidence_links"):
        condition, params = _note_evidence_scope(
            connection,
            document_id=plan.document_id,
            chunk_ids=plan.chunk_ids,
        )
        collected["note_evidence_links"] = _rows(
            connection,
            f"SELECT * FROM note_evidence_links WHERE {condition}",
            params,
        )
    if _table_exists(connection, "personal_notes"):
        collected["personal_notes_detached"] = _rows(
            connection,
            "SELECT * FROM personal_notes WHERE document_id = ?",
            (plan.document_id,),
        )
    if _table_exists(connection, "zotero_inspiration_notes"):
        collected["zotero_inspiration_notes_detached"] = [
            dict(row)
            for row in connection.execute("SELECT * FROM zotero_inspiration_notes")
            if _zotero_row_matches_plan(row, plan)
        ]
    collected.update(
        _review_artifact_rows(
            connection,
            plan.document_id,
        )
    )
    if _table_exists(connection, "mechanism_draft_candidates"):
        collected["mechanism_draft_candidates_detached"] = _rows(
            connection,
            "SELECT * FROM mechanism_draft_candidates WHERE matched_document_id = ?",
            (plan.document_id,),
        )
    return collected


def _audit_payload(
    *,
    audit_id: str,
    plan: InternalDeletionPlan,
    result: str,
    error_code: str,
    db_result: dict[str, Any],
    cleanup: CleanupState,
) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_id": audit_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "document_id": plan.document_id,
        "title_hash": _sha256_text(plan.title)[:16],
        "preview_hash": plan.impact_hash,
        "deletion_options": plan.deletion_options,
        "manual_preservation_acknowledgment_fingerprint": (
            _hash_acknowledgment(
                plan.manual_preservation_acknowledgment
            )
            if plan.manual_preservation_acknowledgment
            else None
        ),
        "database_deleted_rows": int(db_result.get("deleted_rows") or 0),
        "database_detached_rows": int(db_result.get("detached_rows") or 0),
        "vector_deleted_rows": int(cleanup.vectors.get("deleted_passage_vectors") or 0) + int(cleanup.vectors.get("deleted_object_vectors") or 0),
        "note_vector_removed_document_entries": int(
            cleanup.note_vectors.get("removed_document_entries") or 0
        ),
        "note_vector_metadata_updated_entries": int(
            cleanup.note_vectors.get("metadata_updated_count") or 0
        ),
        "files_removed": int(cleanup.files.get("removed_files") or 0),
        "preserved_notes": int(db_result.get("counts", {}).get("personal_notes_detached") or 0) + int(db_result.get("counts", {}).get("zotero_notes_detached") or 0),
        "preserved_pdfs": 0 if plan.deletion_options.get("delete_managed_pdf") else 1,
        "result": result,
        "error_code": error_code or None,
        "cleanup_errors": cleanup.errors,
    }


def _write_audit_result(archive_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(archive_dir / "deletion_report.json", payload)


def _archive_summary(path: Path) -> dict[str, Any]:
    return {
        "basename": path.name,
        "scope": "external_archive",
        "path_hash": _sha256_text(str(path.resolve()).lower()),
        "exists": path.is_dir(),
    }


def _remediation(errors: list[dict[str, str]], *, audit_id: str) -> list[dict[str, str]]:
    if not errors:
        return []
    return [
        {
            "error_code": item["error_code"],
            "action": "使用恢复包中的 deletion_report.json 重新检查派生索引；不要重新删除文档。",
            "audit_id": audit_id,
        }
        for item in errors
    ]


def _deletion_blockers(
    *,
    options: dict[str, Any],
    review_count: int,
    user_object_comment_count: int,
    cross_document_references: int,
    unknown_references: list[str],
    vector_warning: str,
    fts_warning: str,
    managed_pdf_shared: bool,
    manual_preservation_acknowledged: bool = False,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    required_true = (
        "preserve_external_pdf",
        "preserve_personal_notes",
        "preserve_zotero_notes",
        "preserve_shared_objects",
    )
    if any(not options[name] for name in required_true):
        blockers.append({"code": "protected_data_retention_required", "count": 1})
    # Search-owned review artifacts are captured in the recovery
    # package and deleted transactionally after explicit confirmation.
    if user_object_comment_count and not manual_preservation_acknowledged:
        blockers.append({"code": "object_user_comment_requires_manual_preservation", "count": user_object_comment_count})
    if cross_document_references:
        blockers.append({"code": "cross_document_reference_requires_review", "count": cross_document_references})
    if unknown_references:
        blockers.append({"code": "unknown_schema_reference", "tables": unknown_references})
    if vector_warning:
        blockers.append({"code": "vector_impact_unavailable", "count": 1})
    if fts_warning.startswith("fts_impact_unavailable"):
        blockers.append({"code": "fts_impact_unavailable", "count": 1})
    if managed_pdf_shared and options["delete_managed_pdf"]:
        blockers.append({"code": "managed_pdf_is_shared", "count": 1})
    return blockers


def _review_artifact_rows(
    connection: sqlite3.Connection,
    document_id: int,
) -> dict[str, list[dict[str, Any]]]:
    collected: dict[str, list[dict[str, Any]]] = {}

    for table in REVIEW_TABLES:
        if (
            _table_exists(connection, table)
            and "document_id" in _table_columns(connection, table)
        ):
            collected[table] = _rows(
                connection,
                f"SELECT * FROM {table} "
                "WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            )

    for child_table, parent_table in NOTE_REVIEW_CHILD_TABLES:
        if (
            not _table_exists(connection, child_table)
            or parent_table not in collected
        ):
            continue

        review_ids = tuple(
            row["review_id"]
            for row in collected[parent_table]
            if row.get("review_id") is not None
        )

        if not review_ids:
            collected[child_table] = []
            continue

        placeholders = _placeholders(review_ids)
        collected[child_table] = _rows(
            connection,
            f"SELECT * FROM {child_table} "
            f"WHERE review_id IN ({placeholders}) "
            "ORDER BY rowid",
            review_ids,
        )

    return collected


def _delete_review_artifacts(
    connection: sqlite3.Connection,
    document_id: int,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for child_table, parent_table in NOTE_REVIEW_CHILD_TABLES:
        if (
            not _table_exists(connection, child_table)
            or not _table_exists(connection, parent_table)
        ):
            counts[f"{child_table}_deleted"] = 0
            continue

        counts[f"{child_table}_deleted"] = _execute_count(
            connection,
            f"DELETE FROM {child_table} "
            "WHERE review_id IN ("
            f"SELECT review_id FROM {parent_table} "
            "WHERE document_id = ?"
            ")",
            (document_id,),
        )

    for table in (
        "object_candidate_human_review_items",
        "object_candidate_draft_review_items",
        "object_candidate_human_reviews",
        "object_candidate_draft_reviews",
        "note_correction_reviews",
        "note_classification_reviews",
    ):
        if (
            not _table_exists(connection, table)
            or "document_id" not in _table_columns(connection, table)
        ):
            counts[f"{table}_deleted"] = 0
            continue

        counts[f"{table}_deleted"] = _execute_count(
            connection,
            f"DELETE FROM {table} WHERE document_id = ?",
            (document_id,),
        )

    return counts


def _review_artifact_count(connection: sqlite3.Connection, document_id: int) -> int:
    total = 0
    for table in REVIEW_TABLES:
        if _table_exists(connection, table) and "document_id" in _table_columns(connection, table):
            total += _count(connection, table, "document_id = ?", (document_id,))
    if _table_exists(connection, "mechanism_draft_candidates"):
        total += _count(connection, "mechanism_draft_candidates", "matched_document_id = ?", (document_id,))
    return total


def _personal_note_count(connection: sqlite3.Connection, document_id: int, chunk_ids: tuple[int, ...]) -> int:
    if not _table_exists(connection, "personal_notes"):
        return 0
    note_ids = {
        int(row[0])
        for row in connection.execute("SELECT id FROM personal_notes WHERE document_id = ?", (document_id,))
    }
    if chunk_ids and _table_exists(connection, "note_evidence_links"):
        placeholders = _placeholders(chunk_ids)
        note_ids.update(
            int(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT note_id FROM note_evidence_links WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
        )
    return len(note_ids)


def _zotero_note_count(
    connection: sqlite3.Connection,
    document_id: int,
    chunk_ids: tuple[int, ...],
    object_ids: tuple[int, ...],
) -> int:
    if not _table_exists(connection, "zotero_inspiration_notes"):
        return 0
    count = 0
    chunk_set = set(chunk_ids)
    object_set = set(object_ids)
    for row in connection.execute(
        "SELECT matched_document_id, matched_chunk_id, matched_chunk_ids_json, matched_object_ids_json "
        "FROM zotero_inspiration_notes"
    ):
        ids = set(_json_int_list(row["matched_chunk_ids_json"]))
        matched_objects = set(_json_int_list(row["matched_object_ids_json"]))
        if (
            row["matched_document_id"] == document_id
            or row["matched_chunk_id"] in chunk_set
            or ids.intersection(chunk_set)
            or matched_objects.intersection(object_set)
        ):
            count += 1
    return count


def _database_impact_fingerprint(
    *,
    connection_path: Path,
    document_id: int,
    chunk_ids: tuple[int, ...],
    object_ids: tuple[int, ...],
) -> str:
    with _readonly_connection(connection_path) as connection:
        payload: dict[str, Any] = {}
        for table in (
            "documents",
            "book_chapters",
            "document_sources",
            "knowledge_chunks",
            "markdown_nodes",
            "object_candidates",
            *DERIVED_DOCUMENT_TABLES,
        ):
            if not _table_exists(connection, table):
                continue
            column = "id" if table == "documents" else "document_id"
            payload[table] = _rows(
                connection,
                f"SELECT * FROM {table} WHERE {column} = ?",
                (document_id,),
            )
        if _table_exists(connection, "library_archive_states"):
            payload["library_archive_states"] = _rows(
                connection,
                "SELECT * FROM library_archive_states WHERE document_id = ?",
                (document_id,),
            )
        if chunk_ids:
            placeholders = _placeholders(chunk_ids)
            for table, condition in (
                ("chunk_tags", f"chunk_id IN ({placeholders})"),
                ("knowledge_relations", f"evidence_chunk_id IN ({placeholders})"),
                (
                    "inspiration_card_sources",
                    f"source_doc_id = ? OR source_chunk_id IN ({placeholders})",
                ),
            ):
                if not _table_exists(connection, table):
                    continue
                params: tuple[Any, ...] = (
                    (document_id, *chunk_ids)
                    if table == "inspiration_card_sources"
                    else tuple(chunk_ids)
                )
                payload[table] = _rows(
                    connection,
                    f"SELECT * FROM {table} WHERE {condition}",
                    params,
                )
        elif _table_exists(connection, "inspiration_card_sources"):
            payload["inspiration_card_sources"] = _rows(
                connection,
                "SELECT * FROM inspiration_card_sources WHERE source_doc_id = ?",
                (document_id,),
            )
        if _table_exists(connection, "note_evidence_links"):
            condition, params = _note_evidence_scope(
                connection,
                document_id=document_id,
                chunk_ids=chunk_ids,
            )
            payload["note_evidence_links"] = _rows(
                connection,
                f"SELECT * FROM note_evidence_links WHERE {condition}",
                params,
            )
        if _table_exists(connection, "personal_notes"):
            payload["personal_notes"] = _rows(
                connection,
                "SELECT * FROM personal_notes WHERE document_id = ?",
                (document_id,),
            )
        if _table_exists(connection, "zotero_inspiration_notes"):
            payload["zotero_inspiration_notes"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM zotero_inspiration_notes")
                if _zotero_row_matches_scope(
                    row,
                    document_id=document_id,
                    chunk_ids=chunk_ids,
                    object_ids=object_ids,
                )
            ]
        if _table_exists(connection, "mechanism_draft_candidates"):
            payload["mechanism_draft_candidates"] = _rows(
                connection,
                "SELECT * FROM mechanism_draft_candidates WHERE matched_document_id = ?",
                (document_id,),
            )
        payload.update(
            _review_artifact_rows(
                connection,
                document_id,
            )
        )
    return _hash_json(payload)


def _cross_document_reference_count(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
    shared_object_keys: tuple[str, ...],
) -> int:
    if not chunk_ids or not shared_object_keys:
        return 0
    chunk_set = set(chunk_ids)
    count = 0
    placeholders = _placeholders(shared_object_keys)
    rows = connection.execute(
        f"SELECT evidence_refs_json, mapped_chunk_ids_json FROM object_candidates WHERE object_key IN ({placeholders}) AND (document_id IS NULL OR document_id <> ?)",
        (*shared_object_keys, document_id),
    )
    for row in rows:
        mapped = set(_json_int_list(row["mapped_chunk_ids_json"]))
        evidence = {
            int(item.get("chunk_id"))
            for item in _json_list(row["evidence_refs_json"])
            if isinstance(item, dict) and str(item.get("chunk_id") or "").isdigit()
        }
        if chunk_set.intersection(mapped | evidence):
            count += 1
    return count


def _unknown_schema_references(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
) -> list[str]:
    unknown: list[str] = []
    tables = [
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ]
    for table in tables:
        if table in KNOWN_REFERENCE_TABLES:
            continue
        columns = set(_table_columns(connection, table))
        relevant = columns.intersection(RELEVANT_REFERENCE_COLUMNS)
        for column in relevant:
            if column in {"document_id", "matched_document_id", "source_doc_id"}:
                if _count(connection, table, f"{column} = ?", (document_id,)):
                    unknown.append(table)
                    break
            elif chunk_ids:
                if _count(connection, table, f"{column} IN ({_placeholders(chunk_ids)})", chunk_ids):
                    unknown.append(table)
                    break
    return sorted(set(unknown))


def _detach_zotero_notes(connection: sqlite3.Connection, plan: InternalDeletionPlan) -> int:
    if not _table_exists(connection, "zotero_inspiration_notes"):
        return 0
    chunk_set = set(plan.chunk_ids)
    object_set = set(plan.object_ids)
    rows = connection.execute(
        "SELECT id, matched_document_id, matched_chunk_id, matched_chunk_ids_json, matched_object_ids_json FROM zotero_inspiration_notes"
    ).fetchall()
    changed = 0
    for row in rows:
        chunk_ids = _json_int_list(row["matched_chunk_ids_json"])
        object_ids = _json_int_list(row["matched_object_ids_json"])
        matched = _zotero_row_matches_plan(row, plan)
        if not matched:
            continue
        connection.execute(
            """
            UPDATE zotero_inspiration_notes
            SET matched_document_id = CASE WHEN matched_document_id = ? THEN NULL ELSE matched_document_id END,
                matched_chunk_id = CASE WHEN matched_chunk_id IN ({chunk_placeholders}) THEN NULL ELSE matched_chunk_id END,
                matched_chunk_ids_json = ?,
                matched_object_ids_json = ?,
                match_status = 'detached_document_deleted',
                evidence_alignment_status = 'detached_document_deleted'
            WHERE id = ?
            """.format(chunk_placeholders=_placeholders(plan.chunk_ids) if plan.chunk_ids else "NULL"),
            (
                plan.document_id,
                *plan.chunk_ids,
                json.dumps([value for value in chunk_ids if value not in chunk_set]),
                json.dumps([value for value in object_ids if value not in object_set]),
                row["id"],
            ),
        )
        changed += 1
    return changed


def _zotero_row_matches_plan(row: Any, plan: InternalDeletionPlan) -> bool:
    return _zotero_row_matches_scope(
        row,
        document_id=plan.document_id,
        chunk_ids=plan.chunk_ids,
        object_ids=plan.object_ids,
    )


def _zotero_row_matches_scope(
    row: Any,
    *,
    document_id: int,
    chunk_ids: tuple[int, ...],
    object_ids: tuple[int, ...],
) -> bool:
    chunk_set = set(chunk_ids)
    object_set = set(object_ids)
    chunk_ids = set(_json_int_list(row["matched_chunk_ids_json"]))
    object_ids = set(_json_int_list(row["matched_object_ids_json"]))
    return (
        row["matched_document_id"] == document_id
        or row["matched_chunk_id"] in chunk_set
        or bool(chunk_set.intersection(chunk_ids))
        or bool(object_set.intersection(object_ids))
    )


def _dangling_zotero_note_count(connection: sqlite3.Connection, plan: InternalDeletionPlan) -> int:
    if not _table_exists(connection, "zotero_inspiration_notes"):
        return 0
    return sum(
        1
        for row in connection.execute(
            "SELECT matched_document_id, matched_chunk_id, matched_chunk_ids_json, matched_object_ids_json "
            "FROM zotero_inspiration_notes"
        )
        if _zotero_row_matches_plan(row, plan)
    )


def _detach_mechanism_drafts(connection: sqlite3.Connection, plan: InternalDeletionPlan) -> int:
    if not _table_exists(connection, "mechanism_draft_candidates"):
        return 0
    rows = connection.execute(
        "SELECT id, evidence_chunk_ids_json FROM mechanism_draft_candidates WHERE matched_document_id = ?",
        (plan.document_id,),
    ).fetchall()
    for row in rows:
        remaining = [value for value in _json_int_list(row["evidence_chunk_ids_json"]) if value not in set(plan.chunk_ids)]
        connection.execute(
            "UPDATE mechanism_draft_candidates SET matched_document_id = NULL, evidence_chunk_ids_json = ? WHERE id = ?",
            (json.dumps(remaining), row["id"]),
        )
    return len(rows)


def _associated_files(document: dict[str, Any], objects: list[dict[str, Any]], *, data_dir: Path) -> tuple[list[Path], list[Path]]:
    derived: list[Path] = []
    cache: list[Path] = []
    source = _safe_path(document.get("source_path"))
    converted_root = data_dir / "converted_md"
    if source and source.suffix.lower() in {".md", ".markdown"} and _is_within(source, converted_root) and source.is_file():
        derived.append(source)
    for row in objects:
        for key in ("source_package_path", "source_import_manifest_path"):
            path = _safe_path(row.get(key))
            if path and path.is_file() and _is_within(path, data_dir):
                cache.append(path)
    return _unique_paths(derived), _unique_paths(cache)


def _fts_impact(path: Path, document_id: int) -> tuple[int, str]:
    target = Path(path)
    if not target.is_file():
        return 0, "fts_index_not_present"
    try:
        with _readonly_connection(target) as connection:
            return _count(connection, "retrieval_fragments", "document_id = ?", (document_id,)), ""
    except Exception as exc:
        return 0, f"fts_impact_unavailable:{type(exc).__name__}"


def _document_pdf_path(document: dict[str, Any]) -> Path | None:
    candidates = (document.get("pdf_path"), document.get("source_path"))
    for value in candidates:
        path = _safe_path(value)
        if path and path.suffix.lower() == ".pdf":
            return path
    return None


def _path_descriptor(path: Path | None, *, data_dir: Path, hash_file: bool) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve(strict=False)
    exists = resolved.is_file() if hash_file else resolved.exists()
    descriptor: dict[str, Any] = {
        "basename": resolved.name,
        "scope": "managed" if _is_within(resolved, Path(data_dir)) else "external",
        "path_hash": _sha256_text(str(resolved).lower()),
        "exists": exists,
    }
    if hash_file:
        descriptor["file_hash"] = _sha256_file(resolved) if exists else None
    return descriptor


def _file_fingerprint(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve(strict=False)
    exists = resolved.is_file()
    return {
        "basename": resolved.name,
        "path_hash": _sha256_text(str(resolved).lower()),
        "exists": exists,
        "size": resolved.stat().st_size if exists else 0,
        "sha256": _sha256_file(resolved) if exists else None,
    }


def _source_kind(document: dict[str, Any], pdf: dict[str, Any] | None) -> str:
    if document.get("zotero_key"):
        return "zotero_linked"
    if pdf and pdf.get("scope") == "managed":
        return "search_managed_pdf"
    if pdf:
        return "external_pdf"
    return "imported_document"


def _normalize_acknowledgment(
    value: ManualPreservationAcknowledgment | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        model = (
            value
            if isinstance(value, ManualPreservationAcknowledgment)
            else ManualPreservationAcknowledgment.model_validate(value)
        )
    except Exception as exc:
        raise DeletionError(
            "manual_preservation_acknowledgment_invalid",
            "人工保存确认格式无效。",
            status_code=422,
            details={"exception_type": type(exc).__name__},
        ) from exc
    payload = model.model_dump()
    payload["blocker_type"] = _normalize_acknowledgment_text(
        payload["blocker_type"]
    )
    payload["record_ids"] = sorted(int(value) for value in payload["record_ids"])
    payload["preservation_artifact_directory"] = str(
        Path(payload["preservation_artifact_directory"])
        .expanduser()
        .resolve(strict=False)
    )
    payload["preservation_manifest_sha256"] = str(
        payload["preservation_manifest_sha256"]
    ).lower()
    payload["acknowledged_by"] = _normalize_acknowledgment_text(
        payload["acknowledged_by"]
    )
    payload["acknowledgment_text"] = _normalize_acknowledgment_text(
        payload["acknowledgment_text"]
    )
    if not payload["acknowledged_by"] or not payload["acknowledgment_text"]:
        raise DeletionError(
            "manual_preservation_acknowledgment_invalid",
            "人工保存确认人和确认文字不能为空。",
            status_code=422,
        )
    return payload


def _normalize_acknowledgment_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split())


def _hash_acknowledgment(value: dict[str, Any] | None) -> str:
    return _hash_json(value) if value is not None else _sha256_text("")


def _validate_manual_preservation_acknowledgment(
    acknowledgment: dict[str, Any] | None,
    *,
    document_id: int,
    blocker_record_ids: tuple[int, ...],
) -> dict[str, Any] | None:
    if acknowledgment is None:
        return None
    expected_record_ids = tuple(sorted(int(value) for value in blocker_record_ids))
    acknowledged_record_ids = tuple(
        sorted(int(value) for value in acknowledgment["record_ids"])
    )
    if (
        acknowledgment["blocker_type"] != MANUAL_PRESERVATION_BLOCKER_TYPE
        or int(acknowledgment["document_id"]) != int(document_id)
        or not expected_record_ids
        or acknowledged_record_ids != expected_record_ids
    ):
        raise DeletionError(
            "manual_preservation_acknowledgment_scope_mismatch",
            "人工保存确认未绑定当前文档的精确 blocker records。",
            details={
                "document_id": int(document_id),
                "expected_record_ids": list(expected_record_ids),
            },
        )

    artifact_directory = Path(
        acknowledgment["preservation_artifact_directory"]
    ).resolve(strict=False)
    if (
        not artifact_directory.is_absolute()
        or not artifact_directory.is_dir()
    ):
        raise DeletionError(
            "manual_preservation_artifact_directory_invalid",
            "人工保存 artifact 目录不存在或不可用。",
        )
    manifest_path = artifact_directory / PRESERVATION_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise DeletionError(
            "manual_preservation_manifest_missing",
            "人工保存 artifact 缺少 preservation manifest。",
        )
    actual_manifest_sha256 = _sha256_file(manifest_path)
    if actual_manifest_sha256.lower() != acknowledgment[
        "preservation_manifest_sha256"
    ]:
        raise DeletionError(
            "manual_preservation_manifest_sha256_mismatch",
            "人工保存 manifest SHA256 不匹配。",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeletionError(
            "manual_preservation_manifest_invalid",
            "人工保存 manifest 无法解析。",
            details={"exception_type": type(exc).__name__},
        ) from exc
    if (
        manifest.get("schema_version")
        != PRESERVATION_MANIFEST_SCHEMA_VERSION
    ):
        raise DeletionError(
            "manual_preservation_manifest_schema_invalid",
            "人工保存 manifest schema 不受支持。",
        )

    identities = manifest.get("exported_record_identities")
    if identities is None:
        singleton = manifest.get("exported_record_identity")
        identities = [singleton] if isinstance(singleton, dict) else []
    manifest_record_ids: list[int] = []
    for identity in identities:
        if (
            not isinstance(identity, dict)
            or identity.get("source_table") != "object_candidates"
            or int(identity.get("document_id") or 0) != int(document_id)
        ):
            raise DeletionError(
                "manual_preservation_manifest_identity_mismatch",
                "人工保存 manifest 的导出身份与当前 blocker 不一致。",
            )
        manifest_record_ids.append(int(identity.get("record_id") or 0))
    if tuple(sorted(manifest_record_ids)) != expected_record_ids:
        raise DeletionError(
            "manual_preservation_manifest_identity_mismatch",
            "人工保存 manifest 未覆盖精确 blocker records。",
            details={"expected_record_ids": list(expected_record_ids)},
        )

    verified_files: list[dict[str, Any]] = []
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise DeletionError(
            "manual_preservation_manifest_files_invalid",
            "人工保存 manifest 未声明 artifact 文件。",
        )
    for filename, descriptor in sorted(files.items()):
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(descriptor, dict)
        ):
            raise DeletionError(
                "manual_preservation_manifest_files_invalid",
                "人工保存 manifest 含无效文件身份。",
            )
        artifact_path = artifact_directory / filename
        expected_size = int(descriptor.get("size") or -1)
        expected_sha256 = str(descriptor.get("sha256") or "").lower()
        if (
            not artifact_path.is_file()
            or artifact_path.stat().st_size != expected_size
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or _sha256_file(artifact_path).lower() != expected_sha256
        ):
            raise DeletionError(
                "manual_preservation_artifact_file_mismatch",
                "人工保存 artifact 文件与 manifest 不一致。",
                details={"filename": filename},
            )
        verified_files.append(
            {
                "filename": filename,
                "size": expected_size,
                "sha256": expected_sha256,
            }
        )

    acknowledgment_fingerprint = _hash_acknowledgment(acknowledgment)
    artifact_fingerprint = _hash_json(
        {
            "manifest_sha256": actual_manifest_sha256.lower(),
            "files": verified_files,
        }
    )
    return {
        "status": "validated",
        "blocker_type": acknowledgment["blocker_type"],
        "record_ids": list(acknowledged_record_ids),
        "document_id": int(document_id),
        "preservation_artifact": {
            "basename": artifact_directory.name,
            "path_hash": _sha256_text(str(artifact_directory).lower()),
        },
        "preservation_manifest_sha256": actual_manifest_sha256.lower(),
        "verified_file_count": len(verified_files),
        "artifact_fingerprint": artifact_fingerprint,
        "acknowledged_by": acknowledgment["acknowledged_by"],
        "acknowledgment_text": acknowledgment["acknowledgment_text"],
        "acknowledgment_fingerprint": acknowledgment_fingerprint,
    }


def _normalize_options(value: DeletionOptions | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return DeletionOptions().model_dump()
    if isinstance(value, DeletionOptions):
        return value.model_dump()
    return DeletionOptions.model_validate(value).model_dump()


def _consume_preview_token(token: str) -> PreviewRecord:
    return _preview_record(token, consume=True)


def _preview_record(token: str, *, consume: bool) -> PreviewRecord:
    digest = _sha256_text(str(token or ""))
    now = time.monotonic()
    with _TOKEN_LOCK:
        _purge_preview_tokens(now)
        record = _PREVIEW_TOKENS.pop(digest, None) if consume else _PREVIEW_TOKENS.get(digest)
    if record is None or record.expires_at <= now:
        raise DeletionError("deletion_preview_token_invalid_or_expired", "删除预览已失效，请重新检查。")
    return record


def _purge_preview_tokens(now: float) -> None:
    for digest in [key for key, value in _PREVIEW_TOKENS.items() if value.expires_at <= now]:
        _PREVIEW_TOKENS.pop(digest, None)


def _readonly_connection(path: str | Path) -> sqlite3.Connection:
    target = Path(path).resolve()
    connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _rows(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]


def _count(connection: sqlite3.Connection, table: str, condition: str, params: tuple[Any, ...] | list[Any]) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}", tuple(params)).fetchone()[0])


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()[0])


def _execute_count(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] | list[Any]) -> int:
    cursor = connection.execute(sql, tuple(params))
    return max(0, int(cursor.rowcount))


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    escaped = table.replace('"', '""')
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')]


def _placeholders(values: tuple[Any, ...] | list[Any]) -> str:
    return ",".join("?" for _ in values)


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _json_int_list(raw: Any) -> list[int]:
    values: list[int] = []
    for value in _json_list(raw):
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return values


def _safe_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return None
    try:
        return Path(text).resolve(strict=False)
    except OSError:
        return None


def _validated_retry_file(value: Any, root: Path) -> Path:
    path = _safe_path(value)
    if path is None or not _is_within(path, Path(root)):
        raise DeletionError("deletion_retry_file_scope_invalid", "恢复包中的文件目标超出允许范围。")
    return path


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve(strict=False))) == os.path.normcase(str(second.resolve(strict=False)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False


def _unique_paths(paths: list[Path]) -> list[Path]:
    values: dict[str, Path] = {}
    for path in paths:
        values.setdefault(os.path.normcase(str(path.resolve(strict=False))), path.resolve(strict=False))
    return [values[key] for key in sorted(values)]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_text(payload)


def _stable_impact_hash(value: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    recovery = payload.get("recovery_package")
    if isinstance(recovery, dict):
        location = recovery.get("location")
        if isinstance(location, dict):
            location.pop("exists", None)
    return _hash_json(payload)


def _cleanup_error(error_code: str, exc: Exception) -> dict[str, str]:
    return {"error_code": error_code, "exception_type": type(exc).__name__}


def _audit_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"delete-{timestamp}-{uuid4().hex[:12]}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
