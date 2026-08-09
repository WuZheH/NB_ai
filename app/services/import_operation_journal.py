from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import types
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "import_operation_journal.v1"

_OP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_FILE_RE = re.compile(r"^[0-9a-f]{32}\.json$")
_MAX_STRING_LEN = 512

# ---------------------------------------------------------------------------
# shared directory-level lock registry
# ---------------------------------------------------------------------------

_STORE_LOCK_REGISTRY: dict[str, threading.RLock] = {}
_STORE_LOCK_REGISTRY_LOCK = threading.Lock()


def _store_lock_key(path: Path) -> str:
    key = str(path.resolve(strict=False))
    return key.casefold() if os.name == "nt" else key


def _get_store_lock(journal_dir: Path) -> threading.RLock:
    key = _store_lock_key(journal_dir)
    with _STORE_LOCK_REGISTRY_LOCK:
        lock = _STORE_LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCK_REGISTRY[key] = lock
        return lock


# ---------------------------------------------------------------------------
# valid status / stage
# ---------------------------------------------------------------------------

_VALID_STATUS = frozenset(
    {"accepted", "running", "committed", "failed", "orphaned"}
)
_VALID_STAGE = frozenset(
    {
        "confirmation_accepted",
        "body_import_started",
        "body_import_completed",
        "staging_snapshot_created",
        "staging_fts_started",
        "staging_fts_completed",
        "staging_vector_started",
        "staging_vector_completed",
        "derived_backup_started",
        "derived_backup_completed",
        "publish_started",
        "publish_completed",
        "final_verification_started",
        "final_verification_completed",
        "rollback_started",
        "rollback_completed",
        "receipt_persisted",
    }
)

_RUNNING_STAGES = frozenset({
    "body_import_started",
    "body_import_completed",
    "staging_snapshot_created",
    "staging_fts_started",
    "staging_fts_completed",
    "staging_vector_started",
    "staging_vector_completed",
    "derived_backup_started",
    "derived_backup_completed",
    "publish_started",
    "publish_completed",
    "final_verification_started",
    "final_verification_completed",
    "rollback_started",
    "rollback_completed",
})

_NON_TERMINAL_STAGES = frozenset({
    "confirmation_accepted",
    "body_import_started",
    "body_import_completed",
    "staging_snapshot_created",
    "staging_fts_started",
    "staging_fts_completed",
    "staging_vector_started",
    "staging_vector_completed",
    "derived_backup_started",
    "derived_backup_completed",
    "publish_started",
    "publish_completed",
    "final_verification_started",
    "final_verification_completed",
    "rollback_started",
    "rollback_completed",
})

_STAGE_ORDER: tuple[str, ...] = (
    "confirmation_accepted",
    "body_import_started",
    "body_import_completed",
    "staging_snapshot_created",
    "staging_fts_started",
    "staging_fts_completed",
    "staging_vector_started",
    "staging_vector_completed",
    "derived_backup_started",
    "derived_backup_completed",
    "publish_started",
    "publish_completed",
    "final_verification_started",
    "final_verification_completed",
    "rollback_started",
    "rollback_completed",
    "receipt_persisted",
)
_STAGE_INDEX: dict[str, int] = {s: i for i, s in enumerate(_STAGE_ORDER)}

# ---------------------------------------------------------------------------
# transition rules
# ---------------------------------------------------------------------------

_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "accepted": frozenset({"running", "failed", "orphaned"}),
    "running": frozenset({"committed", "failed", "orphaned"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "orphaned": frozenset(),
}

_TERMINAL_STATUS = frozenset({"committed", "failed", "orphaned"})

# ---------------------------------------------------------------------------
# secret field names
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS = frozenset(
    {
        "confirmation_token",
        "raw_confirmation_token",
        "api_key",
        "anthropic_api_key",
        "auth_token",
        "authorization",
        "bearer_token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)
_SECRET_SCAN_MAX_DEPTH = 10


# ---------------------------------------------------------------------------
# custom errors
# ---------------------------------------------------------------------------


class JournalError(RuntimeError):
    """Base for all journal errors."""


class JournalWriteError(JournalError):
    """Atomic write failed; the prior journal file is intact."""


class JournalValidationError(JournalError, ValueError):
    """Record payload failed schema or business-rule validation."""


class JournalConflictError(JournalError):
    """Expected state precondition not met (revision, status, duplicates)."""


# ---------------------------------------------------------------------------
# helpers — operation_id / sha256 / timestamps
# ---------------------------------------------------------------------------


def _validate_operation_id(value: str) -> str:
    if not isinstance(value, str) or not _OP_ID_RE.match(value):
        raise JournalValidationError(
            f"operation_id must be exactly 32 lowercase hex chars, "
            f"got {value!r}"
        )
    return value


def _validate_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise JournalValidationError(
            f"{label} must be exactly 64 lowercase hex chars, "
            f"got {value!r}"
        )
    return value


def _validate_nonempty_str(value: str, label: str, max_len: int = _MAX_STRING_LEN) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JournalValidationError(
            f"{label} must be a non-empty string, got {value!r}"
        )
    if len(value) > max_len:
        raise JournalValidationError(
            f"{label} exceeds max length ({len(value)} > {max_len})"
        )
    return value


def _validate_int_not_bool(value: object, label: str, min_val: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalValidationError(
            f"{label} must be an int (not bool), got {type(value).__name__}"
        )
    if value < min_val:
        raise JournalValidationError(
            f"{label} must be >= {min_val}, got {value}"
        )
    return value


def _validate_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise JournalValidationError(
            f"{label} must be a bool, got {type(value).__name__}"
        )
    return value


def _validate_optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise JournalValidationError(
        f"{label} must be a bool or None, got {type(value).__name__}"
    )


def _parse_timestamp(
    value: str | None | object,
    *,
    required: bool = False,
) -> datetime | None:
    if value is None:
        if required:
            raise JournalValidationError(
                "Timestamp is required but got None"
            )
        return None
    if not isinstance(value, str):
        raise JournalValidationError(
            f"Timestamp must be a string, got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped:
        if required:
            raise JournalValidationError(
                "Timestamp must be a non-empty ISO-8601 string"
            )
        return None
    try:
        dt = datetime.fromisoformat(stripped)
    except (ValueError, TypeError) as exc:
        raise JournalValidationError(
            f"Timestamp {value!r} is not valid ISO-8601: {exc}"
        ) from exc
    if dt.tzinfo is None:
        raise JournalValidationError(
            f"Timestamp {value!r} is missing a timezone; "
            f"naive datetimes are not accepted"
        )
    return dt.astimezone(timezone.utc)


def _format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# helpers — nested immutability
# ---------------------------------------------------------------------------


def _deep_freeze(obj: Any, depth: int = 0) -> Any:
    if depth > _SECRET_SCAN_MAX_DEPTH:
        raise JournalValidationError(
            "Maximum object nesting depth exceeded"
        )
    if isinstance(obj, (dict, types.MappingProxyType)):
        return types.MappingProxyType({
            key: _deep_freeze(value, depth + 1)
            for key, value in obj.items()
        })
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(value, depth + 1) for value in obj)
    return obj


def _deep_thaw(obj: Any) -> Any:
    if isinstance(obj, types.MappingProxyType):
        return {k: _deep_thaw(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_deep_thaw(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _deep_thaw(v) for k, v in obj.items()}
    return obj


def _deep_validate_no_nan(obj: Any, depth: int = 0) -> None:
    if depth > _SECRET_SCAN_MAX_DEPTH:
        raise JournalValidationError(
            "Maximum object nesting depth exceeded during NaN scan"
        )
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise JournalValidationError(
                "NaN and Infinity values are not allowed in journal data"
            )
    elif isinstance(obj, (types.MappingProxyType, dict)):
        for _k, v in obj.items():
            _deep_validate_no_nan(v, depth + 1)
    elif isinstance(obj, (tuple, list)):
        for item in obj:
            _deep_validate_no_nan(item, depth + 1)


def _deep_validate_json_types(obj: Any, depth: int = 0) -> None:
    if depth > _SECRET_SCAN_MAX_DEPTH:
        raise JournalValidationError(
            "Maximum object nesting depth exceeded during type scan"
        )
    if obj is None or isinstance(obj, (bool, int, float)):
        return
    if isinstance(obj, str):
        return
    if isinstance(obj, (dict, types.MappingProxyType)):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise JournalValidationError(
                    f"Non-string dict key {k!r} is not allowed "
                    f"in journal nested data"
                )
            _deep_validate_json_types(v, depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _deep_validate_json_types(item, depth + 1)
        return
    raise JournalValidationError(
        f"Type {type(obj).__name__} is not allowed "
        f"in journal nested data"
    )


def _deep_validate_no_secrets(obj: Any, depth: int = 0) -> None:
    """Recursively scan *obj* for forbidden secret-key names.

    .. note::
       This scan only rejects structured key names.  It cannot detect
       secrets embedded in free-form text values.
    """
    if depth > _SECRET_SCAN_MAX_DEPTH:
        raise JournalValidationError(
            "Maximum object nesting depth exceeded during secret scan"
        )
    if isinstance(obj, (types.MappingProxyType, dict)):
        for key in obj:
            if isinstance(key, str):
                normalized = key.strip().lower()
                if normalized in _FORBIDDEN_KEYS:
                    raise JournalValidationError(
                        f"Forbidden secret key {key!r} found in "
                        f"nested journal data"
                    )
            _deep_validate_no_secrets(obj[key], depth + 1)
    elif isinstance(obj, (tuple, list)):
        for item in obj:
            _deep_validate_no_secrets(item, depth + 1)


def _json_dumps(data: Any) -> str:
    try:
        payload = json.dumps(
            data, ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise JournalValidationError(
            f"Cannot serialize journal: {exc}"
        ) from exc
    return payload + "\n"


# ---------------------------------------------------------------------------
# identity fields — protected from update
# ---------------------------------------------------------------------------

_IDENTITY_FIELDS = frozenset({
    "operation_id",
    "schema_version",
    "operation_type",
    "confirmation_token_digest",
    "transaction_fingerprint",
    "source_revision_fingerprint",
    "source_pdf_sha256",
    "title",
    "zotero_item_key",
    "zotero_attachment_key",
    "owner_process_id",
    "owner_process_started_at",
    "owner_thread_id",
    "started_at",
    "revision",
    "updated_at",
})


# ---------------------------------------------------------------------------
# status / stage transition validation
# ---------------------------------------------------------------------------


def _validate_status_transition(
    current_status: str, new_status: str,
) -> None:
    if new_status == current_status:
        return
    allowed = _ALLOWED_STATUS_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        raise JournalValidationError(
            f"Status cannot transition from {current_status!r} "
            f"to {new_status!r}"
        )


def _validate_status_stage(
    record: "ImportOperationJournal",
) -> None:
    status = record.status
    stage = record.stage

    if status == "accepted":
        if stage != "confirmation_accepted":
            raise JournalValidationError(
                f"Status 'accepted' requires stage "
                f"'confirmation_accepted', got {stage!r}"
            )
        if record.writes_performed is not None:
            raise JournalValidationError(
                "Status 'accepted' requires writes_performed=None"
            )
        if record.document_id is not None:
            raise JournalValidationError(
                "Status 'accepted' requires document_id=None"
            )
        if record.chunk_count != 0:
            raise JournalValidationError(
                "Status 'accepted' requires chunk_count=0"
            )
        if record.error is not None:
            raise JournalValidationError(
                "Status 'accepted' must not carry an error"
            )
        if record.completion_receipt is not None:
            raise JournalValidationError(
                "Status 'accepted' must not carry a completion_receipt"
            )

    elif status == "running":
        if stage not in _RUNNING_STAGES:
            raise JournalValidationError(
                f"Status 'running' is not compatible with "
                f"stage {stage!r}"
            )
        if record.error is not None:
            raise JournalValidationError(
                "Status 'running' must not carry an error"
            )
        if record.completion_receipt is not None:
            raise JournalValidationError(
                "Status 'running' must not carry a completion_receipt"
            )
        if record.document_id is None:
            pass  # ok — not yet assigned
        elif record.document_id is not None:
            _validate_int_not_bool(record.document_id, "document_id", min_val=1)

    elif status == "committed":
        if stage != "receipt_persisted":
            raise JournalValidationError(
                f"Status 'committed' requires stage "
                f"'receipt_persisted', got {stage!r}"
            )
        if record.writes_performed is not True:
            raise JournalValidationError(
                "Status 'committed' requires writes_performed=True"
            )
        _validate_int_not_bool(record.document_id, "document_id", min_val=1)
        _validate_int_not_bool(record.chunk_count, "chunk_count", min_val=1)
        if record.error is not None:
            raise JournalValidationError(
                "Status 'committed' must not carry an error"
            )
        if record.completion_receipt is None:
            raise JournalValidationError(
                "Status 'committed' requires a non-null completion_receipt"
            )

    elif status == "failed":
        if stage != "receipt_persisted":
            raise JournalValidationError(
                f"Status 'failed' requires stage "
                f"'receipt_persisted', got {stage!r}"
            )
        if record.error is None:
            raise JournalValidationError(
                "Status 'failed' requires a non-null error"
            )
        if record.completion_receipt is None:
            raise JournalValidationError(
                "Status 'failed' requires a non-null completion_receipt"
            )

    elif status == "orphaned":
        if stage == "receipt_persisted":
            raise JournalValidationError(
                "Status 'orphaned' must not have stage "
                "'receipt_persisted'"
            )
        if record.error is None:
            raise JournalValidationError(
                "Status 'orphaned' requires a non-null error"
            )
        if record.completion_receipt is not None:
            raise JournalValidationError(
                "Status 'orphaned' must not carry a completion_receipt"
            )


def _validate_stage_transition(
    current_stage: str,
    new_stage: str,
    new_status: str,
) -> None:
    if new_stage == current_stage:
        return

    if new_stage not in _STAGE_INDEX:
        raise JournalValidationError(f"Invalid stage {new_stage!r}")

    if current_stage == "receipt_persisted":
        raise JournalValidationError(
            "Cannot advance beyond receipt_persisted"
        )

    current_idx = _STAGE_INDEX[current_stage]

    if new_stage == "rollback_started":
        if current_stage in ("rollback_completed", "receipt_persisted"):
            raise JournalValidationError(
                f"Cannot transition from {current_stage!r} "
                f"to rollback_started"
            )
        return

    if current_stage == "rollback_completed":
        if new_stage == "receipt_persisted":
            if new_status != "failed":
                raise JournalValidationError(
                    "After rollback_completed, receipt_persisted "
                    "requires status 'failed'"
                )
            return
        raise JournalValidationError(
            f"Cannot transition from rollback_completed "
            f"to {new_stage!r}"
        )

    if current_stage == "rollback_started":
        if new_stage == "rollback_completed":
            return
        if new_stage == "receipt_persisted":
            if new_status != "failed":
                raise JournalValidationError(
                    "After rollback_started, receipt_persisted "
                    "requires status 'failed'"
                )
            return
        raise JournalValidationError(
            f"After rollback_started only rollback_completed "
            f"or receipt_persisted(failed) is allowed, "
            f"not {new_stage!r}"
        )

    if new_stage == "receipt_persisted" and new_status == "committed":
        if current_stage != "final_verification_completed":
            raise JournalValidationError(
                f"Status 'committed' with stage 'receipt_persisted' "
                f"requires current stage 'final_verification_completed', "
                f"got {current_stage!r}"
            )
        return

    if new_stage == "receipt_persisted" and new_status == "failed":
        if current_stage in _NON_TERMINAL_STAGES:
            return
        raise JournalValidationError(
            f"Cannot transition to receipt_persisted(failed) "
            f"from {current_stage!r}"
        )

    new_idx = _STAGE_INDEX[new_stage]
    if new_idx <= current_idx:
        raise JournalValidationError(
            f"Stage cannot regress from {current_stage!r} "
            f"(index {current_idx}) to {new_stage!r} "
            f"(index {new_idx})"
        )


def _validate_record_transition(
    current: "ImportOperationJournal",
    changes: dict[str, Any],
) -> None:
    new_status = changes.get("status", str(current.status))
    new_stage = changes.get("stage", str(current.stage))
    _validate_status_transition(current.status, new_status)
    _validate_stage_transition(current.stage, new_stage, new_status)


# ---------------------------------------------------------------------------
# ImportOperationJournal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportOperationJournal:
    """Persistent record of a single import_document operation."""

    schema_version: str = SCHEMA_VERSION
    operation_id: str = ""
    operation_type: str = "import_document"
    confirmation_token_digest: str = ""
    transaction_fingerprint: str = ""
    source_revision_fingerprint: str = ""
    title: str = ""
    zotero_item_key: str = ""
    zotero_attachment_key: str = ""
    source_pdf_sha256: str = ""
    owner_process_id: int = 0
    owner_process_started_at: str = ""
    owner_thread_id: int = 0
    started_at: str = ""
    updated_at: str = ""
    heartbeat_at: str = ""
    revision: int = 0
    status: str = "accepted"
    stage: str = "confirmation_accepted"
    writes_performed: bool | None = None
    document_id: int | None = None
    chunk_count: int = 0
    error: Mapping[str, Any] | None = None
    rollback: Mapping[str, Any] | None = None
    warnings: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    completion_receipt: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise JournalValidationError(
                f"schema_version must be {SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )

        _validate_operation_id(self.operation_id)
        _validate_sha256(
            self.confirmation_token_digest, "confirmation_token_digest"
        )
        _validate_sha256(self.source_pdf_sha256, "source_pdf_sha256")
        _validate_sha256(
            self.transaction_fingerprint, "transaction_fingerprint"
        )
        _validate_nonempty_str(
            self.source_revision_fingerprint, "source_revision_fingerprint"
        )

        _validate_int_not_bool(self.revision, "revision")
        _validate_int_not_bool(self.owner_process_id, "owner_process_id")
        _validate_int_not_bool(self.owner_thread_id, "owner_thread_id")
        _validate_int_not_bool(self.chunk_count, "chunk_count")
        _validate_optional_bool(self.writes_performed, "writes_performed")
        if self.document_id is not None:
            _validate_int_not_bool(
                self.document_id, "document_id", min_val=1
            )

        if self.status not in _VALID_STATUS:
            raise JournalValidationError(
                f"Invalid status {self.status!r}; "
                f"expected one of {sorted(_VALID_STATUS)}"
            )
        if self.stage not in _VALID_STAGE:
            raise JournalValidationError(
                f"Invalid stage {self.stage!r}; "
                f"expected one of {sorted(_VALID_STAGE)}"
            )

        started = _parse_timestamp(self.started_at, required=True)
        updated = _parse_timestamp(self.updated_at, required=True)
        heartbeat = _parse_timestamp(self.heartbeat_at)
        owner_started = _parse_timestamp(self.owner_process_started_at)

        assert started is not None
        assert updated is not None
        object.__setattr__(self, "started_at", _format_timestamp(started))
        object.__setattr__(self, "updated_at", _format_timestamp(updated))
        object.__setattr__(
            self, "heartbeat_at", _format_timestamp(heartbeat)
        )
        object.__setattr__(
            self,
            "owner_process_started_at",
            _format_timestamp(owner_started),
        )

        if updated < started:
            raise JournalValidationError(
                f"updated_at ({self.updated_at}) must not be "
                f"earlier than started_at ({self.started_at})"
            )
        if heartbeat is not None and heartbeat < started:
            raise JournalValidationError(
                f"heartbeat_at ({self.heartbeat_at}) must not be "
                f"earlier than started_at ({self.started_at})"
            )

        _validate_status_stage(self)

        for container in (
            self.error,
            self.rollback,
            self.warnings,
            self.completion_receipt,
        ):
            _deep_validate_no_secrets(container)
            _deep_validate_no_nan(container)
            _deep_validate_json_types(container)

        object.__setattr__(self, "error", _deep_freeze(self.error))
        object.__setattr__(self, "rollback", _deep_freeze(self.rollback))
        object.__setattr__(self, "warnings", _deep_freeze(self.warnings))
        object.__setattr__(
            self, "completion_receipt", _deep_freeze(self.completion_receipt)
        )

        if self.completion_receipt is not None and not isinstance(
            self.completion_receipt, types.MappingProxyType
        ):
            raise JournalValidationError(
                "completion_receipt must be a JSON object or null"
            )
        if self.error is not None and not isinstance(
            self.error, types.MappingProxyType
        ):
            raise JournalValidationError(
                "error must be a JSON object or null"
            )
        if self.rollback is not None and not isinstance(
            self.rollback, types.MappingProxyType
        ):
            raise JournalValidationError(
                "rollback must be a JSON object or null"
            )
        if not isinstance(self.warnings, tuple):
            raise JournalValidationError("warnings must be a list")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for field_name in (
            "schema_version",
            "operation_id",
            "operation_type",
            "confirmation_token_digest",
            "transaction_fingerprint",
            "source_revision_fingerprint",
            "title",
            "zotero_item_key",
            "zotero_attachment_key",
            "source_pdf_sha256",
            "owner_process_id",
            "owner_process_started_at",
            "owner_thread_id",
            "started_at",
            "updated_at",
            "heartbeat_at",
            "revision",
            "status",
            "stage",
            "writes_performed",
            "document_id",
            "chunk_count",
            "error",
            "rollback",
            "warnings",
            "completion_receipt",
        ):
            raw = getattr(self, field_name)
            value = _deep_thaw(raw) if raw is not None else raw
            if value is not None or field_name in (
                "document_id", "error", "rollback",
                "completion_receipt", "warnings",
            ):
                d[field_name] = value
        return d

    @classmethod
    def from_dict(
        cls, data: dict[str, Any]
    ) -> "ImportOperationJournal":
        allowed = {
            "schema_version", "operation_id", "operation_type",
            "confirmation_token_digest", "transaction_fingerprint",
            "source_revision_fingerprint", "title",
            "zotero_item_key", "zotero_attachment_key",
            "source_pdf_sha256", "owner_process_id",
            "owner_process_started_at", "owner_thread_id",
            "started_at", "updated_at", "heartbeat_at",
            "revision", "status", "stage", "writes_performed",
            "document_id", "chunk_count",
            "error", "rollback", "warnings", "completion_receipt",
        }
        unknown = set(data) - allowed
        if unknown:
            raise JournalValidationError(
                f"Unknown fields in journal data: {sorted(unknown)}"
            )
        safe = deepcopy({k: data.get(k) for k in allowed if k in data})
        return cls(**safe)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# helpers — timestamps for sorting
# ---------------------------------------------------------------------------


def _started_dt(record: ImportOperationJournal) -> datetime:
    dt = _parse_timestamp(record.started_at, required=False)
    return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)


def _updated_dt(record: ImportOperationJournal) -> datetime:
    dt = _parse_timestamp(record.updated_at, required=False)
    return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ImportOperationJournalStore
# ---------------------------------------------------------------------------


class ImportOperationJournalStore:
    """Persistent store for import operation journals."""

    def __init__(self, journal_dir: Path) -> None:
        self._journal_dir = Path(journal_dir)
        self._lock = _get_store_lock(self._journal_dir)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def create(
        self, record: ImportOperationJournal
    ) -> ImportOperationJournal:
        if not isinstance(record, ImportOperationJournal):
            raise TypeError(
                "record must be an ImportOperationJournal instance"
            )

        with self._lock:
            # Initial state constraints — create must start from a
            # pristine accepted record.
            if record.revision != 0:
                raise JournalValidationError(
                    f"revision must be 0 on create, got {record.revision}"
                )
            if record.operation_type != "import_document":
                raise JournalValidationError(
                    "create requires operation_type='import_document'"
                )
            if record.status != "accepted":
                raise JournalValidationError(
                    "create requires status='accepted'"
                )
            if record.stage != "confirmation_accepted":
                raise JournalValidationError(
                    "create requires stage='confirmation_accepted'"
                )
            if record.writes_performed is not None:
                raise JournalValidationError(
                    "create requires writes_performed=None"
                )
            if record.document_id is not None:
                raise JournalValidationError(
                    "create requires document_id=None"
                )
            if record.chunk_count != 0:
                raise JournalValidationError(
                    "create requires chunk_count=0"
                )
            if record.error is not None:
                raise JournalValidationError(
                    "create requires error=None"
                )
            if record.rollback is not None:
                raise JournalValidationError(
                    "create requires rollback=None"
                )
            if record.completion_receipt is not None:
                raise JournalValidationError(
                    "create requires completion_receipt=None"
                )

            final_path = self._journal_path(record.operation_id)
            if final_path.exists():
                raise JournalConflictError(
                    f"Operation {record.operation_id!r} already exists"
                )

            existing = self._find_by_token_digest_unlocked(
                record.confirmation_token_digest
            )
            if existing:
                raise JournalConflictError(
                    f"A journal already exists for confirmation "
                    f"token digest "
                    f"{record.confirmation_token_digest[:16]}…"
                )

            self._write_atomic(record.to_dict(), final_path)
            return record

    def read(self, operation_id: str) -> ImportOperationJournal | None:
        with self._lock:
            return self._read_record_unlocked(operation_id)

    def update(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        expected_status: str | None = None,
        **changes: Any,
    ) -> ImportOperationJournal:
        _validate_operation_id(operation_id)

        # Strict expected_revision validation.
        _validate_int_not_bool(
            expected_revision, "expected_revision", min_val=0
        )
        if expected_status is not None:
            if not isinstance(expected_status, str):
                raise JournalValidationError(
                    f"expected_status must be a str or None, "
                    f"got {type(expected_status).__name__}"
                )
            if expected_status not in _VALID_STATUS:
                raise JournalValidationError(
                    f"Invalid expected_status {expected_status!r}"
                )

        with self._lock:
            current = self._read_record_unlocked(operation_id)
            if current is None:
                raise JournalConflictError(
                    f"Operation {operation_id!r} not found"
                )

            if current.revision != expected_revision:
                raise JournalConflictError(
                    f"Expected revision {expected_revision} but "
                    f"current revision is {current.revision}"
                )

            if (
                expected_status is not None
                and current.status != expected_status
            ):
                raise JournalConflictError(
                    f"Expected status {expected_status!r} but "
                    f"current status is {current.status!r}"
                )

            # Reject modification of any identity / immutable field.
            for forbidden in _IDENTITY_FIELDS:
                if forbidden in changes:
                    raise JournalValidationError(
                        f"{forbidden} must not be modified "
                        f"after creation"
                    )

            if current.status in _TERMINAL_STATUS:
                raise JournalConflictError(
                    f"Status {current.status!r} is terminal; "
                    f"no further updates allowed"
                )

            new_status = changes.get("status", current.status)
            if new_status == current.status:
                if current.status not in ("accepted", "running"):
                    raise JournalConflictError(
                        f"Status {current.status!r} is terminal; "
                        f"no further updates allowed"
                    )

            _validate_record_transition(current, changes)

            for nested_field in (
                "error", "rollback", "warnings", "completion_receipt",
            ):
                if nested_field in changes:
                    _deep_validate_no_secrets(changes[nested_field])
                    _deep_validate_no_nan(changes[nested_field])
                    _deep_validate_json_types(changes[nested_field])

            for ts_field in ("heartbeat_at",):
                if ts_field in changes:
                    raw = changes[ts_field]
                    if raw is not None:
                        dt = _parse_timestamp(raw)
                        if dt is not None:
                            changes[ts_field] = _format_timestamp(dt)

            # Validate writes_performed type if present in changes.
            if "writes_performed" in changes:
                _validate_optional_bool(
                    changes["writes_performed"], "writes_performed"
                )
            if "document_id" in changes and changes["document_id"] is not None:
                _validate_int_not_bool(
                    changes["document_id"], "document_id", min_val=1
                )
            if "chunk_count" in changes:
                _validate_int_not_bool(
                    changes["chunk_count"], "chunk_count"
                )

            now = _format_timestamp(datetime.now(timezone.utc))
            merged = dict(current.to_dict())
            merged.update(changes)
            merged["updated_at"] = now
            merged["revision"] = current.revision + 1
            if "heartbeat_at" not in changes:
                pass

            updated = ImportOperationJournal.from_dict(merged)
            _validate_status_stage(updated)

            self._write_atomic(
                updated.to_dict(),
                self._journal_path(operation_id),
            )
            return updated

    def find_by_token_digest(
        self, token_digest: str
    ) -> list[ImportOperationJournal]:
        with self._lock:
            return self._find_by_token_digest_unlocked(token_digest)

    def find_by_document_id(
        self, document_id: int
    ) -> list[ImportOperationJournal]:
        """Return fail-closed, deterministically ordered records for a document."""
        _validate_int_not_bool(
            document_id,
            "document_id",
            min_val=1,
        )
        with self._lock:
            return [
                record
                for record in self._scan_records_unlocked()
                if record.document_id == document_id
            ]

    def find_latest_by_token_digest(
        self, token_digest: str
    ) -> ImportOperationJournal | None:
        with self._lock:
            candidates = self._find_by_token_digest_unlocked(
                token_digest
            )
            if not candidates:
                return None
            active = [
                r for r in candidates
                if r.status in ("accepted", "running")
            ]
            if len(active) >= 2:
                ids = [r.operation_id for r in active]
                raise JournalConflictError(
                    f"Multiple active operations for digest "
                    f"{token_digest[:16]}…: {ids}"
                )
            return max(
                candidates,
                key=lambda r: (_updated_dt(r), r.operation_id),
            )

    def resolve_by_token_digest(
        self, token_digest: str
    ) -> ImportOperationJournal | None:
        with self._lock:
            candidates = self._find_by_token_digest_unlocked(
                token_digest
            )
            if len(candidates) == 0:
                return None
            if len(candidates) == 1:
                return candidates[0]
            ids = [r.operation_id for r in candidates]
            raise JournalConflictError(
                f"Multiple journals for digest "
                f"{token_digest[:16]}…: {ids}"
            )

    # ------------------------------------------------------------------
    # internal helpers — unified record read
    # ------------------------------------------------------------------

    def _read_record_unlocked(
        self, operation_id: str
    ) -> ImportOperationJournal | None:
        _validate_operation_id(operation_id)
        path = self._journal_path(operation_id)

        if path.is_symlink():
            raise JournalValidationError(
                f"Journal path {path} is a symlink; refusing to follow"
            )
        if not path.exists():
            return None
        if not path.is_file():
            raise JournalValidationError(
                f"Journal path {path} exists but is not a regular file"
            )

        data = self._read_json(path)
        file_op_id = data.get("operation_id")
        if file_op_id != operation_id:
            raise JournalValidationError(
                f"Journal file {path.name} contains "
                f"operation_id {file_op_id!r}, expected "
                f"{operation_id!r}"
            )
        return ImportOperationJournal.from_dict(data)

    # ------------------------------------------------------------------
    # internal helpers — fail-closed scanning
    # ------------------------------------------------------------------

    def _find_by_token_digest_unlocked(
        self, token_digest: str
    ) -> list[ImportOperationJournal]:
        _validate_sha256(token_digest, "token_digest")

        return [
            record
            for record in self._scan_records_unlocked()
            if record.confirmation_token_digest == token_digest
        ]

    def _scan_records_unlocked(self) -> list[ImportOperationJournal]:
        """Read every journal-shaped entry without following unsafe paths."""

        journal_dir = self._journal_dir
        if journal_dir.is_symlink():
            raise JournalValidationError(
                "Journal directory is a symlink; scan aborted"
            )
        if not journal_dir.exists():
            return []
        if not journal_dir.is_dir():
            raise JournalValidationError(
                "Journal path exists but is not a directory; scan aborted"
            )

        results: list[ImportOperationJournal] = []
        for entry in sorted(journal_dir.iterdir()):
            if not _JOURNAL_FILE_RE.match(entry.name):
                continue

            # Entries matching the journal filename pattern MUST be
            # valid, readable journal files.  Fail closed on anything
            # else.
            if entry.is_symlink():
                raise JournalValidationError(
                    f"Journal entry {entry} is a symlink; scan aborted"
                )
            if not entry.is_file():
                raise JournalValidationError(
                    f"Journal entry {entry} matches filename "
                    f"pattern but is not a regular file; scan aborted"
                )

            data = self._read_json(entry)
            file_op_id = data.get("operation_id")
            expected_op_id = entry.stem
            if file_op_id != expected_op_id:
                raise JournalValidationError(
                    f"Journal file {entry.name} contains "
                    f"operation_id {file_op_id!r}, expected "
                    f"{expected_op_id!r}"
                )

            record = ImportOperationJournal.from_dict(data)
            results.append(record)

        results.sort(key=lambda r: (_started_dt(r), r.operation_id))
        return results

    # ------------------------------------------------------------------
    # internal helpers — filesystem
    # ------------------------------------------------------------------

    def _journal_path(self, operation_id: str) -> Path:
        _validate_operation_id(operation_id)
        return self._journal_dir / f"{operation_id}.json"

    def _ensure_dir(self) -> None:
        self._journal_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise JournalValidationError(
                f"Journal {path} is not valid UTF-8: {exc}"
            ) from exc
        except OSError as exc:
            raise JournalWriteError(
                f"Cannot read journal {path}: {exc}"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JournalValidationError(
                f"Journal {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise JournalValidationError(
                f"Journal {path} must be a JSON object"
            )
        return data

    def _write_atomic(
        self, data: dict[str, Any], final_path: Path,
    ) -> None:
        self._ensure_dir()

        payload = _json_dumps(data)
        payload_bytes = payload.encode("utf-8")

        fd, temp_path = tempfile.mkstemp(
            dir=str(self._journal_dir),
            prefix=f".{final_path.stem}.",
            suffix=".json",
        )
        try:
            view = memoryview(payload_bytes)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(
                        "os.write returned 0; incomplete write"
                    )
                view = view[written:]

            os.fsync(fd)
            os.close(fd)
            fd = -1

            try:
                os.replace(temp_path, final_path)
            except OSError as exc:
                raise JournalWriteError(
                    f"Atomic replace failed for "
                    f"{final_path.name}: {exc}"
                ) from exc
        except JournalWriteError:
            raise
        except Exception as exc:
            raise JournalWriteError(
                f"Failed to write journal "
                f"{final_path.name}: {exc}"
            ) from exc
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(temp_path)
            except OSError:
                pass
