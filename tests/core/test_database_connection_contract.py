from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.database import (
    connect_existing_readwrite_sqlite,
    connect_immutable_readonly_sqlite,
    connect_sqlite,
    sqlite_file_uri,
)


def test_in_memory_connection_applies_only_requested_settings() -> None:
    connection = connect_sqlite(
        ":memory:",
        row_factory=sqlite3.Row,
        foreign_keys=True,
        temp_store="MEMORY",
        isolation_level=None,
        check_same_thread=True,
    )
    try:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.isolation_level is None
    finally:
        connection.close()


def test_sqlite_file_uri_distinguishes_modes_and_immutable() -> None:
    path = Path("data/db/research_memory.db")
    readonly = sqlite_file_uri(path, mode="ro")
    readwrite = sqlite_file_uri(path, mode="rw")
    immutable = sqlite_file_uri(path, mode="ro", immutable=True)

    assert readonly.endswith("/data/db/research_memory.db?mode=ro")
    assert readwrite.endswith("/data/db/research_memory.db?mode=rw")
    assert immutable.endswith("/data/db/research_memory.db?mode=ro&immutable=1")
    with pytest.raises(ValueError):
        sqlite_file_uri(path, mode="rw", immutable=True)


def test_readonly_and_readwrite_wrappers_preserve_connection_options(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeConnection:
        row_factory = None

        def execute(self, statement: str):
            calls.append((statement, {}))
            return self

        def close(self) -> None:
            return None

    def fake_connect(target: str, **kwargs):
        calls.append((target, kwargs))
        return FakeConnection()

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    readonly_path = tmp_path / "retrieval_fts_v1.db"
    readonly_path.touch()
    readonly = connect_immutable_readonly_sqlite(readonly_path)
    assert readonly.row_factory is sqlite3.Row
    assert calls[0][0].endswith("?mode=ro&immutable=1")
    assert calls[0][1] == {"uri": True}
    assert ("PRAGMA query_only = ON", {}) in calls
    assert ("PRAGMA temp_store = MEMORY", {}) in calls

    calls.clear()
    readwrite_path = tmp_path / "research_memory.db"
    readwrite_path.touch()
    connect_existing_readwrite_sqlite(
        readwrite_path,
        timeout=2.0,
        foreign_keys=True,
        isolation_level="IMMEDIATE",
        check_same_thread=False,
    )
    assert calls[0][0].endswith("?mode=rw")
    assert calls[0][1] == {
        "uri": True,
        "timeout": 2.0,
        "isolation_level": "IMMEDIATE",
        "check_same_thread": False,
    }
    assert ("PRAGMA foreign_keys = ON", {}) in calls
