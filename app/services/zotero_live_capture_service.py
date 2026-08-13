from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services import zotero_source_cache_service


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CORE_TABLES = frozenset(
    {
        "items",
        "itemTypes",
        "fields",
        "itemData",
        "itemDataValues",
        "itemAttachments",
        "itemAnnotations",
        "itemNotes",
    }
)


class ZoteroLiveCaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ZoteroReadCapture:
    revision: str
    snapshot_path: Path
    metadata_path: Path
    captured_at: str
    source_db_mtime_ns: int
    source_db_size: int
    created: bool


def capture_configured_live_zotero(
    *,
    capture_dir: str | Path,
    busy_timeout_seconds: float = 2.0,
) -> ZoteroReadCapture:
    try:
        config = zotero_source_cache_service._load_config()
    except (OSError, ValueError, KeyError) as exc:
        # A missing or invalid Zotero source config is a safe, clear
        # configuration error; it must never crash the tool layer or fall
        # back to private/legacy snapshots.
        raise ZoteroLiveCaptureError(
            "zotero_source_not_configured",
            "Zotero source is not configured for this installation.",
        ) from exc
    source_path = Path(str(config["zotero_data_dir"])) / "zotero.sqlite"
    return capture_live_zotero_database(
        source_db_path=source_path,
        capture_dir=capture_dir,
        busy_timeout_seconds=busy_timeout_seconds,
    )


def capture_live_zotero_database(
    *,
    source_db_path: str | Path,
    capture_dir: str | Path,
    busy_timeout_seconds: float = 2.0,
) -> ZoteroReadCapture:
    source = Path(source_db_path).resolve(strict=False)
    destination_root = Path(capture_dir).resolve(strict=False)
    timeout_seconds = max(0.05, min(float(busy_timeout_seconds), 10.0))
    timeout_ms = max(1, int(timeout_seconds * 1000))

    if not source.is_file() or source.is_symlink():
        raise ZoteroLiveCaptureError(
            "zotero_live_source_unavailable",
            "The configured live Zotero database is unavailable.",
        )
    if destination_root.exists() and (
        destination_root.is_symlink() or not destination_root.is_dir()
    ):
        raise ZoteroLiveCaptureError(
            "zotero_live_capture_path_invalid",
            "The Zotero read-capture directory is invalid.",
        )
    try:
        source_connection = sqlite3.connect(
            f"file:{source.as_posix()}?mode=ro",
            uri=True,
            timeout=timeout_seconds,
        )
        try:
            source_connection.execute("PRAGMA query_only = ON")
            source_connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
            source_connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            destination_root.mkdir(parents=True, exist_ok=True)
            temporary_snapshot = destination_root / f".{uuid4().hex}.sqlite.tmp"
            started = time.monotonic()

            def progress(status: int, _remaining: int, _total: int) -> None:
                if status in {
                    getattr(sqlite3, "SQLITE_BUSY", 5),
                    getattr(sqlite3, "SQLITE_LOCKED", 6),
                } and time.monotonic() - started >= timeout_seconds:
                    raise ZoteroLiveCaptureError(
                        "zotero_live_capture_busy",
                        "The live Zotero database is busy; no fresh read capture was published.",
                    )

            destination_connection = sqlite3.connect(temporary_snapshot)
            try:
                source_connection.backup(
                    destination_connection,
                    pages=256,
                    progress=progress,
                    sleep=0.05,
                )
                destination_connection.commit()
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    except ZoteroLiveCaptureError:
        if "temporary_snapshot" in locals():
            _remove_owned_temporary(temporary_snapshot)
        raise
    except sqlite3.Error as exc:
        if "temporary_snapshot" in locals():
            _remove_owned_temporary(temporary_snapshot)
        message = str(exc).casefold()
        if "locked" in message or "busy" in message:
            raise ZoteroLiveCaptureError(
                "zotero_live_capture_busy",
                "The live Zotero database is busy; no fresh read capture was published.",
            ) from exc
        raise ZoteroLiveCaptureError(
            "zotero_live_capture_failed",
            "The live Zotero database could not be captured safely.",
        ) from exc

    try:
        _validate_capture_database(temporary_snapshot)
        revision = _sha256_file(temporary_snapshot)
        source_stat = source.stat()
        captured_at = datetime.now(timezone.utc).isoformat()
        final_snapshot = destination_root / f"{revision}.sqlite"
        final_metadata = destination_root / f"{revision}.json"
        created = not final_snapshot.exists()

        if final_snapshot.exists():
            if (
                final_snapshot.is_symlink()
                or not final_snapshot.is_file()
                or _sha256_file(final_snapshot) != revision
            ):
                raise ZoteroLiveCaptureError(
                    "zotero_source_revision_corrupt",
                    "An existing Zotero read capture failed identity validation.",
                )
            _remove_owned_temporary(temporary_snapshot)
        else:
            _fsync_file(temporary_snapshot)
            os.replace(temporary_snapshot, final_snapshot)

        if final_metadata.exists():
            resolved = resolve_zotero_source_revision(
                revision,
                capture_dir=destination_root,
            )
            return ZoteroReadCapture(
                revision=resolved.revision,
                snapshot_path=resolved.snapshot_path,
                metadata_path=resolved.metadata_path,
                captured_at=resolved.captured_at,
                source_db_mtime_ns=resolved.source_db_mtime_ns,
                source_db_size=resolved.source_db_size,
                created=False,
            )

        metadata = {
            "schema_version": 1,
            "capture_revision": revision,
            "capture_path": str(final_snapshot),
            "zotero_source_revision": revision,
            "snapshot_sha256": revision,
            "captured_at": captured_at,
            "source_db_mtime_ns": int(source_stat.st_mtime_ns),
            "source_db_size": int(source_stat.st_size),
        }
        temporary_metadata = destination_root / f".{uuid4().hex}.json.tmp"
        try:
            temporary_metadata.write_text(
                json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _fsync_file(temporary_metadata)
            os.replace(temporary_metadata, final_metadata)
        finally:
            _remove_owned_temporary(temporary_metadata)

        return ZoteroReadCapture(
            revision=revision,
            snapshot_path=final_snapshot,
            metadata_path=final_metadata,
            captured_at=captured_at,
            source_db_mtime_ns=int(source_stat.st_mtime_ns),
            source_db_size=int(source_stat.st_size),
            created=created,
        )
    except ZoteroLiveCaptureError:
        _remove_owned_temporary(temporary_snapshot)
        raise
    except OSError as exc:
        _remove_owned_temporary(temporary_snapshot)
        raise ZoteroLiveCaptureError(
            "zotero_live_capture_failed",
            "The live Zotero database could not be captured safely.",
        ) from exc


def resolve_zotero_source_revision(
    revision: str,
    *,
    capture_dir: str | Path,
) -> ZoteroReadCapture:
    normalized = str(revision or "").strip()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_invalid",
            "The Zotero source revision is invalid.",
        )
    root = Path(capture_dir).resolve(strict=False)
    snapshot = root / f"{normalized}.sqlite"
    metadata_path = root / f"{normalized}.json"
    if not snapshot.is_file() or snapshot.is_symlink() or not metadata_path.is_file() or metadata_path.is_symlink():
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_unknown",
            "The Zotero source revision is not available.",
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision metadata is invalid.",
        ) from exc
    if not isinstance(metadata, dict):
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision metadata is invalid.",
        )
    try:
        snapshot_sha256 = _sha256_file(snapshot)
    except OSError as exc:
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision failed content validation.",
        ) from exc
    if (
        metadata.get("schema_version") != 1
        or metadata.get("capture_revision") != normalized
        or Path(str(metadata.get("capture_path") or "")).resolve(strict=False) != snapshot
        or metadata.get("zotero_source_revision") != normalized
        or metadata.get("snapshot_sha256") != normalized
        or snapshot_sha256 != normalized
    ):
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision failed content validation.",
        )
    captured_at = metadata.get("captured_at")
    source_mtime = metadata.get("source_db_mtime_ns")
    source_size = metadata.get("source_db_size")
    if (
        not isinstance(captured_at, str)
        or not captured_at
        or not isinstance(source_mtime, int)
        or isinstance(source_mtime, bool)
        or source_mtime < 0
        or not isinstance(source_size, int)
        or isinstance(source_size, bool)
        or source_size < 0
    ):
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision metadata is incomplete.",
        )
    return ZoteroReadCapture(
        revision=normalized,
        snapshot_path=snapshot,
        metadata_path=metadata_path,
        captured_at=captured_at,
        source_db_mtime_ns=source_mtime,
        source_db_size=source_size,
        created=False,
    )


def verify_zotero_capture_file(
    snapshot_path: str | Path,
    revision: str,
) -> None:
    normalized = str(revision or "").strip()
    path = Path(snapshot_path).resolve(strict=False)
    try:
        current_sha256 = _sha256_file(path) if path.is_file() else None
    except OSError:
        current_sha256 = None
    if (
        _SHA256_RE.fullmatch(normalized) is None
        or current_sha256 != normalized
        or path.is_symlink()
    ):
        raise ZoteroLiveCaptureError(
            "zotero_source_revision_corrupt",
            "The Zotero source revision failed content validation.",
        )


def _validate_capture_database(path: Path) -> None:
    try:
        with closing(
            sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise ZoteroLiveCaptureError(
                    "zotero_live_capture_invalid",
                    "The Zotero read capture failed SQLite integrity validation.",
                )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except ZoteroLiveCaptureError:
        raise
    except sqlite3.Error as exc:
        raise ZoteroLiveCaptureError(
            "zotero_live_capture_invalid",
            "The Zotero read capture could not be validated.",
        ) from exc
    missing = sorted(_CORE_TABLES - tables)
    if missing:
        raise ZoteroLiveCaptureError(
            "zotero_live_capture_invalid",
            "The Zotero read capture is missing required tables.",
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _remove_owned_temporary(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
