from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.core.paths import DATA_PROJECT_ROOT, DEFAULT_DB_PATH
from app.services import pdf_conversion_service


NO_WRITE_FLAGS: dict[str, bool] = {
    "db_write_performed": False,
    "core_db_write_performed": False,
    "llm_called": False,
    "external_llm_called": False,
    "mechanism_generated": False,
    "final_hypothesis_created": False,
    "vector_store_write_performed": False,
    "ocr_executed": False,
    "marker_executed": False,
}


def check_duplicate_import(
    payload: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    fingerprint_candidate_limit: int = 12,
) -> dict[str, Any]:
    pdf_path = _clean(payload.get("pdf_path"))
    title = _clean(payload.get("title"))
    item_key = _clean(payload.get("zotero_item_key"))
    attachment_key = _clean(payload.get("zotero_attachment_key"))
    warnings: list[str] = []

    db = Path(db_path or DEFAULT_DB_PATH)
    if not db.is_file():
        return _response(
            duplicate_found=False,
            duplicate_reasons=[],
            existing_documents=[],
            warnings=["duplicate_check_db_missing"],
            fingerprint_status="skipped",
        )

    try:
        with _connect_readonly(db) as connection:
            connection.row_factory = sqlite3.Row
            documents = _load_document_rows(connection)
            sources_by_document = _load_document_sources(connection)
            chunk_counts = _load_chunk_counts(connection, [doc["id"] for doc in documents])
    except sqlite3.Error as exc:
        return _response(
            duplicate_found=False,
            duplicate_reasons=[],
            existing_documents=[],
            warnings=[f"duplicate_check_db_read_failed:{type(exc).__name__}"],
            fingerprint_status="skipped",
        )

    doc_records = [
        _document_record(doc, sources_by_document.get(int(doc["id"]), []), chunk_counts.get(int(doc["id"]), 0))
        for doc in documents
    ]

    matched: dict[int, dict[str, Any]] = {}
    global_reasons: list[str] = []

    def add_match(record: dict[str, Any], reason: str) -> None:
        document_id = int(record["document_id"])
        existing = matched.setdefault(document_id, {**record, "duplicate_reasons": []})
        if reason not in existing["duplicate_reasons"]:
            existing["duplicate_reasons"].append(reason)
        if reason not in global_reasons:
            global_reasons.append(reason)

    request_title = _normalize_title(title)
    request_path_keys = _path_keys(pdf_path)

    for record in doc_records:
        source_attachment_keys = {
            _clean(source.get("zotero_attachment_key"))
            for source in record["document_sources"]
            if _clean(source.get("zotero_attachment_key"))
        }
        if attachment_key and attachment_key in source_attachment_keys:
            add_match(record, "same_zotero_attachment_key")

        source_item_keys = {
            _clean(source.get("zotero_item_key"))
            for source in record["document_sources"]
            if _clean(source.get("zotero_item_key"))
        }
        document_item_key = _clean(record.get("zotero_item_key"))
        if item_key and (item_key in source_item_keys or item_key == document_item_key):
            add_match(record, "same_zotero_item_key")

        if request_path_keys:
            candidate_paths = [record.get("pdf_path"), record.get("source_path")]
            for source in record["document_sources"]:
                candidate_paths.extend([source.get("pdf_path"), source.get("source_pdf_path")])
            if any(request_path_keys.intersection(_path_keys(candidate)) for candidate in candidate_paths):
                add_match(record, "same_pdf_path")

    request_fingerprint, fingerprint_meta = first_pages_text_fingerprint(pdf_path) if pdf_path else (None, {"status": "skipped"})
    warnings.extend(fingerprint_meta.get("warnings") or [])
    if request_fingerprint:
        stored_matches = [
            record
            for record in doc_records
            if any(source.get("first_pages_fingerprint") == request_fingerprint for source in record["document_sources"])
        ]
        for record in stored_matches:
            add_match(record, "same_first_pages_fingerprint")

        candidates = _fingerprint_candidates(doc_records, request_title, pdf_path, limit=fingerprint_candidate_limit)
        for record in candidates:
            if int(record["document_id"]) in matched and "same_pdf_path" in matched[int(record["document_id"])]["duplicate_reasons"]:
                continue
            candidate_path = record.get("pdf_path") or record.get("source_path")
            if not candidate_path:
                continue
            candidate_fingerprint, candidate_meta = first_pages_text_fingerprint(candidate_path)
            if candidate_meta.get("status") == "blocked" and candidate_meta.get("warning"):
                warnings.append(str(candidate_meta["warning"]))
            if candidate_fingerprint and candidate_fingerprint == request_fingerprint:
                add_match(record, "same_first_pages_fingerprint")

    if request_title and pdf_path:
        request_meta = _file_page_size_meta(pdf_path)
        if request_meta.get("file_size") and request_meta.get("page_count"):
            for record in _title_candidates(doc_records, request_title):
                candidate_path = record.get("pdf_path") or record.get("source_path")
                if not candidate_path:
                    continue
                candidate_meta = _file_page_size_meta(candidate_path)
                if (
                    candidate_meta.get("file_size") == request_meta.get("file_size")
                    and candidate_meta.get("page_count") == request_meta.get("page_count")
                ):
                    add_match(record, "same_title_page_count_file_size")
        elif request_meta.get("warning"):
            warnings.append(str(request_meta["warning"]))

    existing_documents = sorted(
        matched.values(),
        key=lambda item: (reason_rank(item.get("duplicate_reasons", [])), int(item["document_id"])),
    )
    duplicate_reasons = sorted(global_reasons, key=_reason_priority)
    duplicate_found = bool(existing_documents)
    confidence = _confidence(duplicate_reasons)
    recommended_action = (
        "open_existing_document"
        if duplicate_found and confidence == "high"
        else "ask_user_replace_or_reimport"
        if duplicate_found
        else "continue_import"
    )
    return _response(
        duplicate_found=duplicate_found,
        duplicate_reasons=duplicate_reasons,
        existing_documents=[_public_document(item) for item in existing_documents],
        duplicate_confidence=confidence,
        recommended_action=recommended_action,
        warnings=_dedupe(warnings),
        fingerprint_status=fingerprint_meta.get("status", "skipped"),
        first_pages_fingerprint=request_fingerprint,
    )


def first_pages_text_fingerprint(pdf_path: str | Path | None) -> tuple[str | None, dict[str, Any]]:
    if not pdf_path:
        return None, {"status": "skipped", "warnings": ["fingerprint_skipped_pdf_path_missing"]}
    payload = pdf_conversion_service.preview_pdf_text_layer_sample(
        pdf_path,
        max_pages=3,
        max_chars=12000,
    )
    if payload.get("status") != "OK":
        return None, {
            "status": "blocked",
            "warning": f"first_pages_fingerprint_unavailable:{payload.get('error') or 'unknown'}",
            "warnings": [f"first_pages_fingerprint_unavailable:{payload.get('error') or 'unknown'}"],
            "payload": payload,
        }
    normalized = normalize_fingerprint_text(payload.get("text_sample") or "")
    if not normalized:
        return None, {
            "status": "blocked",
            "warnings": ["first_pages_fingerprint_unavailable:empty_text_sample"],
            "payload": payload,
        }
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}", {
        "status": "available",
        "parser_backend": payload.get("parser_backend"),
        "page_count": payload.get("page_count"),
        "sample_char_count": payload.get("sample_char_count"),
        "warnings": [],
    }


def normalize_fingerprint_text(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"<!--\s*PDF_PAGE:\s*\d+\s*-->", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _load_document_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(connection, "documents"):
        return []
    columns = _columns(connection, "documents")
    wanted = [
        "id",
        "title",
        "document_type",
        "content_layer",
        "source_path",
        "pdf_path",
        "zotero_key",
    ]
    selected = [column for column in wanted if column in columns]
    if "id" not in selected:
        return []
    sql = f"SELECT {', '.join(selected)} FROM documents ORDER BY id"
    return list(connection.execute(sql).fetchall())


def _load_document_sources(connection: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    if not _table_exists(connection, "document_sources"):
        return {}
    columns = _columns(connection, "document_sources")
    wanted = [
        "document_id",
        "source_type",
        "zotero_item_key",
        "zotero_attachment_key",
        "zotero_source_id",
        "zotero_select_uri",
        "zotero_open_pdf_uri",
        "source_trace_json",
        "pdf_path",
        "source_pdf_path",
        "first_pages_fingerprint",
    ]
    selected = [column for column in wanted if column in columns]
    if "document_id" not in selected:
        return {}
    result: dict[int, list[dict[str, Any]]] = {}
    for row in connection.execute(f"SELECT {', '.join(selected)} FROM document_sources").fetchall():
        source = dict(row)
        trace = _parse_json(source.get("source_trace_json"))
        source.setdefault("pdf_path", trace.get("pdf_path"))
        source.setdefault("source_pdf_path", trace.get("source_pdf_path"))
        source.setdefault("first_pages_fingerprint", trace.get("first_pages_fingerprint"))
        if not source.get("zotero_item_key"):
            source["zotero_item_key"] = trace.get("zotero_item_key")
        if not source.get("zotero_attachment_key"):
            source["zotero_attachment_key"] = trace.get("zotero_attachment_key")
        result.setdefault(int(source["document_id"]), []).append(source)
    return result


def _load_chunk_counts(connection: sqlite3.Connection, document_ids: list[int]) -> dict[int, int]:
    if not document_ids or not _table_exists(connection, "knowledge_chunks"):
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    rows = connection.execute(
        f"SELECT document_id, COUNT(*) AS chunk_count FROM knowledge_chunks WHERE document_id IN ({placeholders}) GROUP BY document_id",
        document_ids,
    ).fetchall()
    return {int(row["document_id"]): int(row["chunk_count"]) for row in rows}


def _document_record(
    row: sqlite3.Row,
    sources: list[dict[str, Any]],
    chunk_count: int,
) -> dict[str, Any]:
    data = dict(row)
    return {
        "document_id": int(data.get("id")),
        "title": data.get("title") or "",
        "document_type": data.get("document_type") or "",
        "content_layer": data.get("content_layer") or "",
        "source_path": data.get("source_path"),
        "pdf_path": data.get("pdf_path"),
        "zotero_item_key": data.get("zotero_key"),
        "zotero_attachment_key": _first_nonempty(source.get("zotero_attachment_key") for source in sources),
        "chunk_count": int(chunk_count),
        "document_sources": sources,
    }


def _public_document(record: dict[str, Any]) -> dict[str, Any]:
    source = (record.get("document_sources") or [{}])[0]
    return {
        "document_id": record.get("document_id"),
        "title": record.get("title"),
        "document_type": record.get("document_type") or "unknown",
        "content_layer": record.get("content_layer") or "unknown",
        "chunk_count": int(record.get("chunk_count") or 0),
        "zotero_item_key": source.get("zotero_item_key") or record.get("zotero_item_key"),
        "zotero_attachment_key": source.get("zotero_attachment_key") or record.get("zotero_attachment_key"),
        "pdf_path": record.get("pdf_path") or source.get("source_pdf_path") or source.get("pdf_path"),
        "duplicate_reasons": record.get("duplicate_reasons", []),
    }


def _fingerprint_candidates(
    records: list[dict[str, Any]],
    normalized_title: str,
    pdf_path: str | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    request_stem = _normalize_title(Path(pdf_path).stem) if pdf_path else ""
    candidates = []
    for record in records:
        path = record.get("pdf_path") or record.get("source_path")
        if not path or not Path(str(path)).expanduser().is_file():
            continue
        title_match = normalized_title and _normalize_title(record.get("title")) == normalized_title
        stem_match = request_stem and _normalize_title(Path(str(path)).stem) == request_stem
        if title_match or stem_match:
            candidates.append(record)
    return candidates[: max(0, int(limit))]


def _title_candidates(records: list[dict[str, Any]], normalized_title: str) -> list[dict[str, Any]]:
    return [record for record in records if normalized_title and _normalize_title(record.get("title")) == normalized_title]


def _file_page_size_meta(pdf_path: str | Path) -> dict[str, Any]:
    path = Path(str(pdf_path)).expanduser()
    if not path.is_file():
        return {"warning": "title_page_size_check_skipped:file_missing"}
    meta: dict[str, Any] = {"file_size": path.stat().st_size}
    preview = pdf_conversion_service.preview_pdf_text_layer_sample(path, max_pages=1, max_chars=500)
    if preview.get("status") == "OK":
        meta["page_count"] = preview.get("page_count")
    else:
        meta["warning"] = f"title_page_size_check_skipped:{preview.get('error') or 'page_count_unavailable'}"
    return meta


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _parse_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _path_keys(value: Any) -> set[str]:
    text = _clean(value)
    if not text:
        return set()
    candidates = {text}
    path = Path(text)
    if not path.is_absolute():
        candidates.add(str((DATA_PROJECT_ROOT / path).resolve(strict=False)))
    candidates.add(str(path.expanduser().resolve(strict=False)))
    return {_normalize_path(candidate) for candidate in candidates if candidate}


def _normalize_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    try:
        text = Path(text).expanduser().resolve(strict=False).as_posix()
    except (OSError, RuntimeError, ValueError):
        pass
    return re.sub(r"/+", "/", text).casefold()


def _normalize_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _first_nonempty(values: Any) -> str | None:
    for value in values:
        text = _clean(value)
        if text:
            return text
    return None


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _reason_priority(reason: str) -> int:
    priorities = {
        "same_zotero_attachment_key": 0,
        "same_zotero_item_key": 1,
        "same_zotero_item_key_and_title": 1,
        "same_pdf_path": 2,
        "same_first_pages_fingerprint": 3,
        "same_title_page_count_file_size": 4,
    }
    return priorities.get(reason, 99)


def reason_rank(reasons: list[str]) -> int:
    return min((_reason_priority(reason) for reason in reasons), default=99)


def _confidence(reasons: list[str]) -> str:
    if any(reason in reasons for reason in ("same_zotero_attachment_key", "same_zotero_item_key", "same_zotero_item_key_and_title", "same_pdf_path", "same_first_pages_fingerprint")):
        return "high"
    if "same_title_page_count_file_size" in reasons:
        return "medium"
    return "low"


def _response(
    *,
    duplicate_found: bool,
    duplicate_reasons: list[str],
    existing_documents: list[dict[str, Any]],
    duplicate_confidence: str = "low",
    recommended_action: str | None = None,
    warnings: list[str] | None = None,
    fingerprint_status: str = "skipped",
    first_pages_fingerprint: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "OK",
        "duplicate_found": bool(duplicate_found),
        "duplicate_confidence": duplicate_confidence,
        "duplicate_reasons": duplicate_reasons,
        "existing_documents": existing_documents,
        "recommended_action": recommended_action or ("open_existing_document" if duplicate_found else "continue_import"),
        "warnings": warnings or [],
        "fingerprint_status": fingerprint_status,
        "first_pages_fingerprint": first_pages_fingerprint,
        **NO_WRITE_FLAGS,
    }
