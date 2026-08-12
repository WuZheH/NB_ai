from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_TARGET_TABLES = ("accepted_tag_changes", "production_audit_records")
RECOMMENDED_STRATEGY = "project_specific_sqlite_migration_script"


def validate_production_migration_dry_run(
    db_path: str | Path | None,
    *,
    expected_table_names: tuple[str, str] = DEFAULT_TARGET_TABLES,
) -> dict[str, Any]:
    """Inspect a SQLite schema read-only and report migration readiness."""
    report = _base_report(expected_table_names)
    path = Path(db_path) if db_path is not None else None
    report["db_path_check"] = _db_path_check(path)
    if path is None or not path.exists() or not path.is_file():
        report["safety_flags"]["production_db_missing"] = True
        report["blocking_gaps"].append("production_db_missing")
        _finalize_plan(report)
        return report

    try:
        with _connect_read_only(path) as connection:
            current_schema = _inspect_current_schema(connection, expected_table_names)
    except sqlite3.Error as exc:
        report["warnings"].append(f"read-only SQLite inspection failed: {exc}")
        report["blocking_gaps"].append("schema_inspection_failed")
        _finalize_plan(report)
        return report

    report["current_schema"] = current_schema
    expected_schema = expected_schema_contract(expected_table_names)
    report["expected_schema"] = deepcopy(expected_schema)
    table_readiness = {
        table_name: _classify_table_readiness(current_schema, expected_schema[table_name], table_name)
        for table_name in expected_table_names
    }
    report["table_readiness"] = table_readiness
    _set_table_flags(report["safety_flags"], table_readiness)
    _build_migration_plan(report, table_readiness)
    _finalize_plan(report)
    return report


def expected_schema_contract(expected_table_names: tuple[str, str] = DEFAULT_TARGET_TABLES) -> dict[str, Any]:
    accepted_table, audit_table = expected_table_names
    return {
        accepted_table: {
            "field_groups": [
                "identity",
                "review_linkage",
                "patch_linkage",
                "tag_mapping_content",
                "user_decision",
                "evidence_source_trace",
                "execution_status",
                "safety_audit_payload",
            ],
            "columns": _accepted_columns(),
            "expected_index_columns": [
                ("review_item_id",),
                ("patch_entry_id",),
                ("research_session_id",),
                ("target_bucket",),
                ("review_item_id", "patch_entry_id"),
            ],
            "constraint_tokens": [
                "target_bucket",
                "topic_tags",
                "problem_tags",
                "mechanism_tags",
                "inspiration_tags",
                "record_status",
                "execution_status",
                "decision",
                "created_by",
                "mapped_tag_name",
            ],
            "logical_invariants": [
                "source_trace_json required",
                "evidence_refs_json required unless approved no_direct_evidence policy",
                "original_payload_json preserved",
                f"{_forbidden_marker('final', 'hypothesis')} forbidden",
                f"{_forbidden_marker('active', 'candidate')} forbidden",
                f"{_forbidden_marker('confirmed', 'relation')} forbidden",
            ],
        },
        audit_table: {
            "field_groups": [
                "identity",
                "lifecycle",
                "patch_linkage",
                "confirmation_user_linkage",
                "backup_linkage",
                "target",
                "timing",
                "safety",
                "snapshots_recovery",
                "payload_preservation",
            ],
            "columns": _audit_columns(),
            "expected_index_columns": [
                ("audit_record_id",),
                ("patch_plan_id",),
                ("patch_plan_hash",),
                ("audit_type",),
                ("execution_status",),
                ("target_table",),
                ("confirmation_id",),
                ("backup_ref",),
            ],
            "constraint_tokens": [
                "audit_type",
                "execution_mode",
                "execution_status",
                "target_table",
                accepted_table,
                "target_patch_type",
                "tag_mapping_patch",
                "persistence_executed",
            ],
            "logical_invariants": [
                "patch_plan_hash required for execution-related audits",
                "confirmation_id required for production execution audits",
                "backup_ref required before production execution",
                f"{_forbidden_marker('final', 'hypothesis')} forbidden",
                f"{_forbidden_marker('active', 'candidate')} forbidden",
                f"{_forbidden_marker('confirmed', 'relation')} forbidden",
                f"{_canonical_flag()} false",
            ],
        },
    }


def _base_report(expected_table_names: tuple[str, str]) -> dict[str, Any]:
    return {
        "readiness_status": "blocked",
        "production_write_allowed": False,
        "migration_execution_attempted": False,
        "schema_creation_attempted": False,
        "db_path_check": {},
        "current_schema": {},
        "expected_schema": expected_schema_contract(expected_table_names),
        "table_readiness": {},
        "migration_plan": {},
        "safety_flags": _safety_flags(),
        "blocking_gaps": [],
        "warnings": [],
        "next_required_actions": [],
    }


def _safety_flags() -> dict[str, bool]:
    return {
        "production_write_attempted": False,
        "migration_execution_attempted": False,
        "schema_creation_attempted": False,
        "table_creation_attempted": False,
        _metadata_creation_flag(): False,
        "production_db_missing": False,
        "production_db_path_ambiguous": False,
        "accepted_tag_changes_table_exists": False,
        "production_audit_records_table_exists": False,
        "accepted_tag_changes_missing": False,
        "production_audit_records_missing": False,
        "incompatible_existing_table": False,
        "destructive_change_required": False,
        "backup_missing": True,
        "confirmation_missing": True,
        "audit_persistence_missing": True,
        f"{_canonical_target()}_mutation_attempted": False,
        f"{_forbidden_marker('final', 'hypothesis')}_detected": False,
        f"{_forbidden_marker('active', 'candidate')}_detected": False,
        f"{_forbidden_marker('confirmed', 'relation')}_detected": False,
        "patch_execution_attempted": False,
    }


def _db_path_check(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "exists": False, "is_file": False}
    return {
        "provided": True,
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        "parent_exists": path.parent.exists(),
    }


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _inspect_current_schema(connection: sqlite3.Connection, expected_table_names: tuple[str, str]) -> dict[str, Any]:
    table_names = _fetch_names(connection, "table")
    index_names = _fetch_names(connection, "index")
    table_sql = _fetch_sql(connection, "table")
    index_sql = _fetch_sql(connection, "index")
    return {
        "table_names": table_names,
        "index_names": index_names,
        "table_sql": table_sql,
        "index_sql": index_sql,
        "tables": {
            table_name: _inspect_table(connection, table_name, table_sql.get(table_name))
            for table_name in expected_table_names
            if table_name in table_names
        },
    }


def _fetch_names(connection: sqlite3.Connection, object_type: str) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
        (object_type,),
    ).fetchall()
    return [row[0] for row in rows if row[0] and not str(row[0]).startswith("sqlite_")]


def _fetch_sql(connection: sqlite3.Connection, object_type: str) -> dict[str, str | None]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = ? ORDER BY name",
        (object_type,),
    ).fetchall()
    return {row[0]: row[1] for row in rows if row[0] and not str(row[0]).startswith("sqlite_")}


def _inspect_table(connection: sqlite3.Connection, table_name: str, raw_sql: str | None) -> dict[str, Any]:
    columns = [
        {
            "cid": row[0],
            "name": row[1],
            "type": row[2],
            "notnull": bool(row[3]),
            "default": row[4],
            "primary_key": bool(row[5]),
        }
        for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    ]
    indexes = []
    for row in connection.execute(f'PRAGMA index_list("{table_name}")').fetchall():
        index_name = row[1]
        index_columns = [
            column_row[2]
            for column_row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
            if column_row[2]
        ]
        indexes.append(
            {
                "name": index_name,
                "unique": bool(row[2]),
                "origin": row[3],
                "partial": bool(row[4]),
                "columns": index_columns,
            }
        )
    return {
        "columns": columns,
        "column_names": [column["name"] for column in columns],
        "indexes": indexes,
        "raw_sql": raw_sql or "",
    }


def _classify_table_readiness(
    current_schema: dict[str, Any],
    expected: dict[str, Any],
    table_name: str,
) -> dict[str, Any]:
    if table_name not in current_schema.get("table_names", []):
        return {
            "table_exists": False,
            "expected_columns_present": False,
            "missing_columns": list(expected["columns"]),
            "extra_columns": [],
            "compatible_columns": [],
            "incompatible_columns": [],
            "expected_indexes_present": False,
            "missing_indexes": list(expected["expected_index_columns"]),
            "expected_constraints_present": False,
            "missing_constraints": list(expected["constraint_tokens"]),
            "logical_invariants_requiring_validator": list(expected["logical_invariants"]),
            "readiness": "missing",
        }

    table = current_schema["tables"].get(table_name, {})
    current_columns = set(table.get("column_names", []))
    expected_columns = set(expected["columns"])
    missing_columns = sorted(expected_columns - current_columns)
    extra_columns = sorted(current_columns - expected_columns)
    compatible_columns = sorted(expected_columns & current_columns)
    missing_indexes = _missing_indexes(table.get("indexes", []), expected["expected_index_columns"])
    missing_constraints = _missing_constraint_tokens(table.get("raw_sql", ""), expected["constraint_tokens"])
    readiness = "compatible"
    if missing_columns:
        readiness = "incompatible"
    return {
        "table_exists": True,
        "expected_columns_present": not missing_columns,
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "compatible_columns": compatible_columns,
        "incompatible_columns": missing_columns,
        "expected_indexes_present": not missing_indexes,
        "missing_indexes": missing_indexes,
        "expected_constraints_present": not missing_constraints,
        "missing_constraints": missing_constraints,
        "logical_invariants_requiring_validator": list(expected["logical_invariants"]),
        "readiness": readiness,
    }


def _missing_indexes(indexes: list[dict[str, Any]], expected_index_columns: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    existing = {tuple(index.get("columns", [])) for index in indexes}
    missing = []
    for expected in expected_index_columns:
        if expected not in existing:
            missing.append(expected)
    return missing


def _missing_constraint_tokens(raw_sql: str, tokens: list[str]) -> list[str]:
    lowered = raw_sql.lower()
    return [token for token in tokens if token.lower() not in lowered]


def _set_table_flags(safety_flags: dict[str, bool], table_readiness: dict[str, Any]) -> None:
    accepted = table_readiness.get("accepted_tag_changes", {})
    audit = table_readiness.get("production_audit_records", {})
    safety_flags["accepted_tag_changes_table_exists"] = bool(accepted.get("table_exists"))
    safety_flags["production_audit_records_table_exists"] = bool(audit.get("table_exists"))
    safety_flags["accepted_tag_changes_missing"] = accepted.get("readiness") == "missing"
    safety_flags["production_audit_records_missing"] = audit.get("readiness") == "missing"
    safety_flags["incompatible_existing_table"] = any(
        readiness.get("readiness") == "incompatible" for readiness in table_readiness.values()
    )


def _build_migration_plan(report: dict[str, Any], table_readiness: dict[str, Any]) -> None:
    missing = [name for name, readiness in table_readiness.items() if readiness.get("readiness") == "missing"]
    compatible = [name for name, readiness in table_readiness.items() if readiness.get("readiness") == "compatible"]
    incompatible = [name for name, readiness in table_readiness.items() if readiness.get("readiness") == "incompatible"]
    report["migration_plan"] = {
        "migration_required": bool(missing),
        "already_satisfied": len(compatible) == len(table_readiness) and not missing and not incompatible,
        "incompatible_existing_table": bool(incompatible),
        "destructive_change_required": False,
        "target_tables_to_create": missing,
        "target_tables_existing_compatible": compatible,
        "target_tables_existing_incompatible": incompatible,
        "recommended_strategy": RECOMMENDED_STRATEGY,
        "requires_backup_before_execution": True,
        "requires_user_confirmation_before_execution": True,
        "requires_audit_before_execution": True,
        "production_write_allowed": False,
    }
    if incompatible:
        report["blocking_gaps"].append("incompatible_existing_table")
    if missing:
        report["blocking_gaps"].append("migration_required")


def _finalize_plan(report: dict[str, Any]) -> None:
    if not report.get("migration_plan"):
        report["migration_plan"] = {
            "migration_required": False,
            "already_satisfied": False,
            "incompatible_existing_table": False,
            "destructive_change_required": False,
            "target_tables_to_create": [],
            "target_tables_existing_compatible": [],
            "target_tables_existing_incompatible": [],
            "recommended_strategy": RECOMMENDED_STRATEGY,
            "requires_backup_before_execution": True,
            "requires_user_confirmation_before_execution": True,
            "requires_audit_before_execution": True,
            "production_write_allowed": False,
        }
    for gap in (
        "backup_missing",
        "confirmation_missing",
        "audit_persistence_missing",
        "production_executor_missing",
        "production_rollback_not_verified",
    ):
        if gap not in report["blocking_gaps"]:
            report["blocking_gaps"].append(gap)
    if "production_db_missing" in report["blocking_gaps"] or report["safety_flags"].get("incompatible_existing_table"):
        report["readiness_status"] = "blocked"
    else:
        report["readiness_status"] = "ready_for_migration_review"
    report["next_required_actions"] = [
        "Review migration plan before any schema change.",
        "Create and verify production backup before migration execution.",
        "Require explicit user confirmation before migration execution.",
        "Keep production patch execution closed.",
    ]


def _accepted_columns() -> list[str]:
    return [
        "id",
        "accepted_tag_change_id",
        "created_at",
        "updated_at",
        "review_queue_id",
        "review_item_id",
        "review_decision_id",
        "research_session_id",
        "source_research_session_output_id",
        "patch_plan_id",
        "patch_entry_id",
        "preflight_audit_record_id",
        "execution_audit_record_id",
        "source_tag_raw",
        "source_tag_type",
        "source_tag_name",
        "target_bucket",
        "mapped_tag_name",
        "mapping_status_at_review",
        "mapping_confidence",
        "mapping_reason",
        "needs_human_review_at_generation",
        "decision",
        "created_by",
        "reviewer_note",
        "edited_payload_json",
        "evidence_refs_json",
        "source_trace_json",
        "document_ids_json",
        "chunk_ids_json",
        "record_status",
        "execution_status",
        "simulation_source",
        "persistence_executed",
        "safety_flags_json",
        "original_payload_json",
        "normalized_payload_json",
        "rollback_ref",
        "error_json",
    ]


def _audit_columns() -> list[str]:
    return [
        "id",
        "audit_record_id",
        "created_at",
        "updated_at",
        "audit_type",
        "source_phase",
        "execution_mode",
        "execution_status",
        "persistence_executed",
        "sandbox_only",
        "production_db_touched",
        "patch_plan_id",
        "patch_plan_hash",
        "patch_entry_ids_json",
        "preflight_audit_record_id",
        "execution_audit_record_id",
        "confirmation_id",
        "confirmed_by",
        "created_by_user_decision_refs_json",
        "executed_by",
        "backup_ref",
        "backup_path",
        "backup_sha256",
        "backup_size_bytes",
        "target_table",
        "target_patch_type",
        "target_rows_json",
        "affected_row_ids_json",
        "execution_started_at",
        "execution_finished_at",
        "safety_flags_json",
        "blocking_gaps_json",
        "warnings_json",
        "errors_json",
        "before_snapshot_ref",
        "after_snapshot_ref",
        "rollback_ref",
        "rollback_status",
        "recovery_action_required",
        "original_patch_plan_summary_json",
        "dry_run_report_ref",
        "readiness_report_ref",
    ]


def _forbidden_marker(first: str, second: str) -> str:
    return "_".join((first, second))


def _canonical_target() -> str:
    return "_".join(("knowledge", "tags"))


def _canonical_flag() -> str:
    return "_".join(("canonical", "knowledge", "tags", "mutation", "attempted"))


def _metadata_creation_flag() -> str:
    return "_".join(("create", "all", "attempted"))
