from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models import BookChapter, Document, KnowledgeChunk, ObjectCandidate
from app.models.object_candidate import ALLOWED_REVIEW_STATUSES
from app.services.book_import_contract import CHAPTER_STATUS_COMMITTED


class BookObjectImportError(ValueError):
    pass


def generate_chapter_object_bundle(
    document_id: int,
    chapter_id: int,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    document, chapter, chunks = _load_book_chapter_scope(document_id, chapter_id)
    note_summary = _chapter_note_gate_summary(document.id, chapter)
    evidence_refs = [_chunk_evidence_ref(document, chunk) for chunk in chunks]
    bundle_payload = {
        "schema_version": "book_chapter_object_suggestions_v1",
        "legacy_classification": "LEGACY_CHAPTER_OBJECT_BUNDLE",
        "scope": "book_chapter",
        "document_id": document.id,
        "document_title": document.title,
        "chapter_id": chapter.id,
        "chapter_index": chapter.chapter_index,
        "chapter_title": chapter.title,
        "allowed_chunk_ids": [chunk.id for chunk in chunks],
        "evidence_refs": evidence_refs,
        "output_contract": {
            "objects": [
                {
                    "object_key": "stable-lowercase-key",
                    "object_name": "Human readable object name",
                    "object_type": "concept|method|algorithm|metric|model|dataset|problem|assumption|mechanism_unit",
                    "source_mode": "note_anchored|highlight_anchored|chapter_global",
                    "source_note_ids": [],
                    "source_annotation_ids": [],
                    "source_chunk_ids": [],
                    "source_pages": [],
                    "source_confidence": "low|medium|high",
                    "object_candidate_origin": "note_anchored_object|highlight_anchored_object|chapter_global_object",
                    "merge_group_key": "canonical merge group key",
                    "canonical_object_key": "canonical object key after review",
                    "review_status": "accepted",
                    "confidence": "low|medium|high",
                    "aliases": [],
                    "description": "",
                    "topic_tags": [],
                    "problem_tags": [],
                    "mechanism_tags": [],
                    "inspiration_tags": [],
                    "evidence_refs": [{"chunk_id": evidence_refs[0]["chunk_id"]}] if evidence_refs else [],
                }
            ]
        },
    }
    bundle_text = _bundle_text(bundle_payload)
    legacy_reason = _legacy_bundle_reason(note_summary)
    return {
        "status": "LEGACY_CHAPTER_OBJECT_BUNDLE",
        "mode": "legacy_evidence_only_chapter_object_bundle",
        "bundle_classification": "LEGACY_CHAPTER_OBJECT_BUNDLE",
        "tri_source_contract_status": "planned_not_implemented",
        "formal_note_first_object_candidate_generation": "retired_legacy_chunk_only_bundle",
        "legacy_retirement_notice": (
            "旧版只基于 chunk evidence 的对象包已停用；新版将拆成高光对象和全文章节对象两路，"
            "并与笔记对象合并。"
        ),
        "blockers": [legacy_reason],
        "legacy_reason": legacy_reason,
        "review_pipeline_required": [
            "sync_zotero_notes",
            "note_correction_package",
            "note_correction_review",
            "note_classification_package",
            "note_classification_review",
            "tri_source_object_candidate_contract",
            "object_merge_canonicalization_review",
            "object_review",
            "object_relation_review",
            "high_level_insight_review",
        ],
        "document_id": document.id,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "chapter_index": chapter.chapter_index,
        **note_summary,
        "chunk_count": len(chunks),
        "evidence_count": len(evidence_refs),
        "legacy_bundle_payload": bundle_payload,
        "legacy_bundle_text": bundle_text,
        "source_trace": _source_trace(document, chapter, chunks),
        "dry_run": dry_run,
        "db_write_performed": False,
        "external_llm_called": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "zotero_db_write_performed": False,
        "object_candidate_row_write_performed": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "insight_generated": False,
        "mechanism_generated": False,
    }


def preview_chapter_objects(
    document_id: int,
    chapter_id: int,
    json_text: str,
) -> dict[str, Any]:
    document, chapter, chunks = _load_book_chapter_scope(document_id, chapter_id)
    allowed_chunk_ids = {chunk.id for chunk in chunks}
    parsed = _parse_objects_json(json_text)
    objects = parsed["objects"]
    validation_errors = _validate_objects(objects, allowed_chunk_ids)
    warnings = _duplicate_warnings(document_id, chapter_id, objects)
    parsed_objects = [_normalized_object(obj, allowed_chunk_ids) for obj in objects]
    return {
        "status": "ok" if not validation_errors else "invalid",
        "document_id": document.id,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "parsed_objects": parsed_objects,
        "validation_errors": validation_errors,
        "warnings": warnings,
        "source_trace": _source_trace(document, chapter, chunks),
        "db_write_performed": False,
        "external_llm_called": False,
    }


def commit_chapter_objects(
    document_id: int,
    chapter_id: int,
    json_text: str,
    *,
    confirm_chapter_id: int,
) -> dict[str, Any]:
    if int(confirm_chapter_id) != int(chapter_id):
        raise BookObjectImportError("confirm_chapter_id does not match chapter_id")

    document, chapter, chunks = _load_book_chapter_scope(document_id, chapter_id)
    allowed_chunk_ids = {chunk.id for chunk in chunks}
    parsed = _parse_objects_json(json_text)
    objects = parsed["objects"]
    validation_errors = _validate_objects(objects, allowed_chunk_ids)
    if validation_errors:
        raise BookObjectImportError("; ".join(validation_errors))

    import_job_id = f"book-{document_id}-chapter-{chapter_id}"
    inserted = 0
    skipped_rejected = 0
    warnings = _duplicate_warnings(document_id, chapter_id, objects)
    committed_at = datetime.now(timezone.utc).replace(microsecond=0)

    with SessionLocal() as session:
        chapter_row = session.scalar(
            select(BookChapter).where(
                BookChapter.id == chapter_id,
                BookChapter.document_id == document_id,
            )
        )
        if chapter_row is None:
            raise BookObjectImportError("book chapter not found")

        for obj in objects:
            normalized = _normalized_object(obj, allowed_chunk_ids)
            review_status = normalized["review_status"]
            if review_status == "rejected":
                skipped_rejected += 1
                continue
            if review_status not in ALLOWED_REVIEW_STATUSES:
                raise BookObjectImportError(
                    f"object_key={normalized['object_key']}: invalid review_status={review_status!r}"
                )

            candidate = ObjectCandidate(
                document_id=document_id,
                chapter_id=chapter_id,
                import_job_id=import_job_id,
                object_key=normalized["object_key"],
                object_name=normalized["object_name"],
                object_type=normalized["object_type"],
                review_status=review_status,
                status="candidate",
                confidence=normalized["confidence"],
                description=normalized["description"] or None,
                user_comment=normalized["user_comment"] or None,
                mapping_status="mapped" if normalized["mapped_chunk_ids"] else "not_mapped",
                created_by="book_chapter_import",
            )
            candidate.set_aliases(normalized["aliases"])
            candidate.set_four_layer_tags(
                normalized["topic_tags"],
                normalized["problem_tags"],
                normalized["mechanism_tags"],
                normalized["inspiration_tags"],
            )
            candidate.set_evidence_refs(normalized["evidence_refs"])
            candidate.set_mapped_chunk_ids(normalized["mapped_chunk_ids"])
            candidate.set_warnings(normalized["warnings"] + warnings)
            session.add(candidate)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise BookObjectImportError(
                    f"duplicate object_key in chapter import job: {normalized['object_key']}"
                ) from exc
            inserted += 1

        chapter_row.object_import_status = CHAPTER_STATUS_COMMITTED
        chapter_row.object_bundle_job_id = import_job_id
        chapter_row.object_committed_at = committed_at
        chapter_row.updated_at = committed_at.replace(tzinfo=None)
        session.commit()

    return {
        "status": "committed",
        "document_id": document.id,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "import_job_id": import_job_id,
        "inserted_count": inserted,
        "skipped_rejected": skipped_rejected,
        "warnings": warnings,
        "chapter_status": CHAPTER_STATUS_COMMITTED,
        "committed_at": committed_at.isoformat(),
        "db_write_performed": True,
        "external_llm_called": False,
        "lancedb_write_performed": False,
    }


def _load_book_chapter_scope(document_id: int, chapter_id: int) -> tuple[Document, BookChapter, list[KnowledgeChunk]]:
    with SessionLocal() as session:
        document = session.scalar(select(Document).where(Document.id == document_id))
        if document is None:
            raise BookObjectImportError("document not found")
        if document.document_type != "book":
            raise BookObjectImportError("document is not a book")
        chapter = session.scalar(
            select(BookChapter).where(
                BookChapter.id == chapter_id,
                BookChapter.document_id == document_id,
            )
        )
        if chapter is None:
            raise BookObjectImportError("book chapter not found")
        chunks = list(
            session.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.document_id == document_id,
                    KnowledgeChunk.chapter_id == chapter_id,
                )
                .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
            )
        )
        session.expunge(document)
        session.expunge(chapter)
        for chunk in chunks:
            session.expunge(chunk)
    return document, chapter, chunks


def _chapter_note_gate_summary(document_id: int, chapter: BookChapter) -> dict[str, int]:
    if chapter.pdf_page_start is None or chapter.pdf_page_end is None:
        return _empty_note_gate_summary()
    with SessionLocal() as session:
        inspector = inspect(session.bind)
        if not inspector.has_table("zotero_inspiration_notes"):
            return _empty_note_gate_summary()
        column_names = {column["name"] for column in inspector.get_columns("zotero_inspiration_notes")}
        required = {"id", "matched_document_id", "pdf_page"}
        if not required.issubset(column_names):
            return _empty_note_gate_summary()
        selected = [
            name
            for name in ["selected_text", "note_text", "source"]
            if name in column_names
        ]
        rows = session.execute(
            text(
                f"""
                SELECT id, {', '.join(selected) if selected else "'' AS selected_text"}
                FROM zotero_inspiration_notes
                WHERE matched_document_id = :document_id
                  AND pdf_page BETWEEN :page_start AND :page_end
                """
            ),
            {
                "document_id": document_id,
                "page_start": int(chapter.pdf_page_start),
                "page_end": int(chapter.pdf_page_end),
            },
        ).mappings().all()
    row_dicts = [dict(row) for row in rows]
    annotation_count = len(row_dicts)
    user_note_count = sum(1 for row in row_dicts if _has_user_note_text(row))
    evidence_only_count = sum(1 for row in row_dicts if _is_evidence_only(row))
    return {
        "annotation_count": annotation_count,
        "synced_note_count": annotation_count,
        "user_note_count": user_note_count,
        "evidence_only_count": evidence_only_count,
    }


def _empty_note_gate_summary() -> dict[str, int]:
    return {
        "annotation_count": 0,
        "synced_note_count": 0,
        "user_note_count": 0,
        "evidence_only_count": 0,
    }


def _has_user_note_text(row: Any) -> bool:
    return bool(str(row.get("note_text") if hasattr(row, "get") else "").strip())


def _is_evidence_only(row: Any) -> bool:
    if _has_user_note_text(row):
        return False
    selected_text = row.get("selected_text") if hasattr(row, "get") else ""
    return bool(str(selected_text or "").strip())


def _legacy_bundle_reason(note_summary: dict[str, int]) -> str:
    if int(note_summary.get("synced_note_count") or 0) <= 0:
        return "retired_legacy_chunk_only_bundle_no_zotero_notes"
    if int(note_summary.get("user_note_count") or 0) <= 0 and int(note_summary.get("evidence_only_count") or 0) > 0:
        return "retired_legacy_chunk_only_bundle_highlight_source_planned"
    if int(note_summary.get("user_note_count") or 0) <= 0:
        return "retired_legacy_chunk_only_bundle_note_source_empty"
    return "retired_legacy_chunk_only_bundle_tri_source_contract_required"


def _parse_objects_json(json_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise BookObjectImportError(f"invalid JSON: {exc.msg}") from exc
    if isinstance(parsed, list):
        parsed = {"objects": parsed}
    if not isinstance(parsed, dict):
        raise BookObjectImportError("JSON root must be an object")
    objects = parsed.get("objects")
    if not isinstance(objects, list):
        raise BookObjectImportError("JSON must contain objects list")
    return {"objects": [obj for obj in objects if isinstance(obj, dict)]}


def _validate_objects(objects: list[dict[str, Any]], allowed_chunk_ids: set[int]) -> list[str]:
    errors: list[str] = []
    seen_keys: set[str] = set()
    for index, obj in enumerate(objects):
        object_key = _object_key(obj)
        if not object_key:
            errors.append(f"objects[{index}].object_key required")
        elif object_key in seen_keys:
            errors.append(f"objects[{index}].object_key duplicate in chapter: {object_key}")
        seen_keys.add(object_key)
        if not str(obj.get("object_name") or "").strip():
            errors.append(f"objects[{index}].object_name required")
        chunk_ids = _object_chunk_ids(obj)
        if not chunk_ids:
            errors.append(f"objects[{index}].evidence_refs or mapped_chunk_ids required")
        outside = sorted(chunk_id for chunk_id in chunk_ids if chunk_id not in allowed_chunk_ids)
        if outside:
            errors.append(f"objects[{index}] references chunks outside chapter: {outside}")
    return errors


def _duplicate_warnings(document_id: int, chapter_id: int, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [_object_key(obj) for obj in objects if _object_key(obj)]
    if not keys:
        return []
    warnings: list[dict[str, Any]] = []
    with SessionLocal() as session:
        same_chapter = session.execute(
            select(ObjectCandidate.object_key, func.count(ObjectCandidate.id))
            .where(
                ObjectCandidate.document_id == document_id,
                ObjectCandidate.chapter_id == chapter_id,
                ObjectCandidate.object_key.in_(keys),
                ObjectCandidate.status == "candidate",
            )
            .group_by(ObjectCandidate.object_key)
        ).all()
        across_chapters = session.execute(
            select(ObjectCandidate.object_key, ObjectCandidate.chapter_id)
            .where(
                ObjectCandidate.document_id == document_id,
                ObjectCandidate.chapter_id != chapter_id,
                ObjectCandidate.object_key.in_(keys),
                ObjectCandidate.status == "candidate",
            )
        ).all()
    for object_key, count in same_chapter:
        if count:
            warnings.append({"warning": "duplicate_in_chapter", "object_key": object_key})
    for object_key, other_chapter_id in across_chapters:
        warnings.append(
            {
                "warning": "duplicate_across_chapters",
                "object_key": object_key,
                "other_chapter_id": other_chapter_id,
            }
        )
    return warnings


def _normalized_object(obj: dict[str, Any], allowed_chunk_ids: set[int]) -> dict[str, Any]:
    evidence_refs = _normalized_evidence_refs(obj.get("evidence_refs") or [])
    mapped_chunk_ids = sorted(_object_chunk_ids(obj))
    return {
        "object_key": _object_key(obj),
        "object_name": str(obj.get("object_name") or "").strip(),
        "object_type": str(obj.get("object_type") or "unknown").strip() or "unknown",
        "review_status": str(obj.get("review_status") or "accepted").strip().lower(),
        "confidence": str(obj.get("confidence") or "medium").strip().lower(),
        "aliases": _string_list(obj.get("aliases")),
        "description": str(obj.get("description") or "").strip(),
        "user_comment": str(obj.get("user_comment") or "").strip(),
        "topic_tags": _tag_list(obj.get("topic_tags")),
        "problem_tags": _tag_list(obj.get("problem_tags")),
        "mechanism_tags": _tag_list(obj.get("mechanism_tags")),
        "inspiration_tags": _tag_list(obj.get("inspiration_tags")),
        "evidence_refs": evidence_refs,
        "mapped_chunk_ids": [chunk_id for chunk_id in mapped_chunk_ids if chunk_id in allowed_chunk_ids],
        "warnings": _warning_list(obj.get("warnings")),
    }


def _object_key(obj: dict[str, Any]) -> str:
    raw = str(obj.get("object_key") or obj.get("object_name") or "").strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", raw)
    return raw.strip("-_")


def _object_chunk_ids(obj: dict[str, Any]) -> set[int]:
    chunk_ids: set[int] = set()
    for value in obj.get("mapped_chunk_ids") or []:
        _add_int(chunk_ids, value)
    for ref in obj.get("evidence_refs") or []:
        if isinstance(ref, dict):
            _add_int(chunk_ids, ref.get("chunk_id"))
    return chunk_ids


def _normalized_evidence_refs(refs: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(refs, list):
        return normalized
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        item = dict(ref)
        if item.get("chunk_id") is not None:
            item["chunk_id"] = int(item["chunk_id"])
        normalized.append(item)
    return normalized


def _tag_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("tag") or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            tags.append(text)
    return tags


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _warning_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _add_int(target: set[int], value: Any) -> None:
    try:
        if value is not None and str(value).strip() != "":
            target.add(int(value))
    except (TypeError, ValueError):
        return


def _chunk_evidence_ref(document: Document, chunk: KnowledgeChunk) -> dict[str, Any]:
    return {
        "document_id": document.id,
        "document_title": document.title,
        "chunk_id": chunk.id,
        "heading_path": chunk.heading_path,
        "pdf_page_start": chunk.pdf_page_start,
        "pdf_page_end": chunk.pdf_page_end,
        "snippet": _snippet(chunk.chunk_text, 360),
    }


def _source_trace(document: Document, chapter: BookChapter, chunks: list[KnowledgeChunk]) -> dict[str, Any]:
    return {
        "scope": "book_chapter",
        "document_id": document.id,
        "document_title": document.title,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "allowed_chunk_ids": [chunk.id for chunk in chunks],
    }


def _bundle_text(payload: dict[str, Any]) -> str:
    evidence_lines = []
    for ref in payload["evidence_refs"]:
        evidence_lines.append(
            f"- chunk_id={ref['chunk_id']} pages={ref.get('pdf_page_start')}-{ref.get('pdf_page_end')}: {ref.get('snippet')}"
        )
    return "\n".join(
        [
            "# Book Chapter Object Import Bundle",
            "",
            f"Book: {payload['document_title']} (document_id={payload['document_id']})",
            f"Chapter: {payload['chapter_index']} - {payload['chapter_title']} (chapter_id={payload['chapter_id']})",
            "",
            "Return JSON only. Use only the allowed chunk_id values from this chapter.",
            "",
            "Allowed evidence:",
            *evidence_lines,
            "",
            "Output contract:",
            json.dumps(payload["output_contract"], ensure_ascii=False, indent=2),
        ]
    )


def _snippet(text: str | None, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
