from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.import_preview_service import (
    ImportPreviewError, STAGING_ROOT, JOB_ID_RE, _existing_job_dir,
    _now, _safety_response, _write_json, _relative,
)


ALLOWED_AI_STATUSES = {"suggested"}
ALLOWED_REVIEW_STATUSES = {"accepted", "rejected", "edited", "suggested"}
FORBIDDEN_REVIEW_STATUSES = {"confirmed", "evidence_supported", "committed"}
ALLOWED_OBJECT_TYPES = {
    "method", "mechanism", "problem", "metric", "dataset",
    "task", "concept", "component", "loss", "experiment_setting",
    "method/concept", "unknown",
}
ALLOWED_TAG_LAYERS = {"topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"}
SCHEMA_VERSION = "object_tag_suggestions_v1"
REVIEWED_SCHEMA_VERSION = "reviewed_object_tag_package_v1"
SUGGESTIONS_FILE = "ai_object_tag_suggestions.json"
REVIEWED_FILE = "reviewed_object_tag_package.json"


def upload_ai_suggestions(import_job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Upload ChatGPT-generated object/tag suggestions to staging."""
    job_dir = _existing_job_dir(import_job_id)
    suggestions_path = job_dir / SUGGESTIONS_FILE

    schema_version = str(payload.get("schema_version") or "")
    if schema_version != SCHEMA_VERSION:
        raise ImportPreviewError(
            f"schema_version must be '{SCHEMA_VERSION}', got '{schema_version}'."
        )

    objects = _require_list(payload, "objects", "objects must be a list.")
    warnings: list[Any] = []
    source_trace = _load_source_trace(job_dir)

    validated_objects = []
    for i, obj in enumerate(objects):
        obj_warnings = _validate_ai_object(obj, i, source_trace)
        warnings.extend(obj_warnings)
        validated_objects.append(obj)

    suggestions = {
        "schema_version": SCHEMA_VERSION,
        "import_job_id": import_job_id,
        "status": "ai_suggested",
        "created_by": str(payload.get("created_by") or "external_import"),
        "external_llm_called_by_notebook_ai": False,
        "uploaded_at": _now(),
        "objects": validated_objects,
        "safety": {
            "notebook_ai_external_llm_called": False,
            "core_db_write_performed": False,
            "committed_to_library": False,
        },
    }
    _write_json(suggestions_path, suggestions)

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "suggestions_path": _relative(suggestions_path),
        "object_count": len(validated_objects),
        "warnings": warnings,
        **_safety_response(),
    }


def get_ai_suggestions(import_job_id: str) -> dict[str, Any]:
    """Retrieve summary of AI suggestions for an import preview."""
    job_dir = _existing_job_dir(import_job_id)
    suggestions_path = job_dir / SUGGESTIONS_FILE
    if not suggestions_path.is_file():
        return {
            "status": "not_found",
            "import_job_id": import_job_id,
            "message": "No AI suggestions have been uploaded for this import preview.",
            **_safety_response(),
        }
    data = _read_json_staging(suggestions_path)
    objects = data.get("objects") or []
    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "object_count": len(objects),
        "suggestions_status": data.get("status", "ai_suggested"),
        "created_by": data.get("created_by"),
        "warnings_summary": _summarize_warnings(objects),
        "objects": objects,
        "object_preview": [
            {
                "object_key": obj.get("object_key"),
                "object_name": obj.get("object_name"),
                "object_type": obj.get("object_type"),
                "confidence": obj.get("confidence"),
            }
            for obj in objects[:10]
        ],
        **_safety_response(),
    }


def upload_reviewed_objects(import_job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Upload user-reviewed object/tag package to staging."""
    job_dir = _existing_job_dir(import_job_id)
    reviewed_path = job_dir / REVIEWED_FILE

    schema_version = str(payload.get("schema_version") or "")
    if schema_version != REVIEWED_SCHEMA_VERSION:
        raise ImportPreviewError(
            f"schema_version must be '{REVIEWED_SCHEMA_VERSION}', got '{schema_version}'."
        )

    objects = _require_list(payload, "objects", "objects must be a list.")
    warnings: list[Any] = []
    source_trace = _load_source_trace(job_dir)
    accepted = 0
    edited = 0
    rejected = 0
    suggested_count = 0

    validated_objects = []
    for i, obj in enumerate(objects):
        review_status = str(obj.get("review_status") or "").strip().lower()
        if review_status in FORBIDDEN_REVIEW_STATUSES:
            raise ImportPreviewError(
                f"objects[{i}].review_status='{review_status}' is not allowed. "
                f"Only {ALLOWED_REVIEW_STATUSES} are permitted. "
                f"'{review_status}' is reserved for future commit phases."
            )
        if review_status not in ALLOWED_REVIEW_STATUSES:
            raise ImportPreviewError(
                f"objects[{i}].review_status must be one of {ALLOWED_REVIEW_STATUSES}, "
                f"got '{review_status}'."
            )
        if review_status == "accepted":
            accepted += 1
        elif review_status == "edited":
            edited += 1
        elif review_status == "rejected":
            rejected += 1
        elif review_status == "suggested":
            suggested_count += 1

        obj_warnings = _validate_reviewed_object(obj, i, source_trace)
        warnings.extend(obj_warnings)
        validated_objects.append(obj)

    reviewed = {
        "schema_version": REVIEWED_SCHEMA_VERSION,
        "import_job_id": import_job_id,
        "status": "user_reviewed",
        "reviewed_by": str(payload.get("reviewed_by") or "user"),
        "reviewed_at": _now(),
        "objects": validated_objects,
        "safety": {
            "core_db_write_performed": False,
            "committed_to_library": False,
        },
    }
    _write_json(reviewed_path, reviewed)

    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "reviewed_package_path": _relative(reviewed_path),
        "accepted_count": accepted,
        "edited_count": edited,
        "rejected_count": rejected,
        "suggested_count": suggested_count,
        "warnings": warnings,
        "core_db_write_performed": False,
        "external_llm_called": False,
    }


def get_reviewed_objects(import_job_id: str) -> dict[str, Any]:
    """Retrieve summary of reviewed object/tag package."""
    job_dir = _existing_job_dir(import_job_id)
    reviewed_path = job_dir / REVIEWED_FILE
    if not reviewed_path.is_file():
        return {
            "status": "not_found",
            "import_job_id": import_job_id,
            "message": "No reviewed object/tag package has been uploaded.",
            **_safety_response(),
        }
    data = _read_json_staging(reviewed_path)
    objects = data.get("objects") or []
    accepted = sum(1 for o in objects if o.get("review_status") == "accepted")
    edited = sum(1 for o in objects if o.get("review_status") == "edited")
    rejected = sum(1 for o in objects if o.get("review_status") == "rejected")
    suggested = sum(1 for o in objects if o.get("review_status") == "suggested")
    return {
        "status": "ok",
        "import_job_id": import_job_id,
        "object_count": len(objects),
        "accepted_count": accepted,
        "edited_count": edited,
        "rejected_count": rejected,
        "suggested_count": suggested,
        "objects": objects,
        "object_preview": [
            {
                "object_key": obj.get("object_key"),
                "object_name": obj.get("object_name"),
                "object_type": obj.get("object_type"),
                "review_status": obj.get("review_status"),
            }
            for obj in objects[:10]
        ],
        **_safety_response(),
    }


def get_suggestion_package_status(import_job_id: str) -> dict[str, Any]:
    """Return suggestion/package status for inclusion in GET import preview."""
    job_dir = _existing_job_dir(import_job_id)
    ai_path = job_dir / SUGGESTIONS_FILE
    reviewed_path = job_dir / REVIEWED_FILE
    result: dict[str, Any] = {
        "has_ai_suggestions": ai_path.is_file(),
        "ai_suggestions_count": 0,
        "has_reviewed_objects": reviewed_path.is_file(),
        "reviewed_objects_summary": None,
        "package_warnings": [],
    }
    if ai_path.is_file():
        data = _read_json_staging(ai_path)
        objects = data.get("objects") or []
        result["ai_suggestions_count"] = len(objects)
        result["package_warnings"] = _summarize_warnings(objects)
    if reviewed_path.is_file():
        data = _read_json_staging(reviewed_path)
        objects = data.get("objects") or []
        result["reviewed_objects_summary"] = {
            "total": len(objects),
            "accepted": sum(1 for o in objects if o.get("review_status") == "accepted"),
            "edited": sum(1 for o in objects if o.get("review_status") == "edited"),
            "rejected": sum(1 for o in objects if o.get("review_status") == "rejected"),
        }
    return result


# -- validation helpers --

def _validate_ai_object(obj: Any, index: int, source_trace: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if not isinstance(obj, dict):
        raise ImportPreviewError(f"objects[{index}] must be a dict.")

    for field in ("object_name", "object_type", "object_key"):
        if not str(obj.get(field) or "").strip():
            raise ImportPreviewError(f"objects[{index}].{field} is required.")

    obj_type = str(obj.get("object_type") or "").strip()
    if obj_type not in ALLOWED_OBJECT_TYPES:
        raise ImportPreviewError(
            f"objects[{index}].object_type='{obj_type}' is not allowed. "
            f"Allowed: {ALLOWED_OBJECT_TYPES}"
        )

    status = str(obj.get("status") or "").strip().lower()
    if status not in ALLOWED_AI_STATUSES:
        raise ImportPreviewError(
            f"objects[{index}].status='{status}' is not allowed. "
            f"AI suggestions must use status='suggested'."
        )

    evidence_refs = obj.get("evidence_refs") or []
    if not evidence_refs:
        warnings.append({
            "object_key": obj.get("object_key"),
            "warning": "no_evidence_refs",
            "message": f"objects[{index}] has no evidence_refs. Object confidence downgraded.",
        })
    else:
        section_map = _build_section_map(source_trace)
        for ref_index, ref in enumerate(evidence_refs):
            if not isinstance(ref, dict):
                continue
            section_id = str(ref.get("section_id") or "")
            if section_id and section_id not in section_map:
                warnings.append({
                    "object_key": obj.get("object_key"),
                    "warning": "section_ref_not_found",
                    "message": (
                        f"objects[{index}].evidence_refs[{ref_index}].section_id='{section_id}' "
                        f"not found in source_trace.sections."
                    ),
                })

    return warnings


def _validate_reviewed_object(obj: Any, index: int, source_trace: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if not isinstance(obj, dict):
        raise ImportPreviewError(f"objects[{index}] must be a dict.")

    for field in ("object_name", "object_type", "object_key", "review_status"):
        if not str(obj.get(field) or "").strip():
            raise ImportPreviewError(f"objects[{index}].{field} is required.")

    obj_type = str(obj.get("object_type") or "").strip()
    if obj_type not in ALLOWED_OBJECT_TYPES:
        raise ImportPreviewError(f"objects[{index}].object_type='{obj_type}' is not allowed.")

    evidence_refs = obj.get("evidence_refs") or []
    if not evidence_refs:
        warnings.append({
            "object_key": obj.get("object_key"),
            "warning": "no_evidence_refs",
        })
    else:
        section_map = _build_section_map(source_trace)
        for ri, ref in enumerate(evidence_refs):
            if not isinstance(ref, dict):
                continue
            section_id = str(ref.get("section_id") or "")
            if section_id and section_id not in section_map:
                warnings.append({
                    "object_key": obj.get("object_key"),
                    "warning": "section_ref_not_found",
                    "detail": f"evidence_refs[{ri}].section_id='{section_id}'",
                })

    return warnings


def _build_section_map(source_trace: dict[str, Any]) -> set[str]:
    sections = source_trace.get("sections") or []
    return {str(s.get("section_id") or "") for s in sections if s.get("section_id")}


def _require_list(payload: dict[str, Any], key: str, message: str) -> list[Any]:
    items = payload.get(key)
    if not isinstance(items, list):
        raise ImportPreviewError(message)
    return items


def _load_source_trace(job_dir: Path) -> dict[str, Any]:
    trace_path = job_dir / "source_trace.json"
    if trace_path.is_file():
        return _read_json_staging(trace_path)
    return {}


def _read_json_staging(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_warnings(objects: list[Any]) -> list[str]:
    summary: list[str] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for w in (obj.get("warnings") or []):
            if isinstance(w, dict):
                summary.append(str(w.get("warning") or w.get("message") or str(w)))
            else:
                summary.append(str(w))
    return summary[:20]
