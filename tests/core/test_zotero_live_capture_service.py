from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from app.services import zotero_live_capture_service as service


CORE_TABLES = (
    "items",
    "itemTypes",
    "fields",
    "itemData",
    "itemDataValues",
    "itemAttachments",
    "itemAnnotations",
    "itemNotes",
)


def _make_source(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    for table in CORE_TABLES:
        connection.execute(f'CREATE TABLE "{table}"(value TEXT)')
    connection.execute("INSERT INTO items(value) VALUES('KIT')")
    connection.commit()
    return connection


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_live_capture_uses_consistent_backup_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "zotero.sqlite"
    capture_dir = tmp_path / "captures"
    connection = _make_source(source, wal=True)
    try:
        connection.execute("INSERT INTO items(value) VALUES('committed-in-wal')")
        connection.commit()
        before_sha = _sha256(source)
        before_stat = source.stat()
        wal_path = Path(f"{source}-wal")
        wal_before = (_sha256(wal_path), wal_path.stat().st_mtime_ns)

        capture = service.capture_live_zotero_database(
            source_db_path=source,
            capture_dir=capture_dir,
        )

        assert _sha256(source) == before_sha
        assert source.stat().st_mtime_ns == before_stat.st_mtime_ns
        assert (_sha256(wal_path), wal_path.stat().st_mtime_ns) == wal_before
        assert capture.revision == _sha256(capture.snapshot_path)
        assert capture.snapshot_path.name == f"{capture.revision}.sqlite"
        assert capture.metadata_path.name == f"{capture.revision}.json"
        with sqlite3.connect(
            f"file:{capture.snapshot_path.as_posix()}?mode=ro",
            uri=True,
        ) as captured:
            values = [row[0] for row in captured.execute("SELECT value FROM items")]
        assert values == ["KIT", "committed-in-wal"]
    finally:
        connection.close()


def test_identical_source_reuses_content_addressed_capture(tmp_path: Path) -> None:
    source = tmp_path / "zotero.sqlite"
    connection = _make_source(source)
    connection.close()
    first = service.capture_live_zotero_database(
        source_db_path=source,
        capture_dir=tmp_path / "captures",
    )
    second = service.capture_live_zotero_database(
        source_db_path=source,
        capture_dir=tmp_path / "captures",
    )
    assert second.revision == first.revision
    assert second.snapshot_path == first.snapshot_path
    assert second.created is False

    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO items(value) VALUES('new-state')")
    third = service.capture_live_zotero_database(
        source_db_path=source,
        capture_dir=tmp_path / "captures",
    )
    assert third.revision != first.revision
    assert third.created is True


def test_busy_source_fails_closed_without_publishing_capture(tmp_path: Path) -> None:
    source = tmp_path / "zotero.sqlite"
    owner = _make_source(source)
    owner.execute("PRAGMA journal_mode=DELETE")
    owner.execute("BEGIN EXCLUSIVE")
    try:
        started = time.monotonic()
        with pytest.raises(service.ZoteroLiveCaptureError) as error:
            service.capture_live_zotero_database(
                source_db_path=source,
                capture_dir=tmp_path / "captures",
                busy_timeout_seconds=0.1,
            )
        assert error.value.code == "zotero_live_capture_busy"
        assert time.monotonic() - started < 2.0
        assert not (tmp_path / "captures").exists()
    finally:
        owner.rollback()
        owner.close()


def test_capture_validation_rejects_missing_core_table(tmp_path: Path) -> None:
    source = tmp_path / "zotero.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE items(value TEXT)")
    with pytest.raises(service.ZoteroLiveCaptureError) as error:
        service.capture_live_zotero_database(
            source_db_path=source,
            capture_dir=tmp_path / "captures",
        )
    assert error.value.code == "zotero_live_capture_invalid"
    assert not list((tmp_path / "captures").glob("*.sqlite"))


def test_revision_resolver_rejects_unknown_and_tampered_capture(tmp_path: Path) -> None:
    source = tmp_path / "zotero.sqlite"
    connection = _make_source(source)
    connection.close()
    capture_dir = tmp_path / "captures"
    capture = service.capture_live_zotero_database(
        source_db_path=source,
        capture_dir=capture_dir,
    )
    with pytest.raises(service.ZoteroLiveCaptureError) as unknown:
        service.resolve_zotero_source_revision("0" * 64, capture_dir=capture_dir)
    assert unknown.value.code == "zotero_source_revision_unknown"

    capture.snapshot_path.write_bytes(capture.snapshot_path.read_bytes() + b"tamper")
    with pytest.raises(service.ZoteroLiveCaptureError) as corrupt:
        service.resolve_zotero_source_revision(capture.revision, capture_dir=capture_dir)
    assert corrupt.value.code == "zotero_source_revision_corrupt"
