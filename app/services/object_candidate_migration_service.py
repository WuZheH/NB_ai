from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from app.core.paths import DATA_PROJECT_ROOT, DEFAULT_DB_PATH, OUTPUTS_DIR
from app.db.init_db import init_db
from app.db.session import engine
from app.models.object_candidate import ObjectCandidate

MIGRATION_BACKUP_ROOT = OUTPUTS_DIR / "phase18e_object_migration_backup"
DB_PATH = DEFAULT_DB_PATH


def run_object_candidate_migration() -> dict:
    """Idempotent migration: create object_candidates table if not exists."""
    inspector = inspect(engine)
    existing = inspector.get_table_names()
    if "object_candidates" in existing:
        return {
            "status": "ok",
            "already_exists": True,
            "table": "object_candidates",
            "backup_path": None,
            "db_write_performed": False,
            "message": "Table object_candidates already exists.",
        }

    # Backup before migration
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = MIGRATION_BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "research_memory_pre_object_candidate_migration.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_path)

    # Create table — use raw SQL for true idempotency
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS object_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NULL,
                    import_job_id TEXT NOT NULL,
                    object_key TEXT NOT NULL,
                    object_name TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    confidence TEXT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    description TEXT NULL,
                    topic_tags_json TEXT NOT NULL DEFAULT '[]',
                    problem_tags_json TEXT NOT NULL DEFAULT '[]',
                    mechanism_tags_json TEXT NOT NULL DEFAULT '[]',
                    inspiration_tags_json TEXT NOT NULL DEFAULT '[]',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    note_refs_json TEXT NOT NULL DEFAULT '[]',
                    source_note_ids_json TEXT NOT NULL DEFAULT '[]',
                    source_origin TEXT NULL,
                    necessity_judgment TEXT NULL,
                    importance_score TEXT NULL,
                    source_package_path TEXT NULL,
                    source_import_manifest_path TEXT NULL,
                    mapping_status TEXT NOT NULL DEFAULT 'not_mapped',
                    mapped_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    user_comment TEXT NULL,
                    created_by TEXT NOT NULL DEFAULT 'user_reviewed',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(import_job_id, object_key),
                    CHECK(review_status IN ('accepted', 'edited')),
                    CHECK(confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
                    CHECK(mapping_status IN ('not_mapped', 'mapped', 'partial', 'failed'))
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_object_candidates_review_status ON object_candidates (review_status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_object_candidates_import_job_id ON object_candidates (import_job_id)"
            ))
    except Exception as exc:
        return {
            "status": "error",
            "already_exists": False,
            "table": "object_candidates",
            "backup_path": str(backup_dir.relative_to(DATA_PROJECT_ROOT)) if backup_dir else None,
            "db_write_performed": False,
            "message": f"Migration failed: {exc}",
        }

    # Verify
    inspector2 = inspect(engine)
    created = "object_candidates" in inspector2.get_table_names()

    return {
        "status": "ok" if created else "error",
        "already_exists": False,
        "table": "object_candidates",
        "backup_path": str(backup_dir.relative_to(DATA_PROJECT_ROOT)) if backup_dir else None,
        "db_write_performed": created,
        "message": "Table object_candidates created." if created else "Failed to create object_candidates.",
    }


def count_object_candidates() -> dict:
    """Read-only count of object_candidates rows."""
    inspector = inspect(engine)
    if "object_candidates" not in inspector.get_table_names():
        return {"table_exists": False, "row_count": 0}
    from app.db.session import SessionLocal
    from sqlalchemy import func, select
    with SessionLocal() as session:
        count = session.scalar(select(func.count()).select_from(ObjectCandidate))
    return {"table_exists": True, "row_count": count or 0}


def validate_object_candidate_row(row: dict) -> list[str]:
    """Validate a candidate row dict before insert. Returns list of errors (empty = valid)."""
    errors = []
    if not row.get("object_key", "").strip():
        errors.append("object_key is required")
    if not row.get("object_name", "").strip():
        errors.append("object_name is required")
    if not row.get("import_job_id", "").strip():
        errors.append("import_job_id is required")

    object_type = str(row.get("object_type") or "").strip()
    from app.models.object_candidate import ALLOWED_OBJECT_TYPES
    if object_type not in ALLOWED_OBJECT_TYPES:
        errors.append(f"object_type '{object_type}' not allowed")

    review_status = str(row.get("review_status") or "").strip()
    from app.models.object_candidate import ALLOWED_REVIEW_STATUSES, FORBIDDEN_STATUSES
    if review_status in FORBIDDEN_STATUSES:
        errors.append(f"review_status '{review_status}' is forbidden")
    elif review_status not in ALLOWED_REVIEW_STATUSES:
        errors.append(f"review_status '{review_status}' not in {ALLOWED_REVIEW_STATUSES}")

    confidence = row.get("confidence")
    if confidence is not None:
        from app.models.object_candidate import ALLOWED_CONFIDENCE
        if str(confidence).strip() not in ALLOWED_CONFIDENCE:
            errors.append(f"confidence '{confidence}' not allowed")

    source_origin = row.get("source_origin")
    if source_origin:
        from app.models.object_candidate import ALLOWED_SOURCE_ORIGINS
        if str(source_origin).strip() not in ALLOWED_SOURCE_ORIGINS:
            errors.append(f"source_origin '{source_origin}' not allowed")

    necessity = row.get("necessity_judgment")
    if necessity:
        from app.models.object_candidate import ALLOWED_NECESSITY_JUDGMENTS
        if str(necessity).strip() not in ALLOWED_NECESSITY_JUDGMENTS:
            errors.append(f"necessity_judgment '{necessity}' not allowed")

    importance = row.get("importance_score")
    if importance:
        from app.models.object_candidate import ALLOWED_IMPORTANCE_SCORES
        if str(importance).strip() not in ALLOWED_IMPORTANCE_SCORES:
            errors.append(f"importance_score '{importance}' not allowed")

    # Validate JSON columns are valid JSON
    for json_field in ("aliases_json", "topic_tags_json", "problem_tags_json", "mechanism_tags_json",
                        "inspiration_tags_json", "evidence_refs_json", "note_refs_json",
                        "source_note_ids_json", "mapped_chunk_ids_json", "warnings_json"):
        raw = row.get(json_field, "[]")
        if raw is None:
            continue
        try:
            json.loads(str(raw)) if isinstance(raw, str) else json.loads(json.dumps(raw))
        except (json.JSONDecodeError, TypeError) as e:
            errors.append(f"{json_field} is not valid JSON: {e}")

    return errors
