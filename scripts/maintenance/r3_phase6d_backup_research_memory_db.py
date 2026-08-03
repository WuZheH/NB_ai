from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("data/db/research_memory.db")
DEFAULT_BACKUP_DIR = Path("data/db/backups")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def db_integrity_check(path: Path) -> str:
    with sqlite3.connect(f"file:{path.resolve(strict=False).as_posix()}?mode=ro", uri=True) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def build_backup_plan(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    execute: bool = False,
) -> dict[str, Any]:
    source = Path(db_path)
    target_dir = Path(backup_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    backup_path = target_dir / f"{source.stem}.phase6d_backup_{stamp}{source.suffix}"
    source_hash = sha256_file(source)
    integrity = db_integrity_check(source)
    backup_hash = None
    backup_file_written = False

    if execute:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        backup_hash = sha256_file(backup_path)
        backup_file_written = True

    return {
        "status": "backed_up" if execute else "dry_run",
        "mode": "r3_phase6d_backup_research_memory_db",
        "execute": bool(execute),
        "source_db_path": str(source),
        "backup_dir": str(target_dir),
        "backup_path": str(backup_path),
        "source_sha256": source_hash,
        "backup_sha256": backup_hash,
        "db_integrity_check": integrity,
        "backup_file_written": backup_file_written,
        "db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase6D dry-run-first research_memory.db backup helper.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--execute", action="store_true", help="Copy the DB to the backup path. Omit for dry-run.")
    args = parser.parse_args()
    result = build_backup_plan(
        db_path=args.db_path,
        backup_dir=args.backup_dir,
        execute=args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
