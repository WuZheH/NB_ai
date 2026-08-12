from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.migrations import (
    migrate_zotero_personal_notes_schema as migration,
)


LEGACY_EVIDENCE_COLUMNS = (
    "id",
    "note_id",
    "chunk_id",
    "link_type",
    "evidence_role",
    "quote_text",
    "confidence",
    "created_by",
    "created_at",
)


def _legacy_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            );
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE personal_notes (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                note_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE note_evidence_links (
                id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                chunk_id INTEGER NOT NULL,
                link_type TEXT NOT NULL,
                evidence_role TEXT,
                quote_text TEXT,
                confidence FLOAT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(note_id) REFERENCES personal_notes(id),
                FOREIGN KEY(chunk_id) REFERENCES knowledge_chunks(id)
            );
            CREATE TABLE zotero_inspiration_notes (
                id INTEGER PRIMARY KEY,
                marker TEXT NOT NULL
            );
            INSERT INTO documents VALUES (1, 'Legacy book');
            INSERT INTO knowledge_chunks VALUES (10, 1, 'Legacy chunk');
            INSERT INTO personal_notes VALUES (
                100, 1, 'manual', 'Legacy note', 'Legacy content',
                '2026-01-01', '2026-01-01'
            );
            INSERT INTO note_evidence_links VALUES (
                1000, 100, 10, 'supports', 'evidence', 'Legacy quote',
                0.8, 'manual', '2026-01-01'
            );
            INSERT INTO zotero_inspiration_notes VALUES (1, 'preserve');
            """
        )
        connection.commit()
    return path


@pytest.fixture
def production_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    database = _legacy_database(
        tmp_path / "data" / "db" / "research_memory.db"
    )
    monkeypatch.setattr(migration, "DEFAULT_DB_PATH", database)
    backup = (
        tmp_path
        / "data"
        / "backups"
        / "direction_b"
        / "before.db"
    )
    return database, backup


def _apply(
    database: Path,
    backup: Path,
) -> dict:
    return migration.migrate_database(
        database,
        dry_run=False,
        allow_production=True,
        expected_sha256=migration.file_sha256(database),
        backup_path=backup,
    )


def _table_counts(path: Path) -> dict[str, int]:
    tables = (
        "documents",
        "knowledge_chunks",
        "personal_notes",
        "note_evidence_links",
        "zotero_inspiration_notes",
    )
    with sqlite3.connect(path) as connection:
        return {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
            )
            for table in tables
        }


def _legacy_evidence(path: Path) -> tuple:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT "
            + ", ".join(LEGACY_EVIDENCE_COLUMNS)
            + " FROM note_evidence_links ORDER BY id"
        ).fetchone()


def test_production_dry_run_is_readonly(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _backup = production_target
    before = database.read_bytes()
    observed: list[tuple[bool, int]] = []
    real_connect = migration.connect_database

    def recording_connect(path, *, read_only):
        connection = real_connect(path, read_only=read_only)
        observed.append(
            (
                read_only,
                int(connection.execute("PRAGMA query_only").fetchone()[0]),
            )
        )
        return connection

    monkeypatch.setattr(migration, "connect_database", recording_connect)
    result = migration.migrate_database(database, dry_run=True)
    assert result["production_target"] is True
    assert result["production_write_performed"] is False
    assert result["operations"]
    assert result["remaining_operations"] == result["operations"]
    assert observed == [(True, 1)]
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "allow-production"),
        ({"allow_production": True}, "expected SHA256"),
        (
            {
                "allow_production": True,
                "expected_sha256": "0" * 64,
            },
            "backup path",
        ),
    ],
)
def test_missing_production_gate_is_rejected_before_rw_connect(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict,
    match: str,
) -> None:
    database, _backup = production_target
    monkeypatch.setattr(
        migration,
        "connect_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("RW connect must not occur")
        ),
    )
    with pytest.raises(migration.MigrationSafetyError, match=match):
        migration.migrate_database(database, dry_run=False, **kwargs)


def test_wrong_hash_is_rejected_before_backup_or_rw(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    monkeypatch.setattr(
        migration,
        "connect_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("RW connect must not occur")
        ),
    )
    with pytest.raises(migration.MigrationSafetyError, match="does not match"):
        migration.migrate_database(
            database,
            dry_run=False,
            allow_production=True,
            expected_sha256="0" * 64,
            backup_path=backup,
        )
    assert not backup.exists()


def test_backup_path_must_be_new_and_inside_allowed_root(
    production_target,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    expected = migration.file_sha256(database)
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"occupied")
    forbidden = tmp_path / "outside.db"
    monkeypatch.setattr(
        migration,
        "connect_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("RW connect must not occur")
        ),
    )
    with pytest.raises(migration.MigrationSafetyError, match="already exists"):
        migration.migrate_database(
            database,
            dry_run=False,
            allow_production=True,
            expected_sha256=expected,
            backup_path=backup,
        )
    with pytest.raises(migration.MigrationSafetyError, match="must be under"):
        migration.migrate_database(
            database,
            dry_run=False,
            allow_production=True,
            expected_sha256=expected,
            backup_path=forbidden,
        )


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecar_blocks_apply_without_deleting_it(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    database, backup = production_target
    sidecar = Path(f"{database}{suffix}")
    sidecar.write_bytes(b"guard")
    monkeypatch.setattr(
        migration,
        "connect_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("RW connect must not occur")
        ),
    )
    with pytest.raises(migration.MigrationSafetyError, match="sidecar"):
        _apply(database, backup)
    assert sidecar.read_bytes() == b"guard"
    assert not backup.exists()


def test_valid_gate_creates_verified_backup_and_preserves_legacy_data(
    production_target,
) -> None:
    database, backup = production_target
    before_sha256 = migration.file_sha256(database)
    before_counts = _table_counts(database)
    before_evidence = _legacy_evidence(database)
    result = _apply(database, backup)
    assert result["status"] == "applied"
    assert result["production_target"] is True
    assert result["production_write_performed"] is True
    assert result["before_sha256"] == before_sha256
    assert result["backup_path"] == str(backup.resolve())
    assert result["backup_sha256"] == before_sha256
    assert result["backup_verified"] is True
    assert result["production_restore_performed"] is False
    assert result["remaining_operations"] == []
    assert backup.read_bytes() != b""
    assert migration.file_sha256(backup) == before_sha256
    assert _table_counts(database) == before_counts
    assert _legacy_evidence(database) == before_evidence
    with sqlite3.connect(database) as connection:
        assert migration.plan_migration(connection) == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        evidence = migration.columns(connection, "note_evidence_links")
        assert evidence["chunk_id"]["notnull"] == 0


def test_second_production_run_is_idempotent(
    production_target,
) -> None:
    database, first_backup = production_target
    first = _apply(database, first_backup)
    second_backup = first_backup.with_name("second.db")
    second = _apply(database, second_backup)
    assert first["operations"]
    assert second["operations"] == []
    assert second["applied"] == []
    assert second["remaining_operations"] == []
    assert second["backup_verified"] is True


def test_transaction_failure_rolls_back_and_keeps_backup(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    before = database.read_bytes()
    real_add = migration.add_personal_note_columns

    def failing_add(connection, applied):
        real_add(connection, applied)
        raise RuntimeError("migration failure")

    monkeypatch.setattr(migration, "add_personal_note_columns", failing_add)
    with pytest.raises(migration.MigrationSafetyError) as caught:
        _apply(database, backup)
    assert caught.value.production_restore_performed is False
    assert database.read_bytes() == before
    assert backup.read_bytes() == before


def test_post_commit_validation_failure_restores_verified_backup(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    before = database.read_bytes()
    real_validate = migration.validate
    calls = 0

    def fail_second_validation(connection):
        nonlocal calls
        calls += 1
        real_validate(connection)
        if calls == 2:
            raise RuntimeError("post-commit validation failure")

    monkeypatch.setattr(migration, "validate", fail_second_validation)
    with pytest.raises(migration.MigrationSafetyError) as caught:
        _apply(database, backup)
    assert caught.value.production_restore_performed is True
    assert database.read_bytes() == before
    assert backup.read_bytes() == before


def test_restore_failure_is_explicit_hard_failure(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    real_validate = migration.validate
    calls = 0

    def fail_second_validation(connection):
        nonlocal calls
        calls += 1
        real_validate(connection)
        if calls == 2:
            raise RuntimeError("post-commit validation failure")

    monkeypatch.setattr(migration, "validate", fail_second_validation)
    monkeypatch.setattr(
        migration,
        "_restore_verified_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("restore failure")
        ),
    )
    with pytest.raises(
        migration.MigrationSafetyError,
        match="production migration rollback failed",
    ) as caught:
        _apply(database, backup)
    assert caught.value.production_restore_performed is False
    assert backup.exists()


def test_post_commit_restore_retries_transient_permission_error(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    before = database.read_bytes()

    real_validate = migration.validate
    validation_calls = 0

    def fail_second_validation(connection):
        nonlocal validation_calls
        validation_calls += 1
        real_validate(connection)
        if validation_calls == 2:
            raise RuntimeError(
                "post-commit validation failure"
            )

    real_replace = migration.os.replace
    replace_calls = 0

    def flaky_replace(source, target):
        nonlocal replace_calls
        replace_calls += 1

        if replace_calls <= 2:
            raise PermissionError(
                "transient Windows replace lock"
            )

        return real_replace(source, target)

    sleep_calls: list[float] = []

    monkeypatch.setattr(
        migration,
        "validate",
        fail_second_validation,
    )
    monkeypatch.setattr(
        migration.os,
        "replace",
        flaky_replace,
    )
    monkeypatch.setattr(
        migration.time,
        "sleep",
        sleep_calls.append,
    )

    with pytest.raises(
        migration.MigrationSafetyError
    ) as caught:
        _apply(database, backup)

    assert (
        caught.value.production_restore_performed
        is True
    )
    assert replace_calls == 3
    assert sleep_calls == [0.10, 0.25]
    assert database.read_bytes() == before
    assert backup.read_bytes() == before
    assert not list(
        database.parent.glob(
            ".direction-b-restore-*.db"
        )
    )


def test_post_commit_restore_persistent_permission_error_still_fails_closed(
    production_target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, backup = production_target
    before = database.read_bytes()

    real_validate = migration.validate
    validation_calls = 0

    def fail_second_validation(connection):
        nonlocal validation_calls
        validation_calls += 1
        real_validate(connection)
        if validation_calls == 2:
            raise RuntimeError(
                "post-commit validation failure"
            )

    replace_calls = 0

    def always_locked_replace(_source, _target):
        nonlocal replace_calls
        replace_calls += 1
        raise PermissionError(
            "persistent Windows replace lock"
        )

    sleep_calls: list[float] = []

    monkeypatch.setattr(
        migration,
        "validate",
        fail_second_validation,
    )
    monkeypatch.setattr(
        migration.os,
        "replace",
        always_locked_replace,
    )
    monkeypatch.setattr(
        migration.time,
        "sleep",
        sleep_calls.append,
    )

    with pytest.raises(
        migration.MigrationSafetyError,
        match="production migration rollback failed",
    ) as caught:
        _apply(database, backup)

    assert (
        caught.value.production_restore_performed
        is False
    )

    assert replace_calls == (
        len(
            migration
            ._RESTORE_REPLACE_RETRY_DELAYS_SECONDS
        )
        + 1
    )

    assert sleep_calls == list(
        migration
        ._RESTORE_REPLACE_RETRY_DELAYS_SECONDS
    )

    assert backup.read_bytes() == before

    assert not list(
        database.parent.glob(
            ".direction-b-restore-*.db"
        )
    )
