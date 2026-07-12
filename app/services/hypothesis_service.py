from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_, select

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import (
    ChunkTag,
    KnowledgeRelation,
    KnowledgeTag,
    NoteEvidenceLink,
    NoteTag,
    PersonalNote,
)
from app.services.evidence_hygiene_service import MOCK_OR_TEST_MARKERS, is_mock_or_test_text
from app.services.keyword_search_service import KeywordSearchResult, search_keywords
from app.services.relation_service import RelationResult, show_relation


DEFAULT_LIMIT = 5
NOTE_SNIPPET_CHARS = 180
MOCK_OR_TEST_KEYWORDS = MOCK_OR_TEST_MARKERS
DIMENSION_KEYWORDS = {
    "参数效率": [
        "参数效率",
        "参数量",
        "轻量",
        "轻量化",
        "模型大小",
        "parameter",
        "params",
        "lightweight",
        "efficient",
        "efficiency",
        "flops",
        "macs",
        "computation",
        "complexity",
        "runtime",
        "latency",
        "speed",
    ],
    "纹理恢复": [
        "纹理",
        "细节",
        "高频",
        "视觉质量",
        "感知质量",
        "texture",
        "detail",
        "details",
        "high-frequency",
        "perceptual",
        "visual quality",
        "realistic",
    ],
    "局限或证据缺口": [
        "局限",
        "不足",
        "失败",
        "失败案例",
        "证据缺口",
        "limitation",
        "failure",
        "failure case",
        "ablation",
        "消融",
        "future work",
        "open problem",
        "weakness",
    ],
}
LIMITATION_EVIDENCE_KEYWORDS = (
    "limitation",
    "experiment",
    "ablation",
    "failure",
    "failure case",
    "局限",
    "实验",
    "消融",
    "失败",
    "失败案例",
)


@dataclass(frozen=True)
class DryRunNoteCandidate:
    note_id: int
    title: str
    note_type: str
    source_path: str | None
    snippet: str
    linked_chunk_ids: list[int]
    note_tags: list[str]


@dataclass(frozen=True)
class DryRunTagCandidate:
    tag_id: int
    name: str
    tag_type: str
    description: str | None


@dataclass(frozen=True)
class HypothesisDryRunReport:
    research_question: str
    dry_run: bool
    llm_called: bool
    api_called: bool
    final_hypothesis_generated: bool
    evidence_chunks: list[KeywordSearchResult]
    related_notes: list[DryRunNoteCandidate]
    related_tags: list[DryRunTagCandidate]
    related_relations: list[RelationResult]
    evidence_gaps: list[str]
    suggested_next_actions: list[str]


def generate_hypothesis_dry_run(question: str, limit: int = DEFAULT_LIMIT) -> HypothesisDryRunReport:
    init_db()
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("research question must not be empty.")

    safe_limit = max(1, limit)
    evidence_chunks = _search_evidence(normalized_question, safe_limit)
    chunk_ids = [result.chunk_id for result in evidence_chunks]
    related_notes = _load_related_notes(normalized_question, chunk_ids, safe_limit)
    related_tags = _load_related_tags(normalized_question, chunk_ids, [note.note_id for note in related_notes], safe_limit)
    related_relations = _load_related_relations(
        chunk_ids=chunk_ids,
        tag_ids=[tag.tag_id for tag in related_tags],
        note_ids=[note.note_id for note in related_notes],
        limit=safe_limit,
    )
    evidence_gaps = _build_evidence_gaps(
        normalized_question,
        evidence_chunks,
        related_notes,
        related_tags,
        related_relations,
    )
    suggested_next_actions = _build_suggested_next_actions(evidence_chunks, related_notes, related_relations)

    return HypothesisDryRunReport(
        research_question=normalized_question,
        dry_run=True,
        llm_called=False,
        api_called=False,
        final_hypothesis_generated=False,
        evidence_chunks=evidence_chunks,
        related_notes=related_notes,
        related_tags=related_tags,
        related_relations=related_relations,
        evidence_gaps=evidence_gaps,
        suggested_next_actions=suggested_next_actions,
    )


def _search_evidence(question: str, limit: int) -> list[KeywordSearchResult]:
    results = search_keywords(question, limit=limit)
    seen_chunk_ids = {result.chunk_id for result in results}
    if len(results) >= limit:
        return results[:limit]

    for token in _query_tokens(question):
        for result in search_keywords(token, limit=limit):
            if result.chunk_id in seen_chunk_ids:
                continue
            results.append(result)
            seen_chunk_ids.add(result.chunk_id)
            if len(results) >= limit:
                return results
    return results[:limit]


def _load_related_notes(question: str, chunk_ids: list[int], limit: int) -> list[DryRunNoteCandidate]:
    with SessionLocal() as session:
        notes_by_id: dict[int, tuple[PersonalNote, set[int]]] = {}
        if chunk_ids:
            rows = session.execute(
                select(PersonalNote, NoteEvidenceLink.chunk_id)
                .join(NoteEvidenceLink, NoteEvidenceLink.note_id == PersonalNote.id)
                .where(NoteEvidenceLink.chunk_id.in_(chunk_ids))
                .order_by(PersonalNote.id)
            ).all()
            for note, chunk_id in rows:
                _, linked_chunk_ids = notes_by_id.setdefault(note.id, (note, set()))
                linked_chunk_ids.add(chunk_id)

        for token in _query_tokens(question):
            like_token = f"%{_escape_like(token)}%"
            rows = session.scalars(
                select(PersonalNote)
                .where(
                    or_(
                        PersonalNote.title.like(like_token, escape="\\"),
                        PersonalNote.summary.like(like_token, escape="\\"),
                        PersonalNote.content.like(like_token, escape="\\"),
                    )
                )
                .order_by(PersonalNote.id)
                .limit(limit)
            ).all()
            for note in rows:
                notes_by_id.setdefault(note.id, (note, set()))
                if len(notes_by_id) >= limit:
                    break
            if len(notes_by_id) >= limit:
                break

        note_tags = _load_note_tag_labels(session, list(notes_by_id))
        candidates: list[DryRunNoteCandidate] = []
        for note, linked_chunk_ids in notes_by_id.values():
            candidates.append(
                DryRunNoteCandidate(
                    note_id=note.id,
                    title=note.title,
                    note_type=note.note_type,
                    source_path=note.source_path,
                    snippet=_snippet(note.summary or note.content, NOTE_SNIPPET_CHARS),
                    linked_chunk_ids=sorted(linked_chunk_ids),
                    note_tags=note_tags.get(note.id, []),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates


def _load_related_tags(
    question: str,
    chunk_ids: list[int],
    note_ids: list[int],
    limit: int,
) -> list[DryRunTagCandidate]:
    with SessionLocal() as session:
        tags_by_id: dict[int, KnowledgeTag] = {}
        if chunk_ids:
            rows = session.scalars(
                select(KnowledgeTag)
                .join(ChunkTag, ChunkTag.tag_id == KnowledgeTag.id)
                .where(ChunkTag.chunk_id.in_(chunk_ids))
                .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
            ).all()
            for tag in rows:
                tags_by_id[tag.id] = tag

        if note_ids:
            rows = session.scalars(
                select(KnowledgeTag)
                .join(NoteTag, NoteTag.tag_id == KnowledgeTag.id)
                .where(NoteTag.note_id.in_(note_ids))
                .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
            ).all()
            for tag in rows:
                tags_by_id[tag.id] = tag

        for token in _query_tokens(question):
            like_token = f"%{_escape_like(token)}%"
            rows = session.scalars(
                select(KnowledgeTag)
                .where(
                    or_(
                        KnowledgeTag.name.like(like_token, escape="\\"),
                        KnowledgeTag.description.like(like_token, escape="\\"),
                    )
                )
                .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
                .limit(limit)
            ).all()
            for tag in rows:
                tags_by_id[tag.id] = tag
                if len(tags_by_id) >= limit:
                    break
            if len(tags_by_id) >= limit:
                break

        return [
            DryRunTagCandidate(
                tag_id=tag.id,
                name=tag.name,
                tag_type=tag.tag_type,
                description=tag.description,
            )
            for tag in list(tags_by_id.values())[:limit]
        ]


def _load_related_relations(
    chunk_ids: list[int],
    tag_ids: list[int],
    note_ids: list[int],
    limit: int,
) -> list[RelationResult]:
    with SessionLocal() as session:
        conditions = []
        if chunk_ids:
            conditions.append(KnowledgeRelation.evidence_chunk_id.in_(chunk_ids))
            conditions.append(
                (KnowledgeRelation.source_type == "chunk") & KnowledgeRelation.source_id.in_(chunk_ids)
            )
            conditions.append(
                (KnowledgeRelation.target_type == "chunk") & KnowledgeRelation.target_id.in_(chunk_ids)
            )
        if tag_ids:
            conditions.append((KnowledgeRelation.source_type == "tag") & KnowledgeRelation.source_id.in_(tag_ids))
            conditions.append((KnowledgeRelation.target_type == "tag") & KnowledgeRelation.target_id.in_(tag_ids))
        if note_ids:
            conditions.append(KnowledgeRelation.note_id.in_(note_ids))
            conditions.append((KnowledgeRelation.source_type == "note") & KnowledgeRelation.source_id.in_(note_ids))
            conditions.append((KnowledgeRelation.target_type == "note") & KnowledgeRelation.target_id.in_(note_ids))
        if not conditions:
            return []

        relation_ids = session.scalars(
            select(KnowledgeRelation.id)
            .where(or_(*conditions))
            .order_by(KnowledgeRelation.id.desc())
            .limit(max(1, limit))
        ).all()
    return [show_relation(relation_id) for relation_id in relation_ids]


def _load_note_tag_labels(session, note_ids: list[int]) -> dict[int, list[str]]:
    if not note_ids:
        return {}
    rows = session.execute(
        select(NoteTag.note_id, KnowledgeTag.tag_type, KnowledgeTag.name)
        .join(KnowledgeTag, KnowledgeTag.id == NoteTag.tag_id)
        .where(NoteTag.note_id.in_(note_ids))
        .order_by(KnowledgeTag.tag_type, KnowledgeTag.name)
    ).all()
    labels_by_note: dict[int, list[str]] = {note_id: [] for note_id in note_ids}
    for note_id, tag_type, name in rows:
        labels_by_note.setdefault(note_id, []).append(f"{tag_type}:{name}")
    return labels_by_note


def _build_evidence_gaps(
    question: str,
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_tags: list[DryRunTagCandidate],
    related_relations: list[RelationResult],
) -> list[str]:
    gaps: list[str] = []
    evidence_text = _collect_evidence_text(evidence_chunks, related_notes, related_tags, related_relations)
    if not evidence_chunks:
        gaps.append("当前知识库未检索到相关 evidence chunk，不能进入假设生成。")
    if not related_notes:
        gaps.append("未找到相关 personal_notes，缺少个人理解层判断。")
    if not related_tags:
        gaps.append("未找到相关标签，任务/方法/问题/局限结构仍不明确。")
    if not related_relations:
        gaps.append("未找到相关 knowledge_relations，方法-问题-证据链仍不完整。")
    if evidence_chunks and not any(result.pdf_page_start is not None for result in evidence_chunks):
        gaps.append("检索到的证据缺少 PDF 页码，后续需要补齐页码回溯。")
    if _is_mock_or_test_evidence(evidence_text):
        gaps.append("当前命中的证据包含 mock/test/acceptance 数据，不应作为真实研究问题的充分证据。")
    if evidence_chunks and all(_is_mock_or_test_evidence(_collect_chunk_text(chunk)) for chunk in evidence_chunks):
        gaps.append("当前没有可用于真实研究判断的有效 evidence chunk。")
    for dimension in _find_uncovered_question_dimensions(question, evidence_text):
        gaps.append(f"当前 evidence 未覆盖问题维度：{dimension}。")
    if (
        len(evidence_chunks) == 1
        and not _contains_any_keyword(evidence_text, LIMITATION_EVIDENCE_KEYWORDS)
    ):
        gaps.append("证据数量偏少，且缺少 limitation / experiment / ablation / failure case 相关证据。")
    return gaps


def _collect_evidence_text(
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_tags: list[DryRunTagCandidate],
    related_relations: list[RelationResult],
) -> str:
    parts: list[str] = []
    for chunk in evidence_chunks:
        parts.append(_collect_chunk_text(chunk))
    for note in related_notes:
        parts.extend([note.title, note.snippet, " ".join(note.note_tags)])
    for tag in related_tags:
        parts.extend([tag.name, tag.tag_type, tag.description or ""])
    for relation in related_relations:
        parts.extend(
            [
                relation.relation_type,
                relation.description or "",
                relation.evidence_document_title or "",
                relation.evidence_heading_path or "",
            ]
        )
    return " ".join(part for part in parts if part)


def _collect_chunk_text(chunk: KeywordSearchResult) -> str:
    return " ".join(
        part
        for part in [
            chunk.document_title,
            chunk.heading_path,
            chunk.chunk_text_snippet,
            chunk.pdf_path or "",
            " ".join(chunk.chunk_tags),
            " ".join(chunk.related_note_titles),
        ]
        if part
    )


def _is_mock_or_test_evidence(text: str) -> bool:
    return is_mock_or_test_text(text)


def _find_uncovered_question_dimensions(question: str, evidence_text: str) -> list[str]:
    uncovered: list[str] = []
    for dimension, keywords in DIMENSION_KEYWORDS.items():
        if _contains_any_keyword(question, keywords) and not _contains_any_keyword(evidence_text, keywords):
            uncovered.append(dimension)
    return uncovered


def _contains_any_keyword(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _build_suggested_next_actions(
    evidence_chunks: list[KeywordSearchResult],
    related_notes: list[DryRunNoteCandidate],
    related_relations: list[RelationResult],
) -> list[str]:
    steps = [
        "人工阅读 evidence chunk 与 PDF 页码，确认问题、方法和局限是否真实存在。",
        "必要时为关键 chunk / note 补充 task、method、problem、limitation、metric 标签。",
        "必要时手动创建带 evidence_chunk_id 的 knowledge_relations。",
    ]
    if evidence_chunks and related_notes and related_relations:
        steps.append("证据链已具备初步结构，可在后续 Phase 8B 再接入受控生成或人工撰写候选假设。")
    else:
        steps.append("当前只输出证据准备报告，不生成候选研究假设。")
    return steps


def _query_tokens(question: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", question)
    tokens: list[str] = []
    for token in raw_tokens:
        cleaned = token.strip()
        if len(cleaned) < 2 or cleaned in tokens:
            continue
        tokens.append(cleaned)
    return tokens


def _snippet(text: str | None, max_chars: int) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
