from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase110k_q0_mechanism_draft_write_plan import (
    build_mechanism_draft_write_plan_from_validation,
)
from scripts.phase110k_q0_validate_mechanism_draft_json import (
    validate_mechanism_draft_json_paths,
)


MODE = "phase110k_q2_export_chatgpt_prompt_from_package_v1"
PASTEBACK_MODE = "phase110k_q2_manual_chatgpt_pasteback_smoke_v1"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "mechanism_draft_candidate.schema.json"


def export_chatgpt_prompt_from_package(
    prompt_package_json_path: str | Path,
    output_txt_path: str | Path,
) -> dict[str, Any]:
    package_json, error = _load_json_file(Path(prompt_package_json_path), "prompt_package_json")
    if error:
        return _export_result(
            status="FAIL",
            output_txt_path=output_txt_path,
            source_prompt_package=prompt_package_json_path,
            errors=[error],
        )
    prompt_package = _select_ready_prompt_package(package_json)
    if prompt_package is None:
        return _export_result(
            status="FAIL",
            output_txt_path=output_txt_path,
            source_prompt_package=prompt_package_json_path,
            errors=["ready_prompt_package_not_found"],
        )
    text = render_chatgpt_prompt_text(prompt_package)
    output_path = Path(output_txt_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return _export_result(
        status="OK",
        output_txt_path=output_path,
        source_prompt_package=prompt_package_json_path,
        errors=[],
    )


def render_chatgpt_prompt_text(prompt_package: Mapping[str, Any]) -> str:
    system_instructions = list(prompt_package.get("system_instructions") or [])
    user_payload = _mapping_value(prompt_package.get("user_payload"))
    schema_name = str(prompt_package.get("expected_json_schema_name") or "")
    output_contract_version = str(prompt_package.get("output_contract_version") or "")
    schema = _load_expected_schema(schema_name)
    return "\n".join(
        [
            "MANUAL CHATGPT PROMPT FOR NOTEBOOK_AI MECHANISM DRAFT",
            "",
            "You must return JSON only. Do not include Markdown, prose, explanations, or code fences.",
            "Set requires_human_review=true.",
            "Do not output accepted, activated, validated, confirmed, applied, or any final mechanism-card state.",
            "Do not invent source IDs. source_inspiration_note_id, source_chunk_ids, and source_object_ids must be copied from the USER_PAYLOAD only.",
            "Use only evidence and approved objects supplied in USER_PAYLOAD.",
            "",
            "SYSTEM_INSTRUCTIONS:",
            json.dumps(system_instructions, ensure_ascii=False, indent=2),
            "",
            "USER_PAYLOAD:",
            json.dumps(user_payload, ensure_ascii=False, indent=2),
            "",
            "EXPECTED_JSON_SCHEMA_NAME:",
            schema_name,
            "",
            "OUTPUT_CONTRACT_VERSION:",
            output_contract_version,
            "",
            "EXPECTED_JSON_SCHEMA:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "Return exactly one JSON object matching EXPECTED_JSON_SCHEMA. The JSON must remain a pending human-review draft and must not create or claim a formal mechanism.",
        ]
    ) + "\n"


def build_manual_chatgpt_pasteback_smoke(
    *,
    prompt_package_json_path: str | Path,
    output_txt_path: str | Path,
    candidate_json_path: str | Path,
    validator_result_path: str | Path | None = None,
    write_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    export_report = export_chatgpt_prompt_from_package(
        prompt_package_json_path,
        output_txt_path,
    )
    if export_report["status"] != "OK":
        return {
            "status": "FAIL",
            "mode": PASTEBACK_MODE,
            "export_report": export_report,
            "blocker": None,
            "validator_result": None,
            "write_plan": None,
            **_safety_flags(),
        }

    candidate_path = Path(candidate_json_path)
    if not candidate_path.exists():
        return {
            "status": "BLOCKED",
            "mode": PASTEBACK_MODE,
            "blocker": "waiting_for_manual_chatgpt_json",
            "instructions": [
                f"Open {Path(output_txt_path)}.",
                "Copy the prompt text into ChatGPT manually.",
                f"Save ChatGPT JSON only to {candidate_path}.",
                "Run K-Q2 again.",
            ],
            "export_report": export_report,
            "validator_result": None,
            "write_plan": None,
            **_safety_flags(),
        }

    validator_result = validate_mechanism_draft_json_paths(
        candidate_path,
        prompt_package_json_path,
    )
    if validator_result_path is not None:
        _write_json(Path(validator_result_path), validator_result)
    if validator_result.get("status") != "OK":
        return {
            "status": "FAIL",
            "mode": PASTEBACK_MODE,
            "blocker": "validator_failed",
            "export_report": export_report,
            "validator_result": validator_result,
            "write_plan": None,
            **_safety_flags(),
        }

    write_plan = build_mechanism_draft_write_plan_from_validation(
        validator_result,
        prompt_package_json_path=prompt_package_json_path,
    )
    if write_plan_path is not None:
        _write_json(Path(write_plan_path), write_plan)
    return {
        "status": "OK" if write_plan.get("status") == "OK" else "FAIL",
        "mode": PASTEBACK_MODE,
        "blocker": None,
        "export_report": export_report,
        "validator_result": validator_result,
        "write_plan": write_plan,
        **_safety_flags(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a human-copyable ChatGPT prompt from a K-Q0 mechanism prompt package."
    )
    parser.add_argument("--prompt-package-json", type=Path, required=True)
    parser.add_argument("--output-txt", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = export_chatgpt_prompt_from_package(
        args.prompt_package_json,
        args.output_txt,
    )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0 if report["status"] == "OK" else 1


def _select_ready_prompt_package(package_json: Any) -> dict[str, Any] | None:
    if not isinstance(package_json, Mapping):
        return None
    items = package_json.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            readiness = _mapping_value(item.get("readiness"))
            prompt_package = item.get("prompt_package")
            if readiness.get("ready_for_mechanism_prompt") is True and isinstance(
                prompt_package,
                Mapping,
            ):
                return dict(prompt_package)
        return None
    prompt_package = package_json.get("prompt_package")
    if isinstance(prompt_package, Mapping):
        return dict(prompt_package)
    if "user_payload" in package_json:
        return dict(package_json)
    return None


def _load_expected_schema(schema_name: str) -> dict[str, Any]:
    schema_path = PROJECT_ROOT / "schemas" / schema_name if schema_name else DEFAULT_SCHEMA_PATH
    if not schema_path.exists():
        schema_path = DEFAULT_SCHEMA_PATH
    schema, error = _load_json_file(schema_path, "expected_schema")
    if error or not isinstance(schema, Mapping):
        return {
            "schema_unavailable": True,
            "expected_schema_name": schema_name,
            "error": error,
        }
    return dict(schema)


def _export_result(
    *,
    status: str,
    output_txt_path: str | Path,
    source_prompt_package: str | Path,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": MODE,
        "output_txt_path": str(output_txt_path),
        "source_prompt_package": str(source_prompt_package),
        "errors": list(errors),
        **_safety_flags(),
    }


def _load_json_file(path: Path, label: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{label}_syntax_invalid:{exc.msg}"
    except OSError as exc:
        return None, f"{label}_unreadable:{exc}"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "api_called": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "mechanism_card_created": False,
        "vector_store_write_performed": False,
    }


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
