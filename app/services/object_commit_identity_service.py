from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.services import vector_store_service
from app.services.import_preview_service import ImportPreviewError


@dataclass(frozen=True)
class ObjectCommitFrozenInput:
    """Immutable semantic input shared by reviewed preview and commit."""

    import_job_id: str
    phase: str
    document_id: int
    reviewed_objects: tuple[dict[str, Any], ...]
    remap_objects: tuple[dict[str, Any], ...] = ()


REVIEWED_OBJECT_COMMIT_FIELDS = (
    "object_key",
    "object_name",
    "object_type",
    "review_status",
    "confidence",
    "description",
    "user_comment",
    "source_origin",
    "necessity_judgment",
    "importance_score",
    "aliases",
    "topic_tags",
    "problem_tags",
    "mechanism_tags",
    "inspiration_tags",
    "evidence_refs",
    "source_note_ids",
    "warnings",
)

REMAP_COMMIT_FIELDS = (
    "object_key",
    "mapped_chunk_ids",
    "mapping_status",
    "warnings",
)


def canonical_package_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 10:
        raise ImportPreviewError(
            "object_commit_fingerprint_depth: 对象包嵌套过深。"
        )
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            item = value[key]
            if key == "object_key" and isinstance(item, str):
                item = vector_store_service.canonical_object_key(item)
            normalized[key] = canonical_package_value(item, depth=depth + 1)
        return normalized
    if isinstance(value, list):
        return [canonical_package_value(item, depth=depth + 1) for item in value]
    if isinstance(value, bool) or value is None or isinstance(
        value, (int, float, str)
    ):
        return value
    return str(value)


def project_reviewed_object(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ImportPreviewError(
            "object_commit_fingerprint_input: 对象包条目必须为对象。"
        )
    projected: dict[str, Any] = {}
    for field in REVIEWED_OBJECT_COMMIT_FIELDS:
        value = obj.get(field)
        if field == "object_key":
            value = vector_store_service.canonical_object_key(str(value or ""))
        projected[field] = canonical_package_value(value)
    return projected


def project_remap_object(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ImportPreviewError(
            "object_commit_fingerprint_input: remap 预览条目必须为对象。"
        )
    projected: dict[str, Any] = {}
    for field in REMAP_COMMIT_FIELDS:
        value = obj.get(field)
        if field == "object_key":
            value = vector_store_service.canonical_object_key(str(value or ""))
        projected[field] = canonical_package_value(value)
    return projected


def freeze_object_commit_input(
    *,
    import_job_id: str,
    phase: str,
    document_id: int,
    reviewed_objects: list[Any],
    remap_objects: list[Any] | None = None,
) -> ObjectCommitFrozenInput:
    return ObjectCommitFrozenInput(
        import_job_id=import_job_id,
        phase=phase,
        document_id=document_id,
        reviewed_objects=tuple(
            project_reviewed_object(obj) for obj in reviewed_objects
        ),
        remap_objects=tuple(
            project_remap_object(obj) for obj in (remap_objects or [])
        ),
    )


def _sha256_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def phase_input_fingerprint(frozen: ObjectCommitFrozenInput) -> str:
    return _sha256_payload(
        {
            "phase": frozen.phase,
            "import_job_id": frozen.import_job_id,
            "document_id": frozen.document_id,
            "reviewed_objects": list(frozen.reviewed_objects),
            "remap_objects": list(frozen.remap_objects),
        }
    )


def reviewed_input_fingerprint(frozen: ObjectCommitFrozenInput) -> str:
    """Identity of the reviewed semantic input from which a remap derives.

    Deliberately excludes the remap itself and volatile package metadata.
    """

    return _sha256_payload(
        {
            "import_job_id": frozen.import_job_id,
            "document_id": frozen.document_id,
            "reviewed_objects": list(frozen.reviewed_objects),
        }
    )


__all__ = [
    "ObjectCommitFrozenInput",
    "freeze_object_commit_input",
    "phase_input_fingerprint",
    "reviewed_input_fingerprint",
]
