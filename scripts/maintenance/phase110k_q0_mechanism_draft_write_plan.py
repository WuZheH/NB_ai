from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase110k_q0_validate_mechanism_draft_json import (
    validate_mechanism_draft_json_paths,
)


MODE = "phase110k_q0_mechanism_draft_write_plan_v1"
WRITE_SOURCE = "manual_chatgpt_json"
WRITE_REVIEW_STATUS = "pending"
DEFAULT_DB_PATH = Path("data/db/research_memory.db")


def build_mechanism_draft_write_plan(
    input_json_path: str | Path,
    prompt_package_json_path: str | Path,
) -> dict[str, Any]:
    validation = validate_mechanism_draft_json_paths(
        input_json_path,
        prompt_package_json_path,
    )
    return build_mechanism_draft_write_plan_from_validation(
        validation,
        prompt_package_json_path=prompt_package_json_path,
    )


def build_mechanism_draft_write_plan_from_validation(
    validation_report: Mapping[str, Any],
    *,
    prompt_package_json_path: str | Path,
) -> dict[str, Any]:
    raw_candidate = validation_report.get("input_candidate")
    candidate = validation_report.get("normalized_candidate")
    if validation_report.get("status") != "OK" or not isinstance(candidate, Mapping):
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": list(validation_report.get("errors") or []),
            "validation_report": dict(validation_report),
            "source_note_id": None,
            "source": WRITE_SOURCE,
            "candidate_json": None,
            "draft_json": None,
            "future_insert_fields": None,
            "review_status": None,
            "created_by": WRITE_SOURCE,
            **_safety_flags(),
        }
    prompt_metadata = _prompt_export_metadata(
        prompt_package_json_path,
        validation_report,
    )
    draft_json = dict(raw_candidate) if isinstance(raw_candidate, Mapping) else dict(candidate)
    future_insert_fields = {
        "source": WRITE_SOURCE,
        "source_note_id": candidate["source_inspiration_note_id"],
        "source_inspiration_note_ids_json": json.dumps(
            [candidate["source_inspiration_note_id"]],
            ensure_ascii=False,
        ),
        "bound_inspiration_note_ids_json": json.dumps(
            [candidate["source_inspiration_note_id"]],
            ensure_ascii=False,
        ),
        "evidence_chunk_ids_json": json.dumps(
            list(candidate.get("source_chunk_ids") or []),
            ensure_ascii=False,
        ),
        "candidate_json": dict(candidate),
        "draft_json": draft_json,
        "validation_report_json": dict(validation_report),
        "prompt_export_metadata_json": prompt_metadata,
        "paste_back_readiness_context_json": dict(validation_report),
        "review_status": WRITE_REVIEW_STATUS,
        "created_by": WRITE_SOURCE,
    }
    return {
        "status": "OK",
        "mode": MODE,
        "source_note_id": candidate["source_inspiration_note_id"],
        "source": WRITE_SOURCE,
        "candidate_json": dict(candidate),
        "draft_json": draft_json,
        "review_status": WRITE_REVIEW_STATUS,
        "created_by": WRITE_SOURCE,
        "prompt_export_metadata_json": prompt_metadata,
        "paste_back_readiness_context_json": dict(validation_report),
        "validation_report": dict(validation_report),
        "future_insert_fields": future_insert_fields,
        **_safety_flags(),
    }


def build_mechanism_draft_write_plan_from_validated_json_path(
    validated_json_path: str | Path,
    prompt_package_json_path: str | Path,
) -> dict[str, Any]:
    validation_report, error = _load_json_file(Path(validated_json_path), "validated_json")
    if error:
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": [error],
            "validation_report": None,
            "source_note_id": None,
            "source": WRITE_SOURCE,
            "candidate_json": None,
            "draft_json": None,
            "review_status": None,
            "created_by": WRITE_SOURCE,
            "future_insert_fields": None,
            **_safety_flags(),
        }
    if not isinstance(validation_report, Mapping):
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": ["validated_json_must_be_object"],
            "validation_report": validation_report,
            "source_note_id": None,
            "source": WRITE_SOURCE,
            "candidate_json": None,
            "draft_json": None,
            "review_status": None,
            "created_by": WRITE_SOURCE,
            "future_insert_fields": None,
            **_safety_flags(),
        }
    return build_mechanism_draft_write_plan_from_validation(
        validation_report,
        prompt_package_json_path=prompt_package_json_path,
    )


def build_mechanism_draft_apply_report(
    write_plan_json_path: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    expected_source_note_id: str | None = None,
    apply_production_draft: bool = False,
) -> dict[str, Any]:
    write_plan, error = _load_json_file(Path(write_plan_json_path), "write_plan_json")
    if error or not isinstance(write_plan, Mapping):
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": [error or "write_plan_json_must_be_object"],
            "write_plan_json_path": str(write_plan_json_path),
            "write_plan": write_plan if isinstance(write_plan, Mapping) else None,
            "apply_requested": apply_production_draft,
            "draft_id": None,
            **_safety_flags(),
        }
    write_plan_dict = dict(write_plan)
    if not apply_production_draft:
        return {
            "status": "OK",
            "mode": MODE,
            "write_plan_json_path": str(write_plan_json_path),
            "write_plan": write_plan_dict,
            "apply_requested": False,
            "draft_id": None,
            **_safety_flags(),
        }
    if _is_production_db_path(db_path):
        return {
            "status": "BLOCKED",
            "mode": MODE,
            "blocker": "legacy_production_apply_disabled_use_controlled_candidate_service",
            "errors": [],
            "db_path": str(db_path),
            "write_plan_json_path": str(write_plan_json_path),
            "write_plan": write_plan_dict,
            "apply_requested": True,
            "draft_id": None,
            **_safety_flags(),
        }

    precondition_errors = _validate_apply_write_plan(
        write_plan_dict,
        expected_source_note_id=expected_source_note_id,
    )
    if precondition_errors:
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": precondition_errors,
            "write_plan_json_path": str(write_plan_json_path),
            "write_plan": write_plan_dict,
            "apply_requested": True,
            "draft_id": None,
            **_safety_flags(),
        }

    source_note_id = str(write_plan_dict["source_note_id"])
    candidate = _mapping_value(write_plan_dict.get("candidate_json"))
    draft_json = _mapping_value(write_plan_dict.get("draft_json")) or candidate
    validation_report = _validation_report_from_plan(write_plan_dict)
    mechanism_key = _mechanism_key(draft_json or candidate)
    draft_id = _draft_id(source_note_id, mechanism_key, candidate)
    source_note_ids_json = _json_dumps([source_note_id])
    evidence_chunk_ids = _int_list(candidate.get("source_chunk_ids"))

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": [f"db_open_failed:{exc}"],
            "db_path": str(db_path),
            "write_plan_json_path": str(write_plan_json_path),
            "draft_id": None,
            **_safety_flags(),
        }

    try:
        with conn:
            source_note = _fetch_source_note(conn, source_note_id)
            if source_note is None:
                return _apply_fail(
                    "source_note_not_found",
                    db_path=db_path,
                    write_plan_json_path=write_plan_json_path,
                    draft_id=draft_id,
                    write_plan=write_plan_dict,
                )
            if str(source_note["mechanism_status"] or "") != "not_generated":
                return _apply_fail(
                    "source_note_mechanism_status_must_be_not_generated",
                    db_path=db_path,
                    write_plan_json_path=write_plan_json_path,
                    draft_id=draft_id,
                    write_plan=write_plan_dict,
                    source_note_state=_row_dict(source_note),
                )

            duplicate = _find_duplicate_candidate(
                conn,
                source_note_id=source_note_id,
                mechanism_key=mechanism_key,
            )
            if duplicate is not None:
                return {
                    "status": "BLOCKED",
                    "mode": MODE,
                    "blocker": "duplicate_source_note_mechanism_key",
                    "errors": [],
                    "db_path": str(db_path),
                    "write_plan_json_path": str(write_plan_json_path),
                    "draft_id": duplicate.get("draft_id"),
                    "existing_candidate": duplicate,
                    "apply_requested": True,
                    **_safety_flags(),
                }

            now = _utc_now()
            insert_fields = {
                "draft_id": draft_id,
                "source": WRITE_SOURCE,
                "source_inspiration_note_ids_json": source_note_ids_json,
                "bound_inspiration_note_ids_json": source_note_ids_json,
                "evidence_chunk_ids_json": _json_dumps(evidence_chunk_ids),
                "matched_document_id": source_note["matched_document_id"],
                "pdf_pages_json": _json_dumps(_pdf_pages(source_note)),
                "mechanism_key": mechanism_key,
                "mechanism_name_cn": candidate.get("mechanism_name"),
                "mechanism_name_en": candidate.get("mechanism_name_en"),
                "mechanism_type": candidate.get("mechanism_type"),
                "confidence": candidate.get("confidence"),
                "draft_json": _json_dumps(draft_json),
                "validation_report_json": _json_dumps(validation_report),
                "prompt_export_metadata_json": _json_dumps(
                    _mapping_value(write_plan_dict.get("prompt_export_metadata_json"))
                ),
                "paste_back_readiness_context_json": _json_dumps(
                    _mapping_value(write_plan_dict.get("paste_back_readiness_context_json"))
                ),
                "review_status": WRITE_REVIEW_STATUS,
                "created_at": now,
                "updated_at": now,
            }
            columns = list(insert_fields)
            placeholders = ", ".join("?" for _ in columns)
            cursor = conn.execute(
                f"""
                INSERT INTO mechanism_draft_candidates ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                [insert_fields[column] for column in columns],
            )
            inserted_rows = cursor.rowcount
            if inserted_rows != 1:
                raise sqlite3.DatabaseError(
                    f"mechanism_draft_inserted_rows_must_equal_1:{inserted_rows}"
                )
            row_id = cursor.lastrowid
    except sqlite3.Error as exc:
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": [f"db_apply_failed:{exc}"],
            "db_path": str(db_path),
            "write_plan_json_path": str(write_plan_json_path),
            "draft_id": draft_id,
            "apply_requested": True,
            **_safety_flags(),
        }
    finally:
        conn.close()

    return {
        "status": "OK",
        "mode": MODE,
        "db_path": str(db_path),
        "write_plan_json_path": str(write_plan_json_path),
        "apply_requested": True,
        "inserted_rows": inserted_rows,
        "draft_id": draft_id,
        "row_id": row_id,
        "mechanism_key": mechanism_key,
        "source_note_id": source_note_id,
        "source": WRITE_SOURCE,
        "review_status": WRITE_REVIEW_STATUS,
        "mechanism_generated": False,
        "api_called": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "db_write_performed": True,
        "mechanism_draft_written": True,
        "mechanism_card_created": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a read-only future write plan for a validated K-Q0 mechanism draft candidate."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input-json", type=Path)
    input_group.add_argument("--validated-json", type=Path)
    input_group.add_argument("--write-plan-json", type=Path)
    parser.add_argument("--prompt-package-json", type=Path)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--expected-source-note-id")
    parser.add_argument("--apply-production-draft", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_plan_json is not None:
        report = build_mechanism_draft_apply_report(
            args.write_plan_json,
            args.db_path,
            expected_source_note_id=args.expected_source_note_id,
            apply_production_draft=args.apply_production_draft,
        )
    elif args.validated_json is not None:
        if args.prompt_package_json is None:
            report = _missing_prompt_package_report()
        else:
            report = build_mechanism_draft_write_plan_from_validated_json_path(
                args.validated_json,
                args.prompt_package_json,
            )
            if args.apply_production_draft:
                report = _apply_from_generated_plan(
                    report,
                    args.db_path,
                    args.expected_source_note_id,
                )
    else:
        if args.prompt_package_json is None:
            report = _missing_prompt_package_report()
        else:
            report = build_mechanism_draft_write_plan(
                args.input_json,
                args.prompt_package_json,
            )
            if args.apply_production_draft:
                report = _apply_from_generated_plan(
                    report,
                    args.db_path,
                    args.expected_source_note_id,
                )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0 if report["status"] == "OK" else 1


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "api_called": False,
        "llm_called": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "mechanism_card_created": False,
        "vector_store_write_performed": False,
    }


def _prompt_export_metadata(
    prompt_package_json_path: str | Path,
    validation_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "prompt_package_json_path": str(prompt_package_json_path),
        "expected_json_schema_name": validation_report.get("expected_json_schema_name"),
        "manual_chatgpt_flow": True,
        "llm_called_by_notebook_ai": False,
        "output_contract_version": (
            _mapping_value(validation_report.get("normalized_candidate")).get(
                "output_contract_version"
            )
        ),
    }


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _is_production_db_path(db_path: str | Path) -> bool:
    return Path(db_path).resolve() == DEFAULT_DB_PATH.resolve()


def _validate_apply_write_plan(
    write_plan: Mapping[str, Any],
    *,
    expected_source_note_id: str | None,
) -> list[str]:
    errors: list[str] = []
    validation_report = _validation_report_from_plan(write_plan)
    if write_plan.get("status") != "OK":
        errors.append("write_plan_status_must_be_OK")
    if validation_report.get("status") != "OK":
        errors.append("validator_status_must_be_OK")
    if validation_report.get("errors"):
        errors.append("validator_errors_must_be_empty")
    if write_plan.get("source") != WRITE_SOURCE:
        errors.append("source_must_be_manual_chatgpt_json")
    if write_plan.get("review_status") != WRITE_REVIEW_STATUS:
        errors.append("review_status_must_be_pending")
    if not expected_source_note_id:
        errors.append("expected_source_note_id_required")

    source_note_id = write_plan.get("source_note_id")
    if not source_note_id:
        errors.append("source_note_id_required")
    elif expected_source_note_id and str(source_note_id) != str(expected_source_note_id):
        errors.append("expected_source_note_id_mismatch")

    candidate = _mapping_value(write_plan.get("candidate_json"))
    if not candidate:
        errors.append("candidate_json_required")
    elif source_note_id and candidate.get("source_inspiration_note_id") != source_note_id:
        errors.append("candidate_source_note_id_mismatch")

    return list(dict.fromkeys(errors))


def _validation_report_from_plan(write_plan: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("validation_report", "paste_back_readiness_context_json"):
        value = write_plan.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    future_insert_fields = _mapping_value(write_plan.get("future_insert_fields"))
    value = future_insert_fields.get("validation_report_json")
    return dict(value) if isinstance(value, Mapping) else {}


def _apply_fail(
    error: str,
    *,
    db_path: str | Path,
    write_plan_json_path: str | Path,
    draft_id: str | None,
    write_plan: Mapping[str, Any],
    source_note_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "FAIL",
        "mode": MODE,
        "errors": [error],
        "db_path": str(db_path),
        "write_plan_json_path": str(write_plan_json_path),
        "draft_id": draft_id,
        "write_plan": dict(write_plan),
        "apply_requested": True,
        **_safety_flags(),
    }
    if source_note_state is not None:
        report["source_note_state"] = dict(source_note_state)
    return report


def _apply_from_generated_plan(
    write_plan: Mapping[str, Any],
    db_path: str | Path,
    expected_source_note_id: str | None,
) -> dict[str, Any]:
    if write_plan.get("status") != "OK":
        return {
            "status": "FAIL",
            "mode": MODE,
            "errors": ["write_plan_status_must_be_OK"],
            "write_plan": dict(write_plan),
            "draft_id": None,
            **_safety_flags(),
        }
    temp_dir = Path("tmp")
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / "_phase110k_q3_generated_write_plan.tmp.json"
    temp_path.write_text(_json_dumps(write_plan), encoding="utf-8")
    try:
        return build_mechanism_draft_apply_report(
            temp_path,
            db_path,
            expected_source_note_id=expected_source_note_id,
            apply_production_draft=True,
        )
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _fetch_source_note(conn: sqlite3.Connection, source_note_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            server_note_id,
            client_note_id,
            mechanism_status,
            evidence_alignment_status,
            matched_document_id,
            matched_chunk_ids_json,
            pdf_page
        FROM zotero_inspiration_notes
        WHERE server_note_id = ?
        """,
        (source_note_id,),
    ).fetchone()


def _find_duplicate_candidate(
    conn: sqlite3.Connection,
    *,
    source_note_id: str,
    mechanism_key: str,
) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT id, draft_id, source, source_inspiration_note_ids_json, mechanism_key, review_status
        FROM mechanism_draft_candidates
        WHERE source = ? AND mechanism_key = ?
        """,
        (WRITE_SOURCE, mechanism_key),
    ).fetchall()
    for row in rows:
        source_ids = _json_list(row["source_inspiration_note_ids_json"])
        if source_note_id in {str(value) for value in source_ids}:
            return _row_dict(row)
    return None


def _mechanism_key(candidate: Mapping[str, Any]) -> str:
    raw_name = str(candidate.get("mechanism_name") or candidate.get("mechanism_name_en") or "mechanism")
    slug = re.sub(r"[^a-z0-9]+", "-", raw_name.casefold()).strip("-")
    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:16]
    return f"{slug}-{digest}" if slug else f"mechanism-{digest}"


def _draft_id(
    source_note_id: str,
    mechanism_key: str,
    candidate: Mapping[str, Any],
) -> str:
    payload = {
        "source_note_id": source_note_id,
        "mechanism_key": mechanism_key,
        "source_chunk_ids": _int_list(candidate.get("source_chunk_ids")),
    }
    digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()[:24]
    return f"mdc_{digest}"


def _pdf_pages(source_note: Mapping[str, Any]) -> list[int]:
    value = _row_value(source_note, "pdf_page")
    if value is None:
        return []
    try:
        return [int(value)]
    except (TypeError, ValueError):
        return []


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    results: list[int] = []
    for item in value:
        try:
            integer = int(item)
        except (TypeError, ValueError):
            continue
        if integer not in results:
            results.append(integer)
    return results


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _row_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _row_value(row: sqlite3.Row | Mapping[str, Any], key: str) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row.get(key)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _missing_prompt_package_report() -> dict[str, Any]:
    return {
        "status": "FAIL",
        "mode": MODE,
        "errors": ["prompt_package_json_required"],
        "validation_report": None,
        "source_note_id": None,
        "source": WRITE_SOURCE,
        "candidate_json": None,
        "draft_json": None,
        "review_status": None,
        "created_by": WRITE_SOURCE,
        "future_insert_fields": None,
        **_safety_flags(),
    }


def _load_json_file(path: Path, label: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{label}_syntax_invalid:{exc.msg}"
    except OSError as exc:
        return None, f"{label}_unreadable:{exc}"


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
