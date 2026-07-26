from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "db" / "research_memory.db"


class MigrationSafetyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        production_restore_performed: bool = False,
    ) -> None:
        super().__init__(message)
        self.production_restore_performed = bool(
            production_restore_performed
        )


REQUIRED_TABLES = {
    "documents",
    "knowledge_chunks",
    "personal_notes",
    "note_evidence_links",
}


PERSONAL_NOTE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_system", "TEXT"),
    ("source_library_id", "INTEGER"),
    ("source_item_key", "TEXT"),
    ("source_parent_item_key", "TEXT"),
    ("source_attachment_key", "TEXT"),
    ("source_annotation_key", "TEXT"),
    ("source_note_key", "TEXT"),
    ("source_record_kind", "TEXT"),
    ("source_identity", "TEXT"),
    ("selected_text", "TEXT"),
    ("source_comment", "TEXT"),
    ("pdf_page", "INTEGER"),
    ("page_label", "TEXT"),
    ("position_json", "TEXT"),
    ("source_uri", "TEXT"),
    ("source_created_at", "TEXT"),
    ("source_updated_at", "TEXT"),
    ("source_version", "INTEGER"),
    ("source_content_hash", "TEXT"),
    ("source_missing", "INTEGER NOT NULL DEFAULT 0"),
)


PERSONAL_NOTE_INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    ux_personal_notes_source_identity
    ON personal_notes(source_identity)
    WHERE source_identity IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_system
    ON personal_notes(source_system)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_item_key
    ON personal_notes(source_item_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_attachment_key
    ON personal_notes(source_attachment_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_annotation_key
    ON personal_notes(source_annotation_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_note_key
    ON personal_notes(source_note_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_content_hash
    ON personal_notes(source_content_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_personal_notes_source_updated_at
    ON personal_notes(source_updated_at)
    """,
)


EVIDENCE_COLUMNS = (
    "id",
    "note_id",
    "document_id",
    "chunk_id",
    "link_type",
    "evidence_role",
    "quote_text",
    "confidence",
    "created_by",
    "created_at",
    "pdf_page",
    "page_label",
    "source_locator_json",
    "alignment_status",
    "alignment_method",
    "alignment_warnings_json",
    "source_quote_hash",
)


EVIDENCE_NEW_COLUMNS = {
    "document_id",
    "pdf_page",
    "page_label",
    "source_locator_json",
    "alignment_status",
    "alignment_method",
    "alignment_warnings_json",
    "source_quote_hash",
}


EVIDENCE_TABLE_SQL = """
CREATE TABLE note_evidence_links__direction_b (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL,
    document_id INTEGER,
    chunk_id INTEGER,
    link_type VARCHAR(64) NOT NULL,
    evidence_role VARCHAR(64),
    quote_text TEXT,
    confidence FLOAT,
    created_by VARCHAR(64) NOT NULL DEFAULT 'manual',
    created_at DATETIME NOT NULL,
    pdf_page INTEGER,
    page_label VARCHAR(64),
    source_locator_json TEXT,
    alignment_status VARCHAR(64),
    alignment_method VARCHAR(128),
    alignment_warnings_json TEXT NOT NULL DEFAULT '[]',
    source_quote_hash VARCHAR(64),

    FOREIGN KEY(note_id) REFERENCES personal_notes(id),
    FOREIGN KEY(document_id) REFERENCES documents(id),
    FOREIGN KEY(chunk_id) REFERENCES knowledge_chunks(id),

    CHECK (
        document_id IS NOT NULL
        OR chunk_id IS NOT NULL
    )
)
"""


EVIDENCE_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_id
    ON note_evidence_links(id)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_note_id
    ON note_evidence_links(note_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_document_id
    ON note_evidence_links(document_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_chunk_id
    ON note_evidence_links(chunk_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    ix_note_evidence_links_pdf_page
    ON note_evidence_links(pdf_page)
    """,
)


def resolved(path: str | Path) -> Path:
    return Path(path).resolve(strict=False)


def is_production_database(path: str | Path) -> bool:
    return resolved(path) == resolved(DEFAULT_DB_PATH)


def file_sha256(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest().upper()


def production_backup_root() -> Path:
    return resolved(DEFAULT_DB_PATH).parent.parent / "backups" / "direction_b"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _production_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(f"{path}{suffix}")
        for suffix in ("-wal", "-shm", "-journal")
    )


def _validate_production_apply_gate(
    path: Path,
    *,
    allow_production: bool,
    expected_sha256: str | None,
    backup_path: str | Path | None,
) -> tuple[str, Path]:
    if not allow_production:
        raise MigrationSafetyError(
            "Production schema migration requires "
            "--allow-production."
        )

    expected = str(expected_sha256 or "").strip().upper()
    if re.fullmatch(r"[0-9A-F]{64}", expected) is None:
        raise MigrationSafetyError(
            "Production schema migration requires a "
            "64-character expected SHA256."
        )

    if backup_path is None:
        raise MigrationSafetyError(
            "Production schema migration requires an "
            "explicit backup path."
        )

    backup = resolved(backup_path)
    if backup == path:
        raise MigrationSafetyError(
            "Production backup path cannot equal the database path."
        )
    if backup.exists():
        raise MigrationSafetyError(
            "Production backup path already exists."
        )

    allowed_root = production_backup_root()
    if not _is_within(backup, allowed_root):
        raise MigrationSafetyError(
            "Production backup must be under "
            "data/backups/direction_b."
        )

    existing_sidecars = [
        sidecar
        for sidecar in _production_sidecars(path)
        if sidecar.exists()
    ]
    if existing_sidecars:
        raise MigrationSafetyError(
            "Production database sidecar exists; migration refused."
        )

    current_sha256 = file_sha256(path)
    if current_sha256 != expected:
        raise MigrationSafetyError(
            "Production database SHA256 does not match the expected value."
        )

    return expected, backup


def _create_verified_backup(
    source: Path,
    backup: Path,
    *,
    expected_sha256: str,
) -> tuple[str, int]:
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup)
    backup_sha256 = file_sha256(backup)
    source_size = source.stat().st_size
    if (
        backup_sha256 != expected_sha256
        or backup.stat().st_size != source_size
    ):
        raise MigrationSafetyError(
            "Production database backup verification failed."
        )
    return backup_sha256, source_size


def _restore_verified_backup(
    target: Path,
    backup: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    restore_candidate = target.with_name(
        f".direction-b-restore-{uuid4().hex[:8]}.db"
    )
    try:
        shutil.copy2(backup, restore_candidate)
        if (
            restore_candidate.stat().st_size != expected_size
            or file_sha256(restore_candidate) != expected_sha256
        ):
            raise MigrationSafetyError(
                "production migration rollback failed"
            )
        os.replace(restore_candidate, target)
        if (
            target.stat().st_size != expected_size
            or file_sha256(target) != expected_sha256
        ):
            raise MigrationSafetyError(
                "production migration rollback failed"
            )
    except Exception as exc:
        restore_candidate.unlink(missing_ok=True)
        raise MigrationSafetyError(
            "production migration rollback failed"
        ) from exc


def connect_database(
    path: Path,
    *,
    read_only: bool,
) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only = ON")
    else:
        connection = sqlite3.connect(path)

    connection.row_factory = sqlite3.Row
    return connection


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone() is not None


def columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, sqlite3.Row]:
    connection.row_factory = sqlite3.Row

    return {
        str(row["name"]): row
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def indexes(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA index_list("{table_name}")'
        ).fetchall()
    }


def index_name(sql: str) -> str:
    normalized = " ".join(sql.split())
    return normalized.split(
        "IF NOT EXISTS ",
        1,
    )[1].split(" ", 1)[0]


def preflight(
    connection: sqlite3.Connection,
) -> None:
    missing = sorted(
        table
        for table in REQUIRED_TABLES
        if not table_exists(connection, table)
    )

    if missing:
        raise MigrationSafetyError(
            "Required tables missing: "
            + ", ".join(missing)
        )

    if "document_id" not in columns(
        connection,
        "knowledge_chunks",
    ):
        raise MigrationSafetyError(
            "knowledge_chunks.document_id "
            "is required."
        )


def evidence_needs_rebuild(
    connection: sqlite3.Connection,
) -> bool:
    current = columns(
        connection,
        "note_evidence_links",
    )

    if not EVIDENCE_NEW_COLUMNS.issubset(current):
        return True

    chunk_column = current.get("chunk_id")

    if chunk_column is None:
        return True

    return bool(chunk_column["notnull"])


def plan_migration(
    connection: sqlite3.Connection,
) -> list[str]:
    preflight(connection)

    result: list[str] = []

    personal = columns(
        connection,
        "personal_notes",
    )

    for name, _definition in PERSONAL_NOTE_COLUMNS:
        if name not in personal:
            result.append(
                f"add_column:personal_notes.{name}"
            )

    current_indexes = indexes(
        connection,
        "personal_notes",
    )

    for sql in PERSONAL_NOTE_INDEXES:
        name = index_name(sql)

        if name not in current_indexes:
            result.append(
                f"ensure_index:{name}"
            )

    if evidence_needs_rebuild(connection):
        result.append(
            "rebuild_table:note_evidence_links"
        )

    evidence_indexes = indexes(
        connection,
        "note_evidence_links",
    )

    for sql in EVIDENCE_INDEXES:
        name = index_name(sql)

        if name not in evidence_indexes:
            result.append(
                f"ensure_index:{name}"
            )

    return result


def add_personal_note_columns(
    connection: sqlite3.Connection,
    applied: list[str],
) -> None:
    current = columns(
        connection,
        "personal_notes",
    )

    for name, definition in PERSONAL_NOTE_COLUMNS:
        if name in current:
            continue

        connection.execute(
            f'ALTER TABLE personal_notes '
            f'ADD COLUMN "{name}" {definition}'
        )

        applied.append(
            f"add_column:personal_notes.{name}"
        )


def ensure_indexes(
    connection: sqlite3.Connection,
    statements: tuple[str, ...],
) -> None:
    for sql in statements:
        connection.execute(sql)


def legacy_evidence_index_sql(
    connection: sqlite3.Connection,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index'
          AND tbl_name = 'note_evidence_links'
          AND sql IS NOT NULL
        ORDER BY name
        """
    ).fetchall()

    return [
        str(row[0])
        for row in rows
        if row[0]
    ]


def select_expression(
    name: str,
    existing: set[str],
) -> str:
    if name == "document_id":
        if "document_id" in existing:
            return (
                "COALESCE("
                "old.document_id, "
                "kc.document_id"
                ") AS document_id"
            )

        return (
            "kc.document_id AS document_id"
        )

    if name == "alignment_status":
        if name in existing:
            return "old.alignment_status"

        return (
            "CASE "
            "WHEN old.chunk_id IS NOT NULL "
            "THEN 'matched' "
            "ELSE 'document_only' "
            "END AS alignment_status"
        )

    if name == "alignment_method":
        if name in existing:
            return "old.alignment_method"

        return (
            "CASE "
            "WHEN old.chunk_id IS NOT NULL "
            "THEN 'legacy_existing_chunk_id' "
            "ELSE 'legacy_document_link' "
            "END AS alignment_method"
        )

    if name == "alignment_warnings_json":
        if name in existing:
            return (
                "COALESCE("
                "old.alignment_warnings_json, "
                "'[]'"
                ") AS alignment_warnings_json"
            )

        return (
            "'[]' AS alignment_warnings_json"
        )

    if name in existing:
        return f'old."{name}"'

    return f'NULL AS "{name}"'


def rebuild_evidence_links(
    connection: sqlite3.Connection,
    applied: list[str],
) -> None:
    old_columns = set(
        columns(
            connection,
            "note_evidence_links",
        )
    )

    old_indexes = legacy_evidence_index_sql(
        connection
    )

    connection.execute(
        "DROP TABLE IF EXISTS "
        "note_evidence_links__direction_b"
    )

    connection.execute(
        EVIDENCE_TABLE_SQL
    )

    target_columns = ", ".join(
        EVIDENCE_COLUMNS
    )

    source_columns = ",\n".join(
        select_expression(
            name,
            old_columns,
        )
        for name in EVIDENCE_COLUMNS
    )

    connection.execute(
        f"""
        INSERT INTO note_evidence_links__direction_b
            ({target_columns})
        SELECT
            {source_columns}
        FROM note_evidence_links AS old
        LEFT JOIN knowledge_chunks AS kc
            ON kc.id = old.chunk_id
        """
    )

    connection.execute(
        "DROP TABLE note_evidence_links"
    )

    connection.execute(
        """
        ALTER TABLE
            note_evidence_links__direction_b
        RENAME TO
            note_evidence_links
        """
    )

    for sql in old_indexes:
        connection.execute(sql)

    ensure_indexes(
        connection,
        EVIDENCE_INDEXES,
    )

    applied.append(
        "rebuild_table:note_evidence_links"
    )


def validate(
    connection: sqlite3.Connection,
) -> None:
    integrity = connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity != "ok":
        raise MigrationSafetyError(
            f"integrity_check={integrity}"
        )

    fk_errors = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if fk_errors:
        raise MigrationSafetyError(
            f"foreign_key_check={fk_errors}"
        )

    personal = set(
        columns(
            connection,
            "personal_notes",
        )
    )

    expected_personal = {
        name
        for name, _definition
        in PERSONAL_NOTE_COLUMNS
    }

    missing_personal = sorted(
        expected_personal - personal
    )

    if missing_personal:
        raise MigrationSafetyError(
            "Missing personal_notes columns: "
            + ", ".join(missing_personal)
        )

    evidence = columns(
        connection,
        "note_evidence_links",
    )

    missing_evidence = sorted(
        EVIDENCE_NEW_COLUMNS - set(evidence)
    )

    if missing_evidence:
        raise MigrationSafetyError(
            "Missing evidence columns: "
            + ", ".join(missing_evidence)
        )

    if bool(evidence["chunk_id"]["notnull"]):
        raise MigrationSafetyError(
            "chunk_id must be nullable"
        )


def migrate_database(
    db_path: str | Path,
    *,
    dry_run: bool,
    allow_production: bool = False,
    expected_sha256: str | None = None,
    backup_path: str | Path | None = None,
) -> dict[str, Any]:
    path = resolved(db_path)
    production_target = is_production_database(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}"
        )

    before_sha256 = file_sha256(path)
    verified_backup: Path | None = None
    backup_sha256: str | None = None
    backup_size: int | None = None

    if not dry_run and production_target:
        expected, verified_backup = _validate_production_apply_gate(
            path,
            allow_production=allow_production,
            expected_sha256=expected_sha256,
            backup_path=backup_path,
        )
        backup_sha256, backup_size = _create_verified_backup(
            path,
            verified_backup,
            expected_sha256=expected,
        )

    connection = connect_database(
        path,
        read_only=dry_run,
    )
    committed = False
    try:
        operations = plan_migration(
            connection
        )

        if dry_run:
            connection.close()
            return {
                "status": "dry_run",
                "database": str(path),
                "operations": operations,
                "applied": [],
                "applied_count": 0,
                "remaining_operations": operations,
                "production_target": production_target,
                "production_write_performed": False,
                "before_sha256": before_sha256,
                "after_sha256": before_sha256,
                "backup_path": None,
                "backup_sha256": None,
                "backup_verified": False,
                "production_restore_performed": False,
            }

        original_fk = int(
            connection.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]
        )

        connection.commit()
        connection.execute(
            "PRAGMA foreign_keys = OFF"
        )

        applied: list[str] = []

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            add_personal_note_columns(
                connection,
                applied,
            )

            ensure_indexes(
                connection,
                PERSONAL_NOTE_INDEXES,
            )

            if evidence_needs_rebuild(connection):
                rebuild_evidence_links(
                    connection,
                    applied,
                )
            else:
                ensure_indexes(
                    connection,
                    EVIDENCE_INDEXES,
                )

            validate(connection)

            connection.commit()
            committed = True

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.execute(
                "PRAGMA foreign_keys = "
                + ("ON" if original_fk else "OFF")
            )

        validate(connection)
        remaining = plan_migration(
            connection
        )

        if remaining:
            raise MigrationSafetyError(
                "Migration incomplete: "
                f"{remaining}"
            )

        after_sha256 = file_sha256(path)
        return {
            "status": "applied",
            "database": str(path),
            "operations": operations,
            "applied": applied,
            "applied_count": len(applied),
            "remaining_operations": remaining,
            "production_target": production_target,
            "production_write_performed": production_target,
            "before_sha256": before_sha256,
            "after_sha256": after_sha256,
            "backup_path": (
                str(verified_backup)
                if verified_backup is not None
                else None
            ),
            "backup_sha256": backup_sha256,
            "backup_verified": backup_sha256 == before_sha256,
            "production_restore_performed": False,
        }
    except Exception as exc:
        connection.close()
        if (
            production_target
            and committed
            and verified_backup is not None
            and backup_sha256 is not None
            and backup_size is not None
        ):
            try:
                _restore_verified_backup(
                    path,
                    verified_backup,
                    expected_sha256=backup_sha256,
                    expected_size=backup_size,
                )
            except Exception as restore_exc:
                raise MigrationSafetyError(
                    "production migration rollback failed",
                    production_restore_performed=False,
                ) from restore_exc
            raise MigrationSafetyError(
                str(exc),
                production_restore_performed=True,
            ) from exc

        if isinstance(exc, MigrationSafetyError):
            exc.production_restore_performed = False
            raise
        raise MigrationSafetyError(
            str(exc),
            production_restore_performed=False,
        ) from exc
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Direction-B Zotero schema migration."
        )
    )

    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
    )

    modes = parser.add_mutually_exclusive_group(
        required=True
    )

    modes.add_argument(
        "--dry-run",
        action="store_true",
    )

    modes.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--allow-production",
        action="store_true",
    )
    parser.add_argument(
        "--expected-sha256",
    )
    parser.add_argument(
        "--backup-path",
    )

    args = parser.parse_args()

    result = migrate_database(
        args.db_path,
        dry_run=bool(args.dry_run),
        allow_production=bool(args.allow_production),
        expected_sha256=args.expected_sha256,
        backup_path=args.backup_path,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
