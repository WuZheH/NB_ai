from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

ALLOWED_OBJECT_TYPES = {
    "method", "mechanism", "problem", "metric", "dataset",
    "task", "concept", "component", "loss", "experiment_setting",
    "model", "algorithm", "training_strategy", "evaluation_protocol",
    "problem_setting", "assumption", "method/concept", "unknown",
}
ALLOWED_REVIEW_STATUSES = {"accepted", "edited"}
FORBIDDEN_STATUSES = {"suggested", "rejected", "confirmed", "evidence_supported", "committed"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_MAPPING_STATUSES = {"not_mapped", "mapped", "partial", "failed"}
ALLOWED_SOURCE_ORIGINS = {"note_triggered", "note_sentence_required", "context_supporting", "section_background"}
ALLOWED_NECESSITY_JUDGMENTS = {"essential", "useful", "optional", "probably_not_needed"}
ALLOWED_IMPORTANCE_SCORES = {"high", "medium", "low"}


class ObjectCandidate(Base):
    __tablename__ = "object_candidates"
    __table_args__ = (
        UniqueConstraint("import_job_id", "object_key", name="uq_object_candidates_import_job_object_key"),
        CheckConstraint(
            f"review_status IN ({', '.join(repr(s) for s in sorted(ALLOWED_REVIEW_STATUSES))})",
            name="ck_object_candidates_review_status",
        ),
        CheckConstraint(
            f"confidence IS NULL OR confidence IN ({', '.join(repr(c) for c in sorted(ALLOWED_CONFIDENCE))})",
            name="ck_object_candidates_confidence",
        ),
        CheckConstraint(
            f"mapping_status IN ({', '.join(repr(s) for s in sorted(ALLOWED_MAPPING_STATUSES))})",
            name="ck_object_candidates_mapping_status",
        ),
        Index("ix_object_candidates_review_status", "review_status"),
        Index("ix_object_candidates_import_job_id", "import_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, deferred=True)
    import_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    object_name: Mapped[str] = mapped_column(String(512), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    problem_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    mechanism_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    inspiration_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    note_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_note_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_origin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    necessity_judgment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    importance_score: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_package_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_import_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_mapped")
    mapped_chunk_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    user_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="user_reviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    def set_aliases(self, values: list[str]) -> None:
        self.aliases_json = json.dumps(values, ensure_ascii=False)

    def get_aliases(self) -> list[str]:
        return _parse_json_list(self.aliases_json)

    def set_four_layer_tags(self, topic: list[str], problem: list[str], mechanism: list[str], inspiration: list[str]) -> None:
        self.topic_tags_json = json.dumps(topic, ensure_ascii=False)
        self.problem_tags_json = json.dumps(problem, ensure_ascii=False)
        self.mechanism_tags_json = json.dumps(mechanism, ensure_ascii=False)
        self.inspiration_tags_json = json.dumps(inspiration, ensure_ascii=False)

    def set_evidence_refs(self, refs: list[dict[str, Any]]) -> None:
        self.evidence_refs_json = json.dumps(refs, ensure_ascii=False)

    def get_evidence_refs(self) -> list[dict[str, Any]]:
        return _parse_json_obj_list(self.evidence_refs_json)

    def set_source_note_ids(self, note_ids: list[Any]) -> None:
        self.source_note_ids_json = json.dumps(note_ids, ensure_ascii=False)

    def get_source_note_ids(self) -> list[Any]:
        return _parse_json_list(self.source_note_ids_json)

    def set_mapped_chunk_ids(self, chunk_ids: list[int]) -> None:
        self.mapped_chunk_ids_json = json.dumps(chunk_ids)

    def get_mapped_chunk_ids(self) -> list[int]:
        return _parse_json_list_int(self.mapped_chunk_ids_json)

    def set_warnings(self, warnings: list[Any]) -> None:
        self.warnings_json = json.dumps(warnings, ensure_ascii=False)

    def get_warnings(self) -> list[Any]:
        return _parse_json_list(self.warnings_json)


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_obj_list(raw: str | None) -> list[dict[str, Any]]:
    items = _parse_json_list(raw)
    return [item for item in items if isinstance(item, dict)]


def _parse_json_list_int(raw: str | None) -> list[int]:
    items = _parse_json_list(raw)
    return [int(item) for item in items if isinstance(item, (int, float))]
