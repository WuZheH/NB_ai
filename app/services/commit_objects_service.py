from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.paths import DATA_DIR, DATA_PROJECT_ROOT, DEFAULT_DB_PATH, OUTPUTS_DIR
from app.db.session import SessionLocal
from app.models import KnowledgeChunk
from app.models.object_candidate import (
    ALLOWED_REVIEW_STATUSES,
    ALLOWED_MAPPING_STATUSES,
    ObjectCandidate,
)
from app.services import (
    retrieval_generation_mutation_service,
    retrieval_generation_service,
    vector_store_service,
)
from app.services.import_preview_service import (
    ImportPreviewError, _existing_job_dir, _read_json, _write_json, _relative,
)

COMMIT_OBJECTS_BACKUP_ROOT = OUTPUTS_DIR / "phase18e_commitobjects_backup"
DB_PATH = DEFAULT_DB_PATH
COMMIT_OBJECTS_FILE = "commit_objects_result.json"


def commit_objects_from_staging(
    import_job_id: str,
    *,
    persist_result: bool = True,
) -> dict[str, Any]:
    job_dir = _existing_job_dir(import_job_id)

    commit_paper_path = job_dir / "commit_result.json"
    reviewed_path = job_dir / "reviewed_object_tag_package.json"
    source_trace_path = job_dir / "source_trace.json"
    commit_objects_path = job_dir / COMMIT_OBJECTS_FILE

    if not commit_paper_path.is_file():
        raise ImportPreviewError("Paper not committed. Run commit-paper first.")
    if not reviewed_path.is_file():
        raise ImportPreviewError("reviewed_object_tag_package.json not found.")

    commit_paper = _read_json(commit_paper_path)
    document_id = commit_paper.get("document_id")
    if document_id is None:
        raise ImportPreviewError("commit_result.json has no document_id.")

    reviewed = _read_json(reviewed_path)
    source_trace = _read_json(source_trace_path) if source_trace_path.is_file() else {}

    # Idempotency
    if commit_objects_path.is_file():
        existing = _read_json(commit_objects_path)
        return {
            "status": "already_committed",
            "import_job_id": import_job_id,
            "document_id": document_id,
            "inserted_count": existing.get("inserted_count", 0),
            "committed_at": existing.get("committed_at"),
            "message": "Reviewed objects already committed for this import job.",
            "core_db_write_performed": False,
            "external_llm_called": False,
        }

    # Backup
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = COMMIT_OBJECTS_BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_dir / "research_memory_pre_objects_commit.db")

    # Build chunk index for mapping
    chunk_index = _build_chunk_index(document_id)

    # Filter and validate objects
    objects = reviewed.get("objects") or []
    inserted = 0
    skipped_rejected = 0
    skipped_suggested = 0
    mapping_stats = {"mapped": 0, "partial": 0, "failed": 0, "not_mapped": 0}

    STRICTLY_FORBIDDEN = {"confirmed", "evidence_supported", "committed"}

    with SessionLocal() as session:
        for obj in objects:
            review_status = str(obj.get("review_status") or "").strip().lower()

            if review_status in STRICTLY_FORBIDDEN:
                raise ImportPreviewError(
                    f"object_key={obj.get('object_key')}: review_status='{review_status}' is forbidden."
                )
            if review_status == "rejected":
                skipped_rejected += 1
                continue
            if review_status == "suggested":
                skipped_suggested += 1
                continue
            if review_status not in ALLOWED_REVIEW_STATUSES:
                raise ImportPreviewError(
                    f"object_key={obj.get('object_key')}: invalid review_status='{review_status}'."
                )

            # Map evidence_refs → chunk_ids
            evidence_refs_raw = obj.get("evidence_refs") or []
            mapped_ids, mapping_status, map_warnings = _map_evidence_refs(
                evidence_refs_raw, chunk_index
            )
            mapping_stats[mapping_status] = mapping_stats.get(mapping_status, 0) + 1

            # Extract four-layer tags from reviewed format
            topic_tags = _extract_tag_names(obj.get("topic_tags"))
            problem_tags = _extract_tag_names(obj.get("problem_tags"))
            mechanism_tags = _extract_tag_names(obj.get("mechanism_tags"))
            inspiration_tags = _extract_tag_names(obj.get("inspiration_tags"))

            candidate = ObjectCandidate(
                document_id=document_id,
                import_job_id=import_job_id,
                object_key=str(obj.get("object_key") or "").strip(),
                object_name=str(obj.get("object_name") or "").strip(),
                object_type=str(obj.get("object_type") or "unknown").strip(),
                review_status=review_status,
                status="candidate",
                confidence=str(obj.get("confidence") or "medium").strip(),
                description=str(obj.get("description") or "") if obj.get("description") else None,
                user_comment=str(obj.get("user_comment") or "") if obj.get("user_comment") else None,
                source_origin=str(obj.get("source_origin") or "").strip() or None,
                necessity_judgment=str(obj.get("necessity_judgment") or "").strip() or None,
                importance_score=str(obj.get("importance_score") or "").strip() or None,
                mapping_status=mapping_status,
                created_by="user_reviewed",
            )
            candidate.set_aliases(obj.get("aliases") or [])
            candidate.set_four_layer_tags(topic_tags, problem_tags, mechanism_tags, inspiration_tags)
            candidate.set_evidence_refs(evidence_refs_raw)
            candidate.set_source_note_ids(obj.get("source_note_ids") or [])
            candidate.set_mapped_chunk_ids(mapped_ids)
            candidate.set_warnings((obj.get("warnings") or []) + map_warnings)

            try:
                # A duplicate row must only discard its own insert.  A bare
                # session.rollback() would also undo every earlier flush in
                # this transaction while the returned inserted_count keeps
                # counting them, so each candidate insert gets its own
                # SAVEPOINT instead.
                with session.begin_nested():
                    session.add(candidate)
                    session.flush()
                    inserted += 1
            except IntegrityError:
                continue

        session.commit()

    committed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    result = {
        "status": "committed",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "inserted_count": inserted,
        "skipped_rejected": skipped_rejected,
        "skipped_suggested": skipped_suggested,
        "mapping_status_counts": mapping_stats,
        "backup_dir": str(backup_dir.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
        "committed_at": committed_at,
        "core_db_write_performed": True,
        "external_llm_called": False,
    }
    if persist_result:
        _write_json(commit_objects_path, result)

    return result


def remap_existing_object_candidates(import_job_id: str | None = None) -> dict[str, Any]:
    """Recompute mapping_status and mapped_chunk_ids for existing object_candidates.

    Useful after improving the mapping algorithm.
    No new objects created, only updates mapping fields.
    """
    with SessionLocal() as session:
        if import_job_id:
            candidates = session.scalars(
                select(ObjectCandidate).where(ObjectCandidate.import_job_id == import_job_id)
            ).all()
        else:
            candidates = session.scalars(select(ObjectCandidate)).all()

        if not candidates:
            return {"status": "ok", "message": "No object candidates to remap.", "count": 0}

        doc_ids = {c.document_id for c in candidates if c.document_id is not None}
        updated = 0
        mapping_before = {"mapped": 0, "partial": 0, "failed": 0, "not_mapped": 0}
        mapping_after = {"mapped": 0, "partial": 0, "failed": 0, "not_mapped": 0}

        for doc_id in doc_ids:
            chunk_index = _build_chunk_index(doc_id)
            for candidate in [c for c in candidates if c.document_id == doc_id]:
                mapping_before[candidate.mapping_status] = mapping_before.get(candidate.mapping_status, 0) + 1

                evidence_refs_raw = candidate.get_evidence_refs()
                mapped_ids, new_status, new_warnings = _map_evidence_refs(evidence_refs_raw, chunk_index)

                existing_warnings = candidate.get_warnings()
                # Only keep non-mapping warnings from previous run (e.g., section_ref_not_found from upload)
                non_mapping_warnings = [
                    w for w in existing_warnings
                    if isinstance(w, dict) and w.get("warning", "") not in (
                        "quote_not_found_used_fallback", "page_candidate_not_found",
                        "section_not_found", "no_chunks_in_index",
                    )
                ]
                non_mapping_warnings.extend(new_warnings)

                candidate.set_mapped_chunk_ids(mapped_ids)
                candidate.set_warnings(non_mapping_warnings)
                candidate.mapping_status = new_status
                candidate.updated_at = datetime.now(timezone.utc).replace(microsecond=0)

                mapping_after[new_status] = mapping_after.get(new_status, 0) + 1
                updated += 1

        session.commit()

        return {
            "status": "ok",
            "count": updated,
            "mapping_before": mapping_before,
            "mapping_after": mapping_after,
            "core_db_write_performed": True,
            "external_llm_called": False,
        }


def _build_chunk_index(document_id: int) -> dict[int, dict[str, Any]]:
    """Build a lookup index of all chunks for a document."""
    with SessionLocal() as session:
        chunks = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        ).all()
    index: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        index[chunk.id] = {
            "chunk_id": chunk.id,
            "pdf_page_start": chunk.pdf_page_start,
            "pdf_page_end": chunk.pdf_page_end,
            "heading_path": chunk.heading_path or "",
            "chunk_text": chunk.chunk_text or "",
        }
    return index


def _map_evidence_refs(
    refs: list[dict[str, Any]],
    chunk_index: dict[int, dict[str, Any]],
) -> tuple[list[int], str, list[dict[str, str]]]:
    """Map evidence_refs to chunk_ids. Returns (mapped_ids, mapping_status, warnings)."""
    if not refs:
        return [], "not_mapped", []

    mapped_ids: list[int] = []
    warnings: list[dict[str, str]] = []
    quote_matched = 0
    fallback_used = 0
    failed = 0

    for ref in refs:
        if not isinstance(ref, dict):
            continue
        pdf_page = ref.get("pdf_page")
        section_title = str(ref.get("section_title") or ref.get("section_id") or "").strip()
        quote = str(ref.get("quote_text_short") or "").strip()

        # Pass 1: match by pdf_page range (inclusive)
        page_candidates = [
            c for c in chunk_index.values()
            if pdf_page is None or _page_matches(c, pdf_page)
        ]

        if not page_candidates:
            # Fallback: search all chunks if page missing
            page_candidates = list(chunk_index.values())
            if not page_candidates:
                warnings.append({"ref": section_title or "unknown", "warning": "no_chunks_in_index"})
                failed += 1
                continue

        # Pass 2: narrow by section_title in heading_path
        heading_candidates = page_candidates
        if section_title:
            narrowed = [c for c in page_candidates if _fuzzy_contains(c["heading_path"], section_title)]
            if narrowed:
                heading_candidates = narrowed
            # else: keep page_candidates, will add warning

        # Pass 3: search ALL heading candidates (not just first) for quote
        found = False
        for candidate in heading_candidates:
            if quote and _fuzzy_contains(candidate["chunk_text"], quote):
                mapped_ids.append(candidate["chunk_id"])
                quote_matched += 1
                found = True
                break

        if found:
            continue

        # Fallback: use first heading candidate if section matched
        if heading_candidates and section_title:
            mapped_ids.append(heading_candidates[0]["chunk_id"])
            fallback_used += 1
            warnings.append({"ref": section_title or "unknown", "warning": "quote_not_found_used_fallback"})
        elif page_candidates and not section_title:
            # No section title at all — try to match quote across all page chunks
            for candidate in page_candidates:
                if quote and _fuzzy_contains(candidate["chunk_text"], quote):
                    mapped_ids.append(candidate["chunk_id"])
                    quote_matched += 1
                    found = True
                    break
            if not found:
                failed += 1
                warnings.append({"ref": section_title or "unknown", "warning": "page_candidate_not_found"})
        else:
            failed += 1
            if not page_candidates:
                warnings.append({"ref": section_title or "unknown", "warning": "page_candidate_not_found"})
            else:
                warnings.append({"ref": section_title or "unknown", "warning": "section_not_found"})

    total = len([r for r in refs if isinstance(r, dict)])
    if quote_matched == total and total > 0:
        mapping_status = "mapped"
    elif quote_matched > 0 or fallback_used > 0:
        mapping_status = "partial"
    elif failed == total:
        mapping_status = "failed"
    else:
        mapping_status = "failed"

    return mapped_ids, mapping_status, warnings


def _page_matches(chunk: dict, evidence_page: int) -> bool:
    """Check if evidence_page falls within chunk's page range."""
    start = chunk.get("pdf_page_start")
    end = chunk.get("pdf_page_end")
    if start is None:
        return False
    if end is None:
        return start == evidence_page
    return start <= evidence_page <= end


def _fuzzy_contains(haystack: str, needle: str) -> bool:
    """Normalized substring match: lowercase, collapse whitespace, remove noise."""
    h = _normalize_text(haystack)
    n = _normalize_text(needle)
    if not n:
        return False
    if n in h:
        return True
    # Try first 60 chars of needle
    if len(n) > 60 and n[:60] in h:
        return True
    # Try keyword-based: split into words and check all present
    words = [w for w in n.split() if len(w) > 2]
    if len(words) >= 3 and all(w in h for w in words):
        return True
    # Try first 40 chars
    if len(n) > 40 and n[:40] in h:
        return True
    return False


def _normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, collapse whitespace, strip common noise."""
    t = text.lower()
    # Collapse whitespace
    import re
    t = re.sub(r'\s+', ' ', t)
    # Remove hyphen line breaks: "remov- ing" → "removing"
    t = re.sub(r'(\w)-\s+(\w)', r'\1\2', t)
    # Remove common citation markers like [1], [14, 15]
    t = re.sub(r'\[\d+(?:,\s*\d+)*\]', '', t)
    # Remove trailing/leading punctuation
    t = t.strip('.,;:!?()[]{}""\'\' ')
    return t


def _extract_tag_names(tags: Any) -> list[str]:
    """Extract tag name strings from reviewed package format [{tag, status}, ...]."""
    if not isinstance(tags, list):
        return []
    names: list[str] = []
    for item in tags:
        if isinstance(item, dict):
            name = str(item.get("tag") or "").strip()
            if name:
                names.append(name)
        elif isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
    return names


COMMIT_REVIEWED_FILE = "commit_reviewed_objects_result.json"
COMMIT_REVIEWED_BACKUP_ROOT = OUTPUTS_DIR / "phase18e_commitreviewedobjects_backup"


def commit_reviewed_objects_from_remap(
    import_job_id: str,
    *,
    persist_result: bool = True,
) -> dict[str, Any]:
    """Commit reviewed objects using pre-computed remap preview.

    Reads reviewed_object_tag_package.json + object_evidence_remap_preview.json,
    deprecates old objects for this job not in the latest accepted/edited set,
    then upserts new objects.  Only accepted/edited objects are written.
    """
    job_dir = _existing_job_dir(import_job_id)

    commit_paper_path = job_dir / "commit_result.json"
    reviewed_path = job_dir / "reviewed_object_tag_package.json"
    remap_path = job_dir / "object_evidence_remap_preview.json"
    result_path = job_dir / COMMIT_REVIEWED_FILE

    if not commit_paper_path.is_file():
        raise ImportPreviewError("Paper not committed. Run commit-paper first.")
    if not reviewed_path.is_file():
        raise ImportPreviewError("reviewed_object_tag_package.json not found.")
    if not remap_path.is_file():
        raise ImportPreviewError(
            "object_evidence_remap_preview.json not found. "
            "Run remap-reviewed-objects-preview first."
        )

    commit_paper = _read_json(commit_paper_path)
    document_id = commit_paper.get("document_id")
    if document_id is None:
        raise ImportPreviewError("commit_result.json has no document_id.")

    # Idempotency
    if result_path.is_file():
        existing = _read_json(result_path)
        return {
            "status": "already_committed",
            "import_job_id": import_job_id,
            "document_id": document_id,
            "inserted_count": existing.get("inserted_count", 0),
            "updated_count": existing.get("updated_count", 0),
            "deprecated_count": existing.get("deprecated_count", 0),
            "total_active": existing.get("total_active", existing.get("inserted_count", 0) + existing.get("updated_count", 0)),
            "mapping_status_counts": existing.get("mapping_status_counts", {}),
            "committed_at": existing.get("committed_at"),
            "message": "Reviewed objects already committed via remap for this import job.",
            "core_db_write_performed": False,
            "external_llm_called": False,
        }

    reviewed = _read_json(reviewed_path)
    remap_data = _read_json(remap_path)

    # Build remap lookup: object_key -> remap result
    remap_by_key: dict[str, dict[str, Any]] = {}
    for obj in remap_data.get("objects") or []:
        remap_by_key[str(obj.get("object_key") or "")] = obj

    # Filter accepted/edited objects from reviewed package
    accepted_edited: list[dict[str, Any]] = []
    accepted_edited_keys: set[str] = set()
    for obj in reviewed.get("objects") or []:
        rs = str(obj.get("review_status") or "").strip().lower()
        if rs in ("accepted", "edited"):
            accepted_edited.append(obj)
            accepted_edited_keys.add(str(obj.get("object_key") or ""))

    # Backup DB
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = COMMIT_REVIEWED_BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_dir / "research_memory_pre_commit_reviewed.db")

    with SessionLocal() as session:
        # --- Deprecate old EDSR objects not in new accepted/edited set ---
        old_candidates = session.scalars(
            select(ObjectCandidate).where(
                ObjectCandidate.import_job_id == import_job_id,
                ObjectCandidate.status == "candidate",
            )
        ).all()

        deprecated = 0
        for old in old_candidates:
            if old.object_key not in accepted_edited_keys:
                old.status = "deprecated"
                old.updated_at = datetime.now(timezone.utc).replace(microsecond=0)
                deprecated += 1

        # --- Upsert new objects ---
        inserted = 0
        updated = 0

        for obj in accepted_edited:
            key = str(obj.get("object_key") or "")
            review_status = str(obj.get("review_status") or "").strip().lower()

            # Get remap info
            remap_obj = remap_by_key.get(key, {})
            mapped_ids = remap_obj.get("mapped_chunk_ids") or []
            mapping_status_val = remap_obj.get("mapping_status", "not_mapped")
            remap_warnings = remap_obj.get("warnings") or []

            # Extract tag name strings from reviewed format [{tag, status}, ...]
            topic_tags = _extract_tag_names(obj.get("topic_tags"))
            problem_tags = _extract_tag_names(obj.get("problem_tags"))
            mechanism_tags = _extract_tag_names(obj.get("mechanism_tags"))
            inspiration_tags = _extract_tag_names(obj.get("inspiration_tags"))

            # Upsert: find existing by import_job_id + object_key
            existing = session.scalars(
                select(ObjectCandidate).where(
                    ObjectCandidate.import_job_id == import_job_id,
                    ObjectCandidate.object_key == key,
                )
            ).first()

            if existing is not None:
                # Update existing (even if deprecated — reactivate)
                existing.document_id = document_id
                existing.object_name = str(obj.get("object_name") or "")
                existing.object_type = str(obj.get("object_type") or "unknown")
                existing.review_status = review_status
                existing.status = "candidate"
                existing.confidence = str(obj.get("confidence") or "medium")
                existing.description = str(obj.get("description") or "") if obj.get("description") else None
                existing.user_comment = str(obj.get("user_comment") or "") if obj.get("user_comment") else None
                existing.source_origin = str(obj.get("source_origin") or "").strip() or None
                existing.necessity_judgment = str(obj.get("necessity_judgment") or "").strip() or None
                existing.importance_score = str(obj.get("importance_score") or "").strip() or None
                existing.source_package_path = "outputs/import_staging/" + import_job_id + "/reviewed_object_tag_package.json"
                existing.source_import_manifest_path = "outputs/import_staging/" + import_job_id + "/import_manifest.json"
                existing.mapping_status = mapping_status_val
                existing.created_by = "user_reviewed"
                existing.set_aliases(obj.get("aliases") or [])
                existing.set_four_layer_tags(topic_tags, problem_tags, mechanism_tags, inspiration_tags)
                existing.set_evidence_refs(obj.get("evidence_refs") or [])
                existing.set_source_note_ids(obj.get("source_note_ids") or [])
                existing.set_mapped_chunk_ids(mapped_ids)
                existing.set_warnings(remap_warnings)
                existing.updated_at = datetime.now(timezone.utc).replace(microsecond=0)
                updated += 1
            else:
                candidate = ObjectCandidate(
                    document_id=document_id,
                    import_job_id=import_job_id,
                    object_key=key,
                    object_name=str(obj.get("object_name") or ""),
                    object_type=str(obj.get("object_type") or "unknown"),
                    review_status=review_status,
                    status="candidate",
                    confidence=str(obj.get("confidence") or "medium"),
                    description=str(obj.get("description") or "") if obj.get("description") else None,
                    user_comment=str(obj.get("user_comment") or "") if obj.get("user_comment") else None,
                    source_origin=str(obj.get("source_origin") or "").strip() or None,
                    necessity_judgment=str(obj.get("necessity_judgment") or "").strip() or None,
                    importance_score=str(obj.get("importance_score") or "").strip() or None,
                    source_package_path="outputs/import_staging/" + import_job_id + "/reviewed_object_tag_package.json",
                    source_import_manifest_path="outputs/import_staging/" + import_job_id + "/import_manifest.json",
                    mapping_status=mapping_status_val,
                    created_by="user_reviewed",
                )
                candidate.set_aliases(obj.get("aliases") or [])
                candidate.set_four_layer_tags(topic_tags, problem_tags, mechanism_tags, inspiration_tags)
                candidate.set_evidence_refs(obj.get("evidence_refs") or [])
                candidate.set_source_note_ids(obj.get("source_note_ids") or [])
                candidate.set_mapped_chunk_ids(mapped_ids)
                candidate.set_warnings(remap_warnings)
                session.add(candidate)
                inserted += 1

        session.commit()

    mapping_stats: dict[str, int] = {}
    for obj in accepted_edited:
        key = str(obj.get("object_key") or "")
        ms = remap_by_key.get(key, {}).get("mapping_status", "not_mapped")
        mapping_stats[ms] = mapping_stats.get(ms, 0) + 1

    committed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    result = {
        "status": "committed",
        "import_job_id": import_job_id,
        "document_id": document_id,
        "inserted_count": inserted,
        "updated_count": updated,
        "deprecated_count": deprecated,
        "total_active": inserted + updated,
        "mapping_status_counts": mapping_stats,
        "backup_dir": str(backup_dir.relative_to(DATA_PROJECT_ROOT)).replace("\\", "/"),
        "committed_at": committed_at,
        "core_db_write_performed": True,
        "external_llm_called": False,
    }
    if persist_result:
        _write_json(result_path, result)

    return result


class _ObjectCommitAlreadyPerformed(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("object commit already performed")
        self.payload = payload


def _job_marker(
    import_job_id: str,
    *,
    reviewed: bool,
) -> Path | None:
    job_dir = _existing_job_dir(import_job_id)
    marker_name = COMMIT_REVIEWED_FILE if reviewed else COMMIT_OBJECTS_FILE
    marker = job_dir / marker_name
    return marker if marker.is_file() else None


def _job_document_id(import_job_id: str) -> int | None:
    job_dir = _existing_job_dir(import_job_id)
    commit_paper_path = job_dir / "commit_result.json"
    if not commit_paper_path.is_file():
        return None
    payload = _read_json(commit_paper_path)
    try:
        return int(payload.get("document_id"))
    except (TypeError, ValueError):
        return None


def _object_commit_durably_performed(
    import_job_id: str,
    *,
    reviewed: bool,
    db_path: Path,
) -> bool:
    """Return whether the object commit is durably present in the database.

    The object rows themselves are the durable commit identity: the plain
    phase inserts rows for the job, and the reviewed phase either marks its
    rows with ``source_import_manifest_path`` or deprecates the old rows.
    This decision never depends on the filesystem marker, which is only a
    convenience cache, and it is only ever consulted under the production
    generation writer lock after the active generation has been verified
    against the database revision.
    """
    database = Path(db_path).resolve(strict=False)
    if not database.is_file():
        return False
    with closing(
        sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro",
            uri=True,
        )
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if reviewed:
            row = connection.execute(
                "SELECT 1 FROM object_candidates "
                "WHERE import_job_id = ? "
                "AND (source_import_manifest_path IS NOT NULL "
                "OR status = 'deprecated') LIMIT 1",
                (import_job_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT 1 FROM object_candidates "
                "WHERE import_job_id = ? LIMIT 1",
                (import_job_id,),
            ).fetchone()
    return row is not None


def _reconstructed_already_committed(
    import_job_id: str,
    *,
    reviewed: bool,
    db_path: Path,
) -> dict[str, Any]:
    database = Path(db_path).resolve(strict=False)
    candidate_count = 0
    deprecated_count = 0
    if database.is_file():
        with closing(
            sqlite3.connect(
                f"file:{database.as_posix()}?mode=ro",
                uri=True,
            )
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            candidate_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM object_candidates "
                    "WHERE import_job_id = ? AND status = 'candidate'",
                    (import_job_id,),
                ).fetchone()[0]
            )
            deprecated_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM object_candidates "
                    "WHERE import_job_id = ? AND status = 'deprecated'",
                    (import_job_id,),
                ).fetchone()[0]
            )
    payload: dict[str, Any] = {
        "status": "already_committed",
        "import_job_id": import_job_id,
        "document_id": _job_document_id(import_job_id),
        "committed_at": None,
        "message": "Objects already committed for this import job.",
        "core_db_write_performed": False,
        "external_llm_called": False,
    }
    if reviewed:
        payload["inserted_count"] = candidate_count
        payload["updated_count"] = 0
        payload["deprecated_count"] = deprecated_count
        payload["total_active"] = candidate_count
        payload["mapping_status_counts"] = {}
    else:
        payload["inserted_count"] = candidate_count
        payload["skipped_rejected"] = 0
        payload["skipped_suggested"] = 0
        payload["mapping_status_counts"] = {}
    return payload


def _verified_post_write_snapshot(db_path: Path) -> Path:
    target = Path(db_path).resolve(strict=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.object-commit-post-write-",
        suffix=".sqlite",
        dir=str(target.parent),
    )
    os.close(descriptor)
    snapshot = Path(raw_path)
    expected_sha = _file_sha256(target)
    expected_size = target.stat().st_size
    try:
        shutil.copy2(target, snapshot)
        if (
            snapshot.stat().st_size != expected_size
            or _file_sha256(snapshot) != expected_sha
        ):
            raise RuntimeError("object_commit_post_write_snapshot_invalid")
    except BaseException:
        snapshot.unlink(missing_ok=True)
        raise
    return snapshot


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _affected_object_keys(
    snapshot_db_path: Path,
    import_job_id: str,
) -> list[str]:
    with closing(
        sqlite3.connect(
            f"file:{Path(snapshot_db_path).resolve().as_posix()}?mode=ro",
            uri=True,
        )
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT DISTINCT object_key FROM object_candidates "
            "WHERE import_job_id = ? AND object_key IS NOT NULL "
            "AND TRIM(object_key) <> ''",
            (import_job_id,),
        ).fetchall()
    keys = sorted({str(row["object_key"]).strip() for row in rows})
    if not keys:
        raise ImportPreviewError(
            "object_commit_affected_keys_empty: "
            "本次对象提交未产生任何 affected object keys。"
        )
    return keys


def _strict_affected_object_validation(
    *,
    object_keys: list[str],
    expected_sources: list[dict[str, Any]],
    vector_store_path: Path,
    expected_db_sha256: str,
    db_path: Path,
) -> dict[str, Any]:
    if _file_sha256(db_path).lower() != expected_db_sha256.lower():
        raise RuntimeError("object_commit_generation_database_revision_invalid")
    state = vector_store_service.inspect_affected_object_vector_state(
        object_keys=object_keys,
        expected_sources=expected_sources,
        store_path=vector_store_path,
    )
    if (
        state.get("status") != "ok"
        or int(state.get("missing_count") or 0) != 0
        or int(state.get("duplicate_count") or 0) != 0
        or int(state.get("stale_count") or 0) != 0
        or int(state.get("removed_but_present_count") or 0) != 0
        or int(state.get("identity_variant_count") or 0) != 0
    ):
        raise RuntimeError("object_commit_affected_vectors_invalid")
    return state


def _commit_objects_with_generation(
    import_job_id: str,
    *,
    reviewed: bool,
    db_path: Path,
    data_dir: Path,
) -> dict[str, Any]:
    body_commit = (
        commit_reviewed_objects_from_remap
        if reviewed
        else commit_objects_from_staging
    )
    # No terminal-success decision happens outside the generation writer
    # lock.  The session enter resolves and verifies the active generation
    # against the current database revision (fail-closed on mismatch, which
    # also covers a process crash between the database body commit and the
    # pointer switch), and the durable phase identity is then checked under
    # the writer barrier.
    post_write_snapshot: Path | None = None
    generation_id: str | None = None
    result: dict[str, Any] = {}
    try:
        with retrieval_generation_mutation_service.ProductionGenerationMutationSession(
            data_dir=data_dir,
            db_path=db_path,
        ) as mutation:
            if _object_commit_durably_performed(
                import_job_id,
                reviewed=reviewed,
                db_path=db_path,
            ):
                raise _ObjectCommitAlreadyPerformed(
                    _reconstructed_already_committed(
                        import_job_id,
                        reviewed=reviewed,
                        db_path=db_path,
                    )
                )

            result = body_commit(import_job_id, persist_result=False)
            mutation.mark_body_db_mutated()
            after_db_sha256 = mutation.capture_post_write_database()
            post_write_snapshot = _verified_post_write_snapshot(db_path)
            if _file_sha256(post_write_snapshot).lower() != after_db_sha256:
                raise RuntimeError("object_commit_post_write_snapshot_invalid")

            affected_keys = _affected_object_keys(
                post_write_snapshot,
                import_job_id,
            )
            expected_sources = (
                vector_store_service.collect_affected_object_sources(
                    db_path=post_write_snapshot,
                    object_keys=affected_keys,
                )
            )
            candidate = mutation.candidate
            if candidate is None:
                raise RuntimeError("object_commit_generation_candidate_missing")

            sync_result = (
                vector_store_service.sync_affected_object_embeddings(
                    affected_keys,
                    dry_run=False,
                    apply=True,
                    store_path=candidate.vector_store_path,
                    manifest_path=candidate.vector_manifest_path,
                    sources=expected_sources,
                )
            )
            if (
                sync_result.get("scope") != "affected_object_keys_only"
                or sync_result.get("full_rebuild_allowed") is not False
                or sync_result.get("delete_orphans_allowed") is not False
            ):
                raise RuntimeError("object_commit_affected_scope_invalid")

            mutation.mark_candidate_synced()

            def validate_candidate(candidate_value, expected_sha: str) -> None:
                _strict_affected_object_validation(
                    object_keys=affected_keys,
                    expected_sources=expected_sources,
                    vector_store_path=candidate_value.vector_store_path,
                    expected_db_sha256=expected_sha,
                    db_path=post_write_snapshot,
                )

            mutation.validate_candidate(validate_candidate)
            finalized = mutation.finalize_candidate(
                profile_versions={
                    "object_profile": vector_store_service.OBJECT_PROFILE_VERSION,
                }
            )
            mutation.begin_activation()
            mutation.publish_active()

            def validate_active(active_value) -> None:
                if active_value.generation_id != finalized.generation_id:
                    raise RuntimeError("object_commit_active_generation_mismatch")
                _strict_affected_object_validation(
                    object_keys=affected_keys,
                    expected_sources=expected_sources,
                    vector_store_path=active_value.vector_store_path,
                    expected_db_sha256=after_db_sha256,
                    db_path=db_path,
                )

            mutation.verify_active(validate_active)
            post_write_snapshot.unlink()
            post_write_snapshot = None
            mutation.clear_activation()
            generation_id = finalized.generation_id
            # The filesystem receipt is a convenience cache only; the durable
            # commit identity is the database rows themselves (see
            # _object_commit_durably_performed).  A failed cache write must
            # never surface as a transaction error after the commit point.
            try:
                job_dir = _existing_job_dir(import_job_id)
                marker_name = (
                    COMMIT_REVIEWED_FILE if reviewed else COMMIT_OBJECTS_FILE
                )
                _write_json(job_dir / marker_name, result)
            except OSError:
                pass
    except _ObjectCommitAlreadyPerformed as exc:
        return dict(exc.payload)
    except (
        retrieval_generation_mutation_service
        .ProductionGenerationRollbackError
    ) as exc:
        if post_write_snapshot is not None:
            try:
                post_write_snapshot.unlink(missing_ok=True)
            except OSError:
                pass
        raise ImportPreviewError(
            "object_commit_generation_rollback_failed: "
            "对象提交进入 fail-closed 状态；生产数据库与检索生成已保持一致。"
        ) from exc
    except ImportPreviewError:
        if post_write_snapshot is not None:
            try:
                post_write_snapshot.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except Exception as exc:
        if post_write_snapshot is not None:
            try:
                post_write_snapshot.unlink(missing_ok=True)
            except OSError:
                pass
        raise ImportPreviewError(
            "object_commit_failed: "
            "对象提交未产生已提交结果；生产状态保持 fail-closed，请人工核查。"
        ) from exc

    return {
        **result,
        "generation_id": generation_id,
        "derived_index_publish_performed": True,
    }


def commit_objects_to_production_with_generation(
    import_job_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    data_dir: str | Path = DATA_DIR,
) -> dict[str, Any]:
    return _commit_objects_with_generation(
        import_job_id,
        reviewed=False,
        db_path=Path(db_path),
        data_dir=Path(data_dir),
    )


def commit_reviewed_objects_to_production_with_generation(
    import_job_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    data_dir: str | Path = DATA_DIR,
) -> dict[str, Any]:
    return _commit_objects_with_generation(
        import_job_id,
        reviewed=True,
        db_path=Path(db_path),
        data_dir=Path(data_dir),
    )
