from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import ValidationError

from app.schemas.mechanism_draft import MechanismDraftResponse
from app.services.mechanism_source_parity_service import (
    SOURCE_BALANCE_POLICY,
    SOURCE_COVERAGE_REQUIREMENTS,
    SOURCE_MODES,
    build_mechanism_source_pack,
    contribution_warnings,
)


READY_STATUS = "ready_for_mechanism_prompt"
UNSUPPORTED_ASSERTION_TERMS = (
    "已证明",
    "实验表明",
    "显著提升",
    "显著优于",
    "experiment shows",
    "significantly improves",
    "has been validated",
    "has been proven",
)
UNSUPPORTED_EQUIVALENCE_TERMS = (
    "等价于",
    "完全等同",
    "equivalent to",
    "identical to",
    "is the same as",
)
STRONG_MECHANISM_FIELDS = (
    "mechanism_key",
    "mechanism_name_cn",
    "mechanism_name_en",
    "mechanism_type",
    "short_explanation",
    "long_explanation",
    "abstract_form",
    "source_domain_explanation",
    "transfer_principle",
    "transfer_directions",
    "candidate_methods",
    "research_hypotheses",
)


def build_mechanism_draft_prompt(
    readiness_report: Mapping[str, Any],
    *,
    include_prompt_text: bool = True,
    include_expected_schema: bool = True,
) -> dict[str, Any]:
    blocked_reason = _blocked_reason(readiness_report)
    if blocked_reason:
        return _prompt_result("BLOCKED", True, blocked_reason)
    payload = build_mechanism_draft_prompt_payload(readiness_report)
    return _prompt_result(
        "OK",
        False,
        None,
        prompt_text=_render_prompt_text(payload) if include_prompt_text else None,
        prompt_payload_json=payload,
        expected_response_schema=(
            mechanism_draft_expected_response_schema() if include_expected_schema else None
        ),
    )


def build_mechanism_draft_prompt_payload(
    readiness_report: Mapping[str, Any],
) -> dict[str, Any]:
    blocked_reason = _blocked_reason(readiness_report)
    if blocked_reason:
        raise ValueError(blocked_reason)
    source = readiness_report.get("mechanism_prompt_payload_preview")
    if not isinstance(source, Mapping):
        raise ValueError("ready_readiness_report_missing_prompt_payload_preview")
    payload = json.loads(json.dumps(dict(source), ensure_ascii=False))
    _ensure_source_parity_payload(payload)
    return payload


def validate_mechanism_draft_response(
    response_json: Mapping[str, Any],
    readiness_report: Mapping[str, Any],
) -> dict[str, Any]:
    return build_mechanism_draft_validation_report(response_json, readiness_report)


def build_mechanism_draft_validation_report(
    response_json: Mapping[str, Any],
    readiness_report: Mapping[str, Any],
) -> dict[str, Any]:
    blocked_reason = _blocked_reason(readiness_report)
    if blocked_reason:
        return _validation_result(
            is_valid=False,
            errors=[blocked_reason],
            warnings=[],
            blocked=True,
        )

    errors: list[str] = []
    warnings: list[str] = []
    parsed_response: dict[str, Any] | None = None
    try:
        candidate = _validate_model(MechanismDraftResponse, dict(response_json))
        parsed_response = _model_dict(candidate)
    except ValidationError as exc:
        errors.extend(
            f"schema_error:{'.'.join(str(item) for item in error['loc'])}:{error['msg']}"
            for error in exc.errors()
        )

    payload = json.loads(
        json.dumps(dict(readiness_report.get("mechanism_prompt_payload_preview") or {}), ensure_ascii=False)
    )
    _ensure_source_parity_payload(payload)
    expected_note_ids = _expected_source_note_ids(payload)
    returned_note_ids = list(response_json.get("source_inspiration_note_ids") or [])
    if not expected_note_ids or not set(expected_note_ids).issubset(set(returned_note_ids)):
        errors.append("source_inspiration_note_ids_missing_input_note")

    allowed_evidence_ids = set(_int_values(payload.get("evidence_chunk_ids") or []))
    returned_evidence_ids = set(_int_values(response_json.get("evidence_chunk_ids") or []))
    if not returned_evidence_ids.issubset(allowed_evidence_ids):
        errors.append("evidence_chunk_ids_outside_readiness_evidence")
    if response_json.get("should_generate_mechanism") is True and not returned_evidence_ids:
        errors.append("generated_mechanism_requires_evidence_chunk_ids")

    review_status = str(response_json.get("review_status") or "").strip().lower()
    if review_status != "pending":
        errors.append("review_status_must_be_pending")

    confidence = str(response_json.get("confidence") or "").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        errors.append("confidence_must_be_low_medium_or_high")
    if confidence == "high" and (not allowed_evidence_ids or not returned_evidence_ids):
        errors.append("high_confidence_requires_bounded_evidence")

    if not str(response_json.get("hallucination_guard_reason") or "").strip():
        errors.append("hallucination_guard_reason_required")

    if not list(response_json.get("failure_modes") or []):
        warnings.append("failure_modes_should_include_at_least_one_risk")

    if response_json.get("should_generate_mechanism") is False:
        populated = [
            field for field in STRONG_MECHANISM_FIELDS if response_json.get(field)
        ]
        if populated:
            errors.append("no_mechanism_response_must_not_populate_strong_mechanism_fields")

    _check_required_assumptions_and_evidence(response_json, errors)
    _check_source_parity_fields(response_json, payload, errors, warnings)
    _check_object_review_boundary(response_json, payload, errors)
    _check_note_immutability(response_json, payload, errors)
    _check_unsupported_claims(response_json, payload, errors)

    return _validation_result(
        is_valid=not errors,
        errors=list(dict.fromkeys(errors)),
        warnings=list(dict.fromkeys(warnings)),
        blocked=False,
        parsed_response=parsed_response if not errors else None,
    )


def mechanism_draft_expected_response_schema() -> dict[str, Any]:
    if hasattr(MechanismDraftResponse, "model_json_schema"):
        return MechanismDraftResponse.model_json_schema()
    return MechanismDraftResponse.schema()


def _ensure_source_parity_payload(payload: dict[str, Any]) -> None:
    if "mechanism_source_pack" in payload:
        return
    source_note = (
        payload.get("source_inspiration_notes", [{}])[0]
        if isinstance(payload.get("source_inspiration_notes"), list)
        and payload.get("source_inspiration_notes")
        else {}
    )
    evidence = (
        payload.get("evidence", [{}])[0]
        if isinstance(payload.get("evidence"), list) and payload.get("evidence")
        else {}
    )
    pack = build_mechanism_source_pack(
        note=source_note if isinstance(source_note, Mapping) else {},
        source_note_id=payload.get("source_inspiration_note_id"),
        note_text=payload.get("user_note_text"),
        selected_text=payload.get("selected_text"),
        source_excerpt=evidence if isinstance(evidence, Mapping) else {},
        matched_chunks=list(payload.get("evidence") or []),
        nearby_chunks=(
            [{"nearby_context": payload.get("nearby_context"), "role": "context_support"}]
            if payload.get("nearby_context")
            else []
        ),
        linked_objects=list(payload.get("approved_objects") or []),
    )
    payload["mechanism_source_pack"] = pack
    payload["source_mode"] = pack["source_mode"]
    payload["primary_user_note"] = pack["primary_user_note"]
    payload["primary_source_excerpt"] = pack["primary_source_excerpt"]
    payload["source_balance_policy"] = dict(SOURCE_BALANCE_POLICY)
    payload["source_coverage_requirements"] = list(SOURCE_COVERAGE_REQUIREMENTS)


def _render_prompt_text(payload: Mapping[str, Any]) -> str:
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    schema_json = json.dumps(mechanism_draft_expected_response_schema(), ensure_ascii=False, indent=2)
    return f"""你是科研阅读笔记升华器，不是普通摘要器。

任务：
基于 mechanism_source_pack 中的用户笔记与原文片段这两个平等 primary sources、chunk 上下文和已审核对象，判断是否值得生成机制草稿，并严格输出 JSON。

硬规则：
1. 用户原始 note_text 是不可变来源，不允许覆盖、纠正或代写。
2. selected_text / chunk_text 是 primary source，不允许改写后冒充原文，也不得降级成 citation-only evidence。
3. 不允许生成没有 supplied evidence 支持的机制。
4. 不允许把相关但不等价的概念声称为等价。
5. 不允许声称实验已验证、显著提升或已证明，除非 supplied evidence 明确支持该陈述。
6. 如果只是普通摘录，设置 should_generate_mechanism=false。
7. 如果证据不足，设置 confidence=low，并在 needs_user_review_reason 中明确说明。
8. 机制必须服务于迁移、方法、假设或实验方向，否则不要生成。
9. 只输出符合 schema 的 JSON，不要自由散文。
10. review_status 必须是 pending；不得输出 accepted、activated、validated 或 confirmed 状态。
11. candidate_objects 不是 approved_objects，不得作为已审核对象引用。
12. 必须给出 evidence_use_summary、hallucination_guard_reason、failure_modes，以及适用时的 required_assumptions/required_evidence。
13. 必须输出 source_mode，取值仅限 note_led、source_led、joint_led、unknown；机制可以由用户笔记主导、原文片段主导或二者共同主导。
14. 必须输出 user_note_contribution、source_excerpt_contribution、linked_object_contribution、evidence_alignment 和 source_balance_warnings。
15. linked_objects 只是 semantic support，不是机制本身；不得把对象名称当作机制定义。

[MECHANISM_PROMPT_INPUT_JSON]
{input_json}

[EXPECTED_RESPONSE_SCHEMA_JSON]
{schema_json}
"""


def _blocked_reason(readiness_report: Mapping[str, Any]) -> str | None:
    status = str(readiness_report.get("readiness_status") or "")
    if status != READY_STATUS:
        return f"readiness_status_not_ready:{status or 'missing'}"
    if readiness_report.get("object_review_required") is True:
        return "readiness_report_still_requires_object_review"
    payload = readiness_report.get("mechanism_prompt_payload_preview")
    if not isinstance(payload, Mapping):
        return "ready_readiness_report_missing_prompt_payload_preview"
    if payload.get("schema_version") != "mechanism_prompt_input_v1":
        return "ready_readiness_report_has_invalid_prompt_payload_schema"
    return None


def _check_required_assumptions_and_evidence(
    response_json: Mapping[str, Any],
    errors: list[str],
) -> None:
    for index, method in enumerate(response_json.get("candidate_methods") or []):
        if not isinstance(method, Mapping) or not list(method.get("required_assumptions") or []):
            errors.append(f"candidate_methods[{index}]_requires_required_assumptions")
    for index, hypothesis in enumerate(response_json.get("research_hypotheses") or []):
        if not isinstance(hypothesis, Mapping) or not list(hypothesis.get("required_evidence") or []):
            errors.append(f"research_hypotheses[{index}]_requires_required_evidence")


def _check_object_review_boundary(
    response_json: Mapping[str, Any],
    payload: Mapping[str, Any],
    errors: list[str],
) -> None:
    approved_names = {
        str(item.get("object_name") or "").strip().casefold()
        for item in payload.get("approved_objects") or []
        if isinstance(item, Mapping)
    }
    candidate_names = {
        str(item.get("object_name") or "").strip().casefold()
        for item in payload.get("candidate_objects") or []
        if isinstance(item, Mapping)
    }
    for item in response_json.get("linked_objects") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("object_name") or "").strip().casefold()
        if name in candidate_names and name not in approved_names:
            errors.append("candidate_object_must_not_be_used_as_approved_linked_object")
        elif name and name not in approved_names:
            errors.append("linked_object_not_in_approved_objects")


def _check_source_parity_fields(
    response_json: Mapping[str, Any],
    payload: Mapping[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    source_mode = str(response_json.get("source_mode") or "unknown").strip()
    if source_mode not in SOURCE_MODES:
        errors.append("source_mode_not_allowed")
    linked_objects_present = bool(payload.get("approved_objects") or payload.get("candidate_objects"))
    warnings.extend(
        contribution_warnings(
            response_json,
            linked_objects_present=linked_objects_present,
        )
    )
    evidence_alignment = str(response_json.get("evidence_alignment") or "").strip()
    if not evidence_alignment:
        warnings.append("source_conflict_unresolved")


def _check_note_immutability(
    response_json: Mapping[str, Any],
    payload: Mapping[str, Any],
    errors: list[str],
) -> None:
    for field in ("user_note_text", "note_text", "selected_text", "user_tags"):
        if field in response_json:
            errors.append(f"mechanism_response_must_not_output_raw_note_field:{field}")
    raw_note = payload.get("user_note_text")
    if raw_note is not None and response_json.get("original_user_note_text") not in (None, raw_note):
        errors.append("original_user_note_text_mismatch")


def _check_unsupported_claims(
    response_json: Mapping[str, Any],
    payload: Mapping[str, Any],
    errors: list[str],
) -> None:
    response_text = _flatten_text(response_json).casefold()
    evidence_text = " ".join(
        [
            str(payload.get("chunk_text") or ""),
            str(payload.get("nearby_context") or ""),
        ]
    ).casefold()
    for phrase in UNSUPPORTED_ASSERTION_TERMS:
        if phrase.casefold() in response_text and phrase.casefold() not in evidence_text:
            errors.append(f"unsupported_validated_result_claim:{phrase}")
            errors.append("unsupported_mechanism_claim")
    for phrase in UNSUPPORTED_EQUIVALENCE_TERMS:
        if phrase.casefold() in response_text and phrase.casefold() not in evidence_text:
            errors.append(f"unsupported_equivalence_claim:{phrase}")
            errors.append("unsupported_mechanism_claim")


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value) if value is not None else ""


def _int_values(values: list[Any]) -> list[int]:
    results = []
    for value in values:
        try:
            results.append(int(value))
        except (TypeError, ValueError):
            continue
    return results


def _expected_source_note_ids(payload: Mapping[str, Any]) -> list[Any]:
    grouped = payload.get("source_inspiration_note_ids")
    if isinstance(grouped, list) and grouped:
        return list(grouped)
    single = payload.get("source_inspiration_note_id")
    if single is not None:
        return [single]
    return [
        item.get("inspiration_note_id")
        for item in payload.get("source_inspiration_notes") or []
        if isinstance(item, Mapping) and item.get("inspiration_note_id") is not None
    ]


def _validate_model(model: Any, value: dict[str, Any]) -> Any:
    if hasattr(model, "model_validate"):
        return model.model_validate(value)
    return model.parse_obj(value)


def _model_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _prompt_result(
    status: str,
    blocked: bool,
    blocked_reason: str | None,
    *,
    prompt_text: str | None = None,
    prompt_payload_json: dict[str, Any] | None = None,
    expected_response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "prompt_text": prompt_text,
        "prompt_payload_json": prompt_payload_json,
        "expected_response_schema": expected_response_schema,
        **_dry_run_flags(),
    }


def _validation_result(
    *,
    is_valid: bool,
    errors: list[str],
    warnings: list[str],
    blocked: bool,
    parsed_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED" if blocked else "OK",
        "is_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "blocked": blocked,
        "parsed_response": parsed_response,
        **_dry_run_flags(),
    }


def _dry_run_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "mechanism_generated": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
    }
