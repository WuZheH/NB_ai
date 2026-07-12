from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal


SQLiteOpenMode = Literal["ro", "rw", "rwc", "memory"]
SQLiteTempStore = Literal["DEFAULT", "FILE", "MEMORY"]

_UNSET = object()


def sqlite_file_uri(
    path: str | Path,
    *,
    mode: SQLiteOpenMode,
    immutable: bool = False,
    resolve_strict: bool = False,
) -> str:
    """Build the same local SQLite file URI used by legacy connection helpers."""
    if immutable and mode != "ro":
        raise ValueError("immutable SQLite connections must use mode='ro'")
    resolved = Path(path).resolve(strict=resolve_strict)
    immutable_query = "&immutable=1" if immutable else ""
    return f"file:{resolved.as_posix()}?mode={mode}{immutable_query}"


def connect_sqlite(
    database: str | Path,
    *,
    mode: SQLiteOpenMode | None = None,
    immutable: bool = False,
    uri: bool = False,
    resolve_strict: bool = False,
    timeout: float | None = None,
    row_factory: Any | None = None,
    query_only: bool | None = None,
    foreign_keys: bool | None = None,
    temp_store: SQLiteTempStore | None = None,
    isolation_level: str | None | object = _UNSET,
    check_same_thread: bool | object = _UNSET,
) -> sqlite3.Connection:
    """Open SQLite with explicit, opt-in connection semantics.

    Defaults deliberately match ``sqlite3.connect``: no PRAGMA is issued and
    timeout, isolation level, and thread checks are left to sqlite3 unless the
    caller supplies them.  A mode creates a file URI; callers that already
    have a URI can instead set ``uri=True`` without a mode.
    """
    if immutable and mode is None:
        raise ValueError("immutable SQLite connections require an explicit mode")

    target = str(database)
    use_uri = uri
    if mode is not None:
        target = sqlite_file_uri(
            database,
            mode=mode,
            immutable=immutable,
            resolve_strict=resolve_strict,
        )
        use_uri = True

    kwargs: dict[str, Any] = {"uri": use_uri}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if isolation_level is not _UNSET:
        kwargs["isolation_level"] = isolation_level
    if check_same_thread is not _UNSET:
        kwargs["check_same_thread"] = check_same_thread

    connection = sqlite3.connect(target, **kwargs)
    try:
        if row_factory is not None:
            connection.row_factory = row_factory
        if foreign_keys is not None:
            connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
        if temp_store is not None:
            normalized_temp_store = str(temp_store).upper()
            if normalized_temp_store not in {"DEFAULT", "FILE", "MEMORY"}:
                raise ValueError(f"unsupported SQLite temp_store: {temp_store}")
            connection.execute(f"PRAGMA temp_store = {normalized_temp_store}")
        if query_only is not None:
            connection.execute(f"PRAGMA query_only = {'ON' if query_only else 'OFF'}")
    except Exception:
        connection.close()
        raise
    return connection


def connect_readonly_sqlite(
    path: str | Path,
    *,
    immutable: bool = False,
    resolve_strict: bool = False,
    timeout: float | None = None,
    row_factory: Any | None = None,
    query_only: bool | None = None,
    temp_store: SQLiteTempStore | None = None,
) -> sqlite3.Connection:
    """Open an existing database in SQLite read-only mode."""
    return connect_sqlite(
        path,
        mode="ro",
        immutable=immutable,
        resolve_strict=resolve_strict,
        timeout=timeout,
        row_factory=row_factory,
        query_only=query_only,
        temp_store=temp_store,
    )


def connect_existing_readwrite_sqlite(
    path: str | Path,
    *,
    resolve_strict: bool = False,
    timeout: float | None = None,
    row_factory: Any | None = None,
    foreign_keys: bool | None = None,
    temp_store: SQLiteTempStore | None = None,
    isolation_level: str | None | object = _UNSET,
    check_same_thread: bool | object = _UNSET,
) -> sqlite3.Connection:
    """Open an existing database in read/write mode without creating it."""
    return connect_sqlite(
        path,
        mode="rw",
        resolve_strict=resolve_strict,
        timeout=timeout,
        row_factory=row_factory,
        foreign_keys=foreign_keys,
        temp_store=temp_store,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
    )


def connect_immutable_readonly_sqlite(
    path: str | Path,
    *,
    resolve_strict: bool = True,
) -> sqlite3.Connection:
    """Open an immutable read-only DB with the established retrieval settings."""
    return connect_readonly_sqlite(
        path,
        immutable=True,
        resolve_strict=resolve_strict,
        row_factory=sqlite3.Row,
        query_only=True,
        temp_store="MEMORY",
    )
