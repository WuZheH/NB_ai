from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services.chapter_review_pipeline_service import (
    NOTE_CORRECTION_REVIEW_ITEM_TABLE,
    NOTE_CORRECTION_REVIEW_TABLE,
    note_correction_review_schema_sql,
)


def build_migration_plan(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    existing_tables = _existing_tables(path)
    existing_indexes = _existing_indexes(path)
    target_indexes = [
        "ux_note_correction_reviews_active_scope",
        "ix_note_correction_reviews_chapter",
        "ux_note_correction_review_items_review_note",
        "ix_note_correction_review_items_server_note",
    ]
    return {
        "status": "ok",
        "mode": "r3_note_correction_review_migration",
        "db_path": str(path),
        "apply": False,
        "existing_tables": sorted(existing_tables),
        "would_create_tables": [
            table
            for table in [NOTE_CORRECTION_REVIEW_TABLE, NOTE_CORRECTION_REVIEW_ITEM_TABLE]
            if table not in existing_tables
        ],
        "would_create_indexes": [index for index in target_indexes if index not in existing_indexes],
        "would_not_modify_zotero": True,
        "would_not_touch_vector": True,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "db_write_performed": False,
    }


def apply_migration(db_path: str | Path, *, backup: bool = False) -> dict[str, Any]:
    path = Path(db_path)
    before = build_migration_plan(path)
    backup_path = None
    if backup:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.r3_note_correction_review_backup_{stamp}{path.suffix}")
        shutil.copy2(path, backup_path)
    with sqlite3.connect(path) as conn:
        for statement in note_correction_review_schema_sql():
            conn.execute(statement)
        conn.commit()
    after = build_migration_plan(path)
    return {
        **after,
        "apply": True,
        "status": "applied",
        "backup_path": str(backup_path) if backup_path else None,
        "created_tables": before["would_create_tables"],
        "created_indexes": before["would_create_indexes"],
        "db_write_performed": True,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


def _existing_tables(db_path: Path) -> set[str]:
    with sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _existing_indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {str(row[0]) for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create NOTEBOOK_AI note correction review persistence tables.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apply", action="store_true", help="Apply schema changes. Omit for dry-run.")
    parser.add_argument("--backup", action="store_true", help="Create a DB file backup before --apply.")
    args = parser.parse_args()

    if args.apply:
        result = apply_migration(args.db_path, backup=args.backup)
    else:
        result = build_migration_plan(args.db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
