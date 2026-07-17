from __future__ import annotations

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

import pytest

from app.core.paths import DEFAULT_DB_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_production_database_integrity_is_read_only() -> None:
    if not DEFAULT_DB_PATH.is_file():
        pytest.skip("production database is intentionally absent from this checkout")
    database_path = DEFAULT_DB_PATH.resolve(strict=True)
    sha256_before = _sha256(database_path)

    with closing(
        sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
    ) as connection:
        connection.execute("PRAGMA query_only = ON")
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

    sha256_after = _sha256(database_path)
    assert integrity_rows == [("ok",)]
    assert foreign_key_rows == []
    assert sha256_after == sha256_before

