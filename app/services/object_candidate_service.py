from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk, NoteEvidenceLink, PersonalNote
from app.models.object_candidate import ObjectCandidate
from app.services import library_service
from app.services.object_candidate_rerank_service import rerank_object_candidates


SNIPPET_CHARS = 220
MAX_EVIDENCE_REFS = 6
MAX_LINKED_NOTES = 8
READ_STATUSES = {"read", "mastered"}


@dataclass(frozen=True)
class ObjectSpec:
    object_key: str
    object_name: str
    object_type: str
    aliases: tuple[str, ...]
    topic_tags: tuple[str, ...] = ()
    problem_tags: tuple[str, ...] = ()
    mechanism_tags: tuple[str, ...] = ()
    inspiration_tags: tuple[str, ...] = ()


OBJECT_SPECS: tuple[ObjectSpec, ...] = (
    ObjectSpec(
        object_key="edsr",
        object_name="EDSR / Enhanced Deep Residual Networks",
        object_type="method",
        aliases=("EDSR", "Enhanced Deep Residual Networks", "Enhanced Deep Residual Network"),
        topic_tags=("single image super-resolution", "image restoration"),
        mechanism_tags=("deep residual network",),
    ),
    ObjectSpec(
        object_key="residual-block",
        object_name="Residual Block",
        object_type="mechanism",
        aliases=("residual block", "residual blocks", "残差块"),
        topic_tags=("deep neural network", "super-resolution"),
        problem_tags=("deep network training",),
        mechanism_tags=("residual learning",),
    ),
    ObjectSpec(
        object_key="residual-scaling",
        object_name="Residual Scaling",
        object_type="mechanism",
        aliases=("residual scaling", "残差缩放"),
        topic_tags=("deep residual network",),
        problem_tags=("training stability",),
        mechanism_tags=("residual scaling",),
    ),
    ObjectSpec(
        object_key="remove-batch-normalization",
        object_name="Remove Batch Normalization",
        object_type="mechanism",
        aliases=("remove batch normalization", "remove BN", "BN layers", "batch normalization"),
        topic_tags=("image restoration", "CNN architecture"),
        problem_tags=("unnecessary modules", "training/inference cost"),
        mechanism_tags=("remove normalization",),
    ),
    ObjectSpec(
        object_key="psnr",
        object_name="PSNR",
        object_type="metric",
        aliases=("PSNR", "Peak signal-to-noise ratio"),
        topic_tags=("image restoration", "super-resolution"),
        problem_tags=("reconstruction quality evaluation",),
    ),
    ObjectSpec(
        object_key="ssim",
        object_name="SSIM",
        object_type="metric",
        aliases=("SSIM", "Structural similarity"),
        topic_tags=("image restoration", "super-resolution"),
        problem_tags=("reconstruction quality evaluation",),
    ),
    ObjectSpec(
        object_key="div2k",
        object_name="DIV2K",
        object_type="dataset",
        aliases=("DIV2K",),
        topic_tags=("super-resolution dataset",),
    ),
    ObjectSpec(
        object_key="physdiff",
        object_name="PhysDiff",
        object_type="method",
        aliases=("PhysDiff", "Physics-Guided Human Motion Diffusion Model", "physics-guided diffusion"),
        topic_tags=("human motion generation", "diffusion model"),
        problem_tags=("physical implausibility",),
        mechanism_tags=("physics-guided diffusion", "physical constraint"),
    ),
    ObjectSpec(
        object_key="physical-plausibility",
        object_name="Physical Plausibility",
        object_type="problem",
        aliases=("physical plausibility", "physical implausibility", "物理合理性", "物理不合理"),
        problem_tags=("physical implausibility",),
    ),
    ObjectSpec(
        object_key="foot-sliding",
        object_name="Foot Sliding",
        object_type="problem",
        aliases=("foot sliding", "skating", "foot skating", "脚滑"),
        problem_tags=("foot sliding",),
    ),
    ObjectSpec(
        object_key="ground-penetration",
        object_name="Ground Penetration",
        object_type="problem",
        aliases=("ground penetration", "penetration", "穿地"),
        problem_tags=("ground penetration",),
    ),
    ObjectSpec(
        object_key="floating",
        object_name="Floating",
        object_type="problem",
        aliases=("floating", "float"),
        problem_tags=("floating",),
    ),
    ObjectSpec(
        object_key="mdm",
        object_name="MDM / Human Motion Diffusion Model",
        object_type="method",
        aliases=("MDM", "Human Motion Diffusion Model", "motion diffusion"),
        topic_tags=("human motion generation", "text-to-motion", "diffusion model"),
    ),
    ObjectSpec(
        object_key="text-to-motion",
        object_name="Text-to-motion",
        object_type="task",
        aliases=("text-to-motion", "text driven motion", "text2motion"),
        topic_tags=("human motion generation",),
    ),
    ObjectSpec(
        object_key="moment-estimation",
        object_name="Moment Estimation",
        object_type="method/concept",
        aliases=("moment estimation", "method of moments", "矩估计"),
        topic_tags=("statistics", "parameter estimation"),
        problem_tags=("estimate population parameters",),
        mechanism_tags=("match sample moments with population moments",),
    ),
    ObjectSpec(
        object_key="maximum-likelihood-estimation",
        object_name="Maximum Likelihood Estimation",
        object_type="method/concept",
        aliases=("maximum likelihood estimation", "MLE", "likelihood estimation", "极大似然估计", "似然估计"),
        topic_tags=("statistics", "parameter estimation", "optimization"),
        problem_tags=("estimate unknown parameters",),
        mechanism_tags=("maximize likelihood of observed data",),
    ),
)


def search_object_candidates(query: str, limit: int = 10) -> dict[str, Any]:
    """Search object_candidates DB first, then rule-derived specs as fallback."""
    normalized_query = query.strip()
    if not normalized_query:
        return _response(query=query, objects=[])

    with SessionLocal() as session:
        db_objects = _query_db_objects(session, normalized_query)

    if db_objects:
        ranked_db = rerank_object_candidates(normalized_query, db_objects)
        return _response(query=query, objects=ranked_db[: max(1, min(limit, 20))])

    # Fallback: rule-derived from OBJECT_SPECS
    with SessionLocal() as session:
        rows = _load_chunk_rows(session)
        notes_by_chunk = _load_notes_by_chunk(session)
        candidates = [
            candidate
            for spec in OBJECT_SPECS
            if _spec_matches_query(spec, normalized_query)
            for candidate in [_build_spec_candidate(spec, rows, notes_by_chunk)]
            if candidate["evidence_refs"]
        ]
    candidates = rerank_object_candidates(normalized_query, candidates)
    return _response(query=query, objects=candidates[: max(1, min(limit, 20))])


def get_object_candidate(object_key: str) -> dict[str, Any]:
    """Object detail — DB first, then spec fallback."""
    clean_key = object_key.strip().lower()
    if not clean_key:
        return _response(status="not_found", object_key=object_key, objects=[], message="object_key required.")

    # DB lookup first
    with SessionLocal() as session:
        db_obj = session.scalar(
            select(ObjectCandidate).where(
                (ObjectCandidate.object_key == clean_key),
                (ObjectCandidate.status == "candidate"),
            )
        )
    if db_obj:
        candidate = _build_db_candidate(db_obj)
        ranked = rerank_object_candidates(candidate["object_name"], [candidate])
        return _response(object_key=object_key, object=ranked[0], objects=ranked)

    # Spec fallback
    spec = _spec_by_key(clean_key)
    if spec is None:
        return _response(status="not_found", object_key=object_key, objects=[], message="Object candidate not found in DB or specs.")

    with SessionLocal() as session:
        candidate = _build_spec_candidate(spec, _load_chunk_rows(session), _load_notes_by_chunk(session))
    if not candidate["evidence_refs"]:
        return _response(status="not_found", object_key=object_key, objects=[], message="Object candidate has no local evidence.")
    candidate = rerank_object_candidates(candidate["object_name"], [candidate])[0]
    return _response(object_key=object_key, object=candidate, objects=[candidate])


def document_object_groups(document_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(ObjectCandidate)
            .where(
                ObjectCandidate.document_id == document_id,
                ObjectCandidate.status == "candidate",
            )
            .order_by(ObjectCandidate.object_type, ObjectCandidate.object_name, ObjectCandidate.id)
        ).all()

    candidates = [_document_object_item(_build_db_candidate(row)) for row in rows]
    candidates.sort(key=_document_object_sort_key)

    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(_object_group_key(candidate.get("object_type")), []).append(candidate)

    return [
        {
            "object_type": object_type,
            "label": _object_group_label(object_type),
            "objects": groups[object_type],
        }
        for object_type in sorted(groups, key=_object_group_order)
    ]


def objects_for_evidence(chunk_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        rows = _load_chunk_rows(session, chunk_id=chunk_id)
        notes_by_chunk = _load_notes_by_chunk(session, chunk_ids=[chunk_id])
        objects = [
            candidate
            for spec in OBJECT_SPECS
            for candidate in [_build_spec_candidate(spec, rows, notes_by_chunk, evidence_chunk_id=chunk_id)]
            if candidate["evidence_refs"]
        ]
    objects = rerank_object_candidates(str(chunk_id), objects)
    return _response(chunk_id=chunk_id, objects=objects)


def _document_object_item(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = _representative_evidence(candidate.get("evidence_refs") or [])
    return {
        "object_key": candidate.get("object_key"),
        "object_name": candidate.get("object_name"),
        "object_type": candidate.get("object_type") or "unknown",
        "description": candidate.get("description") or "",
        "status": candidate.get("status") or "candidate",
        "review_status": candidate.get("review_status"),
        "confidence": candidate.get("confidence"),
        "mapping_status": candidate.get("mapping_status"),
        "source_origin": candidate.get("source_origin"),
        "necessity_judgment": candidate.get("necessity_judgment"),
        "importance_score": candidate.get("importance_score"),
        "source_note_ids": list(candidate.get("source_note_ids") or []),
        "source_note_count": len(candidate.get("source_note_ids") or []),
        "object_score": candidate.get("object_score"),
        "score_label": candidate.get("score_label"),
        "evidence_count": len(candidate.get("evidence_refs") or []),
        "representative_evidence": evidence_refs,
        "topic_tags": list(candidate.get("topic_tags") or []),
        "problem_tags": list(candidate.get("problem_tags") or []),
        "mechanism_tags": list(candidate.get("mechanism_tags") or []),
        "inspiration_tags": list(candidate.get("inspiration_tags") or []),
    }


def _representative_evidence(evidence_refs: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_chunks: set[int] = set()
    sorted_refs = sorted(
        [ref for ref in evidence_refs if isinstance(ref, dict)],
        key=lambda ref: (
            0 if ref.get("is_locatable") else 1,
            -(float(ref.get("score") or 0.0)),
            int(ref.get("chunk_id") or 0),
        ),
    )
    for ref in sorted_refs:
        chunk_id = ref.get("chunk_id")
        if not chunk_id:
            continue
        chunk_id_int = int(chunk_id)
        if chunk_id_int in seen_chunks:
            continue
        seen_chunks.add(chunk_id_int)
        selected.append(
            {
                "chunk_id": chunk_id_int,
                "document_id": ref.get("document_id"),
                "snippet": ref.get("snippet") or ref.get("quote_text_short") or "",
                "chunk_text": ref.get("chunk_text") or "",
                "heading_path": ref.get("heading_path") or ref.get("section_title") or ref.get("section_label") or "",
                "section_title": ref.get("section_title") or ref.get("heading_path") or ref.get("section_label") or "",
                "pdf_page": ref.get("pdf_page") or ref.get("pdf_page_start"),
                "pdf_page_start": ref.get("pdf_page_start") or ref.get("pdf_page"),
                "is_metadata_chunk": bool(ref.get("is_metadata_chunk")),
                "is_locatable": bool(ref.get("is_locatable")),
                "locator_status": ref.get("locator_status"),
                "locator_reason": ref.get("locator_reason"),
                "match_method": ref.get("match_method"),
                "highlight_count": ref.get("highlight_count", 0),
            }
        )
        if len(selected) >= limit:
            break
    return selected


OBJECT_GROUP_ORDER = {
    "method": 0,
    "dataset": 1,
    "metric": 2,
    "problem": 3,
    "mechanism": 4,
    "contribution": 5,
    "limitation": 6,
    "inspiration": 7,
    "other": 8,
}


OBJECT_GROUP_LABELS = {
    "method": "方法",
    "dataset": "数据集",
    "metric": "指标",
    "problem": "问题",
    "mechanism": "机制",
    "contribution": "贡献",
    "limitation": "限制",
    "inspiration": "灵感",
    "other": "其他对象",
}


def _object_group_key(object_type: Any) -> str:
    normalized = str(object_type or "other").strip().lower()
    if normalized in OBJECT_GROUP_ORDER:
        return normalized
    if normalized in {
        "task",
        "concept",
        "component",
        "loss",
        "experiment_setting",
        "model",
        "algorithm",
        "training_strategy",
        "evaluation_protocol",
        "problem_setting",
        "assumption",
        "method/concept",
    }:
        return "method"
    return "other"


def _object_group_label(object_type: str) -> str:
    return OBJECT_GROUP_LABELS.get(object_type, "其他对象")


def _object_group_order(object_type: str) -> int:
    return OBJECT_GROUP_ORDER.get(object_type, OBJECT_GROUP_ORDER["other"])


def _document_object_sort_key(candidate: dict[str, Any]) -> tuple[int, int, float, str]:
    review_rank = 0 if candidate.get("review_status") == "accepted" else 1
    evidence_count = int(candidate.get("evidence_count") or 0)
    score = float(candidate.get("object_score") or 0.0)
    return (review_rank, -evidence_count, -score, str(candidate.get("object_name") or "").lower())


def _response(status: str = "ok", **fields: Any) -> dict[str, Any]:
    return {
        "status": status,
        "mode": "read_only_object_candidates_v1",
        **fields,
        "production_write_enabled": False,
        "db_write_performed": False,
        "external_llm_called": False,
        "final_hypothesis_created": False,
    }


def _load_chunk_rows(session, chunk_id: int | None = None) -> list[tuple[Document, KnowledgeChunk]]:
    statement = (
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(Document.read_status.in_(READ_STATUSES))
    )
    if chunk_id is not None:
        statement = statement.where(KnowledgeChunk.id == chunk_id)
    rows = list(session.execute(statement).all())
    rows.sort(key=lambda item: (int(item[0].id), int(item[1].chunk_index), int(item[1].id)))
    return rows


def _load_notes_by_chunk(session, chunk_ids: list[int] | None = None) -> dict[int, list[dict[str, Any]]]:
    statement = (
        select(NoteEvidenceLink, PersonalNote)
        .join(PersonalNote, PersonalNote.id == NoteEvidenceLink.note_id)
        .order_by(NoteEvidenceLink.chunk_id, NoteEvidenceLink.id)
    )
    if chunk_ids:
        statement = statement.where(NoteEvidenceLink.chunk_id.in_(chunk_ids))
    notes_by_chunk: dict[int, list[dict[str, Any]]] = {}
    for link, note in session.execute(statement).all():
        notes_by_chunk.setdefault(link.chunk_id, []).append(
            {
                "note_id": note.id,
                "note_type": note.note_type,
                "title": note.title,
                "short_preview": _snippet(note.summary or note.content, SNIPPET_CHARS),
                "linked_chunk_id": link.chunk_id,
                "evidence_role": link.evidence_role,
            }
        )
    return notes_by_chunk


def _build_db_candidate(db_obj: ObjectCandidate) -> dict[str, Any]:
    """Build a candidate dict from an object_candidates row."""
    import json
    aliases = db_obj.get_aliases()
    topic = json.loads(db_obj.topic_tags_json) if db_obj.topic_tags_json else []
    problem = json.loads(db_obj.problem_tags_json) if db_obj.problem_tags_json else []
    mechanism = json.loads(db_obj.mechanism_tags_json) if db_obj.mechanism_tags_json else []
    inspiration = json.loads(db_obj.inspiration_tags_json) if db_obj.inspiration_tags_json else []
    evidence_refs_raw = db_obj.get_evidence_refs()
    source_note_ids = db_obj.get_source_note_ids()
    mapped_ids = db_obj.get_mapped_chunk_ids()
    warnings = db_obj.get_warnings()

    # Build evidence_refs with details if chunks are mapped
    evidence_refs = []
    if mapped_ids:
        with SessionLocal() as session:
            chunks = session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.id.in_(mapped_ids))
            ).all()
            chunk_map = {c.id: c for c in chunks}
            docs = session.scalars(
                select(Document).where(Document.id == db_obj.document_id)
            ).all()
            doc_map = {d.id: d for d in docs}
        for ref in evidence_refs_raw:
            ref_out = dict(ref) if isinstance(ref, dict) else {}
            chunk_id_for_ref = None
            if isinstance(ref, dict):
                for mid in mapped_ids:
                    if mid in chunk_map:
                        chunk_id_for_ref = mid
                        break
            if chunk_id_for_ref and chunk_id_for_ref in chunk_map:
                c = chunk_map[chunk_id_for_ref]
                if library_service.is_metadata_chunk(c):
                    continue
                d = doc_map.get(c.document_id)
                locator_contract = library_service.evidence_locator_contract(
                    chunk_text=c.chunk_text,
                    pdf_page_start=c.pdf_page_start,
                    pdf_path=(c.pdf_path or (d.pdf_path if d else None)),
                    is_metadata=False,
                )
                ref_out["chunk_id"] = c.id
                ref_out["pdf_page"] = c.pdf_page_start
                ref_out["pdf_page_start"] = c.pdf_page_start
                ref_out["pdf_page_end"] = c.pdf_page_end
                ref_out["heading_path"] = c.heading_path or ""
                ref_out["snippet"] = _snippet(c.chunk_text, 220)
                ref_out["chunk_text"] = c.chunk_text
                ref_out["document_title"] = d.title if d else ""
                ref_out.update(locator_contract)
            evidence_refs.append(ref_out)
    else:
        evidence_refs = [dict(r) if isinstance(r, dict) else {} for r in evidence_refs_raw]

    # Build top_documents
    top_docs = []
    if db_obj.document_id:
        with SessionLocal() as session:
            d = session.scalar(select(Document).where(Document.id == db_obj.document_id))
        if d:
            top_docs.append({"document_id": d.id, "title": d.title})

    candidate = {
        "object_key": db_obj.object_key,
        "object_name": db_obj.object_name,
        "object_type": db_obj.object_type,
        "aliases": aliases,
        "topic_tags": topic,
        "problem_tags": problem,
        "mechanism_tags": mechanism,
        "inspiration_tags": inspiration,
        "evidence_refs": evidence_refs,
        "linked_personal_notes": [],
        "status": "candidate",
        "review_status": db_obj.review_status,
        "source_origin": db_obj.source_origin,
        "necessity_judgment": db_obj.necessity_judgment,
        "importance_score": db_obj.importance_score,
        "source_note_ids": source_note_ids,
        "source": "object_candidates",
        "confidence": db_obj.confidence or "medium",
        "mapping_status": db_obj.mapping_status,
        "mapped_chunk_ids": mapped_ids,
        "mapping_warnings": warnings,
        "description": db_obj.description or "",
        "user_comment": db_obj.user_comment or "",
        "import_job_id": db_obj.import_job_id,
        "document_id": db_obj.document_id,
        "top_documents": top_docs,
        "warnings": warnings,
    }
    return candidate


def build_db_candidate_from_snapshot(
    row: sqlite3.Row,
    *,
    chunks_by_id: dict[int, dict[str, Any]],
    document_titles: dict[int, str],
) -> dict[str, Any]:
    """Build the same candidate dict from a raw object_candidates row.

    Mirrors :func:`_build_db_candidate` against a SQLite snapshot so the
    generation transaction and the global object model agree on the exact
    authoritative object content without touching the live ORM session.
    Evidence enrichment (snippets, chunk identity, document title) uses only
    snapshot data; fields that are not read by the object profile are kept
    minimal but equivalent.
    """
    import json

    def _json_list(value: str | None) -> list[Any]:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    mapped_ids = [int(value) for value in _json_list(row["mapped_chunk_ids_json"])]
    evidence_refs_raw = _json_list(row["evidence_refs_json"])

    evidence_refs: list[dict[str, Any]] = []
    if mapped_ids:
        for ref in evidence_refs_raw:
            ref_out = dict(ref) if isinstance(ref, dict) else {}
            chunk_id_for_ref = None
            if isinstance(ref, dict):
                for mid in mapped_ids:
                    if mid in chunks_by_id:
                        chunk_id_for_ref = mid
                        break
            if chunk_id_for_ref is not None:
                chunk = chunks_by_id[chunk_id_for_ref]
                if library_service.is_metadata_chunk_text(
                    chunk.get("chunk_text") or ""
                ):
                    continue
                ref_out["chunk_id"] = chunk["id"]
                ref_out["pdf_page"] = chunk.get("pdf_page_start")
                ref_out["pdf_page_start"] = chunk.get("pdf_page_start")
                ref_out["pdf_page_end"] = chunk.get("pdf_page_end")
                ref_out["heading_path"] = chunk.get("heading_path") or ""
                ref_out["snippet"] = _snippet(chunk.get("chunk_text"), 220)
                ref_out["chunk_text"] = chunk.get("chunk_text") or ""
                ref_out["document_title"] = document_titles.get(
                    chunk.get("document_id")
                ) or ""
            evidence_refs.append(ref_out)
    else:
        evidence_refs = [
            dict(r) if isinstance(r, dict) else {} for r in evidence_refs_raw
        ]

    document_id = row["document_id"]
    top_docs = (
        [{"document_id": document_id, "title": document_titles.get(document_id) or ""}]
        if document_id is not None
        else []
    )

    return {
        "object_key": row["object_key"],
        "object_name": row["object_name"],
        "object_type": row["object_type"],
        "aliases": _json_list(row["aliases_json"]),
        "topic_tags": _json_list(row["topic_tags_json"]),
        "problem_tags": _json_list(row["problem_tags_json"]),
        "mechanism_tags": _json_list(row["mechanism_tags_json"]),
        "inspiration_tags": _json_list(row["inspiration_tags_json"]),
        "evidence_refs": evidence_refs,
        "linked_personal_notes": [],
        "status": row["status"],
        "review_status": row["review_status"],
        "source_origin": row["source_origin"],
        "necessity_judgment": row["necessity_judgment"],
        "importance_score": row["importance_score"],
        "source_note_ids": _json_list(row["source_note_ids_json"]),
        "source": "object_candidates",
        "confidence": row["confidence"] or "medium",
        "mapping_status": row["mapping_status"],
        "mapped_chunk_ids": mapped_ids,
        "mapping_warnings": _json_list(row["warnings_json"]),
        "description": row["description"] or "",
        "user_comment": row["user_comment"] or "",
        "import_job_id": row["import_job_id"],
        "document_id": document_id,
        "top_documents": top_docs,
        "warnings": _json_list(row["warnings_json"]),
    }


def _query_db_objects(session, query: str) -> list[dict[str, Any]]:
    """Query object_candidates table by name, key, type, or aliases."""
    import json
    normalized = _normalize_text(query)
    terms = [t for t in normalized.split() if len(t) >= 2]
    if not terms:
        return []

    rows = session.scalars(
        select(ObjectCandidate).where(ObjectCandidate.status == "candidate")
    ).all()
    matches = []
    seen_keys = set()
    for row in rows:
        if row.object_key in seen_keys:
            continue
        haystack = _normalize_text(
            f"{row.object_key} {row.object_name} {row.object_type} "
            f"{row.aliases_json} {row.description or ''}"
        )
        # Match if query words appear in haystack
        if all(term in haystack for term in terms):
            seen_keys.add(row.object_key)
            matches.append(_build_db_candidate(row))

    return matches


def _build_spec_candidate(
    spec: ObjectSpec,
    rows: list[tuple[Document, KnowledgeChunk]],
    notes_by_chunk: dict[int, list[dict[str, Any]]],
    evidence_chunk_id: int | None = None,
) -> dict[str, Any]:
    evidence_refs: list[dict[str, Any]] = []
    linked_notes: list[dict[str, Any]] = []
    seen_notes: set[int] = set()

    for document, chunk in rows:
        if library_service.is_metadata_chunk(chunk):
            continue
        match = _match_strength(spec, document, chunk)
        if not match:
            continue
        evidence = _evidence_ref(spec, document, chunk, match)
        evidence_refs.append(evidence)
        for note in notes_by_chunk.get(chunk.id, []):
            if note["note_id"] in seen_notes:
                continue
            if _note_mentions_spec(note, spec) or chunk.id == evidence_chunk_id or evidence_refs:
                linked_notes.append(note)
                seen_notes.add(note["note_id"])

    evidence_refs.sort(key=lambda item: (item["score"], -int(item["chunk_id"])), reverse=True)
    evidence_refs = evidence_refs[:MAX_EVIDENCE_REFS]
    evidence_ids = {item["chunk_id"] for item in evidence_refs}
    linked_notes = [note for note in linked_notes if note["linked_chunk_id"] in evidence_ids][:MAX_LINKED_NOTES]

    confidence = _confidence(evidence_refs)
    warnings = [] if evidence_refs else [{"source_gap_reason": "no_local_evidence_found"}]
    return {
        "object_key": spec.object_key,
        "object_name": spec.object_name,
        "object_type": spec.object_type,
        "aliases": list(spec.aliases),
        "topic_tags": list(spec.topic_tags),
        "problem_tags": list(spec.problem_tags),
        "mechanism_tags": list(spec.mechanism_tags),
        "inspiration_tags": list(spec.inspiration_tags),
        "evidence_refs": [{key: value for key, value in item.items() if key != "score"} for item in evidence_refs],
        "linked_personal_notes": linked_notes,
        "status": "suggested",
        "source": "derived_from_existing_chunks_notes",
        "confidence": confidence,
        "warnings": warnings,
    }


def _evidence_ref(spec: ObjectSpec, document: Document, chunk: KnowledgeChunk, match: dict[str, Any]) -> dict[str, Any]:
    section = library_service._chunk_section_metadata(document, chunk)
    pdf_page = chunk.pdf_page_start
    locator_contract = library_service.evidence_locator_contract(
        chunk_text=chunk.chunk_text,
        pdf_page_start=pdf_page,
        pdf_path=chunk.pdf_path or document.pdf_path,
        is_metadata=False,
    )
    return {
        "document_id": document.id,
        "document_title": document.title,
        "chunk_id": chunk.id,
        "pdf_page": pdf_page,
        "pdf_page_start": pdf_page,
        "pdf_page_end": chunk.pdf_page_end,
        "section_label": section["section_label"],
        "heading_path": chunk.heading_path or "",
        "evidence_role": _evidence_role(spec, document, chunk, match),
        "snippet": _best_snippet(chunk.chunk_text, match["matched_alias"]),
        "chunk_text": chunk.chunk_text,
        "score": match["score"],
        **locator_contract,
    }


def _match_strength(spec: ObjectSpec, document: Document, chunk: KnowledgeChunk) -> dict[str, Any] | None:
    chunk_text = _normalize_text(chunk.chunk_text)
    heading_text = _normalize_text(chunk.heading_path)
    title_text = _normalize_text(document.title)
    best: dict[str, Any] | None = None
    for alias in spec.aliases:
        normalized_alias = _normalize_text(alias)
        if not normalized_alias:
            continue
        score = 0.0
        matched = False
        if normalized_alias in chunk_text:
            score += 5.0
            matched = True
        if normalized_alias in heading_text:
            score += 2.2
            matched = True
        if normalized_alias in title_text:
            score += 1.6
            matched = True
        if not matched:
            continue
        section = library_service._chunk_section_metadata(document, chunk)
        score += _section_priority(section["section_label"])
        if best is None or score > best["score"]:
            best = {"matched_alias": alias, "score": score}
    return best


def _spec_matches_query(spec: ObjectSpec, query: str) -> bool:
    normalized_query = _normalize_text(query)
    haystack = " ".join(
        [_normalize_text(spec.object_key), _normalize_text(spec.object_name)]
        + [_normalize_text(alias) for alias in spec.aliases]
    )
    return normalized_query in haystack or any(term in haystack for term in _query_terms(normalized_query))


def _spec_by_key(object_key: str) -> ObjectSpec | None:
    normalized = object_key.strip().lower()
    return next((spec for spec in OBJECT_SPECS if spec.object_key == normalized), None)


def _query_terms(query: str) -> list[str]:
    return [term for term in re.split(r"[\s/_:-]+", query) if len(term) >= 2]


def _note_mentions_spec(note: dict[str, Any], spec: ObjectSpec) -> bool:
    text = _normalize_text(" ".join([note.get("title", ""), note.get("short_preview", "")]))
    return any(_normalize_text(alias) in text for alias in spec.aliases)


def _evidence_role(spec: ObjectSpec, document: Document, chunk: KnowledgeChunk, match: dict[str, Any]) -> str:
    heading_title = _normalize_text(f"{document.title} {chunk.heading_path}")
    if chunk.pdf_page_start in (None, 1) and match["score"] < 3:
        return "mention"
    if spec.object_type == "metric":
        return "metric"
    if spec.object_type == "dataset":
        return "dataset"
    if spec.object_type == "problem":
        return "problem"
    if spec.object_type == "mechanism" or any(term in heading_title for term in ("method", "proposed", "approach")):
        return "mechanism"
    return "unknown"


def _section_priority(section_label: str) -> float:
    normalized = _normalize_text(section_label)
    if any(term in normalized for term in ("proposed", "method", "approach")):
        return 1.5
    if any(term in normalized for term in ("experiment", "evaluation", "metric", "training")):
        return 0.8
    if "front matter" in normalized or "abstract" in normalized:
        return -0.4
    return 0.0


def _confidence(evidence_refs: list[dict[str, Any]]) -> str:
    direct_hits = [item for item in evidence_refs if item["score"] >= 5.0]
    if len(direct_hits) >= 2:
        return "high"
    if direct_hits:
        return "medium"
    if evidence_refs:
        return "low"
    return "low"


def _confidence_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def _best_snippet(text: str | None, alias: str) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    lower = compact.lower()
    alias_lower = alias.lower()
    index = lower.find(alias_lower)
    if index < 0:
        return _snippet(compact, SNIPPET_CHARS)
    start = max(0, index - 80)
    end = min(len(compact), start + SNIPPET_CHARS)
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(compact):
        snippet += "..."
    return _snippet(snippet, SNIPPET_CHARS)


def _snippet(text: str | None, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _normalize_text(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())
