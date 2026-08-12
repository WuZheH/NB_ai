from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH


ZOTERO_INSPIRATION_NOTES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS zotero_inspiration_notes (
    id INTEGER PRIMARY KEY,
    server_note_id TEXT NOT NULL UNIQUE,
    client_note_id TEXT NOT NULL,
    source TEXT NOT NULL,
    zotero_item_key TEXT,
    zotero_attachment_key TEXT,
    zotero_annotation_key TEXT,
    pdf_page INTEGER,
    page_label TEXT,
    selected_text TEXT NOT NULL,
    selected_text_hash TEXT NOT NULL,
    note_text TEXT NOT NULL,
    user_tags_json TEXT NOT NULL,
    selection_type TEXT NOT NULL,
    context_before TEXT,
    context_after TEXT,
    bbox_json TEXT,
    matched_document_id INTEGER,
    matched_chunk_id INTEGER,
    matched_object_ids_json TEXT NOT NULL DEFAULT '[]',
    sync_status TEXT NOT NULL,
    match_status TEXT NOT NULL DEFAULT 'unmatched',
    review_status TEXT NOT NULL DEFAULT 'imported',
    mechanism_status TEXT NOT NULL DEFAULT 'not_generated',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);
""".strip()

MECHANISM_DRAFT_CANDIDATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mechanism_draft_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'pasted_chatgpt_json',
    source_inspiration_note_ids_json TEXT NOT NULL,
    bound_inspiration_note_ids_json TEXT NOT NULL,
    evidence_chunk_ids_json TEXT NOT NULL,
    matched_document_id INTEGER NULL,
    pdf_pages_json TEXT,
    mechanism_key TEXT,
    mechanism_name_cn TEXT,
    mechanism_name_en TEXT,
    mechanism_type TEXT,
    confidence TEXT,
    draft_json TEXT NOT NULL,
    validation_report_json TEXT NOT NULL,
    prompt_export_metadata_json TEXT,
    paste_back_readiness_context_json TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'accepted', 'rejected', 'merged', 'needs_edit', 'deferred')),
    review_decision TEXT NULL,
    review_notes TEXT NULL,
    merged_into_draft_id TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT NULL
);
""".strip()

REQUIRED_TABLE_DDL: dict[str, str] = {
    "zotero_inspiration_notes": ZOTERO_INSPIRATION_NOTES_TABLE_SQL,
    "mechanism_draft_candidates": MECHANISM_DRAFT_CANDIDATES_TABLE_SQL,
}

REQUIRED_INDEX_DDL: dict[str, str] = {
    "ix_zotero_inspiration_notes_client_note_id": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_client_note_id "
        "ON zotero_inspiration_notes (client_note_id);"
    ),
    "ix_zotero_inspiration_notes_annotation_key": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_annotation_key "
        "ON zotero_inspiration_notes (zotero_annotation_key);"
    ),
    "ix_zotero_inspiration_notes_attachment_key": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_attachment_key "
        "ON zotero_inspiration_notes (zotero_attachment_key);"
    ),
    "ix_zotero_inspiration_notes_selected_text_hash": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_selected_text_hash "
        "ON zotero_inspiration_notes (selected_text_hash);"
    ),
    "ix_zotero_inspiration_notes_document_id": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_document_id "
        "ON zotero_inspiration_notes (matched_document_id);"
    ),
    "ix_zotero_inspiration_notes_chunk_id": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_chunk_id "
        "ON zotero_inspiration_notes (matched_chunk_id);"
    ),
    "ix_zotero_inspiration_notes_review_status": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_review_status "
        "ON zotero_inspiration_notes (review_status);"
    ),
    "ix_zotero_inspiration_notes_mechanism_status": (
        "CREATE INDEX IF NOT EXISTS ix_zotero_inspiration_notes_mechanism_status "
        "ON zotero_inspiration_notes (mechanism_status);"
    ),
    "idx_mechanism_draft_candidates_draft_id": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_draft_id "
        "ON mechanism_draft_candidates (draft_id);"
    ),
    "idx_mechanism_draft_candidates_review_status": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_review_status "
        "ON mechanism_draft_candidates (review_status);"
    ),
    "idx_mechanism_draft_candidates_mechanism_key": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_mechanism_key "
        "ON mechanism_draft_candidates (mechanism_key);"
    ),
    "idx_mechanism_draft_candidates_mechanism_type": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_mechanism_type "
        "ON mechanism_draft_candidates (mechanism_type);"
    ),
    "idx_mechanism_draft_candidates_source": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_source "
        "ON mechanism_draft_candidates (source);"
    ),
    "idx_mechanism_draft_candidates_created_at": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_created_at "
        "ON mechanism_draft_candidates (created_at);"
    ),
    "idx_mechanism_draft_candidates_matched_document_id": (
        "CREATE INDEX IF NOT EXISTS idx_mechanism_draft_candidates_matched_document_id "
        "ON mechanism_draft_candidates (matched_document_id);"
    ),
}

INDEX_TO_TABLE: dict[str, str] = {
    **{name: "zotero_inspiration_notes" for name in REQUIRED_INDEX_DDL if name.startswith("ix_zotero")},
    **{
        name: "mechanism_draft_candidates"
        for name in REQUIRED_INDEX_DDL
        if name.startswith("idx_mechanism")
    },
}


@dataclass(frozen=True)
class StorageEnablementPlan:
    db_path: str
    db_exists: bool
    db_size_bytes: int | None
    db_sha256: str | None
    dry_run: bool
    prepare_only: bool
    applied: bool
    existing_tables: list[str]
    missing_tables: list[str]
    existing_indexes: list[str]
    missing_indexes: list[str]
    planned_ddl: list[str]
    backup_path: str | None
    backup_created: bool
    backup_integrity_ok: bool | None
    apply_allowed: bool
    blockers: list[str]
    schema_ready: bool
    production_persistence_enabled: bool
    write_available: bool
    missing_required_objects: list[str]
    side_effects: dict[str, bool]
    next_apply_command: str
    rollback_plan: list[str]


def build_storage_enablement_plan(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    prepare_only: bool = False,
) -> StorageEnablementPlan:
    db = Path(db_path)
    exists = db.exists()
    existing_tables: list[str] = []
    existing_indexes: list[str] = []
    blockers: list[str] = []
    if not exists:
        blockers.append("database_file_missing")
    else:
        try:
            with _readonly_connection(db) as connection:
                existing_tables = _existing_sqlite_objects(connection, "table", REQUIRED_TABLE_DDL)
                existing_indexes = _existing_sqlite_objects(connection, "index", REQUIRED_INDEX_DDL)
        except sqlite3.Error as exc:
            blockers.append(f"readonly_schema_inspection_failed:{exc}")

    missing_tables = sorted(set(REQUIRED_TABLE_DDL) - set(existing_tables))
    missing_indexes = sorted(set(REQUIRED_INDEX_DDL) - set(existing_indexes))
    planned_ddl = _planned_ddl(missing_tables, missing_indexes)
    missing_objects = [*missing_tables, *missing_indexes]
    backup_path = str(_planned_backup_path(db)) if exists else None
    apply_allowed = exists and not blockers
    schema_ready = not missing_objects and exists and not blockers
    return StorageEnablementPlan(
        db_path=str(db),
        db_exists=exists,
        db_size_bytes=db.stat().st_size if exists else None,
        db_sha256=_sha256(db) if exists else None,
        dry_run=True,
        prepare_only=prepare_only,
        applied=False,
        existing_tables=sorted(existing_tables),
        missing_tables=missing_tables,
        existing_indexes=sorted(existing_indexes),
        missing_indexes=missing_indexes,
        planned_ddl=planned_ddl,
        backup_path=backup_path,
        backup_created=False,
        backup_integrity_ok=None,
        apply_allowed=apply_allowed,
        blockers=blockers,
        schema_ready=schema_ready,
        production_persistence_enabled=False,
        write_available=False,
        missing_required_objects=missing_objects,
        side_effects=_side_effects(),
        next_apply_command=_next_apply_command(db),
        rollback_plan=_rollback_plan(db),
    )


def apply_storage_enablement(db_path: str | Path = DEFAULT_DB_PATH) -> StorageEnablementPlan:
    before = build_storage_enablement_plan(db_path)
    if not before.apply_allowed:
        raise RuntimeError("storage_enablement_apply_blocked:" + ",".join(before.blockers))
    db = Path(db_path)
    backup_path = _backup_sqlite_online(db)
    backup_ok = _integrity_ok(backup_path)
    if not backup_ok:
        raise RuntimeError(f"backup_integrity_check_failed:{backup_path}")

    with sqlite3.connect(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in before.planned_ddl:
                connection.execute(statement)
            _verify_required_schema(connection)
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    after = build_storage_enablement_plan(db)
    blockers = list(after.blockers)
    if not after.schema_ready:
        blockers.append("schema_not_ready_after_apply")
    return StorageEnablementPlan(
        db_path=after.db_path,
        db_exists=after.db_exists,
        db_size_bytes=after.db_size_bytes,
        db_sha256=after.db_sha256,
        dry_run=False,
        prepare_only=False,
        applied=after.schema_ready,
        existing_tables=after.existing_tables,
        missing_tables=after.missing_tables,
        existing_indexes=after.existing_indexes,
        missing_indexes=after.missing_indexes,
        planned_ddl=before.planned_ddl,
        backup_path=str(backup_path),
        backup_created=True,
        backup_integrity_ok=backup_ok,
        apply_allowed=before.apply_allowed,
        blockers=blockers,
        schema_ready=after.schema_ready,
        production_persistence_enabled=False,
        write_available=False,
        missing_required_objects=after.missing_required_objects,
        side_effects=_side_effects(db_write_performed=True),
        next_apply_command=_next_apply_command(db),
        rollback_plan=_rollback_plan(db, backup_path=backup_path),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 110K-I safety gate for inspiration/mechanism SQLite storage."
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect and plan only. This is the default.")
    mode.add_argument("--apply", action="store_true", help="Back up SQLite and create missing schema.")
    mode.add_argument("--prepare-only", action="store_true", help="Explicit read-only preparation mode.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = (
        apply_storage_enablement(args.db_path)
        if args.apply
        else build_storage_enablement_plan(args.db_path, prepare_only=args.prepare_only)
    )
    payload = asdict(plan)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_plan(payload)
    return 0


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def _existing_sqlite_objects(
    connection: sqlite3.Connection,
    object_type: str,
    required: dict[str, str],
) -> list[str]:
    placeholders = ", ".join(["?"] * len(required))
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE type = ? AND name IN ({placeholders})",
        (object_type, *required),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _planned_ddl(missing_tables: list[str], missing_indexes: list[str]) -> list[str]:
    statements = [REQUIRED_TABLE_DDL[name] for name in sorted(missing_tables)]
    missing_index_set = set(missing_indexes)
    for index_name, table_name in REQUIRED_INDEX_DDL.items():
        if table_name in missing_tables or index_name in missing_index_set:
            statements.append(REQUIRED_INDEX_DDL[index_name])
    return statements


def _planned_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_utc")
    return db_path.parent / "backups" / f"{db_path.stem}_before_phase110k_i_{timestamp}{db_path.suffix}"


def _backup_sqlite_online(db_path: Path) -> Path:
    backup_path = _planned_backup_path(db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
    return backup_path


def _integrity_ok(db_path: Path) -> bool:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _verify_required_schema(connection: sqlite3.Connection) -> None:
    existing_tables = set(_existing_sqlite_objects(connection, "table", REQUIRED_TABLE_DDL))
    existing_indexes = set(_existing_sqlite_objects(connection, "index", REQUIRED_INDEX_DDL))
    missing_tables = set(REQUIRED_TABLE_DDL) - existing_tables
    missing_indexes = set(REQUIRED_INDEX_DDL) - existing_indexes
    if missing_tables or missing_indexes:
        missing = ",".join(sorted([*missing_tables, *missing_indexes]))
        raise RuntimeError(f"required_schema_missing_after_apply:{missing}")


def _sha256(db_path: Path) -> str:
    digest = hashlib.sha256()
    with db_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _side_effects(*, db_write_performed: bool = False) -> dict[str, bool]:
    return {
        "db_write_performed": db_write_performed,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
        "llm_called": False,
        "external_model_called": False,
        "mechanism_card_created": False,
        "ocr_or_marker_run": False,
        "pdf_import_started": False,
    }


def _next_apply_command(db_path: Path) -> str:
    python = Path(sys.executable)
    return (
        f"{python} scripts/phase110k_enable_inspiration_mechanism_storage.py "
        f"--db-path {db_path} --apply --json"
    )


def _rollback_plan(db_path: Path, *, backup_path: Path | None = None) -> list[str]:
    backup = str(backup_path) if backup_path is not None else "<backup_path_reported_by_apply>"
    return [
        "Stop the application before rollback.",
        f"Move the current database at {db_path} aside for forensic retention.",
        f"Restore the verified SQLite backup from {backup} to {db_path}.",
        "Run PRAGMA integrity_check on the restored database.",
        "Run this script again with --dry-run --json to confirm schema status.",
    ]


def _print_plan(payload: dict[str, Any]) -> None:
    print(f"db_path={payload['db_path']}")
    print(f"db_exists={payload['db_exists']}")
    print(f"dry_run={payload['dry_run']}")
    print(f"prepare_only={payload['prepare_only']}")
    print(f"applied={payload['applied']}")
    print(f"schema_ready={payload['schema_ready']}")
    print(f"apply_allowed={payload['apply_allowed']}")
    print("missing_tables=" + ", ".join(payload["missing_tables"]))
    print("missing_indexes=" + ", ".join(payload["missing_indexes"]))
    print(f"backup_path={payload['backup_path']}")
    print("blockers=" + ", ".join(payload["blockers"]))
    print("planned_ddl:")
    for statement in payload["planned_ddl"]:
        print(f"  - {statement}")
    print(f"next_apply_command={payload['next_apply_command']}")


if __name__ == "__main__":
    raise SystemExit(main())
