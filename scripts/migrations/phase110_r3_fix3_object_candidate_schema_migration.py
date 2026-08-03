from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS: dict[str, str] = {
    "source_note_ids_json": "ALTER TABLE object_candidates ADD COLUMN source_note_ids_json TEXT NOT NULL DEFAULT '[]'",
    "source_origin": "ALTER TABLE object_candidates ADD COLUMN source_origin TEXT",
    "necessity_judgment": "ALTER TABLE object_candidates ADD COLUMN necessity_judgment TEXT",
    "importance_score": "ALTER TABLE object_candidates ADD COLUMN importance_score TEXT",
}


def inspect_object_candidate_schema(db_path: str | Path, readonly: bool = True) -> dict[str, Any]:
    path = Path(db_path)
    if readonly:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'object_candidates'"
        ).fetchone() is not None
        if not table_exists:
            return {
                "table_exists": False,
                "columns": [],
                "missing_columns": sorted(REQUIRED_COLUMNS),
                "row_count": None,
                "integrity_check": None,
            }
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(object_candidates)").fetchall()]
        row_count = conn.execute("SELECT COUNT(*) FROM object_candidates").fetchone()[0]
        integrity_check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        missing = [name for name in REQUIRED_COLUMNS if name not in columns]
        return {
            "table_exists": True,
            "columns": columns,
            "missing_columns": missing,
            "row_count": row_count,
            "integrity_check": integrity_check,
        }
    finally:
        conn.close()


def build_migration_plan(db_path: str | Path) -> dict[str, Any]:
    before = inspect_object_candidate_schema(db_path, readonly=True)
    planned_sql = [REQUIRED_COLUMNS[name] for name in before["missing_columns"]]
    return {
        "status": "ready" if before["table_exists"] else "blocked",
        "dry_run": True,
        "apply": False,
        "db_path": str(db_path),
        "before": before,
        "planned_sql": planned_sql,
        "planned_add_columns": list(before["missing_columns"]),
        "object_candidate_rows_would_change": False,
        "db_write_performed": False,
        "object_candidate_row_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "mechanism_generated": False,
    }


def apply_migration(db_path: str | Path) -> dict[str, Any]:
    before = inspect_object_candidate_schema(db_path, readonly=True)
    if not before["table_exists"]:
        return {
            **build_migration_plan(db_path),
            "status": "blocked",
            "message": "object_candidates table missing; refusing to create table in this narrow migration.",
        }

    planned_sql = [REQUIRED_COLUMNS[name] for name in before["missing_columns"]]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        for statement in planned_sql:
            conn.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    after = inspect_object_candidate_schema(db_path, readonly=True)
    return {
        "status": "applied" if not after["missing_columns"] else "partial",
        "dry_run": False,
        "apply": True,
        "db_path": str(db_path),
        "before": before,
        "after": after,
        "planned_sql": planned_sql,
        "applied_sql": planned_sql,
        "added_columns": [name for name in REQUIRED_COLUMNS if name in before["missing_columns"]],
        "row_count_before": before["row_count"],
        "row_count_after": after["row_count"],
        "row_count_unchanged": before["row_count"] == after["row_count"],
        "integrity_check": after["integrity_check"],
        "db_write_performed": bool(planned_sql),
        "schema_write_performed": bool(planned_sql),
        "object_candidate_row_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "mechanism_generated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="R3-Fix3 object_candidates ADD COLUMN migration.")
    parser.add_argument("--db-path", default="data/db/research_memory.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")

    result = apply_migration(args.db_path) if args.apply else build_migration_plan(args.db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
