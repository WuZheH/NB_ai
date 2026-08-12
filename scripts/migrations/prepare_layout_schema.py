from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH
from app.services.pdf_layout_service import (
    LAYOUT_SCHEMA_TABLES,
    create_pdf_layout_schema,
    planned_layout_schema_sql,
)


@dataclass(frozen=True)
class LayoutSchemaPlan:
    db_path: str
    dry_run: bool
    existing_tables: list[str]
    missing_tables: list[str]
    planned_sql: list[str]
    backup_path: str | None = None
    applied: bool = False


def inspect_layout_schema(db_path: str | Path, *, dry_run: bool = True) -> LayoutSchemaPlan:
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(str(db))
    existing = _existing_layout_tables(db, readonly=dry_run)
    missing = sorted(set(LAYOUT_SCHEMA_TABLES) - set(existing))
    return LayoutSchemaPlan(
        db_path=str(db),
        dry_run=dry_run,
        existing_tables=sorted(existing),
        missing_tables=missing,
        planned_sql=planned_layout_schema_sql(set(missing)),
    )


def apply_layout_schema(db_path: str | Path) -> LayoutSchemaPlan:
    db = Path(db_path)
    before = inspect_layout_schema(db, dry_run=True)
    backup_path = _backup_sqlite(db)
    with sqlite3.connect(db) as connection:
        create_pdf_layout_schema(connection)
        connection.commit()
    after = inspect_layout_schema(db, dry_run=True)
    return LayoutSchemaPlan(
        db_path=str(db),
        dry_run=False,
        existing_tables=after.existing_tables,
        missing_tables=after.missing_tables,
        planned_sql=before.planned_sql,
        backup_path=str(backup_path),
        applied=True,
    )


def _existing_layout_tables(db_path: Path, *, readonly: bool) -> set[str]:
    uri = f"file:{db_path.as_posix()}?mode=ro" if readonly else str(db_path)
    placeholders = ", ".join(["?"] * len(LAYOUT_SCHEMA_TABLES))
    with sqlite3.connect(uri, uri=readonly) as connection:
        rows = connection.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
            LAYOUT_SCHEMA_TABLES,
        ).fetchall()
    return {str(row[0]) for row in rows}


def _backup_sqlite(db_path: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"research_memory_before_layout_schema_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare layout bbox schema for book PDF preview repair.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Check schema only. This is the default.")
    mode.add_argument("--apply", action="store_true", help="Create missing layout tables after backing up SQLite.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = apply_layout_schema(args.db_path) if args.apply else inspect_layout_schema(args.db_path, dry_run=True)
    payload = asdict(plan)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_plan(payload)
    return 0


def _print_plan(payload: dict[str, Any]) -> None:
    print(f"db_path={payload['db_path']}")
    print(f"dry_run={payload['dry_run']}")
    print("existing_tables=" + ", ".join(payload["existing_tables"]))
    print("missing_tables=" + ", ".join(payload["missing_tables"]))
    print("planned_sql:")
    for statement in payload["planned_sql"]:
        print(f"  - {statement}")
    if payload.get("backup_path"):
        print(f"backup_path={payload['backup_path']}")
    print(f"applied={payload['applied']}")


if __name__ == "__main__":
    raise SystemExit(main())
