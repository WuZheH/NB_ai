from __future__ import annotations

import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

from app.core.paths import DATA_PROJECT_ROOT, ZOTERO_DIR
from app.db.session import Base, SessionLocal, engine
from app.models import Document, DocumentSource, ZoteroPdfSource
from app.services import import_duplicate_check_service


CONFIG_PATH = ZOTERO_DIR / "zotero_source_config.json"
ZOTERO_SELECT_URI = "zotero://select/library/items/{item_key}"
ZOTERO_OPEN_PDF_URI = "zotero://open-pdf/library/items/{attachment_key}"


def refresh_snapshot() -> dict[str, Any]:
    config = _load_config()
    source_db = Path(config["zotero_data_dir"]) / "zotero.sqlite"
    snapshot_path = _project_path(config["zotero_db_snapshot"])
    if not source_db.is_file():
        raise ValueError(f"Zotero source sqlite not found: {source_db}")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if snapshot_path.is_file():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = snapshot_path.parent / "backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / snapshot_path.name
        shutil.copy2(snapshot_path, backup_path)

    shutil.copy2(source_db, snapshot_path)
    stat = source_db.stat()
    snapshot_stat = snapshot_path.stat()
    return {
        "status": "ok",
        "message": "Zotero snapshot refreshed. 建议关闭 Zotero 后刷新，以获得完全静止的快照。",
        "source_path": str(source_db),
        "snapshot_path": _rel(snapshot_path),
        "backup_path": _rel(backup_path) if backup_path else None,
        "source_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "source_size": stat.st_size,
        "snapshot_time": datetime.fromtimestamp(snapshot_stat.st_mtime, timezone.utc).isoformat(),
        "db_write_performed": False,
        "zotero_db_write_performed": False,
        "external_llm_called": False,
    }


def sync_pdf_sources() -> dict[str, Any]:
    _ensure_tables()
    config = _load_config()
    snapshot_path = _project_path(config["zotero_db_snapshot"])
    if not snapshot_path.is_file():
        raise ValueError(f"Zotero snapshot sqlite not found: {snapshot_path}")

    rows = _fetch_pdf_rows(snapshot_path)
    resolved = [_row_to_source(row, config, snapshot_path) for row in rows if row.get("attachment_key")]
    path_counts = Counter(item["resolved_pdf_path"] for item in resolved if item["resolved_pdf_path"])
    for item in resolved:
        if item["cache_status"] == "available" and path_counts[item["resolved_pdf_path"]] > 1:
            item["cache_status"] = "duplicate"

    now = datetime.utcnow()
    with SessionLocal() as session:
        for item in resolved:
            existing = session.scalar(
                select(ZoteroPdfSource).where(ZoteroPdfSource.zotero_attachment_key == item["zotero_attachment_key"])
            )
            if existing is None:
                existing = ZoteroPdfSource(zotero_attachment_key=item["zotero_attachment_key"])
                session.add(existing)
            for key, value in item.items():
                setattr(existing, key, value)
            existing.last_synced_at = now
            existing.updated_at = now
        session.commit()

    counts = Counter(item["cache_status"] for item in resolved)
    return {
        "status": "ok",
        "source_count": len(resolved),
        "available_count": counts.get("available", 0),
        "missing_count": counts.get("missing", 0),
        "duplicate_count": counts.get("duplicate", 0),
        "unsupported_count": counts.get("unsupported", 0),
        "db_write_performed": True,
        "zotero_db_write_performed": False,
        "external_llm_called": False,
    }


def list_pdf_sources(q: str = "", status: str | None = "available") -> dict[str, Any]:
    _ensure_tables()
    query = (q or "").strip().casefold()
    with SessionLocal() as session:
        stmt = select(ZoteroPdfSource).order_by(ZoteroPdfSource.title, ZoteroPdfSource.zotero_attachment_key)
        if status:
            stmt = stmt.where(ZoteroPdfSource.cache_status == status)
        rows = list(session.scalars(stmt).all())
        if query:
            rows = [row for row in rows if _matches_query(row, query)]
        attachment_keys = [row.zotero_attachment_key for row in rows]
        item_keys = [row.zotero_item_key for row in rows if row.zotero_item_key]
        source_by_attachment = {
            row.zotero_attachment_key: row.document_id
            for row in session.scalars(
                select(DocumentSource).where(DocumentSource.zotero_attachment_key.in_(attachment_keys))
            ).all()
            if row.zotero_attachment_key
        } if attachment_keys else {}
        document_by_item = {
            document.zotero_key: document.id
            for document in session.scalars(select(Document).where(Document.zotero_key.in_(item_keys))).all()
            if document.zotero_key
        } if item_keys else {}

    items = []
    for row in rows:
        linked_document_id = source_by_attachment.get(row.zotero_attachment_key) or document_by_item.get(row.zotero_item_key)
        items.append(_source_item(row, linked_document_id))
    items = enrich_zotero_sources_with_import_status(items)
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
        "db_write_performed": False,
        "zotero_db_write_performed": False,
        "external_llm_called": False,
    }


def enrich_zotero_sources_with_import_status(
    sources: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Annotate Zotero cache rows with read-only import status.

    This intentionally does not mutate the Zotero cache table. It reuses the
    same duplicate matching contract used by the import page so the list can
    show already-imported PDFs before the user selects one.
    """
    enriched: list[dict[str, Any]] = []
    for source in sources:
        item = dict(source)
        cache_status = str(item.get("cache_status") or "")
        if cache_status == "missing" or not item.get("path_exists"):
            item.update(_import_status_payload("unknown", [], recommended_action="recheck_import_status"))
            enriched.append(item)
            continue

        duplicate = import_duplicate_check_service.check_duplicate_import(
            {
                "pdf_path": item.get("resolved_pdf_path") or "",
                "title": item.get("title") or "",
                "zotero_item_key": item.get("zotero_item_key") or "",
                "zotero_attachment_key": item.get("zotero_attachment_key") or "",
            },
            db_path=db_path,
        )
        documents = [
            _zotero_existing_document_payload(document, db_path=db_path)
            for document in duplicate.get("existing_documents") or []
        ]
        if documents:
            imported_status = _resolve_import_status_for_documents(documents)
            recommended = "view_existing_document" if imported_status == "sibling_imported" else "open_existing_document"
            item.update(_import_status_payload(imported_status, documents, recommended_action=recommended))
            item["duplicate_check"] = _duplicate_summary(duplicate)
        elif duplicate.get("duplicate_found"):
            item.update(_import_status_payload("unknown", documents, recommended_action="recheck_import_status"))
            item["duplicate_check"] = _duplicate_summary(duplicate)
        else:
            item.update(_import_status_payload("not_imported", [], recommended_action="select_for_import"))
            item["duplicate_check"] = _duplicate_summary(duplicate)
        enriched.append(item)
    return enriched


def get_pdf_source(source_id: int) -> ZoteroPdfSource:
    _ensure_tables()
    with SessionLocal() as session:
        source = session.get(ZoteroPdfSource, source_id)
        if source is None:
            raise ValueError(f"zotero_pdf_source_id does not exist: {source_id}")
        session.expunge(source)
        return source


def record_document_source(document_id: int, source_trace: dict[str, Any]) -> None:
    if source_trace.get("source_type") != "zotero_pdf":
        return
    attachment_key = source_trace.get("zotero_attachment_key")
    if not attachment_key:
        return
    _ensure_tables()
    with SessionLocal() as session:
        existing = session.scalar(
            select(DocumentSource).where(
                DocumentSource.document_id == document_id,
                DocumentSource.source_type == "zotero_pdf",
                DocumentSource.zotero_attachment_key == attachment_key,
            )
        )
        if existing is None:
            existing = DocumentSource(
                document_id=document_id,
                source_type="zotero_pdf",
                zotero_attachment_key=attachment_key,
            )
            session.add(existing)
        existing.zotero_item_key = source_trace.get("zotero_item_key")
        existing.zotero_source_id = source_trace.get("zotero_source_id")
        existing.zotero_select_uri = source_trace.get("zotero_select_uri")
        existing.zotero_open_pdf_uri = source_trace.get("zotero_open_pdf_uri")
        existing.source_trace_json = json.dumps(source_trace, ensure_ascii=False)
        session.commit()


def _fetch_pdf_rows(snapshot_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{snapshot_path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                attachment_items.key AS attachment_key,
                parent_items.key AS parent_key,
                itemAttachments.path AS attachment_path,
                itemAttachments.linkMode AS link_mode,
                itemAttachments.contentType AS content_type,
                title_values.value AS title,
                date_values.value AS date_value,
                COALESCE(pub_values.value, proceedings_values.value, conference_values.value, journal_values.value) AS publication_title
            FROM itemAttachments
            JOIN items AS attachment_items
                ON attachment_items.itemID = itemAttachments.itemID
            LEFT JOIN items AS parent_items
                ON parent_items.itemID = itemAttachments.parentItemID
            LEFT JOIN itemData AS title_data
                ON title_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND title_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'title' LIMIT 1)
            LEFT JOIN itemDataValues AS title_values ON title_values.valueID = title_data.valueID
            LEFT JOIN itemData AS date_data
                ON date_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND date_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'date' LIMIT 1)
            LEFT JOIN itemDataValues AS date_values ON date_values.valueID = date_data.valueID
            LEFT JOIN itemData AS pub_data
                ON pub_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND pub_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'publicationTitle' LIMIT 1)
            LEFT JOIN itemDataValues AS pub_values ON pub_values.valueID = pub_data.valueID
            LEFT JOIN itemData AS proceedings_data
                ON proceedings_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND proceedings_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'proceedingsTitle' LIMIT 1)
            LEFT JOIN itemDataValues AS proceedings_values ON proceedings_values.valueID = proceedings_data.valueID
            LEFT JOIN itemData AS conference_data
                ON conference_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND conference_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'conferenceName' LIMIT 1)
            LEFT JOIN itemDataValues AS conference_values ON conference_values.valueID = conference_data.valueID
            LEFT JOIN itemData AS journal_data
                ON journal_data.itemID = COALESCE(itemAttachments.parentItemID, itemAttachments.itemID)
                AND journal_data.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'journalAbbreviation' LIMIT 1)
            LEFT JOIN itemDataValues AS journal_values ON journal_values.valueID = journal_data.valueID
            WHERE lower(COALESCE(itemAttachments.contentType, '')) = 'application/pdf'
                OR lower(COALESCE(itemAttachments.path, '')) LIKE '%.pdf'
            ORDER BY attachment_items.key
            """
        ).fetchall()
        creators = _fetch_creators(connection)
    return [{**dict(row), "creators": creators.get(row["parent_key"], [])} for row in rows]


def _fetch_creators(connection: sqlite3.Connection) -> dict[str, list[str]]:
    rows = connection.execute(
        """
        SELECT parent_items.key AS parent_key, creators.firstName, creators.lastName, creators.fieldMode, itemCreators.orderIndex
        FROM itemCreators
        JOIN items AS parent_items ON parent_items.itemID = itemCreators.itemID
        JOIN creators ON creators.creatorID = itemCreators.creatorID
        ORDER BY parent_items.key, itemCreators.orderIndex
        """
    ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        name = _creator_name(row["firstName"], row["lastName"], row["fieldMode"])
        if not name:
            continue
        result.setdefault(row["parent_key"], []).append(name)
    return result


def _row_to_source(row: dict[str, Any], config: dict[str, Any], snapshot_path: Path) -> dict[str, Any]:
    resolved_path, supported = _resolve_attachment_path(
        attachment_key=row["attachment_key"],
        raw_path=row.get("attachment_path"),
        zotero_data_dir=Path(config["zotero_data_dir"]),
        storage_root=Path(config["zotero_storage_root"]),
    )
    exists = bool(resolved_path and Path(resolved_path).is_file())
    if not supported:
        status = "unsupported"
    elif exists:
        status = "available"
    else:
        status = "missing"
    stat = snapshot_path.stat()
    return {
        "zotero_item_key": row.get("parent_key"),
        "zotero_attachment_key": row["attachment_key"],
        "title": row.get("title") or "",
        "creators_json": json.dumps(row.get("creators") or [], ensure_ascii=False),
        "year": _year(row.get("date_value")),
        "publication_title": row.get("publication_title") or "",
        "attachment_path_raw": row.get("attachment_path"),
        "resolved_pdf_path": resolved_path,
        "path_exists": exists,
        "link_mode": str(row.get("link_mode")) if row.get("link_mode") is not None else None,
        "content_type": row.get("content_type"),
        "zotero_select_uri": ZOTERO_SELECT_URI.format(item_key=row["parent_key"]) if row.get("parent_key") else None,
        "zotero_open_pdf_uri": ZOTERO_OPEN_PDF_URI.format(attachment_key=row["attachment_key"]),
        "source_snapshot_path": _rel(snapshot_path),
        "source_snapshot_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "source_data_dir": str(config["zotero_data_dir"]),
        "cache_status": status,
    }


def _resolve_attachment_path(attachment_key: str, raw_path: str | None, zotero_data_dir: Path, storage_root: Path) -> tuple[str | None, bool]:
    if not raw_path:
        return None, True
    text = str(raw_path)
    if text.startswith("storage:"):
        candidate = storage_root / attachment_key / text.removeprefix("storage:")
    elif text.startswith("attachments:"):
        candidate = zotero_data_dir / text.removeprefix("attachments:")
    else:
        candidate = Path(text)
        if not candidate.is_absolute():
            return None, False
    resolved = candidate.resolve(strict=False)
    allowed_roots = [zotero_data_dir.resolve(strict=False), storage_root.resolve(strict=False)]
    return str(resolved), any(_is_relative_to(resolved, root) for root in allowed_roots)


def _source_item(row: ZoteroPdfSource, linked_document_id: int | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "creators": json.loads(row.creators_json or "[]"),
        "year": row.year,
        "publication_title": row.publication_title,
        "zotero_item_key": row.zotero_item_key,
        "zotero_attachment_key": row.zotero_attachment_key,
        "resolved_pdf_path": row.resolved_pdf_path,
        "path_exists": row.path_exists,
        "cache_status": row.cache_status,
        "zotero_select_uri": row.zotero_select_uri,
        "zotero_open_pdf_uri": row.zotero_open_pdf_uri,
        "already_imported": linked_document_id is not None,
        "linked_document_id": linked_document_id,
    }


def _import_status_payload(
    import_status: str,
    existing_documents: list[dict[str, Any]],
    *,
    recommended_action: str,
) -> dict[str, Any]:
    primary_document_id = existing_documents[0]["document_id"] if existing_documents else None
    imported = import_status in {"exact_imported", "sibling_imported", "path_imported", "fingerprint_imported"}
    matching_reasons = _matching_reasons(existing_documents)
    existing_document_title = existing_documents[0].get("title") if existing_documents else None
    return {
        "import_status": import_status,
        "imported": imported,
        "already_imported": imported,
        "existing_documents": existing_documents,
        "existing_document_id": primary_document_id,
        "existing_document_title": existing_document_title,
        "match_reason": matching_reasons[0] if matching_reasons else "none",
        "matching_reasons": matching_reasons or ["none"],
        "recommended_action": recommended_action,
        "primary_document_id": primary_document_id,
        "linked_document_id": primary_document_id,
    }


def _zotero_existing_document_payload(document: dict[str, Any], *, db_path: str | Path | None = None) -> dict[str, Any]:
    document_id = int(document.get("document_id") or 0)
    chapter_count = _chapter_count(document_id, db_path=db_path)
    matched_by = [_normalize_match_reason(str(reason)) for reason in document.get("duplicate_reasons") or []]
    warnings: list[str] = []
    document_type = str(document.get("document_type") or "unknown")
    if document_type == "book" and chapter_count == 0:
        warnings.append("book_import_completeness_unknown")
    return {
        "document_id": document_id,
        "title": document.get("title") or "",
        "document_type": document_type,
        "content_layer": document.get("content_layer") or "unknown",
        "chunk_count": int(document.get("chunk_count") or 0),
        "chapter_count": chapter_count,
        "matched_by": matched_by,
        "warnings": warnings,
    }


def _resolve_import_status_for_documents(documents: list[dict[str, Any]]) -> str:
    reasons = set(_matching_reasons(documents))
    if "same_zotero_attachment_key" in reasons:
        return "exact_imported"
    if "same_zotero_item_key" in reasons:
        return "sibling_imported"
    if "same_pdf_path" in reasons:
        return "path_imported"
    if "same_first_pages_fingerprint" in reasons:
        return "fingerprint_imported"
    return "unknown"


def _matching_reasons(documents: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for document in documents:
        for reason in document.get("matched_by") or []:
            normalized = _normalize_match_reason(str(reason))
            if normalized and normalized not in reasons:
                reasons.append(normalized)
    return reasons


def _normalize_match_reason(reason: str) -> str:
    if reason == "same_zotero_item_key_and_title":
        return "same_zotero_item_key"
    if reason in {
        "same_zotero_attachment_key",
        "same_zotero_item_key",
        "same_pdf_path",
        "same_first_pages_fingerprint",
    }:
        return reason
    return reason or "none"


def _duplicate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "duplicate_found": bool(payload.get("duplicate_found")),
        "duplicate_confidence": payload.get("duplicate_confidence"),
        "duplicate_reasons": payload.get("duplicate_reasons") or [],
        "recommended_action": payload.get("recommended_action"),
        "db_write_performed": bool(payload.get("db_write_performed")),
        "external_llm_called": bool(payload.get("external_llm_called")),
        "vector_store_write_performed": bool(payload.get("vector_store_write_performed")),
    }


def _chapter_count(document_id: int, *, db_path: str | Path | None = None) -> int:
    db = Path(db_path or import_duplicate_check_service.DEFAULT_DB_PATH)
    if not document_id or not db.is_file():
        return 0
    try:
        with sqlite3.connect(f"file:{db.resolve(strict=False).as_posix()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_chapters'"
            ).fetchone()
            if row is None:
                return 0
            count = connection.execute(
                "SELECT COUNT(*) FROM book_chapters WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            return int(count[0] or 0) if count else 0
    except sqlite3.Error:
        return 0


def _matches_query(row: ZoteroPdfSource, query: str) -> bool:
    alias_terms = {
        "edsr": ("enhanced", "deep", "residual"),
        "senet": ("squeeze", "excitation"),
    }
    haystack = " ".join([
        row.title or "",
        row.creators_json or "",
        row.year or "",
        row.publication_title or "",
        row.zotero_item_key or "",
        row.zotero_attachment_key or "",
        row.attachment_path_raw or "",
    ]).casefold()
    if query in haystack:
        return True
    terms = alias_terms.get(query)
    return bool(terms and all(term in haystack for term in terms))


def _load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ["zotero_storage_root", "zotero_db_snapshot", "zotero_data_dir"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing Zotero source config keys: {', '.join(missing)}")
    return config


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else DATA_PROJECT_ROOT / path


def _ensure_tables() -> None:
    Base.metadata.create_all(bind=engine, tables=[ZoteroPdfSource.__table__, DocumentSource.__table__])


def _creator_name(first: Any, last: Any, field_mode: Any) -> str:
    if field_mode == 1:
        return str(last or first or "").strip()
    return " ".join(part for part in [str(first or "").strip(), str(last or "").strip()] if part)


def _year(value: Any) -> str | None:
    text = str(value or "")
    for index in range(0, max(0, len(text) - 3)):
        part = text[index:index + 4]
        if part.isdigit() and 1500 <= int(part) <= 2200:
            return part
    return None


def _rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve(strict=False).relative_to(DATA_PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
