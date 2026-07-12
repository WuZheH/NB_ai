from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MODE = "phase110k_q0_validate_mechanism_draft_json_v1"
OUTPUT_CONTRACT_VERSION = "phase110k_q0_mechanism_draft_candidate_v1"
EXPECTED_JSON_SCHEMA_NAME = "mechanism_draft_candidate.schema.json"

MECHANISM_TYPES = {
    "method_pattern",
    "causal_mechanism",
    "optimization_strategy",
    "evaluation_strategy",
    "representation_strategy",
    "research_hypothesis_seed",
}
EVIDENCE_SUPPORT_LEVELS = {
    "directly_supported",
    "user_interpretation",
    "speculative_extension",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
SOURCE_MODES = {"note_led", "source_led", "joint_led", "unknown"}
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
FORBIDDEN_ACCEPTED_STATUSES = {
    "accepted",
    "activated",
    "validated",
    "confirmed",
    "applied",
}
REQUIRED_FIELDS = (
    "mechanism_name",
    "mechanism_summary",
    "mechanism_type",
    "source_inspiration_note_id",
    "source_chunk_ids",
    "source_object_ids",
    "source_mode",
    "evidence_support_level",
    "user_note_contribution",
    "source_excerpt_contribution",
    "linked_object_contribution",
    "evidence_alignment",
    "source_balance_warnings",
    "user_interpretation",
    "generalized_mechanism",
    "original_domain_explanation",
    "transferable_pattern",
    "applicable_conditions",
    "failure_modes",
    "research_idea_seeds",
    "confidence",
    "limitations",
    "requires_human_review",
)
ALLOWED_FIELDS = set(REQUIRED_FIELDS) | {"status", "review_status"}
NON_EMPTY_STRING_FIELDS = (
    "mechanism_name",
    "mechanism_summary",
    "user_interpretation",
    "generalized_mechanism",
    "original_domain_explanation",
    "transferable_pattern",
    "user_note_contribution",
    "source_excerpt_contribution",
    "linked_object_contribution",
    "evidence_alignment",
)
LIST_STRING_FIELDS = (
    "applicable_conditions",
    "failure_modes",
    "research_idea_seeds",
    "limitations",
    "source_balance_warnings",
)


def validate_mechanism_draft_json(
    candidate_json: Mapping[str, Any] | Any,
    prompt_package_json: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(candidate_json, Mapping):
        return _result(
            errors=["candidate_json_must_be_object"],
            warnings=[],
            normalized_candidate=None,
            input_candidate=None,
        )
    candidate = dict(candidate_json)
    context = _select_prompt_context(prompt_package_json, candidate)
    errors.extend(context["errors"])

    for field in REQUIRED_FIELDS:
        if field not in candidate:
            errors.append(f"missing_required_field:{field}")
    for field in candidate:
        if field not in ALLOWED_FIELDS:
            errors.append(f"unexpected_field:{field}")

    for field in NON_EMPTY_STRING_FIELDS:
        if field in candidate and not _is_non_empty_string(candidate.get(field)):
            errors.append(f"field_must_be_non_empty_string:{field}")

    mechanism_type = str(candidate.get("mechanism_type") or "")
    if "mechanism_type" in candidate and mechanism_type not in MECHANISM_TYPES:
        errors.append("mechanism_type_not_allowed")

    evidence_level = candidate.get("evidence_support_level")
    if isinstance(evidence_level, list):
        normalized_levels = [str(item) for item in evidence_level]
        if any(level not in EVIDENCE_SUPPORT_LEVELS for level in normalized_levels):
            errors.append("evidence_support_level_not_allowed")
        if normalized_levels and all(level == "speculative_extension" for level in normalized_levels):
            errors.append("evidence_support_level_must_not_be_all_speculative")
    else:
        evidence_level_text = str(evidence_level or "")
        if "evidence_support_level" in candidate and evidence_level_text not in EVIDENCE_SUPPORT_LEVELS:
            errors.append("evidence_support_level_not_allowed")
        if evidence_level_text == "speculative_extension":
            errors.append("evidence_support_level_must_not_be_all_speculative")

    confidence = str(candidate.get("confidence") or "")
    if "confidence" in candidate and confidence not in CONFIDENCE_LEVELS:
        errors.append("confidence_must_be_low_medium_or_high")

    source_mode = str(candidate.get("source_mode") or "")
    if "source_mode" in candidate and source_mode not in SOURCE_MODES:
        errors.append("source_mode_not_allowed")

    if candidate.get("requires_human_review") is not True:
        errors.append("requires_human_review_must_be_true")

    for field in ("status", "review_status", "draft_status", "mechanism_status"):
        value = str(candidate.get(field) or "").strip().casefold()
        if value in FORBIDDEN_ACCEPTED_STATUSES:
            errors.append(f"{field}_must_not_be_{value}")

    source_note_id = candidate.get("source_inspiration_note_id")
    expected_note_id = context.get("source_inspiration_note_id")
    if expected_note_id is not None and str(source_note_id) != str(expected_note_id):
        errors.append("source_inspiration_note_id_mismatch")

    chunk_ids = _int_list(candidate.get("source_chunk_ids"))
    object_ids = _int_list(candidate.get("source_object_ids"))
    if "source_chunk_ids" in candidate and not chunk_ids:
        errors.append("source_chunk_ids_required")
    if "source_object_ids" in candidate and not object_ids:
        errors.append("source_object_ids_required")
    allowed_chunk_ids = set(_int_list(context.get("source_chunk_ids")))
    allowed_object_ids = set(_int_list(context.get("source_object_ids")))
    if allowed_chunk_ids and not set(chunk_ids).issubset(allowed_chunk_ids):
        errors.append("source_chunk_ids_outside_prompt_package")
    if allowed_object_ids and not set(object_ids).issubset(allowed_object_ids):
        errors.append("source_object_ids_outside_prompt_package")
    if object_ids and not _is_non_empty_string(candidate.get("linked_object_contribution")):
        errors.append("missing_linked_object_contribution")
    if not _is_non_empty_string(candidate.get("user_note_contribution")):
        errors.append("missing_user_note_contribution")
    if not _is_non_empty_string(candidate.get("source_excerpt_contribution")):
        errors.append("missing_source_excerpt_contribution")
    if source_mode == "note_led" and not _is_non_empty_string(candidate.get("source_excerpt_contribution")):
        warnings.append("source_imbalance_user_note_dominates")
    if source_mode == "source_led" and not _is_non_empty_string(candidate.get("user_note_contribution")):
        warnings.append("source_imbalance_source_excerpt_dominates")
    if _contains_unresolved_conflict(candidate):
        warnings.append("source_conflict_unresolved")
    _check_unsupported_claims(candidate, context, errors)

    for field in LIST_STRING_FIELDS:
        if field in candidate and not _is_string_list(candidate.get(field)):
            errors.append(f"field_must_be_string_list:{field}")
    for field in ("applicable_conditions", "failure_modes", "limitations"):
        if field in candidate and not _non_empty_list(candidate.get(field)):
            errors.append(f"field_must_not_be_empty:{field}")

    normalized_candidate = None if errors else _normalized_candidate(candidate, chunk_ids, object_ids)
    return _result(
        errors=errors,
        warnings=warnings,
        normalized_candidate=normalized_candidate,
        input_candidate=candidate,
    )


def validate_mechanism_draft_json_paths(
    input_json_path: str | Path,
    prompt_package_json_path: str | Path,
) -> dict[str, Any]:
    candidate, candidate_error = _load_json_file(Path(input_json_path), "input_json")
    prompt_package, prompt_error = _load_json_file(
        Path(prompt_package_json_path),
        "prompt_package_json",
    )
    if candidate_error or prompt_error:
        return _result(
            errors=[error for error in (candidate_error, prompt_error) if error],
            warnings=[],
            normalized_candidate=None,
            input_candidate=candidate if isinstance(candidate, Mapping) else None,
        )
    return validate_mechanism_draft_json(candidate, prompt_package)


def _select_prompt_context(
    prompt_package_json: Mapping[str, Any] | Any,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(prompt_package_json, Mapping):
        return {"errors": ["prompt_package_json_must_be_object"]}
    contexts = _prompt_contexts(prompt_package_json)
    if not contexts:
        return {"errors": ["ready_prompt_package_not_found"]}
    candidate_note_id = candidate.get("source_inspiration_note_id")
    if candidate_note_id is not None:
        for context in contexts:
            if str(context.get("source_inspiration_note_id")) == str(candidate_note_id):
                return {**context, "errors": []}
    return {**contexts[0], "errors": []}


def _prompt_contexts(prompt_package_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = prompt_package_json.get("items")
    if isinstance(items, list):
        contexts = []
        for item in items:
            if isinstance(item, Mapping):
                context = _context_from_item(item)
                if context is not None:
                    contexts.append(context)
        return contexts
    if "prompt_package" in prompt_package_json:
        context = _context_from_item(prompt_package_json)
        return [context] if context is not None else []
    context = _context_from_prompt_package(prompt_package_json)
    return [context] if context is not None else []


def _context_from_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    prompt_package = item.get("prompt_package")
    if not isinstance(prompt_package, Mapping):
        return None
    context = _context_from_prompt_package(prompt_package)
    if context is None:
        return None
    return context


def _context_from_prompt_package(prompt_package: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = prompt_package.get("user_payload")
    if not isinstance(payload, Mapping):
        return None
    return {
        "source_inspiration_note_id": payload.get("source_inspiration_note_id"),
        "source_chunk_ids": _int_list(payload.get("source_chunk_ids")),
        "source_object_ids": _int_list(payload.get("source_object_ids")),
        "source_mode": payload.get("source_mode"),
        "evidence_text": _prompt_evidence_text(payload),
        "expected_json_schema_name": prompt_package.get("expected_json_schema_name"),
        "output_contract_version": prompt_package.get("output_contract_version"),
    }


def _normalized_candidate(
    candidate: Mapping[str, Any],
    source_chunk_ids: list[int],
    source_object_ids: list[int],
) -> dict[str, Any]:
    normalized = {field: candidate[field] for field in REQUIRED_FIELDS}
    normalized["source_inspiration_note_id"] = str(candidate["source_inspiration_note_id"])
    normalized["source_chunk_ids"] = _unique_ints(source_chunk_ids)
    normalized["source_object_ids"] = _unique_ints(source_object_ids)
    normalized["requires_human_review"] = True
    normalized["review_status"] = "pending_review"
    normalized["created_by"] = "manual_chatgpt_json"
    normalized["output_contract_version"] = OUTPUT_CONTRACT_VERSION
    return normalized


def _prompt_evidence_text(payload: Mapping[str, Any]) -> str:
    parts = [
        payload.get("source_mode"),
        payload.get("primary_user_note", {}).get("note_text")
        if isinstance(payload.get("primary_user_note"), Mapping)
        else None,
        payload.get("primary_source_excerpt", {}).get("selected_text")
        if isinstance(payload.get("primary_source_excerpt"), Mapping)
        else None,
        payload.get("primary_source_excerpt", {}).get("chunk_text")
        if isinstance(payload.get("primary_source_excerpt"), Mapping)
        else None,
    ]
    inspiration_note = payload.get("inspiration_note")
    if isinstance(inspiration_note, Mapping):
        parts.extend([inspiration_note.get("note_text"), inspiration_note.get("selected_text")])
    evidence = payload.get("evidence")
    if isinstance(evidence, Mapping):
        for chunk in evidence.get("chunks") or []:
            if isinstance(chunk, Mapping):
                parts.append(chunk.get("chunk_text"))
    return " ".join(str(part) for part in parts if part is not None).casefold()


def _contains_unresolved_conflict(candidate: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(candidate.get(field) or "")
        for field in ("evidence_alignment", "source_balance_warnings")
    ).casefold()
    return "conflict" in text or "冲突" in text or "unresolved" in text


def _check_unsupported_claims(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
    errors: list[str],
) -> None:
    candidate_text = _flatten_text(candidate).casefold()
    evidence_text = str(context.get("evidence_text") or "").casefold()
    for phrase in UNSUPPORTED_ASSERTION_TERMS:
        if phrase.casefold() in candidate_text and phrase.casefold() not in evidence_text:
            errors.append("unsupported_mechanism_claim")
            return
    for phrase in UNSUPPORTED_EQUIVALENCE_TERMS:
        if phrase.casefold() in candidate_text and phrase.casefold() not in evidence_text:
            errors.append("unsupported_mechanism_claim")
            return


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value) if value is not None else ""


def _load_json_file(path: Path, label: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{label}_syntax_invalid:{exc.msg}"
    except OSError as exc:
        return None, f"{label}_unreadable:{exc}"


def _result(
    *,
    errors: list[str],
    warnings: list[str],
    normalized_candidate: dict[str, Any] | None,
    input_candidate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    unique_errors = list(dict.fromkeys(errors))
    return {
        "status": "FAIL" if unique_errors else "OK",
        "mode": MODE,
        "errors": unique_errors,
        "warnings": list(dict.fromkeys(warnings)),
        "input_candidate": dict(input_candidate) if input_candidate is not None else None,
        "normalized_candidate": normalized_candidate,
        "expected_json_schema_name": EXPECTED_JSON_SCHEMA_NAME,
        **_safety_flags(),
    }


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    results: list[int] = []
    for item in value:
        try:
            integer = int(item)
        except (TypeError, ValueError):
            continue
        results.append(integer)
    return results


def _unique_ints(values: list[int]) -> list[int]:
    results: list[int] = []
    for value in values:
        if value not in results:
            results.append(value)
    return results


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "vector_store_write_performed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a manually pasted ChatGPT mechanism draft JSON against a K-Q0 prompt package."
    )
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--prompt-package-json", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_mechanism_draft_json_paths(
        args.input_json,
        args.prompt_package_json,
    )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0 if report["status"] == "OK" else 1


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
