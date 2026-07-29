from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from app.services import (
    import_duplicate_check_service,
    pdf_extraction_strategy_service,
    zotero_source_cache_service,
)
from app.services.retrieval.fragment_normalizer import (
    html_to_text,
    page_number_from_position,
    parse_json,
)


DEFAULT_PREVIEW_TTL_SECONDS = 15 * 60
SUPPORTED_BIBLIOGRAPHIC_ITEM_TYPES = frozenset(
    {
        "book",
        "journalArticle",
        "conferencePaper",
        "preprint",
        "thesis",
        "report",
    }
)

_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_PREVIEW_CACHE_LOCK = threading.Lock()


class ZoteroSelectedBookPreviewError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.details = details or {}

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def open_snapshot_readonly(
    snapshot_path: str | Path,
) -> sqlite3.Connection:
    path = Path(snapshot_path).resolve(strict=False)

    if not path.is_file():
        raise ZoteroSelectedBookPreviewError(
            status_code=503,
            code="zotero_snapshot_missing",
            message=f"Zotero snapshot does not exist: {path}",
        )

    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def build_selected_book_preview(
    *,
    zotero_item_key: str,
    zotero_attachment_key: str | None = None,
    snapshot_path: str | Path | None = None,
    db_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
    now_ts: float | None = None,
    token_ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
    issue_token: bool = True,
) -> dict[str, Any]:
    item_key = str(zotero_item_key or "").strip()
    attachment_key = str(zotero_attachment_key or "").strip() or None

    if not item_key:
        raise ZoteroSelectedBookPreviewError(
            status_code=422,
            code="zotero_item_key_required",
            message="A Zotero parent item key is required.",
        )

    source_config = dict(
        config
        if config is not None
        else zotero_source_cache_service._load_config()
    )

    snapshot = (
        Path(snapshot_path).resolve(strict=False)
        if snapshot_path is not None
        else zotero_source_cache_service._project_path(
            source_config["zotero_db_snapshot"]
        ).resolve(strict=False)
    )

    research_db = Path(
        db_path if db_path is not None else DEFAULT_DB_PATH
    ).resolve(strict=False)

    with open_snapshot_readonly(snapshot) as connection:
        parent = _read_parent_item(
            connection,
            item_key,
        )

        attachments = _read_pdf_attachments(
            connection,
            parent_item_id=int(parent["item_id"]),
            config=source_config,
        )

        if not attachments:
            raise ZoteroSelectedBookPreviewError(
                status_code=422,
                code="no_pdf_attachment",
                message=(
                    "The selected Zotero item has no direct PDF attachment."
                ),
                details={
                    "zotero_item_key": item_key,
                },
            )

        selected = _select_attachment(
            attachments,
            requested_attachment_key=attachment_key,
        )

        parent_payload = {
            "zotero_item_key": item_key,
            "library_id": int(parent["library_id"] or 0),
            "title": str(parent["title"] or ""),
            "item_type": str(parent["item_type"]),
            "date_added": parent["date_added"],
            "date_modified": parent["date_modified"],
            "version": parent["version"],
            "zotero_select_uri": (
                f"zotero://select/library/items/{item_key}"
            ),
        }

        attachment_choices = [
            _public_attachment_choice(item)
            for item in attachments
        ]

        if selected is None:
            return {
                "status": "attachment_choice_required",
                "zotero_item": parent_payload,
                "attachment_choices": attachment_choices,
                "attachment_count": len(attachment_choices),
                "selected_attachment": None,
                "annotation_count": None,
                "annotation_comment_count": None,
                "child_note_count": None,
                "annotations": [],
                "child_notes": [],
                "duplicate_check": None,
                "preview_token": None,
                "preview_expires_at": None,
                "source_revision": None,
                **_no_write_flags(),
            }

        if not selected["path_exists"]:
            raise ZoteroSelectedBookPreviewError(
                status_code=422,
                code="pdf_file_missing",
                message=(
                    "The selected Zotero PDF attachment is not available "
                    "as a local file."
                ),
                details={
                    "zotero_item_key": item_key,
                    "zotero_attachment_key": selected[
                        "attachment_key"
                    ],
                },
            )

        if selected["path_status"] == "unsupported":
            raise ZoteroSelectedBookPreviewError(
                status_code=422,
                code="pdf_path_unsupported",
                message=(
                    "The selected Zotero attachment path is outside "
                    "the configured Zotero roots."
                ),
            )

        pdf_path = Path(
            str(selected["resolved_pdf_path"])
        ).resolve(strict=False)

        pdf_sha256 = _sha256_file(pdf_path)

        pdf_meta = (
            import_duplicate_check_service._file_page_size_meta(
                pdf_path
            )
        )

        page_count = pdf_meta.get("page_count")
        warnings: list[str] = []

        if pdf_meta.get("warning"):
            warnings.append(
                str(pdf_meta["warning"])
            )

        extraction_plan = (
            pdf_extraction_strategy_service
            .build_pdf_extraction_plan(
                pdf_path,
                pdf_sha256=pdf_sha256,
            )
        )
        warnings.extend(
            str(value)
            for value in extraction_plan.get("warnings") or []
            if value
        )

        annotations = _read_annotations(
            connection,
            attachment_item_id=int(selected["item_id"]),
        )

        child_notes = _read_child_notes(
            connection,
            parent_item_id=int(parent["item_id"]),
            attachment_item_id=int(selected["item_id"]),
            regular_item_key=item_key,
            attachment_key=str(selected["attachment_key"]),
        )

    duplicate = import_duplicate_check_service.check_duplicate_import(
        {
            "pdf_path": str(pdf_path),
            "title": parent_payload["title"],
            "zotero_item_key": item_key,
            "zotero_attachment_key": selected["attachment_key"],
        },
        db_path=research_db,
    )

    warnings.extend(
        str(value)
        for value in duplicate.get("warnings") or []
        if value
    )

    source_revision = _build_source_revision(
        parent=parent_payload,
        attachment=selected,
        pdf_sha256=pdf_sha256,
        annotations=annotations,
        child_notes=child_notes,
        extraction_plan=extraction_plan,
    )

    timestamp = float(
        time.time() if now_ts is None else now_ts
    )

    ttl = max(1, int(token_ttl_seconds))
    expires_at = timestamp + ttl

    preview_token = None

    if (
        issue_token
        and not bool(duplicate.get("duplicate_found"))
        and bool(extraction_plan.get("extraction_ready"))
        and int(extraction_plan.get("estimated_chunks") or 0) > 0
        and not bool(extraction_plan.get("blockers"))
    ):
        preview_token = secrets.token_urlsafe(24)

        _store_preview(
            preview_token,
            {
                "created_at": timestamp,
                "expires_at": expires_at,
                "source_revision_fingerprint": (
                    source_revision["fingerprint"]
                ),
                "zotero_item_key": item_key,
                "zotero_attachment_key": str(
                    selected["attachment_key"]
                ),
                "snapshot_path": str(snapshot),
                "db_path": str(research_db),
                "resolved_pdf_path": str(pdf_path),
                "config": source_config,
            },
            now_ts=timestamp,
        )

    return {
        "status": "ready",
        "zotero_item": parent_payload,
        "attachment_choices": attachment_choices,
        "attachment_count": len(attachment_choices),
        "selected_attachment": {
            **_public_attachment_choice(selected),
            "pdf_sha256": pdf_sha256,
            "file_size": pdf_path.stat().st_size,
            "page_count": page_count,
            "zotero_open_pdf_uri": (
                "zotero://open-pdf/library/items/"
                f"{selected['attachment_key']}"
            ),
        },
        "annotation_count": len(annotations),
        "annotation_comment_count": sum(
            1
            for item in annotations
            if str(item.get("source_comment") or "").strip()
        ),
        "child_note_count": len(child_notes),
        "annotations": annotations,
        "child_notes": child_notes,
        "duplicate_check": {
            "duplicate_found": bool(
                duplicate.get("duplicate_found")
            ),
            "duplicate_confidence": duplicate.get(
                "duplicate_confidence"
            ),
            "duplicate_reasons": (
                duplicate.get("duplicate_reasons") or []
            ),
            "existing_documents": (
                duplicate.get("existing_documents") or []
            ),
            "recommended_action": duplicate.get(
                "recommended_action"
            ),
        },
        "warnings": _dedupe(warnings),
        **extraction_plan,
        "source_revision": source_revision,
        "preview_token": preview_token,
        "preview_expires_at": (
            _iso_timestamp(expires_at)
            if preview_token
            else None
        ),
        **_no_write_flags(),
    }


def resolve_selected_book_preview_token(
    preview_token: str,
    *,
    now_ts: float | None = None,
    expected_db_path: str | Path | None = None,
) -> dict[str, Any]:
    token = str(preview_token or "").strip()

    if not token:
        raise ZoteroSelectedBookPreviewError(
            status_code=422,
            code="preview_token_required",
            message="Preview token is required.",
        )

    timestamp = float(
        time.time() if now_ts is None else now_ts
    )

    with _PREVIEW_CACHE_LOCK:
        entry = dict(
            _PREVIEW_CACHE.get(token) or {}
        )

    if not entry:
        raise ZoteroSelectedBookPreviewError(
            status_code=410,
            code="preview_token_unknown",
            message=(
                "The Zotero import preview is unavailable. "
                "Run preview again."
            ),
        )

    if timestamp >= float(entry["expires_at"]):
        with _PREVIEW_CACHE_LOCK:
            _PREVIEW_CACHE.pop(token, None)

        raise ZoteroSelectedBookPreviewError(
            status_code=410,
            code="preview_token_expired",
            message=(
                "The Zotero import preview expired. "
                "Run preview again."
            ),
        )

    if expected_db_path is not None:
        token_db = Path(
            str(entry["db_path"])
        ).resolve(strict=False)

        expected_db = Path(
            expected_db_path
        ).resolve(strict=False)

        if token_db != expected_db:
            raise ZoteroSelectedBookPreviewError(
                status_code=409,
                code="preview_target_db_mismatch",
                message=(
                    "The Zotero import preview "
                    "belongs to a different "
                    "target database."
                ),
            )

    try:
        current = build_selected_book_preview(
            zotero_item_key=entry[
                "zotero_item_key"
            ],
            zotero_attachment_key=entry[
                "zotero_attachment_key"
            ],
            snapshot_path=entry[
                "snapshot_path"
            ],
            db_path=entry["db_path"],
            config=entry["config"],
            now_ts=timestamp,
            issue_token=False,
        )
    except ZoteroSelectedBookPreviewError as exc:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero source changed after preview. "
                "Run preview again."
            ),
            details={
                "cause_code": exc.code,
            },
        ) from exc

    current_fingerprint = current[
        "source_revision"
    ]["fingerprint"]

    if current_fingerprint != entry[
        "source_revision_fingerprint"
    ]:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero source changed after preview. "
                "Run preview again."
            ),
            details={
                "preview_fingerprint": entry[
                    "source_revision_fingerprint"
                ],
                "current_fingerprint": (
                    current_fingerprint
                ),
            },
        )

    current["_preview_audit"] = {
        "preview_token_fingerprint": hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest(),
        "previewed_at": _iso_timestamp(float(entry["created_at"])),
    }
    return current




def resolve_selected_book_preview_source(
    preview_token: str,
    *,
    now_ts: float | None = None,
    expected_db_path: str | Path | None = None,
) -> tuple[dict[str, Any], Path]:
    current = resolve_selected_book_preview_token(
        preview_token,
        now_ts=now_ts,
        expected_db_path=expected_db_path,
    )

    token = str(
        preview_token
        or ""
    ).strip()

    with _PREVIEW_CACHE_LOCK:
        entry = dict(
            _PREVIEW_CACHE.get(
                token
            )
            or {}
        )

    if not entry:
        raise ZoteroSelectedBookPreviewError(
            status_code=410,
            code="preview_token_unknown",
            message=(
                "The Zotero import preview "
                "is unavailable. Run preview again."
            ),
        )

    raw_path = str(
        entry.get(
            "resolved_pdf_path"
        )
        or ""
    ).strip()

    if not raw_path:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero PDF source is no "
                "longer available. Run preview again."
            ),
            details={
                "cause_code": (
                    "resolved_pdf_path_missing"
                ),
            },
        )

    pdf_path = Path(
        raw_path
    ).resolve(strict=False)

    if (
        not pdf_path.is_file()
        or pdf_path.suffix.lower()
        != ".pdf"
    ):
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero PDF source changed "
                "after preview. Run preview again."
            ),
            details={
                "cause_code": (
                    "resolved_pdf_unavailable"
                ),
            },
        )

    expected_hash = str(
        (
            current.get(
                "selected_attachment"
            )
            or {}
        ).get(
            "pdf_sha256"
        )
        or ""
    )

    if (
        not expected_hash
        or _sha256_file(
            pdf_path
        )
        != expected_hash
    ):
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero PDF source changed "
                "after preview. Run preview again."
            ),
            details={
                "cause_code": (
                    "resolved_pdf_hash_changed"
                ),
            },
        )

    return current, pdf_path

def validate_selected_book_preview_token(
    preview_token: str,
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    token = str(preview_token or "").strip()

    if not token:
        raise ZoteroSelectedBookPreviewError(
            status_code=422,
            code="preview_token_required",
            message="Preview token is required.",
        )

    timestamp = float(
        time.time() if now_ts is None else now_ts
    )

    with _PREVIEW_CACHE_LOCK:
        entry = dict(
            _PREVIEW_CACHE.get(token) or {}
        )

    if not entry:
        raise ZoteroSelectedBookPreviewError(
            status_code=410,
            code="preview_token_unknown",
            message=(
                "The Zotero import preview is unavailable. "
                "Run preview again."
            ),
        )

    if timestamp >= float(entry["expires_at"]):
        with _PREVIEW_CACHE_LOCK:
            _PREVIEW_CACHE.pop(token, None)

        raise ZoteroSelectedBookPreviewError(
            status_code=410,
            code="preview_token_expired",
            message=(
                "The Zotero import preview expired. "
                "Run preview again."
            ),
        )

    try:
        current = build_selected_book_preview(
            zotero_item_key=entry["zotero_item_key"],
            zotero_attachment_key=entry[
                "zotero_attachment_key"
            ],
            snapshot_path=entry["snapshot_path"],
            db_path=entry["db_path"],
            config=entry["config"],
            now_ts=timestamp,
            issue_token=False,
        )
    except ZoteroSelectedBookPreviewError as exc:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero source changed after preview. "
                "Run preview again."
            ),
            details={
                "cause_code": exc.code,
            },
        ) from exc

    current_fingerprint = current[
        "source_revision"
    ]["fingerprint"]

    if current_fingerprint != entry[
        "source_revision_fingerprint"
    ]:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="preview_source_drift",
            message=(
                "The Zotero source changed after preview. "
                "Run preview again."
            ),
            details={
                "preview_fingerprint": entry[
                    "source_revision_fingerprint"
                ],
                "current_fingerprint": (
                    current_fingerprint
                ),
            },
        )

    return {
        "status": "valid",
        "preview_token": token,
        "zotero_item_key": entry["zotero_item_key"],
        "zotero_attachment_key": entry[
            "zotero_attachment_key"
        ],
        "source_revision_fingerprint": (
            current_fingerprint
        ),
        "expires_at": _iso_timestamp(
            float(entry["expires_at"])
        ),
        **_no_write_flags(),
    }


def _read_parent_item(
    connection: sqlite3.Connection,
    item_key: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            item.itemID AS item_id,
            item.libraryID AS library_id,
            item.dateAdded AS date_added,
            item.dateModified AS date_modified,
            item.version AS version,
            COALESCE(item_types.typeName, 'unknown') AS item_type,
            title_value.value AS title
        FROM items AS item
        LEFT JOIN deletedItems AS deleted
            ON deleted.itemID = item.itemID
        LEFT JOIN itemData AS title_data
            ON title_data.itemID = item.itemID
            AND title_data.fieldID = (
                SELECT fieldID
                FROM fields
                WHERE fieldName = 'title'
                LIMIT 1
            )
        LEFT JOIN itemDataValues AS title_value
            ON title_value.valueID = title_data.valueID
        LEFT JOIN itemTypes AS item_types
            ON item_types.itemTypeID = item.itemTypeID
        WHERE item.key = ?
          AND deleted.itemID IS NULL
        ORDER BY
            item.libraryID,
            item.itemID
        LIMIT 2
        """,
        (item_key,),
    ).fetchall()

    if not rows:
        raise ZoteroSelectedBookPreviewError(
            status_code=404,
            code="zotero_item_not_found",
            message=(
                "The selected Zotero parent item "
                "was not found in the snapshot."
            ),
            details={
                "zotero_item_key": item_key,
            },
        )

    if len(rows) > 1:
        raise ZoteroSelectedBookPreviewError(
            status_code=409,
            code="zotero_item_key_ambiguous",
            message=(
                "The Zotero item key exists in more than "
                "one library. The source must be resolved "
                "unambiguously before import preview."
            ),
            details={
                "zotero_item_key": item_key,
                "matching_library_count": len(rows),
            },
        )

    parent = dict(rows[0])
    item_type = str(parent.get("item_type") or "unknown")
    if item_type not in SUPPORTED_BIBLIOGRAPHIC_ITEM_TYPES:
        raise ZoteroSelectedBookPreviewError(
            status_code=422,
            code="zotero_item_type_unsupported",
            message=(
                "The selected Zotero item type is not supported for "
                "bibliographic PDF import."
            ),
            details={
                "zotero_item_key": item_key,
                "item_type": item_type,
            },
        )
    return parent


def document_type_for_item_type(item_type: str) -> str:
    normalized = str(item_type or "").strip()
    if normalized not in SUPPORTED_BIBLIOGRAPHIC_ITEM_TYPES:
        raise ValueError("zotero_item_type_unsupported")
    return normalized


def _read_pdf_attachments(
    connection: sqlite3.Connection,
    *,
    parent_item_id: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            attachment.itemID AS item_id,
            attachment.libraryID AS library_id,
            attachment.key AS attachment_key,
            attachment.dateAdded AS date_added,
            attachment.dateModified AS date_modified,
            attachment.version AS version,
            ia.path AS attachment_path,
            ia.linkMode AS link_mode,
            ia.contentType AS content_type
        FROM itemAttachments AS ia
        JOIN items AS attachment
            ON attachment.itemID = ia.itemID
        LEFT JOIN deletedItems AS deleted
            ON deleted.itemID = attachment.itemID
        WHERE ia.parentItemID = ?
          AND deleted.itemID IS NULL
          AND (
              lower(COALESCE(ia.contentType, ''))
                  = 'application/pdf'
              OR lower(COALESCE(ia.path, ''))
                  LIKE '%.pdf'
          )
        ORDER BY attachment.key
        """,
        (parent_item_id,),
    ).fetchall()

    result: list[dict[str, Any]] = []

    for row in rows:
        data = dict(row)

        resolved, supported = (
            zotero_source_cache_service._resolve_attachment_path(
                attachment_key=str(data["attachment_key"]),
                raw_path=data.get("attachment_path"),
                zotero_data_dir=Path(
                    config["zotero_data_dir"]
                ),
                storage_root=Path(
                    config["zotero_storage_root"]
                ),
            )
        )

        exists = bool(
            resolved
            and Path(resolved).is_file()
        )

        result.append(
            {
                **data,
                "resolved_pdf_path": resolved,
                "path_exists": exists,
                "path_status": (
                    "unsupported"
                    if not supported
                    else "available"
                    if exists
                    else "missing"
                ),
            }
        )

    return result


def _select_attachment(
    attachments: list[dict[str, Any]],
    *,
    requested_attachment_key: str | None,
) -> dict[str, Any] | None:
    if requested_attachment_key:
        for item in attachments:
            if (
                str(item["attachment_key"])
                == requested_attachment_key
            ):
                return item

        raise ZoteroSelectedBookPreviewError(
            status_code=422,
            code="attachment_not_owned_by_item",
            message=(
                "The requested PDF attachment does not belong "
                "to the selected Zotero item."
            ),
            details={
                "requested_attachment_key": (
                    requested_attachment_key
                ),
                "available_attachment_keys": [
                    str(item["attachment_key"])
                    for item in attachments
                ],
            },
        )

    if len(attachments) == 1:
        return attachments[0]

    return None


def _read_annotations(
    connection: sqlite3.Connection,
    *,
    attachment_item_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            annotation.itemID AS annotation_item_id,
            annotation.libraryID AS library_id,
            annotation.key AS annotation_key,
            annotation.dateAdded AS date_added,
            annotation.dateModified AS date_modified,
            annotation.version AS version,
            ia.text AS selected_text,
            ia.comment AS source_comment,
            ia.pageLabel AS page_label,
            ia.position AS position_json
        FROM itemAnnotations AS ia
        JOIN items AS annotation
            ON annotation.itemID = ia.itemID
        LEFT JOIN deletedItems AS deleted
            ON deleted.itemID = annotation.itemID
        WHERE ia.parentItemID = ?
          AND deleted.itemID IS NULL
        ORDER BY annotation.dateAdded, annotation.key
        """,
        (attachment_item_id,),
    ).fetchall()

    result = []

    for row in rows:
        data = dict(row)

        key = str(
            data.get("annotation_key") or ""
        ).strip()

        if not key:
            continue

        position_json = data.get(
            "position_json"
        )

        position = parse_json(
            position_json,
            {},
        )

        if not isinstance(position, dict):
            position = {}

        pdf_page = page_number_from_position(
            position
        )

        content_payload = {
            "selected_text": (
                str(data.get("selected_text") or "")
            ),
            "source_comment": (
                str(data.get("source_comment") or "")
            ),
            "pdf_page": pdf_page,
            "page_label": data.get("page_label"),
            "position_json": position_json,
        }

        result.append(
            {
                "source_identity": (
                    f"zotero:"
                    f"{int(data.get('library_id') or 0)}:"
                    f"annotation:{key}"
                ),
                "source_record_kind": "annotation",
                "library_id": int(
                    data.get("library_id") or 0
                ),
                "zotero_annotation_key": key,
                "selected_text": content_payload[
                    "selected_text"
                ],
                "source_comment": content_payload[
                    "source_comment"
                ],
                "pdf_page": pdf_page,
                "page_label": data.get(
                    "page_label"
                ),
                "position_json": position_json,
                "source_created_at": data.get(
                    "date_added"
                ),
                "source_updated_at": data.get(
                    "date_modified"
                ),
                "source_version": data.get(
                    "version"
                ),
                "source_content_hash": _sha256_json(
                    content_payload
                ),
            }
        )

    return result


def _read_child_notes(
    connection: sqlite3.Connection,
    *,
    parent_item_id: int,
    attachment_item_id: int,
    regular_item_key: str,
    attachment_key: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            note_item.itemID AS note_item_id,
            note_item.libraryID AS library_id,
            note_item.key AS note_key,
            note_item.dateAdded AS date_added,
            note_item.dateModified AS date_modified,
            note_item.version AS version,
            note.parentItemID AS parent_item_id,
            note.note AS note_html,
            note.title AS note_title
        FROM itemNotes AS note
        JOIN items AS note_item
            ON note_item.itemID = note.itemID
        LEFT JOIN deletedItems AS deleted
            ON deleted.itemID = note.itemID
        WHERE note.parentItemID IN (?, ?)
          AND deleted.itemID IS NULL
        ORDER BY note_item.dateAdded, note_item.key
        """,
        (parent_item_id, attachment_item_id),
    ).fetchall()

    result = []

    for row in rows:
        data = dict(row)
        note_key = str(
            data.get("note_key") or ""
        ).strip()

        if not note_key:
            continue

        note_html = str(
            data.get("note_html") or ""
        )
        note_text = html_to_text(note_html)

        if not note_text:
            continue

        parent_is_attachment = (
            int(data["parent_item_id"])
            == int(attachment_item_id)
        )

        content_payload = {
            "title": str(
                data.get("note_title") or ""
            ),
            "note_html": note_html,
        }

        result.append(
            {
                "source_identity": (
                    f"zotero:{int(data.get('library_id') or 0)}:"
                    f"child_note:{note_key}"
                ),
                "source_record_kind": "child_note",
                "library_id": int(
                    data.get("library_id") or 0
                ),
                "zotero_note_key": note_key,
                "zotero_item_key": regular_item_key,
                "zotero_attachment_key": (
                    attachment_key
                    if parent_is_attachment
                    else None
                ),
                "parent_kind": (
                    "pdf_attachment"
                    if parent_is_attachment
                    else "regular_item"
                ),
                "title": str(
                    data.get("note_title") or ""
                ),
                "note_text": note_text,
                "source_created_at": data.get(
                    "date_added"
                ),
                "source_updated_at": data.get(
                    "date_modified"
                ),
                "source_version": data.get("version"),
                "source_content_hash": _sha256_json(
                    content_payload
                ),
            }
        )

    return result


def _build_source_revision(
    *,
    parent: dict[str, Any],
    attachment: dict[str, Any],
    pdf_sha256: str,
    annotations: list[dict[str, Any]],
    child_notes: list[dict[str, Any]],
    extraction_plan: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "parent": {
            "zotero_item_key": parent[
                "zotero_item_key"
            ],
            "library_id": parent["library_id"],
            "date_modified": parent[
                "date_modified"
            ],
            "version": parent["version"],
            "item_type": str(parent.get("item_type") or "unknown"),
        },
        "attachment": {
            "zotero_attachment_key": attachment[
                "attachment_key"
            ],
            "date_modified": attachment[
                "date_modified"
            ],
            "version": attachment["version"],
            "pdf_sha256": pdf_sha256,
        },
        "annotations": [
            {
                "source_identity": item[
                    "source_identity"
                ],
                "source_updated_at": item[
                    "source_updated_at"
                ],
                "source_version": item[
                    "source_version"
                ],
                "source_content_hash": item[
                    "source_content_hash"
                ],
            }
            for item in annotations
        ],
        "child_notes": [
            {
                "source_identity": item[
                    "source_identity"
                ],
                "source_updated_at": item[
                    "source_updated_at"
                ],
                "source_version": item[
                    "source_version"
                ],
                "source_content_hash": item[
                    "source_content_hash"
                ],
            }
            for item in child_notes
        ],
        "extraction_plan": {
            "fingerprint": (
                pdf_extraction_strategy_service
                .extraction_plan_fingerprint(
                    extraction_plan
                )
            ),
            "extractor_strategy": extraction_plan[
                "extractor_strategy"
            ],
            "converted_markdown_status": extraction_plan[
                "converted_markdown_status"
            ],
            "converted_markdown_pdf_sha256": extraction_plan.get(
                "converted_markdown_pdf_sha256"
            ),
            "converted_markdown_sha256": extraction_plan.get(
                "converted_markdown_sha256"
            ),
            "extraction_ready": bool(
                extraction_plan["extraction_ready"]
            ),
        },
    }

    return {
        **payload,
        "fingerprint": _sha256_json(payload),
    }


def _public_attachment_choice(
    item: dict[str, Any],
) -> dict[str, Any]:
    path = item.get("resolved_pdf_path")

    return {
        "zotero_attachment_key": str(
            item["attachment_key"]
        ),
        "file_name": (
            Path(str(path)).name
            if path
            else None
        ),
        "path_exists": bool(
            item.get("path_exists")
        ),
        "path_status": item.get("path_status"),
        "content_type": item.get("content_type"),
        "date_modified": item.get(
            "date_modified"
        ),
        "version": item.get("version"),
    }


def _store_preview(
    token: str,
    entry: dict[str, Any],
    *,
    now_ts: float,
) -> None:
    with _PREVIEW_CACHE_LOCK:
        expired = [
            key
            for key, value in _PREVIEW_CACHE.items()
            if float(value["expires_at"]) <= now_ts
        ]

        for key in expired:
            _PREVIEW_CACHE.pop(key, None)

        _PREVIEW_CACHE[token] = dict(entry)


def _clear_preview_cache_for_tests() -> None:
    with _PREVIEW_CACHE_LOCK:
        _PREVIEW_CACHE.clear()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)

    return digest.hexdigest()


def _sha256_json(payload: Any) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(
        value,
        tz=timezone.utc,
    ).isoformat()


def _dedupe(values: list[str]) -> list[str]:
    result = []

    for value in values:
        if value and value not in result:
            result.append(value)

    return result


def _no_write_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_data_modified": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "fts_write_performed": False,
        "external_llm_called": False,
    }
